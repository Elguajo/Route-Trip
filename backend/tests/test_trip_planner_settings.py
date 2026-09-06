import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from trip.config import get_settings
from trip.deps import get_session
from trip.models.models import (
    PlannerOptimizationObjective,
    PlannerRoutingProfile,
    Trip,
    TripPlannerSettings,
    User,
)
from trip.routers import trips
from trip.security import create_access_token


def _load_planner_settings_migration():
    path = Path(__file__).parents[1] / "trip/alembic/versions/e4b3f14f9a2c_trip_planner_settings.py"
    spec = importlib.util.spec_from_file_location("trip_planner_settings_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_planner_settings_migration_backfills_existing_trips_with_compatible_defaults() -> None:
    engine = create_engine("sqlite://")
    migration = _load_planner_settings_migration()

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE trip (id INTEGER PRIMARY KEY, user VARCHAR NOT NULL)"))
        connection.execute(text("INSERT INTO trip (id, user) VALUES (10, 'one'), (20, 'two')"))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        settings = connection.execute(
            text(
                "SELECT trip_id, requested_days, start_lat, end_lat, return_to_start, "
                "allowed_profiles, objective FROM tripplannersettings ORDER BY trip_id"
            )
        ).mappings().all()
        assert [dict(row) for row in settings] == [
            {
                "trip_id": 10,
                "requested_days": 1,
                "start_lat": None,
                "end_lat": None,
                "return_to_start": 0,
                "allowed_profiles": '["car"]',
                "objective": "duration",
            },
            {
                "trip_id": 20,
                "requested_days": 1,
                "start_lat": None,
                "end_lat": None,
                "return_to_start": 0,
                "allowed_profiles": '["car"]',
                "objective": "duration",
            },
        ]
        foreign_keys = inspect(connection).get_foreign_keys("tripplannersettings")
        assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"

        with Operations.context(context):
            migration.downgrade()
        assert "tripplannersettings" not in inspect(connection).get_table_names()


@pytest.fixture
def planner_settings_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "SECRET_KEY", "trip-planner-settings-test-secret-0000")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="planner-user", password="not-used")
        session.add(user)
        session.flush()
        session.add(Trip(name="Planner test", user=user.username))
        session.commit()

    app = FastAPI()
    app.include_router(trips.router)
    app.state.planner_settings_engine = engine

    def get_test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as client:
        yield client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': 'planner-user'})}"}


def test_planner_settings_are_serialized_and_persisted_through_the_trip_router(
    planner_settings_api: TestClient,
) -> None:
    default_trip = planner_settings_api.get("/api/trips/1", headers=_auth())
    assert default_trip.status_code == 200
    assert default_trip.json()["planner_settings"] == {
        "requested_days": 1,
        "start_location": None,
        "end_location": None,
        "return_to_start": False,
        "allowed_profiles": ["car"],
        "objective": "duration",
    }

    payload = {
        "requested_days": 3,
        "start_location": {"lat": 41.2995, "lng": 69.2401},
        "end_location": {"lat": 41.3111, "lng": 69.2797},
        "return_to_start": False,
        "allowed_profiles": ["car", "foot"],
        "objective": "distance",
    }
    saved = planner_settings_api.put("/api/trips/1/planner-settings", json=payload, headers=_auth())

    assert saved.status_code == 200
    assert saved.json() == payload
    assert planner_settings_api.get("/api/trips/1", headers=_auth()).json()["planner_settings"] == payload

    with Session(planner_settings_api.app.state.planner_settings_engine) as session:
        settings = session.get(TripPlannerSettings, 1)
        assert settings is not None
        assert settings.requested_days == 3
        assert settings.start_lat == 41.2995
        assert settings.end_lng == 69.2797
        assert settings.allowed_profiles == [PlannerRoutingProfile.CAR, PlannerRoutingProfile.FOOT]
        assert settings.objective == PlannerOptimizationObjective.DISTANCE


def test_new_trips_receive_default_planner_settings(planner_settings_api: TestClient) -> None:
    created = planner_settings_api.post("/api/trips", json={"name": "New planner trip"}, headers=_auth())

    assert created.status_code == 200
    trip_id = created.json()["id"]
    settings = planner_settings_api.get(f"/api/trips/{trip_id}/planner-settings", headers=_auth())
    assert settings.status_code == 200
    assert settings.json()["allowed_profiles"] == ["car"]
    assert settings.json()["objective"] == "duration"


def test_planner_settings_reject_an_empty_or_duplicated_profile_list(planner_settings_api: TestClient) -> None:
    base = {
        "requested_days": 1,
        "return_to_start": False,
        "objective": "duration",
    }

    assert (
        planner_settings_api.put(
            "/api/trips/1/planner-settings", json={**base, "allowed_profiles": []}, headers=_auth()
        ).status_code
        == 422
    )
    assert (
        planner_settings_api.put(
            "/api/trips/1/planner-settings",
            json={**base, "allowed_profiles": ["car", "car"]},
            headers=_auth(),
        ).status_code
        == 422
    )
