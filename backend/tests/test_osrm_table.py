import asyncio

import httpx
import pytest

from trip.optimization import (
    CoordinateSnapshot,
    OSRMTableRoutingProvider,
    RoutingFailure,
    RoutingFailureKind,
    RoutingProfile,
    RoutingProvider,
    TravelLocation,
    TravelMatrix,
    TravelMatrixCache,
)


def _snapshot(*coordinates: tuple[float, float]) -> CoordinateSnapshot:
    return CoordinateSnapshot(
        coordinates=tuple(TravelLocation(lat=lat, lng=lng) for lat, lng in coordinates)
    )


SNAPSHOT = _snapshot((41.9, 12.1), (41.8, 12.5))


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": "Ok",
            "durations": [[0, 456.7], [410.2, 0]],
            "distances": [[0, 1_234.5], [1_100.25, 0]],
        },
    )


def test_osrm_table_returns_duration_and_distance_matrix(mock_routing_http) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/routed-car/table/v1/driving/12.1,41.9;12.5,41.8"
        assert dict(request.url.params) == {"annotations": "duration,distance"}
        return _ok_response(request)

    requests = mock_routing_http(response)
    provider = OSRMTableRoutingProvider("osm")

    result = asyncio.run(provider.get_matrix(SNAPSHOT, RoutingProfile.CAR))

    assert isinstance(provider, RoutingProvider)
    assert isinstance(result, TravelMatrix)
    assert result.provider == "osm"
    assert result.profile is RoutingProfile.CAR
    assert result.snapshot == SNAPSHOT
    assert result.durations_s == ((0.0, 456.7), (410.2, 0.0))
    assert result.distances_m == ((0.0, 1_234.5), (1_100.25, 0.0))
    assert len(requests) == 1


def test_osrm_table_keeps_unreachable_cells_as_none(mock_routing_http) -> None:
    requests = mock_routing_http(
        lambda request: httpx.Response(
            200,
            json={
                "code": "Ok",
                "durations": [[0, None], [None, 0]],
                "distances": [[0, None], [None, 0]],
            },
        )
    )

    result = asyncio.run(OSRMTableRoutingProvider("osm").get_matrix(SNAPSHOT, RoutingProfile.CAR))

    assert isinstance(result, TravelMatrix)
    assert result.durations_s == ((0.0, None), (None, 0.0))
    assert result.distances_m == ((0.0, None), (None, 0.0))
    assert len(requests) == 1


def test_osrm_table_rejects_malformed_response(mock_routing_http) -> None:
    mock_routing_http(
        lambda request: httpx.Response(
            200,
            json={"code": "Ok", "durations": [[0, 12]], "distances": [[0, 10]]},
        )
    )

    result = asyncio.run(OSRMTableRoutingProvider("osm").get_matrix(SNAPSHOT, RoutingProfile.CAR))

    assert isinstance(result, RoutingFailure)
    assert result.kind is RoutingFailureKind.MALFORMED_RESPONSE
    assert result.retryable is False


@pytest.mark.parametrize(
    "handler, expected_retryable",
    [
        (lambda request: httpx.Response(503, json={"message": "down"}), True),
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)), True),
    ],
)
def test_osrm_table_maps_http_failures_and_timeouts(mock_routing_http, handler, expected_retryable) -> None:
    mock_routing_http(handler)

    result = asyncio.run(OSRMTableRoutingProvider("osm").get_matrix(SNAPSHOT, RoutingProfile.CAR))

    assert isinstance(result, RoutingFailure)
    assert result.kind is RoutingFailureKind.PROVIDER_UNAVAILABLE
    assert result.retryable is expected_retryable


def test_osrm_table_cache_hits_exact_request_and_misses_profile_or_coordinates(mock_routing_http) -> None:
    requests = mock_routing_http(_ok_response)
    provider = OSRMTableRoutingProvider("osm", cache=TravelMatrixCache(max_entries=4, ttl_seconds=60))

    first = asyncio.run(provider.get_matrix(SNAPSHOT, RoutingProfile.CAR))
    equivalent = asyncio.run(provider.get_matrix(SNAPSHOT, RoutingProfile.CAR))
    changed_profile = asyncio.run(provider.get_matrix(SNAPSHOT, RoutingProfile.FOOT))
    changed_coordinates = asyncio.run(
        provider.get_matrix(_snapshot((41.9, 12.1), (41.7, 12.5)), RoutingProfile.CAR)
    )

    assert all(isinstance(result, TravelMatrix) for result in (first, equivalent, changed_profile, changed_coordinates))
    assert first is not equivalent
    assert len(requests) == 3
    assert requests[1].url.path.startswith("/routed-foot/table/")
    assert requests[2].url.path.endswith("/12.1,41.9;12.5,41.7")


def test_travel_matrix_cache_expires_and_evicts_oldest_entry() -> None:
    now = [100.0]
    cache = TravelMatrixCache(max_entries=1, ttl_seconds=5, clock=lambda: now[0])
    first = TravelMatrix(
        provider="osm",
        profile=RoutingProfile.CAR,
        snapshot=SNAPSHOT,
        durations_s=((0, 1), (1, 0)),
    )
    second_snapshot = _snapshot((41.9, 12.1), (41.7, 12.5))
    second = first.model_copy(update={"snapshot": second_snapshot})

    cache.set(first)
    assert cache.get("osm", RoutingProfile.CAR, SNAPSHOT) is not first
    cache.set(second)
    assert cache.get("osm", RoutingProfile.CAR, SNAPSHOT) is None
    now[0] += 5
    assert cache.get("osm", RoutingProfile.CAR, second_snapshot) is None


@pytest.mark.parametrize("provider_name", ["osm", "photon"])
@pytest.mark.parametrize("profile", [RoutingProfile.CAR, RoutingProfile.FOOT, RoutingProfile.BIKE])
def test_osm_and_photon_capabilities_use_supported_osrm_table_profiles(
    mock_routing_http, provider_name, profile
) -> None:
    requests = mock_routing_http(_ok_response)
    provider = OSRMTableRoutingProvider(provider_name)

    result = asyncio.run(provider.get_matrix(SNAPSHOT, profile))

    assert isinstance(result, TravelMatrix)
    assert provider.capability.supports_matrix(profile)
    assert "/table/v1/driving/" in requests[0].url.path
