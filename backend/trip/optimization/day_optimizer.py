"""Deterministic single-day ordering based on a provider-neutral matrix."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @classmethod
    def from_trip_items(cls, items: Iterable[object]) -> tuple["DayItemSnapshot", ...]:
        """Create an immutable calculation input from persisted ``TripItem`` rows."""

        return tuple(
            cls(
                item_id=item.id,
                sequence=item.sequence,
                lat=item.lat,
                lng=item.lng,
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
            return DayOptimizationResult(
                starting_item_ids=starting_item_ids,
                optimized_item_ids=starting_item_ids,
                cost_comparison=DayCostComparison(
                    starting=zero_cost,
                    optimized=zero_cost,
                    duration_saved_s=0,
                    distance_saved_m=0,
                ),
                diagnostics=tuple(diagnostics),
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
        return DayOptimizationResult(
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
            diagnostics=tuple(diagnostics),
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
