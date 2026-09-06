from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from trip.config import get_settings
from trip.deps import get_session
from trip.models.models import MapProvider, Trip, TripDay, TripItem, User
from trip.optimization import TravelMatrixCache
from trip.routers import trips
from trip.security import create_access_token


@pytest.fixture
def optimize_day_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "SECRET_KEY", "optimize-day-api-test-secret-0000")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        owner = User(username="owner", password="not-used", map_provider=MapProvider.OPENSTREETMAP)
        other = User(username="other", password="not-used", map_provider=MapProvider.OPENSTREETMAP)
        session.add_all([owner, other])
        session.flush()
        owned_trip = Trip(name="Owned trip", user=owner.username)
        other_trip = Trip(name="Other trip", user=other.username)
        session.add_all([owned_trip, other_trip])
        session.flush()
        first_day = TripDay(label="Optimized day", trip_id=owned_trip.id)
        second_day = TripDay(label="Untouched day", trip_id=owned_trip.id)
        foreign_day = TripDay(label="Foreign day", trip_id=other_trip.id)
        session.add_all([first_day, second_day, foreign_day])
        session.flush()
        session.add_all(
            [
                TripItem(text="First", lat=41.0, lng=12.0, day_id=first_day.id, sequence=0),
                TripItem(text="Second", lat=41.1, lng=12.1, day_id=first_day.id, sequence=1),
                TripItem(text="Third", lat=41.2, lng=12.2, day_id=first_day.id, sequence=2),
                TripItem(text="Other day", lat=42.0, lng=13.0, day_id=second_day.id, sequence=7),
            ]
        )
        session.commit()

    app = FastAPI()
    app.include_router(trips.router)
    app.state.optimize_day_engine = engine

    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    monkeypatch.setattr(trips, "_day_matrix_cache", TravelMatrixCache())
    with TestClient(app) as client:
        yield client


def _auth(username: str = "owner") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': username})}"}


def _matrix_response(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": "Ok",
            "durations": [[0, 10, 1], [10, 0, 1], [1, 1, 0]],
            "distances": [[0, 1_000, 100], [1_000, 0, 100], [100, 100, 0]],
        },
    )


def _sequences(client: TestClient, day_id: int) -> list[tuple[int, int]]:
    with Session(client.app.state.optimize_day_engine) as session:
        return session.exec(
            select(TripItem.id, TripItem.sequence)
            .where(TripItem.day_id == day_id)
            .order_by(TripItem.id)
        ).all()


def _preview(client: TestClient, headers: dict[str, str]) -> httpx.Response:
    return client.post("/api/trips/1/optimize-day/1", json={"profile": "car"}, headers=headers)


def test_optimize_day_requires_authentication_and_scopes_day_to_accessible_trip(
    optimize_day_api: TestClient,
) -> None:
    unauthenticated = optimize_day_api.post("/api/trips/1/optimize-day/1", json={"profile": "car"})
    foreign_trip = optimize_day_api.post(
        "/api/trips/2/optimize-day/3", json={"profile": "car"}, headers=_auth()
    )
    foreign_day = optimize_day_api.post(
        "/api/trips/1/optimize-day/3", json={"profile": "car"}, headers=_auth()
    )

    assert unauthenticated.status_code == 401
    assert foreign_trip.status_code == 404
    assert foreign_day.status_code == 404


def test_preview_does_not_mutate_sequences_and_returns_proposal(
    optimize_day_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    before_first_day = _sequences(optimize_day_api, 1)
    before_second_day = _sequences(optimize_day_api, 2)

    response = _preview(optimize_day_api, _auth())

    assert response.status_code == 200
    assert response.json()["starting_item_ids"] == [1, 2, 3]
    assert response.json()["optimized_item_ids"] == [1, 3, 2]
    assert response.json()["cost_comparison"]["duration_saved_s"] == 9
    assert _sequences(optimize_day_api, 1) == before_first_day
    assert _sequences(optimize_day_api, 2) == before_second_day


def test_apply_recalculates_preview_and_changes_only_the_selected_day(
    optimize_day_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    preview = _preview(optimize_day_api, _auth())
    before_other_day = _sequences(optimize_day_api, 2)

    response = optimize_day_api.post(
        "/api/trips/1/optimize-day/1/apply",
        json={"profile": "car", "starting_item_ids": preview.json()["starting_item_ids"]},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert _sequences(optimize_day_api, 1) == [(1, 0), (2, 2), (3, 1)]
    assert _sequences(optimize_day_api, 2) == before_other_day


def test_apply_rejects_a_stale_or_invalid_preview_snapshot_without_mutating(
    optimize_day_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    before = _sequences(optimize_day_api, 1)

    stale = optimize_day_api.post(
        "/api/trips/1/optimize-day/1/apply",
        json={"profile": "car", "starting_item_ids": [1, 3, 2]},
        headers=_auth(),
    )
    invalid = optimize_day_api.post(
        "/api/trips/1/optimize-day/1/apply",
        json={"profile": "car", "starting_item_ids": [1, 1, 3]},
        headers=_auth(),
    )

    assert stale.status_code == 409
    assert invalid.status_code == 422
    assert _sequences(optimize_day_api, 1) == before


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda _: httpx.Response(503, json={"message": "down"}),
        lambda _: httpx.Response(
            200,
            json={
                "code": "Ok",
                "durations": [[0, 10, 1], [10, 0, None], [1, 1, 0]],
                "distances": [[0, 1_000, 100], [1_000, 0, None], [100, 100, 0]],
            },
        ),
    ],
    ids=["unavailable", "incomplete"],
)
def test_apply_preserves_sequence_when_the_matrix_cannot_support_a_complete_calculation(
    optimize_day_api: TestClient, mock_routing_http, response_factory
) -> None:
    mock_routing_http(response_factory)
    before = _sequences(optimize_day_api, 1)

    response = optimize_day_api.post(
        "/api/trips/1/optimize-day/1/apply",
        json={"profile": "car", "starting_item_ids": [1, 2, 3]},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert _sequences(optimize_day_api, 1) == before


def test_apply_is_atomic_when_persisting_a_sequence_fails(
    optimize_day_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    before = _sequences(optimize_day_api, 1)
    with Session(optimize_day_api.app.state.optimize_day_engine) as session:
        session.exec(
            text(
                "CREATE TRIGGER reject_second_item_sequence "
                "BEFORE UPDATE OF sequence ON tripitem WHEN NEW.id = 2 "
                "BEGIN SELECT RAISE(ABORT, 'sequence failure'); END"
            )
        )
        session.commit()

    response = optimize_day_api.post(
        "/api/trips/1/optimize-day/1/apply",
        json={"profile": "car", "starting_item_ids": [1, 2, 3]},
        headers=_auth(),
    )

    assert response.status_code == 500
    assert _sequences(optimize_day_api, 1) == before


def test_preview_reports_coordinateless_items_without_dropping_them(
    optimize_day_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(
        lambda _: httpx.Response(
            200,
            json={
                "code": "Ok",
                "durations": [[0, 10], [8, 0]],
                "distances": [[0, 1_000], [800, 0]],
            },
        )
    )
    with Session(optimize_day_api.app.state.optimize_day_engine) as session:
        item = session.get(TripItem, 2)
        assert item is not None
        item.lat = None
        item.lng = None
        session.commit()

    response = _preview(optimize_day_api, _auth())

    assert response.status_code == 200
    assert response.json()["starting_item_ids"] == [1, 2, 3]
    assert sorted(response.json()["optimized_item_ids"]) == [1, 2, 3]
    assert response.json()["diagnostics"][0]["kind"] == "coordinateless_item"
    assert response.json()["diagnostics"][0]["item_ids"] == [2]


def test_preview_uses_only_the_authenticated_users_selected_matrix_provider(
    optimize_day_api: TestClient, mock_routing_http
) -> None:
    requests = mock_routing_http(_matrix_response)
    with Session(optimize_day_api.app.state.optimize_day_engine) as session:
        owner = session.get(User, "owner")
        assert owner is not None
        owner.map_provider = MapProvider.GOOGLE
        session.commit()

    response = _preview(optimize_day_api, _auth())

    assert response.status_code == 200
    assert response.json()["optimized_item_ids"] == [1, 2, 3]
    assert response.json()["cost_comparison"] is None
    assert response.json()["diagnostics"][0]["routing_failure"]["provider"] == "google"
    assert requests == []
