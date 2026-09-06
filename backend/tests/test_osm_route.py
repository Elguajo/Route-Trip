import asyncio

import httpx

from trip.models.models import LatLng, RoutingQuery
from trip.utils.providers.osm import OpenStreetMapProvider


def test_osm_direct_route_uses_expected_osrm_request(mock_routing_http) -> None:
    def route_response(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/routed-car/route/v1/driving/12.1,41.9;12.5,41.8"
        assert dict(request.url.params) == {
            "overview": "simplified",
            "alternatives": "false",
            "steps": "false",
            "annotations": "false",
        }
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [{"distance": 1_234.5, "duration": 456.7, "geometry": "_p~iF~ps|U_ulLnnqC"}],
            },
        )

    requests = mock_routing_http(route_response)

    route = asyncio.run(
        OpenStreetMapProvider().get_route(
            RoutingQuery(
                profile="car",
                coordinates=[LatLng(lat=41.9, lng=12.1), LatLng(lat=41.8, lng=12.5)],
            )
        )
    )

    assert len(requests) == 1
    assert route.distance == 1_234.5
    assert route.duration == 456.7
    assert route.coordinates == [(-120.2, 38.5), (-120.95, 40.7)]
