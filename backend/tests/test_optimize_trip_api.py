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
from trip.models.models import Category, MapProvider, Place, Trip, TripDay, TripItem, TripPlannerSettings, User
from trip.optimization import TravelMatrixCache
from trip.routers import trips
from trip.security import create_access_token


@pytest.fixture
def optimize_trip_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "SECRET_KEY", "optimize-trip-api-test-secret-0000")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        owner = User(username="owner", password="not-used", map_provider=MapProvider.OPENSTREETMAP)
        session.add(owner)
        session.flush()
        category = Category(name="Test", user=owner.username)
        trip = Trip(name="Whole trip", user=owner.username)
        session.add_all([category, trip])
        session.flush()
        session.add(TripPlannerSettings(trip_id=trip.id, requested_days=2))
        first_day = TripDay(label="Existing day", trip_id=trip.id)
        session.add(first_day)
        session.flush()
        places = [
            Place(name=f"POI {index}", lat=lat, lng=0, place="Test", user=owner.username, category_id=category.id)
            for index, lat in enumerate((1.0, 1.1, 9.0, 9.1), start=1)
        ]
        session.add_all(places)
        session.flush()
        session.add_all(
            [
                TripItem(text=place.name, day_id=first_day.id, place_id=place.id, sequence=index)
                for index, place in enumerate(places)
            ]
            + [TripItem(text="Unmanaged event", day_id=first_day.id, sequence=10)]
        )
        session.commit()

    app = FastAPI()
    app.include_router(trips.router)
    app.state.optimize_trip_engine = engine

    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    monkeypatch.setattr(trips, "_day_matrix_cache", TravelMatrixCache())
    with TestClient(app) as client:
        yield client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}


def _matrix_response(request: httpx.Request) -> httpx.Response:
    coordinates = request.url.path.rsplit("/", 1)[-1].split(";")
    lats = [float(coordinate.split(",")[1]) for coordinate in coordinates]
    durations = [[0 if i == j else abs(left - right) * 100 for j, right in enumerate(lats)] for i, left in enumerate(lats)]
    distances = [[value * 10 for value in row] for row in durations]
    return httpx.Response(200, json={"code": "Ok", "durations": durations, "distances": distances})


def _assignments(client: TestClient) -> list[tuple[int, int, int]]:
    with Session(client.app.state.optimize_trip_engine) as session:
        return session.exec(
            select(TripItem.id, TripItem.day_id, TripItem.sequence).order_by(TripItem.id)
        ).all()


def _preview(client: TestClient) -> httpx.Response:
    return client.post("/api/trips/1/optimize", headers=_auth())


def test_whole_trip_preview_is_non_mutating_and_preserves_every_eligible_poi(
    optimize_trip_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    before = _assignments(optimize_trip_api)

    response = _preview(optimize_trip_api)

    assert response.status_code == 200
    body = response.json()
    assert [assignment["item_id"] for assignment in body["starting_assignments"]] == [1, 2, 3, 4]
    assert sorted(item_id for day in body["allocation"]["days"] for item_id in day["item_ids"]) == [1, 2, 3, 4]
    assert body["target_day_ids"] == [1, None]
    assert body["totals"] is not None
    assert _assignments(optimize_trip_api) == before


def test_whole_trip_apply_recalculates_and_persists_only_poi_assignments(
    optimize_trip_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    preview = _preview(optimize_trip_api)
    assert preview.status_code == 200

    response = optimize_trip_api.post(
        "/api/trips/1/optimize/apply",
        json={
            "starting_assignments": preview.json()["starting_assignments"],
            "snapshot_token": preview.json()["snapshot_token"],
        },
        headers=_auth(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is True
    assert len(body["applied_day_ids"]) == 2
    assert body["target_day_ids"] == body["applied_day_ids"]
    reloaded = optimize_trip_api.get("/api/trips/1", headers=_auth())
    assert reloaded.status_code == 200
    planned_items = [item for day in reloaded.json()["days"] for item in day["items"] if item["id"] in {1, 2, 3, 4}]
    assert sorted(item["id"] for item in planned_items) == [1, 2, 3, 4]
    assert {item["day_id"] for item in planned_items} == set(body["applied_day_ids"])
    unmanaged = next(item for day in reloaded.json()["days"] for item in day["items"] if item["id"] == 5)
    assert unmanaged["day_id"] == 1
    assert unmanaged["sequence"] == 10


def test_whole_trip_apply_rejects_a_stale_snapshot_without_mutating(
    optimize_trip_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    preview = _preview(optimize_trip_api)
    before = _assignments(optimize_trip_api)
    with Session(optimize_trip_api.app.state.optimize_trip_engine) as session:
        first_item = session.get(TripItem, 1)
        assert first_item is not None
        first_item.sequence = 99
        session.commit()
    changed = _assignments(optimize_trip_api)

    response = optimize_trip_api.post(
        "/api/trips/1/optimize/apply",
        json={
            "starting_assignments": preview.json()["starting_assignments"],
            "snapshot_token": preview.json()["snapshot_token"],
        },
        headers=_auth(),
    )

    assert response.status_code == 409
    assert changed != before
    assert _assignments(optimize_trip_api) == changed


def test_whole_trip_preview_reports_invalid_coordinates_without_dropping_the_poi(
    optimize_trip_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    with Session(optimize_trip_api.app.state.optimize_trip_engine) as session:
        item = session.get(TripItem, 4)
        assert item is not None
        item.lat = 999
        item.lng = 0
        session.commit()

    response = _preview(optimize_trip_api)

    assert response.status_code == 200
    body = response.json()
    assert sorted(item_id for day in body["allocation"]["days"] for item_id in day["item_ids"]) == [1, 2, 3, 4]
    assert body["allocation"]["diagnostics"][0]["kind"] == "coordinateless_item"
    assert body["allocation"]["diagnostics"][0]["item_ids"] == [4]


def test_whole_trip_apply_rolls_back_day_creation_and_assignments_on_failure(
    optimize_trip_api: TestClient, mock_routing_http
) -> None:
    mock_routing_http(_matrix_response)
    preview = _preview(optimize_trip_api)
    before = _assignments(optimize_trip_api)
    with Session(optimize_trip_api.app.state.optimize_trip_engine) as session:
        session.exec(
            text(
                "CREATE TRIGGER reject_planned_item BEFORE UPDATE OF day_id ON tripitem "
                "WHEN NEW.id = 1 BEGIN SELECT RAISE(ABORT, 'assignment failure'); END"
            )
        )
        session.commit()

    response = optimize_trip_api.post(
        "/api/trips/1/optimize/apply",
        json={
            "starting_assignments": preview.json()["starting_assignments"],
            "snapshot_token": preview.json()["snapshot_token"],
        },
        headers=_auth(),
    )

    assert response.status_code == 500
    assert _assignments(optimize_trip_api) == before
    with Session(optimize_trip_api.app.state.optimize_trip_engine) as session:
        assert session.exec(select(TripDay)).all() == [session.get(TripDay, 1)]
