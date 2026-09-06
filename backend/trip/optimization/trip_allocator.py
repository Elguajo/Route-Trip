"""Deterministic, matrix-informed allocation for a prospective multi-day trip.

This module is deliberately a calculation boundary.  It consumes the persisted
planner settings and immutable item snapshots but does not load or write
``TripDay``/``TripItem`` rows.  The future preview/apply endpoint owns those
persistence concerns.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constraints import DayTimeBudget
from .day_optimizer import (
    DayItemSnapshot,
    DayOptimizationDiagnostic,
    DayOptimizationDiagnosticKind,
    DayOptimizationResult,
    TripOptimizer,
)
from .routing import (
    CoordinateSnapshot,
    RoutingFailure,
    RoutingProfile,
    RoutingProvider,
    TravelLocation,
    TravelMatrix,
    unsupported_matrix_failure,
)


class TripAllocationDiagnosticKind(str, Enum):
    """Reasons an allocation could not use the selected matrix as requested."""

    COORDINATELESS_ITEM = "coordinateless_item"
    MATRIX_FAILURE = "matrix_failure"
    INCOMPLETE_MATRIX = "incomplete_matrix"
    MATRIX_SNAPSHOT_MISMATCH = "matrix_snapshot_mismatch"
    DISTANCE_OBJECTIVE_UNAVAILABLE = "distance_objective_unavailable"


class TripAllocationObjective(str, Enum):
    """The persisted planner objective used only for grouping candidates."""

    DURATION = "duration"
    DISTANCE = "distance"


class TripPlanningSettings(BaseModel):
    """Immutable calculation snapshot derived from a saved planner-settings row."""

    model_config = ConfigDict(frozen=True)

    requested_days: int = Field(ge=1)
    start_location: TravelLocation | None = None
    end_location: TravelLocation | None = None
    return_to_start: bool = False
    allowed_profiles: tuple[RoutingProfile, ...] = Field(min_length=1)
    objective: TripAllocationObjective

    @classmethod
    def from_persisted(cls, settings: object) -> "TripPlanningSettings":
        """Copy only saved settings fields, keeping calculation free of ORM state."""

        start_lat, start_lng = getattr(settings, "start_lat"), getattr(settings, "start_lng")
        end_lat, end_lng = getattr(settings, "end_lat"), getattr(settings, "end_lng")
        return cls(
            requested_days=getattr(settings, "requested_days"),
            start_location=(
                TravelLocation(lat=start_lat, lng=start_lng)
                if start_lat is not None and start_lng is not None
                else None
            ),
            end_location=(
                TravelLocation(lat=end_lat, lng=end_lng)
                if end_lat is not None and end_lng is not None
                else None
            ),
            return_to_start=getattr(settings, "return_to_start"),
            allowed_profiles=tuple(
                getattr(profile, "value", profile)
                for profile in getattr(settings, "allowed_profiles")
            ),
            objective=getattr(getattr(settings, "objective"), "value", getattr(settings, "objective")),
        )


class TripAllocationDiagnostic(BaseModel):
    """A non-mutating allocation diagnostic."""

    model_config = ConfigDict(frozen=True)

    kind: TripAllocationDiagnosticKind
    message: str
    item_ids: tuple[int, ...] = ()
    routing_failure: RoutingFailure | None = None


class TripAllocationDay(BaseModel):
    """One prospective day and the existing Phase 002 order calculation for it."""

    model_config = ConfigDict(frozen=True)

    day_index: int = Field(ge=0)
    item_ids: tuple[int, ...]
    optimization: DayOptimizationResult


class TripAllocationResult(BaseModel):
    """A complete, non-persisted allocation for the requested number of days."""

    model_config = ConfigDict(frozen=True)

    requested_days: int = Field(ge=1)
    profile: RoutingProfile
    days: tuple[TripAllocationDay, ...]
    diagnostics: tuple[TripAllocationDiagnostic, ...] = ()


class TripPlanningSnapshotAssignment(BaseModel):
    """The persisted POI position a whole-trip preview was calculated from."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    day_id: int
    sequence: int


class TripPlanningTotals(BaseModel):
    """Aggregate matrix-backed totals for a whole-trip proposal."""

    model_config = ConfigDict(frozen=True)

    starting_duration_s: float = Field(ge=0)
    optimized_duration_s: float = Field(ge=0)
    starting_distance_m: float | None = Field(default=None, ge=0)
    optimized_distance_m: float | None = Field(default=None, ge=0)


class TripOptimizationPreviewResult(BaseModel):
    """Non-persisted whole-trip proposal plus its compare-and-apply snapshot."""

    model_config = ConfigDict(frozen=True)

    starting_assignments: tuple[TripPlanningSnapshotAssignment, ...]
    snapshot_token: str
    target_day_ids: tuple[int | None, ...]
    allocation: TripAllocationResult
    totals: TripPlanningTotals | None = None


class TripOptimizationApplyRequest(BaseModel):
    """The exact snapshot returned by a whole-trip preview."""

    starting_assignments: tuple[TripPlanningSnapshotAssignment, ...]
    snapshot_token: str = Field(min_length=1)

    @field_validator("starting_assignments")
    @classmethod
    def _require_unique_item_ids(
        cls, assignments: tuple[TripPlanningSnapshotAssignment, ...]
    ) -> tuple[TripPlanningSnapshotAssignment, ...]:
        item_ids = tuple(assignment.item_id for assignment in assignments)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("starting_assignments must not contain duplicate item IDs")
        return assignments


class TripOptimizationApplyResult(TripOptimizationPreviewResult):
    """A whole-trip proposal whose assignments and sequence were persisted."""

    applied: bool = True
    applied_day_ids: tuple[int, ...]


class TripAllocator:
    """Allocate coordinates with one selected matrix, then order each prospective day.

    The first allowed profile in the persisted settings is the deterministic
    planner profile.  The setting is an ordered user choice, so selecting a
    different provider/profile is never an implicit fallback.
    """

    def __init__(self, routing_provider: RoutingProvider) -> None:
        self._routing_provider = routing_provider

    async def allocate(
        self,
        settings: TripPlanningSettings,
        items: Iterable[DayItemSnapshot],
        day_time_budgets: Iterable[DayTimeBudget] | None = None,
    ) -> TripAllocationResult:
        ordered_items = tuple(sorted(items, key=lambda item: (item.sequence, item.item_id)))
        profile = self._profile_from_settings(settings)
        budgets = self._day_time_budgets(settings.requested_days, day_time_budgets)
        routable = tuple(item for item in ordered_items if item.travel_location() is not None)
        diagnostics: list[TripAllocationDiagnostic] = []
        coordinateless_item_ids = tuple(
            item.item_id for item in ordered_items if item.travel_location() is None
        )
        if coordinateless_item_ids:
            diagnostics.append(
                TripAllocationDiagnostic(
                    kind=TripAllocationDiagnosticKind.COORDINATELESS_ITEM,
                    item_ids=coordinateless_item_ids,
                    message=(
                        "Items without valid coordinates were retained and assigned "
                        "deterministically without matrix-based clustering"
                    ),
                )
            )

        groups: list[list[DayItemSnapshot]]
        if len(routable) < 2:
            groups = self._balanced_baseline_groups(ordered_items, settings.requested_days)
        else:
            groups = await self._matrix_informed_groups(
                settings,
                ordered_items,
                routable,
                profile,
                diagnostics,
            )

        groups = await self._rebalance_budget_overflow(groups, budgets, profile)

        optimizer = TripOptimizer(self._routing_provider)
        days: list[TripAllocationDay] = []
        for day_index, group in enumerate(groups):
            days.append(
                TripAllocationDay(
                    day_index=day_index,
                    item_ids=tuple(item.item_id for item in group),
                    optimization=await optimizer.optimize_day(
                        group, profile, budgets[day_index]
                    ),
                )
            )
        return TripAllocationResult(
            requested_days=settings.requested_days,
            profile=profile,
            days=tuple(days),
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _day_time_budgets(
        requested_days: int, day_time_budgets: Iterable[DayTimeBudget] | None
    ) -> tuple[DayTimeBudget, ...]:
        if day_time_budgets is None:
            return tuple(
                DayTimeBudget(start_time="09:00", end_time="18:00")
                for _ in range(requested_days)
            )
        budgets = tuple(day_time_budgets)
        if len(budgets) != requested_days:
            raise ValueError("day_time_budgets must contain one budget per requested day")
        return budgets

    async def _rebalance_budget_overflow(
        self,
        groups: list[list[DayItemSnapshot]],
        budgets: tuple[DayTimeBudget, ...],
        profile: RoutingProfile,
    ) -> list[list[DayItemSnapshot]]:
        """Move a trailing proposed stop only when it strictly reduces overflow.

        Geographic clustering remains the first allocation input.  This bounded,
        deterministic pass then uses the selected provider's actual day travel
        costs plus resolved visits to make spare daily capacity useful.  If a
        matrix cannot support a schedule, no guessed travel duration is used and
        the prior stable allocation is retained.
        """

        optimizer = TripOptimizer(self._routing_provider)
        for _ in range(sum(len(group) for group in groups)):
            results = tuple([
                await optimizer.optimize_day(group, profile, budgets[index])
                for index, group in enumerate(groups)
            ])
            overflow_minutes = tuple(
                result.schedule.overflow_minutes if result.schedule is not None else None
                for result in results
            )
            if not any(value for value in overflow_minutes):
                break

            moved = False
            for source_index, source_result in enumerate(results):
                if not source_result.schedule or not source_result.schedule.overflow_minutes:
                    continue
                for item_id in reversed(source_result.optimized_item_ids):
                    candidate_item = next(item for item in groups[source_index] if item.item_id == item_id)
                    for destination_index in range(len(groups)):
                        if destination_index == source_index:
                            continue
                        proposal = [list(group) for group in groups]
                        proposal[source_index].remove(candidate_item)
                        proposal[destination_index].append(candidate_item)
                        proposal_results = tuple([
                            await optimizer.optimize_day(
                                group, profile, budgets[index]
                            )
                            for index, group in enumerate(proposal)
                        ])
                        if any(result.schedule is None for result in proposal_results):
                            continue
                        before = sum(value for value in overflow_minutes if value is not None)
                        after = sum(result.schedule.overflow_minutes for result in proposal_results if result.schedule)
                        if after < before:
                            groups = proposal
                            moved = True
                            break
                    if moved:
                        break
                if moved:
                    break
            if not moved:
                break
        return groups

    async def _matrix_informed_groups(
        self,
        settings: TripPlanningSettings,
        ordered_items: tuple[DayItemSnapshot, ...],
        routable: tuple[DayItemSnapshot, ...],
        profile: RoutingProfile,
        diagnostics: list[TripAllocationDiagnostic],
    ) -> list[list[DayItemSnapshot]]:
        capability = self._routing_provider.capability
        if not capability.supports_matrix(profile):
            diagnostics.append(
                self._failure_diagnostic(unsupported_matrix_failure(capability, profile))
            )
            return self._balanced_baseline_groups(ordered_items, settings.requested_days)

        snapshot, start_index, destination_index = self._allocation_snapshot(settings, routable)
        matrix_result = await self._routing_provider.get_matrix(snapshot, profile)
        if isinstance(matrix_result, RoutingFailure):
            diagnostics.append(self._failure_diagnostic(matrix_result))
            return self._balanced_baseline_groups(ordered_items, settings.requested_days)
        if matrix_result.snapshot != snapshot:
            diagnostics.append(
                TripAllocationDiagnostic(
                    kind=TripAllocationDiagnosticKind.MATRIX_SNAPSHOT_MISMATCH,
                    message="The routing provider returned a matrix for different coordinates",
                )
            )
            return self._balanced_baseline_groups(ordered_items, settings.requested_days)
        metric = self._metric(settings, matrix_result, diagnostics)
        if metric is None:
            return self._balanced_baseline_groups(ordered_items, settings.requested_days)
        if not self._is_complete(metric):
            diagnostics.append(
                TripAllocationDiagnostic(
                    kind=TripAllocationDiagnosticKind.INCOMPLETE_MATRIX,
                    message="The routing matrix contains unreachable allocation pairs",
                )
            )
            return self._balanced_baseline_groups(ordered_items, settings.requested_days)

        groups = self._cluster_routable_items(
            routable,
            metric,
            settings.requested_days,
            start_index,
            destination_index,
        )
        self._assign_coordinateless_items(groups, ordered_items)
        return groups

    @staticmethod
    def _profile_from_settings(settings: TripPlanningSettings) -> RoutingProfile:
        return settings.allowed_profiles[0]

    @staticmethod
    def _allocation_snapshot(
        settings: TripPlanningSettings,
        routable: tuple[DayItemSnapshot, ...],
    ) -> tuple[CoordinateSnapshot, int | None, int | None]:
        coordinates = [item.travel_location() for item in routable]
        start_index: int | None = None
        destination_index: int | None = None
        if settings.start_location is not None:
            start_index = len(coordinates)
            coordinates.append(settings.start_location)
        if settings.return_to_start and start_index is not None:
            destination_index = start_index
        elif settings.end_location is not None:
            destination_index = len(coordinates)
            coordinates.append(settings.end_location)
        return CoordinateSnapshot(coordinates=tuple(coordinates)), start_index, destination_index

    @staticmethod
    def _metric(
        settings: TripPlanningSettings,
        matrix: TravelMatrix,
        diagnostics: list[TripAllocationDiagnostic],
    ) -> tuple[tuple[float | None, ...], ...] | None:
        if settings.objective is TripAllocationObjective.DURATION:
            return matrix.durations_s
        if matrix.distances_m is None or not TripAllocator._is_complete(matrix.distances_m):
            diagnostics.append(
                TripAllocationDiagnostic(
                    kind=TripAllocationDiagnosticKind.DISTANCE_OBJECTIVE_UNAVAILABLE,
                    message="The selected matrix does not contain complete distance values",
                )
            )
            return None
        return matrix.distances_m

    @staticmethod
    def _is_complete(metric: tuple[tuple[float | None, ...], ...]) -> bool:
        return all(
            value is not None
            for row_index, row in enumerate(metric)
            for column_index, value in enumerate(row)
            if row_index != column_index
        )

    @staticmethod
    def _failure_diagnostic(failure: RoutingFailure) -> TripAllocationDiagnostic:
        return TripAllocationDiagnostic(
            kind=TripAllocationDiagnosticKind.MATRIX_FAILURE,
            message=failure.message,
            routing_failure=failure,
        )

    @classmethod
    def _cluster_routable_items(
        cls,
        routable: tuple[DayItemSnapshot, ...],
        metric: tuple[tuple[float | None, ...], ...],
        requested_days: int,
        start_index: int | None,
        destination_index: int | None,
    ) -> list[list[DayItemSnapshot]]:
        group_count = min(requested_days, len(routable))
        seeds = cls._seed_indices(
            len(routable), metric, group_count, start_index, destination_index
        )
        groups = [[routable[seed]] for seed in seeds]
        for item_index, item in enumerate(routable):
            if item_index in seeds:
                continue
            group_index = min(
                range(len(groups)),
                key=lambda candidate: (
                    cls._symmetric_cost(metric, seeds[candidate], item_index),
                    len(groups[candidate]),
                    candidate,
                ),
            )
            groups[group_index].append(item)
        groups.extend([] for _ in range(requested_days - group_count))
        return groups

    @classmethod
    def _seed_indices(
        cls,
        item_count: int,
        metric: tuple[tuple[float | None, ...], ...],
        group_count: int,
        start_index: int | None,
        destination_index: int | None,
    ) -> tuple[int, ...]:
        if start_index is None:
            seeds = [0]
        else:
            seeds = [min(range(item_count), key=lambda item: (metric[start_index][item], item))]
        if group_count > 1 and destination_index is not None:
            destination_seed = min(
                (item for item in range(item_count) if item not in seeds),
                key=lambda item: (metric[item][destination_index], item),
                default=None,
            )
            if destination_seed is not None:
                seeds.append(destination_seed)
        while len(seeds) < group_count:
            seeds.append(
                max(
                    (item for item in range(item_count) if item not in seeds),
                    key=lambda item: (
                        min(cls._symmetric_cost(metric, item, seed) for seed in seeds),
                        -item,
                    ),
                )
            )
        return tuple(seeds)

    @staticmethod
    def _symmetric_cost(
        metric: tuple[tuple[float | None, ...], ...], left: int, right: int
    ) -> float:
        return (metric[left][right] + metric[right][left]) / 2

    @staticmethod
    def _balanced_baseline_groups(
        items: tuple[DayItemSnapshot, ...], requested_days: int
    ) -> list[list[DayItemSnapshot]]:
        groups = [[] for _ in range(requested_days)]
        for item in items:
            groups[min(range(requested_days), key=lambda day_index: (len(groups[day_index]), day_index))].append(
                item
            )
        return groups

    @staticmethod
    def _assign_coordinateless_items(
        groups: list[list[DayItemSnapshot]], ordered_items: tuple[DayItemSnapshot, ...]
    ) -> None:
        for item in ordered_items:
            if item.travel_location() is None:
                groups[min(range(len(groups)), key=lambda day_index: (len(groups[day_index]), day_index))].append(
                    item
                )
