import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from trip.config import get_settings
from trip.deps import get_session
from trip.models.models import Trip, TripDay, User
from trip.optimization import DayTimeBudget, VisitDurationSource, resolve_visit_duration
from trip.routers import categories, places, trips
from trip.security import create_access_token


def _load_time_budget_migration():
    path = Path(__file__).parents[1] / "trip/alembic/versions/f1c3d9a8e2b4_time_budget_settings.py"
    spec = importlib.util.spec_from_file_location("time_budget_settings_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_time_budget_migration_backfills_compatible_category_and_day_defaults() -> None:
    engine = create_engine("sqlite://")
    migration = _load_time_budget_migration()

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE category (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL)"))
        connection.execute(text("CREATE TABLE tripday (id INTEGER PRIMARY KEY, label VARCHAR NOT NULL)"))
        connection.execute(text("CREATE TABLE tripitem (id INTEGER PRIMARY KEY, text VARCHAR NOT NULL)"))
        connection.execute(text("INSERT INTO category (id, name) VALUES (1, 'Culture')"))
        connection.execute(text("INSERT INTO tripday (id, label) VALUES (1, 'Day one')"))
        connection.execute(text("INSERT INTO tripitem (id, text) VALUES (1, 'Museum')"))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        assert connection.execute(
            text("SELECT default_duration FROM category WHERE id = 1")
        ).scalar_one() == 60
        assert connection.execute(
            text("SELECT start_time, end_time FROM tripday WHERE id = 1")
        ).one() == ("09:00", "18:00")
        assert connection.execute(text("SELECT duration FROM tripitem WHERE id = 1")).scalar_one() is None

        with Operations.context(context):
            migration.downgrade()

        assert "default_duration" not in {column["name"] for column in inspect(connection).get_columns("category")}
        assert "start_time" not in {column["name"] for column in inspect(connection).get_columns("tripday")}
        assert "duration" not in {column["name"] for column in inspect(connection).get_columns("tripitem")}


def test_duration_resolution_prefers_item_then_place_then_category_default() -> None:
    category = SimpleNamespace(default_duration=45)
    place = SimpleNamespace(duration=30, category=category)
    item = SimpleNamespace(duration=None, place=place)

    assert resolve_visit_duration(item).model_dump() == {
        "minutes": 30,
        "source": VisitDurationSource.PLACE_OVERRIDE,
    }

    item.duration = 15
    assert resolve_visit_duration(item).model_dump() == {
        "minutes": 15,
        "source": VisitDurationSource.ITEM_OVERRIDE,
    }

    item.duration = None
    place.duration = None
    assert resolve_visit_duration(item).model_dump() == {
        "minutes": 45,
        "source": VisitDurationSource.CATEGORY_DEFAULT,
    }


def test_day_time_budget_derives_a_positive_usable_window() -> None:
    assert DayTimeBudget(start_time="09:00", end_time="18:00").maximum_usable_minutes == 540
    with pytest.raises(ValidationError, match="end_time must be later"):
        DayTimeBudget(start_time="18:00", end_time="09:00")


@pytest.fixture
def time_budget_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "SECRET_KEY", "time-budget-settings-test-secret-0000")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="time-user", password="not-used")
        session.add(user)
        session.flush()
        session.add(Trip(name="Time settings", user=user.username))
        session.commit()

    app = FastAPI()
    app.include_router(categories.router)
    app.include_router(places.router)
    app.include_router(trips.router)
    app.state.time_budget_engine = engine

    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as client:
        yield client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': 'time-user'})}"}


def test_duration_and_day_time_settings_are_persisted_by_existing_contracts(
    time_budget_api: TestClient,
) -> None:
    category = time_budget_api.post(
        "/api/categories",
        json={"name": "Culture", "default_duration": 45},
        headers=_auth(),
    )
    assert category.status_code == 200
    assert category.json()["default_duration"] == 45

    updated_category = time_budget_api.put(
        f"/api/categories/{category.json()['id']}",
        json={"default_duration": 50},
        headers=_auth(),
    )
    assert updated_category.status_code == 200
    assert updated_category.json()["default_duration"] == 50

    place = time_budget_api.post(
        "/api/places",
        json={
            "name": "Museum",
            "place": "Test address",
            "lat": 41.0,
            "lng": 12.0,
            "category_id": category.json()["id"],
            "duration": 30,
        },
        headers=_auth(),
    )
    assert place.status_code == 200
    assert place.json()["duration"] == 30

    updated_place = time_budget_api.put(
        f"/api/places/{place.json()['id']}",
        json={"duration": 40},
        headers=_auth(),
    )
    assert updated_place.status_code == 200
    assert updated_place.json()["duration"] == 40

    created_day = time_budget_api.post(
        "/api/trips/1/days",
        json={"label": "Day one"},
        headers=_auth(),
    )
    assert created_day.status_code == 200
    assert created_day.json()["start_time"] == "09:00"
    assert created_day.json()["end_time"] == "18:00"

    day_id = created_day.json()["id"]
    updated_day = time_budget_api.put(
        f"/api/trips/1/days/{day_id}",
        json={"label": "Day one", "start_time": "10:00", "end_time": "17:30"},
        headers=_auth(),
    )
    assert updated_day.status_code == 200
    assert updated_day.json()["start_time"] == "10:00"
    assert updated_day.json()["end_time"] == "17:30"

    item = time_budget_api.post(
        f"/api/trips/1/days/{day_id}/items",
        json={"text": "Private tour", "duration": 75},
        headers=_auth(),
    )
    assert item.status_code == 200
    assert item.json()["duration"] == 75

    updated_item = time_budget_api.put(
        f"/api/trips/1/days/{day_id}/items/{item.json()['id']}",
        json={"duration": 90},
        headers=_auth(),
    )
    assert updated_item.status_code == 200
    assert updated_item.json()["duration"] == 90

    invalid_window = time_budget_api.put(
        f"/api/trips/1/days/{day_id}",
        json={"label": "Day one", "start_time": "18:00", "end_time": "09:00"},
        headers=_auth(),
    )
    assert invalid_window.status_code == 422
