"""Pure duration and time-window policy for the Smart Planner.

This module deliberately resolves inputs only.  Phase 004's later allocation
and ordering work consumes these values; it must not make a routing, provider,
or persistence decision here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_CATEGORY_DURATION_MINUTES = 60
MAX_VISIT_DURATION_MINUTES = 24 * 60


class VisitDurationSource(str, Enum):
    """The persisted layer that supplied a resolved visit duration."""

    ITEM_OVERRIDE = "item_override"
    PLACE_OVERRIDE = "place_override"
    CATEGORY_DEFAULT = "category_default"
    FALLBACK_DEFAULT = "fallback_default"


class VisitDurationResolution(BaseModel):
    """An immutable, explainable estimated visit duration."""

    model_config = ConfigDict(frozen=True)

    minutes: int = Field(ge=0, le=MAX_VISIT_DURATION_MINUTES)
    source: VisitDurationSource


class DayTimeBudget(BaseModel):
    """Validated daily time settings with their derived usable duration."""

    model_config = ConfigDict(frozen=True)

    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def require_24_hour_minutes(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or any(not part.isdigit() for part in parts):
            raise ValueError("time must use HH:MM format")
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59 or len(value) != 5:
            raise ValueError("time must use HH:MM format")
        return value

    @model_validator(mode="after")
    def require_positive_window(self) -> "DayTimeBudget":
        if _minutes_since_midnight(self.end_time) <= _minutes_since_midnight(self.start_time):
            raise ValueError("end_time must be later than start_time")
        return self

    @property
    def maximum_usable_minutes(self) -> int:
        return _minutes_since_midnight(self.end_time) - _minutes_since_midnight(self.start_time)

    @classmethod
    def from_day(cls, day: object) -> "DayTimeBudget":
        return cls(
            start_time=getattr(day, "start_time"),
            end_time=getattr(day, "end_time"),
        )


def resolve_visit_duration(item: object) -> VisitDurationResolution:
    """Resolve item, place, category, then system-default duration precedence.

    ``Place.duration`` predates Smart Planner and is treated as the durable
    place-specific override.  A nullable ``TripItem.duration`` takes priority
    for a single itinerary occurrence.  Old categories are backfilled to the
    same 60-minute default used when a relation is absent.
    """

    item_duration = getattr(item, "duration", None)
    if item_duration is not None:
        return _resolution(item_duration, VisitDurationSource.ITEM_OVERRIDE)

    place = getattr(item, "place", None)
    place_duration = getattr(place, "duration", None) if place is not None else None
    if place_duration is not None:
        return _resolution(place_duration, VisitDurationSource.PLACE_OVERRIDE)

    category = getattr(place, "category", None) if place is not None else None
    category_duration = getattr(category, "default_duration", None) if category is not None else None
    if category_duration is not None:
        return _resolution(category_duration, VisitDurationSource.CATEGORY_DEFAULT)

    return VisitDurationResolution(
        minutes=DEFAULT_CATEGORY_DURATION_MINUTES,
        source=VisitDurationSource.FALLBACK_DEFAULT,
    )


def _resolution(minutes: int, source: VisitDurationSource) -> VisitDurationResolution:
    return VisitDurationResolution(minutes=minutes, source=source)


def _minutes_since_midnight(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute
