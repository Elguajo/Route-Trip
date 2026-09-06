"""Deterministic single-day ordering based on a provider-neutral matrix."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constraints import DayTimeBudget, resolve_visit_duration
from .routing import (
    CoordinateSnapshot,
    RoutingFailure,
    RoutingProfile,
    RoutingProvider,
    TravelLocation,
    TravelMatrix,
    unsupported_matrix_failure,
)


class DayItemSnapshot(BaseModel):
    """The persisted planner fields required to calculate one day's order."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    sequence: int
    lat: float | None = None
    lng: float | None = None
    visit_duration_minutes: int = Field(default=0, ge=0, le=24 * 60)

    @classmethod
    def from_trip_items(cls, items: Iterable[object]) -> tuple["DayItemSnapshot", ...]:
        """Create an immutable calculation input from persisted ``TripItem`` rows."""

        return tuple(
            cls(
                item_id=item.id,
                sequence=item.sequence,
                lat=item.lat,
                lng=item.lng,
                visit_duration_minutes=resolve_visit_duration(item).minutes,
            )
            for item in items
        )

    def travel_location(self) -> TravelLocation | None:
        """Return a valid location, retaining invalid or missing coordinates as diagnostics."""

        if self.lat is None or self.lng is None:
            return None
        if not isfinite(self.lat) or not isfinite(self.lng):
            return None
        if not -90 <= self.lat <= 90 or not -180 <= self.lng <= 180:
            return None
        return TravelLocation(lat=self.lat, lng=self.lng)


class DayOptimizationDiagnosticKind(str, Enum):
    COORDINATELESS_ITEM = "coordinateless_item"
    MATRIX_FAILURE = "matrix_failure"
    INCOMPLETE_MATRIX = "incomplete_matrix"
    MATRIX_SNAPSHOT_MISMATCH = "matrix_snapshot_mismatch"
    TIME_BUDGET_OVERFLOW = "time_budget_overflow"


class DayScheduleItemEstimate(BaseModel):
    """A deterministic local-time estimate for one item in the proposed order."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    arrival_time: str
    departure_time: str
    travel_minutes_before: int = Field(ge=0)
    visit_minutes: int = Field(ge=0)


class DayScheduleEstimate(BaseModel):
    """Matrix-backed visit/travel schedule within one local planning window."""

    model_config = ConfigDict(frozen=True)

    start_time: str
    end_time: str
    usable_minutes: int = Field(ge=1)
    travel_minutes: int = Field(ge=0)
    visit_minutes: int = Field(ge=0)
    total_minutes: int = Field(ge=0)
    overflow_minutes: int = Field(ge=0)
    items: tuple[DayScheduleItemEstimate, ...]


class DayOptimizationDiagnostic(BaseModel):
    """A reason a day result was only partially or not routable."""

    model_config = ConfigDict(frozen=True)

    kind: DayOptimizationDiagnosticKind
    message: str
    item_ids: tuple[int, ...] = ()
    routing_failure: RoutingFailure | None = None


class DayRouteCost(BaseModel):
    """Total travel for the routable items in one proposed order."""

    model_config = ConfigDict(frozen=True)

    duration_s: float = Field(ge=0)
    distance_m: float | None = Field(default=None, ge=0)


class DayCostComparison(BaseModel):
    """Before/after cost comparison for a deterministic day calculation."""

    model_config = ConfigDict(frozen=True)

    starting: DayRouteCost
    optimized: DayRouteCost
    duration_saved_s: float
    distance_saved_m: float | None = None


class DayOptimizationResult(BaseModel):
    """A non-mutating order proposal and its matrix-backed cost comparison."""

    model_config = ConfigDict(frozen=True)

    starting_item_ids: tuple[int, ...]
    optimized_item_ids: tuple[int, ...]
    cost_comparison: DayCostComparison | None = None
    schedule: DayScheduleEstimate | None = None
    diagnostics: tuple[DayOptimizationDiagnostic, ...] = ()


class DayOptimizationPreviewRequest(BaseModel):
    """Input shared by preview and explicit apply operations."""

    profile: RoutingProfile


class DayOptimizationApplyRequest(DayOptimizationPreviewRequest):
    """Apply only the order calculated from the itinerary the user previewed."""

    starting_item_ids: tuple[int, ...]

    @field_validator("starting_item_ids")
    @classmethod
    def _require_unique_item_ids(cls, item_ids: tuple[int, ...]) -> tuple[int, ...]:
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("starting_item_ids must not contain duplicates")
        return item_ids


class DayManualReorderRequest(BaseModel):
    """The complete persisted order for one existing day."""

    item_ids: tuple[int, ...]

    @field_validator("item_ids")
    @classmethod
    def _require_unique_item_ids(cls, item_ids: tuple[int, ...]) -> tuple[int, ...]:
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("item_ids must not contain duplicates")
        return item_ids


class DayOptimizationApplyResult(DayOptimizationResult):
    """A calculated result whose proposed sequence was persisted."""

    applied: bool = True


class TripOptimizer:
    """Optimize one persisted day without selecting or falling back between providers."""

    def __init__(self, routing_provider: RoutingProvider) -> None:
        self._routing_provider = routing_provider

    async def optimize_day(
        self,
        items: Iterable[DayItemSnapshot],
        profile: RoutingProfile,
        time_budget: DayTimeBudget | None = None,
    ) -> DayOptimizationResult:
        ordered_items = tuple(sorted(items, key=lambda item: (item.sequence, item.item_id)))
        starting_item_ids = tuple(item.item_id for item in ordered_items)
        routable = tuple(
            (index, item, location)
            for index, item in enumerate(ordered_items)
            if (location := item.travel_location()) is not None
        )
        coordinateless_item_ids = tuple(
            item.item_id for item in ordered_items if item.travel_location() is None
        )
        diagnostics: list[DayOptimizationDiagnostic] = []
        if coordinateless_item_ids:
            diagnostics.append(
                DayOptimizationDiagnostic(
                    kind=DayOptimizationDiagnosticKind.COORDINATELESS_ITEM,
                    item_ids=coordinateless_item_ids,
                    message=(
                        "Items without valid coordinates were retained in their original "
                        "sequence positions and excluded from travel cost"
                    ),
                )
            )

        if len(routable) < 2:
            zero_cost = DayRouteCost(duration_s=0, distance_m=0)
            return self._with_schedule(
                starting_item_ids=starting_item_ids,
                optimized_item_ids=starting_item_ids,
                cost_comparison=DayCostComparison(
                    starting=zero_cost,
                    optimized=zero_cost,
                    duration_saved_s=0,
                    distance_saved_m=0,
                ),
                diagnostics=diagnostics,
                ordered_items=ordered_items,
                matrix=None,
                time_budget=time_budget,
            )

        capability = self._routing_provider.capability
        if not capability.supports_matrix(profile):
            return self._unavailable_result(
                starting_item_ids,
                diagnostics,
                unsupported_matrix_failure(capability, profile),
            )

        snapshot = CoordinateSnapshot(coordinates=tuple(location for _, _, location in routable))
        matrix_result = await self._routing_provider.get_matrix(snapshot, profile)
        if isinstance(matrix_result, RoutingFailure):
            return self._unavailable_result(starting_item_ids, diagnostics, matrix_result)
        if matrix_result.snapshot != snapshot:
            diagnostics.append(
                DayOptimizationDiagnostic(
                    kind=DayOptimizationDiagnosticKind.MATRIX_SNAPSHOT_MISMATCH,
                    message="The routing provider returned a matrix for different coordinates",
                )
            )
            return self._result_without_comparison(starting_item_ids, diagnostics)
        if not self._is_complete(matrix_result):
            diagnostics.append(
                DayOptimizationDiagnostic(
                    kind=DayOptimizationDiagnosticKind.INCOMPLETE_MATRIX,
                    message="The routing matrix contains unreachable item pairs",
                )
            )
            return self._result_without_comparison(starting_item_ids, diagnostics)

        starting_indices = tuple(range(len(routable)))
        starting_cost = self._cost(starting_indices, matrix_result)
        candidate_indices = self._improve_order(starting_indices, matrix_result)
        candidate_cost = self._cost(candidate_indices, matrix_result)
        optimized_indices = (
            candidate_indices
            if candidate_cost.duration_s < starting_cost.duration_s
            else starting_indices
        )
        optimized_item_ids = self._merge_routable_order(
            ordered_items,
            tuple(routable[index][1].item_id for index in optimized_indices),
        )
        optimized_cost = self._cost(optimized_indices, matrix_result)
        return self._with_schedule(
            starting_item_ids=starting_item_ids,
            optimized_item_ids=optimized_item_ids,
            cost_comparison=DayCostComparison(
                starting=starting_cost,
                optimized=optimized_cost,
                duration_saved_s=starting_cost.duration_s - optimized_cost.duration_s,
                distance_saved_m=(
                    None
                    if starting_cost.distance_m is None or optimized_cost.distance_m is None
                    else starting_cost.distance_m - optimized_cost.distance_m
                ),
            ),
            diagnostics=diagnostics,
            ordered_items=ordered_items,
            matrix=matrix_result,
            time_budget=time_budget,
        )

    @classmethod
    def _with_schedule(
        cls,
        *,
        starting_item_ids: tuple[int, ...],
        optimized_item_ids: tuple[int, ...],
        cost_comparison: DayCostComparison | None,
        diagnostics: list[DayOptimizationDiagnostic],
        ordered_items: tuple[DayItemSnapshot, ...],
        matrix: TravelMatrix | None,
        time_budget: DayTimeBudget | None,
    ) -> DayOptimizationResult:
        schedule = cls._schedule(optimized_item_ids, ordered_items, matrix, time_budget)
        if schedule is not None and schedule.overflow_minutes:
            overflow_item_ids = tuple(
                item.item_id
                for item in schedule.items
                if (
                    _absolute_minutes(item.departure_time)
                    - _absolute_minutes(time_budget.start_time)
                    > time_budget.maximum_usable_minutes
                )
            )
            diagnostics.append(
                DayOptimizationDiagnostic(
                    kind=DayOptimizationDiagnosticKind.TIME_BUDGET_OVERFLOW,
                    item_ids=overflow_item_ids,
                    message=(
                        f"The proposed day exceeds its {time_budget.maximum_usable_minutes}-minute "
                        f"window by {schedule.overflow_minutes} minutes"
                    ),
                )
            )
        return DayOptimizationResult(
            starting_item_ids=starting_item_ids,
            optimized_item_ids=optimized_item_ids,
            cost_comparison=cost_comparison,
            schedule=schedule,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _schedule(
        optimized_item_ids: tuple[int, ...],
        ordered_items: tuple[DayItemSnapshot, ...],
        matrix: TravelMatrix | None,
        time_budget: DayTimeBudget | None,
    ) -> DayScheduleEstimate | None:
        """Build a schedule only when every travel leg has matrix evidence.

        Missing coordinates deliberately do not become zero-minute travel.  This
        preserves the provider-neutral no-estimate contract while still retaining
        those items in the proposed order and diagnostics.
        """

        if time_budget is None:
            return None
        items_by_id = {item.item_id: item for item in ordered_items}
        proposed_items = tuple(items_by_id[item_id] for item_id in optimized_item_ids)
        if len(proposed_items) > 1 and (
            matrix is None or any(item.travel_location() is None for item in proposed_items)
        ):
            return None

        matrix_index = (
            {
                item.item_id: index
                for index, item in enumerate(item for item in ordered_items if item.travel_location() is not None)
            }
            if matrix is not None
            else {}
        )
        elapsed_minutes = 0
        travel_minutes = 0
        estimates: list[DayScheduleItemEstimate] = []
        previous: DayItemSnapshot | None = None
        for item in proposed_items:
            leg_minutes = 0
            if previous is not None:
                leg_seconds = matrix.durations_s[matrix_index[previous.item_id]][matrix_index[item.item_id]]
                if leg_seconds is None:
                    return None
                leg_minutes = _round_up_minutes(leg_seconds)
                elapsed_minutes += leg_minutes
                travel_minutes += leg_minutes
            arrival_minutes = elapsed_minutes
            elapsed_minutes += item.visit_duration_minutes
            estimates.append(
                DayScheduleItemEstimate(
                    item_id=item.item_id,
                    arrival_time=_format_local_time(time_budget.start_time, arrival_minutes),
                    departure_time=_format_local_time(time_budget.start_time, elapsed_minutes),
                    travel_minutes_before=leg_minutes,
                    visit_minutes=item.visit_duration_minutes,
                )
            )
            previous = item

        visit_minutes = sum(item.visit_duration_minutes for item in proposed_items)
        return DayScheduleEstimate(
            start_time=time_budget.start_time,
            end_time=time_budget.end_time,
            usable_minutes=time_budget.maximum_usable_minutes,
            travel_minutes=travel_minutes,
            visit_minutes=visit_minutes,
            total_minutes=elapsed_minutes,
            overflow_minutes=max(0, elapsed_minutes - time_budget.maximum_usable_minutes),
            items=tuple(estimates),
        )

    @staticmethod
    def _unavailable_result(
        starting_item_ids: tuple[int, ...],
        diagnostics: list[DayOptimizationDiagnostic],
        failure: RoutingFailure,
    ) -> DayOptimizationResult:
        diagnostics.append(
            DayOptimizationDiagnostic(
                kind=DayOptimizationDiagnosticKind.MATRIX_FAILURE,
                message=failure.message,
                routing_failure=failure,
            )
        )
        return TripOptimizer._result_without_comparison(starting_item_ids, diagnostics)

    @staticmethod
    def _result_without_comparison(
        starting_item_ids: tuple[int, ...],
        diagnostics: list[DayOptimizationDiagnostic],
    ) -> DayOptimizationResult:
        return DayOptimizationResult(
            starting_item_ids=starting_item_ids,
            optimized_item_ids=starting_item_ids,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _is_complete(matrix: TravelMatrix) -> bool:
        return all(
            duration is not None
            for row_index, row in enumerate(matrix.durations_s)
            for column_index, duration in enumerate(row)
            if row_index != column_index
        )

    @classmethod
    def _improve_order(cls, starting_indices: tuple[int, ...], matrix: TravelMatrix) -> tuple[int, ...]:
        order = cls._nearest_neighbour(starting_indices, matrix)
        best_cost = cls._cost(order, matrix).duration_s
        improved = True
        while improved:
            improved = False
            for start in range(1, len(order) - 1):
                for end in range(start + 1, len(order)):
                    candidate = order[:start] + tuple(reversed(order[start : end + 1])) + order[end + 1 :]
                    candidate_cost = cls._cost(candidate, matrix).duration_s
                    if candidate_cost < best_cost:
                        order = candidate
                        best_cost = candidate_cost
                        improved = True
                        break
                if improved:
                    break
        return order

    @staticmethod
    def _nearest_neighbour(
        starting_indices: tuple[int, ...], matrix: TravelMatrix
    ) -> tuple[int, ...]:
        order = [starting_indices[0]]
        remaining = set(starting_indices[1:])
        while remaining:
            current = order[-1]
            next_index = min(
                remaining,
                key=lambda candidate: (matrix.durations_s[current][candidate], candidate),
            )
            order.append(next_index)
            remaining.remove(next_index)
        return tuple(order)

    @staticmethod
    def _merge_routable_order(
        ordered_items: tuple[DayItemSnapshot, ...], routable_item_ids: tuple[int, ...]
    ) -> tuple[int, ...]:
        replacement_ids = iter(routable_item_ids)
        return tuple(
            next(replacement_ids) if item.travel_location() is not None else item.item_id
            for item in ordered_items
        )

    @staticmethod
    def _cost(order: tuple[int, ...], matrix: TravelMatrix) -> DayRouteCost:
        duration_s = sum(matrix.durations_s[start][end] for start, end in zip(order, order[1:]))
        if matrix.distances_m is None:
            return DayRouteCost(duration_s=duration_s)
        distance_legs = tuple(
            matrix.distances_m[start][end] for start, end in zip(order, order[1:])
        )
        if any(distance is None for distance in distance_legs):
            return DayRouteCost(duration_s=duration_s)
        return DayRouteCost(
            duration_s=duration_s,
            distance_m=sum(distance_legs),
        )


def _round_up_minutes(seconds: float) -> int:
    """Round routing seconds upward so a displayed schedule never understates time."""

    return int(-(-seconds // 60))


def _format_local_time(start_time: str, elapsed_minutes: int) -> str:
    start_hour, start_minute = (int(part) for part in start_time.split(":"))
    absolute_minutes = start_hour * 60 + start_minute + elapsed_minutes
    day_offset, minute_of_day = divmod(absolute_minutes, 24 * 60)
    hour, minute = divmod(minute_of_day, 60)
    suffix = f"+{day_offset}d " if day_offset else ""
    return f"{suffix}{hour:02d}:{minute:02d}"


def _absolute_minutes(value: str) -> int:
    """Return a formatted local-time estimate as an absolute minute offset."""

    day_offset = 0
    time_value = value
    if value.startswith("+"):
        day_prefix, time_value = value.split("d ", maxsplit=1)
        day_offset = int(day_prefix[1:])
    hour, minute = (int(part) for part in time_value.split(":"))
    return day_offset * 24 * 60 + hour * 60 + minute
