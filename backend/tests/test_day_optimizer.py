import asyncio

from trip.optimization import (
    CoordinateSnapshot,
    DayItemSnapshot,
    DayOptimizationDiagnosticKind,
    DayTimeBudget,
    RoutingCapability,
    RoutingFailure,
    RoutingFailureKind,
    RoutingProfile,
    TravelMatrix,
    TripOptimizer,
)


class StubRoutingProvider:
    def __init__(self, result: TravelMatrix | RoutingFailure) -> None:
        self.capability = RoutingCapability(provider="stub", matrix_profiles=(RoutingProfile.CAR,))
        self.result = result
        self.requests: list[CoordinateSnapshot] = []

    async def get_matrix(self, snapshot: CoordinateSnapshot, profile: RoutingProfile):
        self.requests.append(snapshot)
        return self.result


def _items() -> tuple[DayItemSnapshot, ...]:
    return (
        DayItemSnapshot(item_id=12, sequence=2, lat=41.2, lng=12.2),
        DayItemSnapshot(item_id=10, sequence=0, lat=41.0, lng=12.0),
        DayItemSnapshot(item_id=11, sequence=1, lat=41.1, lng=12.1),
        DayItemSnapshot(item_id=14, sequence=4, lat=41.4, lng=12.4),
        DayItemSnapshot(item_id=13, sequence=3, lat=41.3, lng=12.3),
    )


def _matrix(*, durations: tuple[tuple[float | None, ...], ...]) -> TravelMatrix:
    coordinates = tuple(
        item.travel_location() for item in sorted(_items(), key=lambda item: (item.sequence, item.item_id))
    )
    return TravelMatrix(
        provider="stub",
        profile=RoutingProfile.CAR,
        snapshot=CoordinateSnapshot(coordinates=coordinates),
        durations_s=durations,
        distances_m=tuple(
            tuple(None if value is None else value * 100 for value in row) for row in durations
        ),
    )


def test_day_optimizer_is_deterministic_and_reports_matrix_cost_improvement() -> None:
    matrix = _matrix(
        durations=(
            (0, 20, 5, 6, 7),
            (20, 0, 20, 2, 3),
            (5, 1, 0, 1, 2),
            (6, 2, 1, 0, 1),
            (7, 3, 2, 1, 0),
        )
    )
    provider = StubRoutingProvider(matrix)
    optimizer = TripOptimizer(provider)

    first = asyncio.run(optimizer.optimize_day(_items(), RoutingProfile.CAR))
    second = asyncio.run(optimizer.optimize_day(reversed(_items()), RoutingProfile.CAR))

    assert first.starting_item_ids == (10, 11, 12, 13, 14)
    assert first.optimized_item_ids == (10, 12, 11, 13, 14)
    assert second == first
    assert first.cost_comparison is not None
    assert first.cost_comparison.starting.duration_s == 42
    assert first.cost_comparison.optimized.duration_s == 9
    assert first.cost_comparison.duration_saved_s == 33
    assert first.cost_comparison.starting.distance_m == 4_200
    assert first.cost_comparison.optimized.distance_m == 900
    assert first.cost_comparison.distance_saved_m == 3_300


def test_day_optimizer_preserves_order_and_diagnostic_when_matrix_is_unavailable() -> None:
    failure = RoutingFailure(
        kind=RoutingFailureKind.PROVIDER_UNAVAILABLE,
        provider="stub",
        profile=RoutingProfile.CAR,
        message="Matrix is unavailable",
        retryable=True,
    )

    result = asyncio.run(TripOptimizer(StubRoutingProvider(failure)).optimize_day(_items(), RoutingProfile.CAR))

    assert result.optimized_item_ids == result.starting_item_ids == (10, 11, 12, 13, 14)
    assert result.cost_comparison is None
    assert result.diagnostics[0].kind is DayOptimizationDiagnosticKind.MATRIX_FAILURE
    assert result.diagnostics[0].message == "Matrix is unavailable"
    assert result.diagnostics[0].routing_failure == failure


def test_day_optimizer_does_not_compare_costs_from_an_incomplete_matrix() -> None:
    matrix = _matrix(
        durations=(
            (0, 20, 5, 6, 7),
            (20, 0, None, 2, 3),
            (5, 1, 0, 1, 2),
            (6, 2, 1, 0, 1),
            (7, 3, 2, 1, 0),
        )
    )

    result = asyncio.run(TripOptimizer(StubRoutingProvider(matrix)).optimize_day(_items(), RoutingProfile.CAR))

    assert result.optimized_item_ids == result.starting_item_ids == (10, 11, 12, 13, 14)
    assert result.cost_comparison is None
    assert result.diagnostics[0].kind is DayOptimizationDiagnosticKind.INCOMPLETE_MATRIX


def test_day_optimizer_compares_duration_when_matrix_has_no_distances() -> None:
    matrix = _matrix(
        durations=(
            (0, 20, 5, 6, 7),
            (20, 0, 20, 2, 3),
            (5, 1, 0, 1, 2),
            (6, 2, 1, 0, 1),
            (7, 3, 2, 1, 0),
        )
    ).model_copy(update={"distances_m": None})

    result = asyncio.run(TripOptimizer(StubRoutingProvider(matrix)).optimize_day(_items(), RoutingProfile.CAR))

    assert result.cost_comparison is not None
    assert result.cost_comparison.duration_saved_s == 33
    assert result.cost_comparison.starting.distance_m is None
    assert result.cost_comparison.optimized.distance_m is None
    assert result.cost_comparison.distance_saved_m is None


def test_day_optimizer_retains_coordinateless_items_in_order_and_diagnostics() -> None:
    items = (
        DayItemSnapshot(item_id=1, sequence=0),
        DayItemSnapshot(item_id=2, sequence=1, lat=41.0, lng=12.0),
        DayItemSnapshot(item_id=3, sequence=2, lat=41.1, lng=12.1),
    )
    matrix = TravelMatrix(
        provider="stub",
        profile=RoutingProfile.CAR,
        snapshot=CoordinateSnapshot(coordinates=(items[1].travel_location(), items[2].travel_location())),
        durations_s=((0, 12), (10, 0)),
        distances_m=((0, 900), (800, 0)),
    )
    provider = StubRoutingProvider(matrix)

    result = asyncio.run(TripOptimizer(provider).optimize_day(items, RoutingProfile.CAR))

    assert result.optimized_item_ids == (1, 2, 3)
    assert result.diagnostics[0].kind is DayOptimizationDiagnosticKind.COORDINATELESS_ITEM
    assert result.diagnostics[0].item_ids == (1,)
    assert provider.requests[0].coordinates == (items[1].travel_location(), items[2].travel_location())


def test_day_optimizer_reports_a_09_to_18_overflow_from_travel_and_visits() -> None:
    items = tuple(
        DayItemSnapshot(
            item_id=item_id,
            sequence=item_id - 1,
            lat=float(item_id),
            lng=0.0,
            visit_duration_minutes=200,
        )
        for item_id in range(1, 4)
    )
    matrix = TravelMatrix(
        provider="stub",
        profile=RoutingProfile.CAR,
        snapshot=CoordinateSnapshot(coordinates=tuple(item.travel_location() for item in items)),
        durations_s=((0, 2_700, 2_700), (2_700, 0, 2_700), (2_700, 2_700, 0)),
        distances_m=((0, 1, 1), (1, 0, 1), (1, 1, 0)),
    )

    result = asyncio.run(
        TripOptimizer(StubRoutingProvider(matrix)).optimize_day(
            items,
            RoutingProfile.CAR,
            DayTimeBudget(start_time="09:00", end_time="18:00"),
        )
    )

    assert result.schedule is not None
    assert result.schedule.model_dump() == {
        "start_time": "09:00",
        "end_time": "18:00",
        "usable_minutes": 540,
        "travel_minutes": 90,
        "visit_minutes": 600,
        "total_minutes": 690,
        "overflow_minutes": 150,
        "items": (
            {
                "item_id": 1,
                "arrival_time": "09:00",
                "departure_time": "12:20",
                "travel_minutes_before": 0,
                "visit_minutes": 200,
            },
            {
                "item_id": 2,
                "arrival_time": "13:05",
                "departure_time": "16:25",
                "travel_minutes_before": 45,
                "visit_minutes": 200,
            },
            {
                "item_id": 3,
                "arrival_time": "17:10",
                "departure_time": "20:30",
                "travel_minutes_before": 45,
                "visit_minutes": 200,
            },
        ),
    }
    assert result.diagnostics[-1].kind is DayOptimizationDiagnosticKind.TIME_BUDGET_OVERFLOW
    assert result.diagnostics[-1].item_ids == (3,)
