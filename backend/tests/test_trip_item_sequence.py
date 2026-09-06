import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from trip.config import get_settings
from trip.deps import get_session
from trip.models.models import Trip, TripDay, TripItem, User
from trip.routers import trips
from trip.security import create_access_token


def _load_sequence_migration():
    path = Path(__file__).parents[1] / "trip/alembic/versions/c8a5b1d3e7f2_tripitem_sequence.py"
    spec = importlib.util.spec_from_file_location("tripitem_sequence_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sequence_migration_backfills_existing_time_order_per_day() -> None:
    engine = create_engine("sqlite://")
    migration = _load_sequence_migration()

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE tripitem ("
                "id INTEGER PRIMARY KEY, day_id INTEGER NOT NULL, time VARCHAR NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO tripitem (id, day_id, time) VALUES (:id, :day_id, :time)"),
            [
                {"id": 31, "day_id": 1, "time": "10:00"},
                {"id": 12, "day_id": 1, "time": "08:00"},
                {"id": 14, "day_id": 1, "time": "08:00"},
                {"id": 9, "day_id": 1, "time": None},
                {"id": 22, "day_id": 2, "time": "09:00"},
                {"id": 18, "day_id": 2, "time": None},
            ],
        )

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        rows = connection.execute(
            text("SELECT id, day_id, sequence FROM tripitem ORDER BY day_id, sequence")
        ).all()
        assert rows == [(9, 1, 0), (12, 1, 1), (14, 1, 2), (31, 1, 3), (18, 2, 0), (22, 2, 1)]
        assert inspect(connection).get_columns("tripitem")[-1]["nullable"] is False


@pytest.fixture
def trip_items_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "SECRET_KEY", "trip-item-sequence-test-secret-0000")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="sequence-user", password="not-used")
        session.add(user)
        session.flush()
        trip = Trip(name="Sequence test", user=user.username)
        session.add(trip)
        session.flush()
        session.add_all(
            [
                TripDay(label="First day", trip_id=trip.id),
                TripDay(label="Second day", trip_id=trip.id),
            ]
        )
        session.commit()

    app = FastAPI()
    app.include_router(trips.router)
    app.state.trip_items_engine = engine

    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as client:
        yield client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': 'sequence-user'})}"}


def test_trip_item_sequence_is_serialized_and_does_not_replace_time_display_order(
    trip_items_api: TestClient,
) -> None:
    first = trip_items_api.post(
        "/api/trips/1/days/1/items",
        json={"text": "Later", "time": "10:00"},
        headers=_auth(),
    )
    second = trip_items_api.post(
        "/api/trips/1/days/1/items",
        json={"text": "Earlier", "time": "08:00"},
        headers=_auth(),
    )

    assert first.status_code == 200
    assert first.json()["sequence"] == 0
    assert second.status_code == 200
    assert second.json()["sequence"] == 1

    trip = trip_items_api.get("/api/trips/1", headers=_auth())

    assert trip.status_code == 200
    assert [item["text"] for item in trip.json()["days"][0]["items"]] == ["Earlier", "Later"]
    assert [item["sequence"] for item in trip.json()["days"][0]["items"]] == [1, 0]


def test_moving_an_item_assigns_a_sequence_at_the_end_of_its_new_day(
    trip_items_api: TestClient,
) -> None:
    moved = trip_items_api.post(
        "/api/trips/1/days/1/items",
        json={"text": "Move me"},
        headers=_auth(),
    )
    existing = trip_items_api.post(
        "/api/trips/1/days/2/items",
        json={"text": "Already there"},
        headers=_auth(),
    )

    response = trip_items_api.put(
        f"/api/trips/1/days/1/items/{moved.json()['id']}",
        json={"day_id": 2},
        headers=_auth(),
    )

    assert existing.status_code == 200
    assert response.status_code == 200
    assert response.json()["day_id"] == 2
    assert response.json()["sequence"] == 1
    with Session(trip_items_api.app.state.trip_items_engine) as session:
        assert session.exec(select(TripItem.sequence).where(TripItem.day_id == 2).order_by(TripItem.sequence)).all() == [
            0,
            1,
        ]
