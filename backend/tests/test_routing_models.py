import asyncio

import pytest
from pydantic import ValidationError

from trip.optimization.routing import (
    CoordinateSnapshot,
    RoutingCapability,
    RoutingFailure,
    RoutingFailureKind,
    RoutingProfile,
    RoutingProvider,
    TravelLocation,
    TravelMatrix,
    get_routing_capability,
    unsupported_matrix_failure,
)


def _snapshot() -> CoordinateSnapshot:
    return CoordinateSnapshot(
        coordinates=(
            TravelLocation(lat=41.9, lng=12.1),
            TravelLocation(lat=41.8, lng=12.5),
        )
    )


def test_travel_matrix_keeps_snapshot_and_units_without_distance_requirement() -> None:
    snapshot = _snapshot()

    matrix = TravelMatrix(
        provider="osm",
        profile=RoutingProfile.CAR,
        snapshot=snapshot,
        durations_s=((0, 456.7), (410.2, 0)),
    )

    assert matrix.snapshot == snapshot
    assert matrix.durations_s == ((0.0, 456.7), (410.2, 0.0))
    assert matrix.distances_m is None


@pytest.mark.parametrize(
    ("durations_s", "distances_m"),
    [
        (((0, 1),), None),
        (((0, -1), (1, 0)), None),
        (((0, 1), (1, 0)), ((0, 1),)),
    ],
)
def test_travel_matrix_rejects_invalid_shape_or_values(durations_s, distances_m) -> None:
    with pytest.raises(ValidationError):
        TravelMatrix(
            provider="osm",
            profile=RoutingProfile.CAR,
            snapshot=_snapshot(),
            durations_s=durations_s,
            distances_m=distances_m,
        )


def test_routing_capability_matches_existing_provider_selection_without_fallback() -> None:
    assert get_routing_capability("osm").matrix_profiles == (
        RoutingProfile.CAR,
        RoutingProfile.FOOT,
        RoutingProfile.BIKE,
    )
    assert get_routing_capability("photon").supports_matrix(RoutingProfile.BIKE)

    google = get_routing_capability("google")
    failure = unsupported_matrix_failure(google, RoutingProfile.TRANSIT)

    assert not google.supports_matrix(RoutingProfile.TRANSIT)
    assert failure.kind is RoutingFailureKind.UNSUPPORTED_PROVIDER
    assert failure.retryable is False
    assert get_routing_capability("unconfigured").provider == "unconfigured"
    assert get_routing_capability("unconfigured").matrix_profiles == ()


def test_unsupported_profile_has_a_typed_failure() -> None:
    failure = unsupported_matrix_failure(get_routing_capability("osm"), RoutingProfile.TRANSIT)

    assert failure == RoutingFailure(
        kind=RoutingFailureKind.UNSUPPORTED_PROFILE,
        provider="osm",
        profile=RoutingProfile.TRANSIT,
        message="The selected provider does not support this travel-matrix profile",
        retryable=False,
    )


def test_routing_provider_protocol_accepts_adapter_without_map_provider_contract() -> None:
    class StubMatrixProvider:
        capability = RoutingCapability(provider="stub", matrix_profiles=(RoutingProfile.CAR,))

        async def get_matrix(self, snapshot: CoordinateSnapshot, profile: RoutingProfile) -> RoutingFailure:
            return RoutingFailure(
                kind=RoutingFailureKind.PROVIDER_UNAVAILABLE,
                provider="stub",
                profile=profile,
                message=f"No matrix for {len(snapshot.coordinates)} coordinates",
                retryable=True,
            )

    provider = StubMatrixProvider()
    result = asyncio.run(provider.get_matrix(_snapshot(), RoutingProfile.CAR))

    assert isinstance(provider, RoutingProvider)
    assert isinstance(result, RoutingFailure)
    assert result.kind is RoutingFailureKind.PROVIDER_UNAVAILABLE
