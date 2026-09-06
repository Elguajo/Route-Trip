import asyncio

from trip.models.models import (
    PlannerOptimizationObjective,
    PlannerRoutingProfile,
    TripPlannerSettings,
)
from trip.optimization import (
    CoordinateSnapshot,
    DayItemSnapshot,
    RoutingCapability,
    RoutingProfile,
    TravelMatrix,
    TripAllocationDiagnosticKind,
    TripAllocator,
    TripPlanningSettings,
)


class ClusterMatrixProvider:
    """Complete matrices with low within-cluster travel and high cross-cluster travel."""

    def __init__(self, profiles: tuple[RoutingProfile, ...] = (RoutingProfile.CAR,)) -> None:
        self.capability = RoutingCapability(provider="stub", matrix_profiles=profiles)
        self.requests: list[tuple[CoordinateSnapshot, RoutingProfile]] = []

    async def get_matrix(self, snapshot: CoordinateSnapshot, profile: RoutingProfile) -> TravelMatrix:
        self.requests.append((snapshot, profile))
        labels = tuple(int(location.lat // 10) for location in snapshot.coordinates)
        metric = tuple(
            tuple(0 if row == column else (1 if labels[row] == labels[column] else 100) for column in range(len(labels)))
            for row in range(len(labels))
        )
        return TravelMatrix(
            provider="stub",
            profile=profile,
            snapshot=snapshot,
            durations_s=metric,
            distances_m=metric,
        )


class ObjectiveMatrixProvider(ClusterMatrixProvider):
    async def get_matrix(self, snapshot: CoordinateSnapshot, profile: RoutingProfile) -> TravelMatrix:
        self.requests.append((snapshot, profile))
        labels = tuple(round(location.lat) for location in snapshot.coordinates)
        duration_groups = ({1, 3}, {2, 4})
        distance_groups = ({1, 2}, {3, 4})

        def metric(groups: tuple[set[int], ...]) -> tuple[tuple[int, ...], ...]:
            return tuple(
                tuple(
                    0
                    if row == column
                    else 1
                    if any(labels[row] in group and labels[column] in group for group in groups)
                    else 100
                    for column in range(len(labels))
                )
                for row in range(len(labels))
            )

        return TravelMatrix(
            provider="stub",
            profile=profile,
            snapshot=snapshot,
            durations_s=metric(duration_groups),
            distances_m=metric(distance_groups),
        )


def _settings(**overrides) -> TripPlanningSettings:
    values = {
        "trip_id": 1,
        "requested_days": 3,
        "allowed_profiles": [PlannerRoutingProfile.CAR],
        "objective": PlannerOptimizationObjective.DURATION,
    }
    values.update(overrides)
    return TripPlanningSettings.from_persisted(TripPlannerSettings(**values))


def _interleaved_items() -> tuple[DayItemSnapshot, ...]:
    return (
        DayItemSnapshot(item_id=101, sequence=0, lat=1.0, lng=0.0),
        DayItemSnapshot(item_id=201, sequence=1, lat=10.0, lng=0.0),
        DayItemSnapshot(item_id=301, sequence=2, lat=20.0, lng=0.0),
        DayItemSnapshot(item_id=102, sequence=3, lat=1.1, lng=0.0),
        DayItemSnapshot(item_id=202, sequence=4, lat=10.1, lng=0.0),
        DayItemSnapshot(item_id=302, sequence=5, lat=20.1, lng=0.0),
        DayItemSnapshot(item_id=999, sequence=6),
    )


def test_trip_allocator_groups_nearby_pois_orders_each_day_and_retains_coordinateless_items() -> None:
    provider = ClusterMatrixProvider(profiles=(RoutingProfile.FOOT,))
    settings = _settings(allowed_profiles=[PlannerRoutingProfile.FOOT])
    items = _interleaved_items()

    result = asyncio.run(TripAllocator(provider).allocate(settings, items))

    assert result.profile is RoutingProfile.FOOT
    assert [day.item_ids for day in result.days] == [
        (101, 102, 999),
        (201, 202),
        (301, 302),
    ]
    assert sorted(item_id for day in result.days for item_id in day.item_ids) == [
        101,
        102,
        201,
        202,
        301,
        302,
        999,
    ]
    assert all(day.optimization.cost_comparison is not None for day in result.days)
    assert result.diagnostics[0].kind is TripAllocationDiagnosticKind.COORDINATELESS_ITEM
    assert result.diagnostics[0].item_ids == (999,)
    assert all(profile is RoutingProfile.FOOT for _, profile in provider.requests)
    assert provider.requests[0][0].coordinates == tuple(
        item.travel_location() for item in items if item.travel_location() is not None
    )


def test_trip_allocator_is_stable_for_an_equivalent_input_order() -> None:
    settings = _settings()
    items = _interleaved_items()

    first = asyncio.run(TripAllocator(ClusterMatrixProvider()).allocate(settings, items))
    second = asyncio.run(TripAllocator(ClusterMatrixProvider()).allocate(settings, reversed(items)))

    assert second == first
    assert tuple(item.item_id for item in items) == (101, 201, 301, 102, 202, 302, 999)


def test_trip_allocator_uses_the_saved_distance_objective_for_geographic_groups() -> None:
    settings = _settings(
        requested_days=2,
        objective=PlannerOptimizationObjective.DISTANCE,
    )
    items = tuple(
        DayItemSnapshot(item_id=item_id, sequence=item_id - 1, lat=float(item_id), lng=0.0)
        for item_id in range(1, 5)
    )

    result = asyncio.run(TripAllocator(ObjectiveMatrixProvider()).allocate(settings, items))

    assert [day.item_ids for day in result.days] == [(1, 2), (3, 4)]


def test_trip_allocator_uses_saved_start_and_end_anchors_without_persisting_anything() -> None:
    settings = _settings(
        requested_days=2,
        start_lat=20.0,
        start_lng=0.0,
        end_lat=1.0,
        end_lng=0.0,
    )
    items = tuple(
        DayItemSnapshot(item_id=item_id, sequence=item_id - 1, lat=lat, lng=0.0)
        for item_id, lat in ((1, 1.0), (2, 1.1), (3, 20.0), (4, 20.1))
    )

    result = asyncio.run(TripAllocator(ClusterMatrixProvider()).allocate(settings, items))

    assert result.days[0].item_ids == (3, 4)
    assert result.days[1].item_ids == (1, 2)


def test_trip_allocator_reports_unsupported_matrix_without_fallback_or_item_loss() -> None:
    provider = ClusterMatrixProvider(profiles=())
    items = _interleaved_items()[:3]

    result = asyncio.run(TripAllocator(provider).allocate(_settings(requested_days=2), items))

    assert [day.item_ids for day in result.days] == [(101, 301), (201,)]
    assert result.diagnostics[0].kind is TripAllocationDiagnosticKind.MATRIX_FAILURE
    assert result.diagnostics[0].routing_failure is not None
    assert result.diagnostics[0].routing_failure.kind.value == "unsupported_provider"
    assert provider.requests == []
    assert result.days[0].optimization.cost_comparison is None
    assert result.days[1].optimization.cost_comparison is not None
