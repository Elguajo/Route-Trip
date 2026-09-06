from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from trip.config import get_settings
from trip.deps import get_session
from trip.models.models import MapProvider, User
from trip.optimization import TravelMatrixCache
from trip.routers import providers
from trip.security import create_access_token


@pytest.fixture
def matrix_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An API app using the real authentication dependency and an isolated DB."""

    monkeypatch.setattr(get_settings(), "SECRET_KEY", "matrix-api-test-secret-for-jwt-tests-0000")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                User(username="osm-user", password="not-used", map_provider=MapProvider.OPENSTREETMAP),
                User(username="photon-user", password="not-used", map_provider=MapProvider.PHOTON),
                User(username="google-user", password="not-used", map_provider=MapProvider.GOOGLE),
            ]
        )
        session.commit()

    app = FastAPI()
    app.include_router(providers.router)
    app.state.matrix_engine = engine

    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    monkeypatch.setattr(providers, "_matrix_cache", TravelMatrixCache())
    with TestClient(app) as client:
        yield client


def _auth(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': username})}"}


REQUEST = {
    "profile": "car",
    "coordinates": [{"lat": 41.9, "lng": 12.1}, {"lat": 41.8, "lng": 12.5}],
}


def _matrix_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": "Ok",
            "durations": [[0, 456.7], [410.2, 0]],
            "distances": [[0, 1_234.5], [1_100.25, 0]],
        },
    )


def test_matrix_api_requires_authentication(matrix_api: TestClient) -> None:
    response = matrix_api.post("/api/completions/matrix", json=REQUEST)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_matrix_api_uses_authenticated_users_selected_provider_and_keeps_snapshot(
    matrix_api: TestClient, mock_routing_http
) -> None:
    requests = mock_routing_http(_matrix_response)

    response = matrix_api.post(
        "/api/completions/matrix", json=REQUEST, headers=_auth("photon-user")
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "photon",
        "profile": "car",
        "snapshot": {"coordinates": REQUEST["coordinates"]},
        "durations_s": [[0.0, 456.7], [410.2, 0.0]],
        "distances_m": [[0.0, 1_234.5], [1_100.25, 0.0]],
    }
    assert len(requests) == 1
    assert requests[0].url.path.startswith("/routed-car/table/")

    cached = matrix_api.post(
        "/api/completions/matrix", json=REQUEST, headers=_auth("photon-user")
    )
    assert cached.status_code == 200
    assert len(requests) == 1


@pytest.mark.parametrize("profile", ["car", "foot", "bike"])
def test_matrix_api_supports_all_osrm_profiles(
    matrix_api: TestClient, mock_routing_http, profile: str
) -> None:
    requests = mock_routing_http(_matrix_response)
    payload = {**REQUEST, "profile": profile}

    response = matrix_api.post(
        "/api/completions/matrix", json=payload, headers=_auth("osm-user")
    )

    assert response.status_code == 200
    assert response.json()["profile"] == profile
    assert f"/routed-{profile if profile != 'bike' else 'bike'}/table/" in requests[0].url.path


@pytest.mark.parametrize(
    ("username", "profile", "kind"),
    [
        ("google-user", "car", "unsupported_provider"),
        ("osm-user", "transit", "unsupported_profile"),
    ],
)
def test_matrix_api_returns_typed_unsupported_capability_failure(
    matrix_api: TestClient, mock_routing_http, username: str, profile: str, kind: str
) -> None:
    requests = mock_routing_http(_matrix_response)

    response = matrix_api.post(
        "/api/completions/matrix",
        json={**REQUEST, "profile": profile},
        headers=_auth(username),
    )

    assert response.status_code == 200
    assert response.json()["kind"] == kind
    assert response.json()["retryable"] is False
    assert requests == []


def test_matrix_api_returns_typed_routing_failure_without_mutating_data(
    matrix_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(lambda request: httpx.Response(503, json={"message": "down"}))

    response = matrix_api.post(
        "/api/completions/matrix", json=REQUEST, headers=_auth("osm-user")
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "provider_unavailable",
        "provider": "osm",
        "profile": "car",
        "message": "OSRM Table request failed with status 503",
        "retryable": True,
    }


def test_matrix_api_does_not_mutate_database(matrix_api: TestClient, mock_routing_http) -> None:
    mock_routing_http(_matrix_response)
    with Session(matrix_api.app.state.matrix_engine) as session:
        before = session.exec(select(User.username, User.map_provider)).all()

    response = matrix_api.post(
        "/api/completions/matrix", json=REQUEST, headers=_auth("osm-user")
    )

    assert response.status_code == 200
    with Session(matrix_api.app.state.matrix_engine) as session:
        assert session.exec(select(User.username, User.map_provider)).all() == before


def test_direct_route_api_remains_compatible(matrix_api: TestClient, mock_routing_http) -> None:
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
                "routes": [
                    {
                        "distance": 1_234.5,
                        "duration": 456.7,
                        "geometry": "_p~iF~ps|U_ulLnnqC",
                    }
                ],
            },
        )

    requests = mock_routing_http(route_response)

    response = matrix_api.post(
        "/api/completions/route", json=REQUEST, headers=_auth("osm-user")
    )

    assert response.status_code == 200
    assert response.json() == {
        "distance": 1_234.5,
        "duration": 456.7,
        "coordinates": [[-120.2, 38.5], [-120.95, 40.7]],
    }
    assert len(requests) == 1
