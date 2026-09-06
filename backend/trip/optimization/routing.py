"""Value objects and contracts for provider-neutral travel matrices.

This module deliberately does not import map-search providers or OSRM.  An
adapter supplies a matrix for one selected provider, while callers can make an
explicit decision from its capability when that provider cannot do so.
"""

from __future__ import annotations

from enum import Enum
from math import isfinite
from typing import Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoutingProfile(str, Enum):
    CAR = "car"
    FOOT = "foot"
    BIKE = "bike"
    TRANSIT = "transit"


class TravelLocation(BaseModel):
    """An immutable geographic coordinate used for a routing calculation."""

    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class CoordinateSnapshot(BaseModel):
    """An ordered, immutable set of coordinates for one matrix request."""

    model_config = ConfigDict(frozen=True)

    coordinates: tuple[TravelLocation, ...] = Field(min_length=1)


class TravelMatrixRequest(BaseModel):
    """Input for a non-mutating matrix calculation."""

    profile: RoutingProfile
    coordinates: tuple[TravelLocation, ...] = Field(min_length=2)

    def snapshot(self) -> CoordinateSnapshot:
        return CoordinateSnapshot(coordinates=self.coordinates)


MatrixCell: TypeAlias = float | None
MatrixRows: TypeAlias = tuple[tuple[MatrixCell, ...], ...]


class TravelMatrix(BaseModel):
    """Travel values for one provider/profile/coordinate snapshot.

    ``None`` represents an unreachable origin/destination pair.  It is not a
    substitute for a straight-line estimate.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    profile: RoutingProfile
    snapshot: CoordinateSnapshot
    durations_s: MatrixRows
    distances_m: MatrixRows | None = None

    @model_validator(mode="after")
    def _validate_dimensions_and_values(self) -> "TravelMatrix":
        expected_size = len(self.snapshot.coordinates)
        self._validate_rows("durations_s", self.durations_s, expected_size)
        if self.distances_m is not None:
            self._validate_rows("distances_m", self.distances_m, expected_size)
        return self

    @staticmethod
    def _validate_rows(name: str, rows: MatrixRows, expected_size: int) -> None:
        if len(rows) != expected_size:
            raise ValueError(f"{name} must contain one row per snapshot coordinate")
        for row in rows:
            if len(row) != expected_size:
                raise ValueError(f"{name} rows must contain one cell per snapshot coordinate")
            for value in row:
                if value is not None and (not isfinite(value) or value < 0):
                    raise ValueError(f"{name} cells must be finite, non-negative values or null")


class RoutingCapability(BaseModel):
    """Matrix profiles supplied by a selected routing provider."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    matrix_profiles: tuple[RoutingProfile, ...] = ()

    def supports_matrix(self, profile: RoutingProfile) -> bool:
        return profile in self.matrix_profiles


class RoutingFailureKind(str, Enum):
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    UNSUPPORTED_PROFILE = "unsupported_profile"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"


class RoutingFailure(BaseModel):
    """A provider-neutral, non-exceptional matrix failure."""

    model_config = ConfigDict(frozen=True)

    kind: RoutingFailureKind
    provider: str = Field(min_length=1)
    profile: RoutingProfile
    message: str = Field(min_length=1)
    retryable: bool


TravelMatrixResult: TypeAlias = TravelMatrix | RoutingFailure


@runtime_checkable
class RoutingProvider(Protocol):
    """Contract implemented by a selected provider's matrix adapter."""

    @property
    def capability(self) -> RoutingCapability: ...

    async def get_matrix(
        self, snapshot: CoordinateSnapshot, profile: RoutingProfile
    ) -> TravelMatrixResult: ...


_OSRM_MATRIX_PROFILES = (
    RoutingProfile.CAR,
    RoutingProfile.FOOT,
    RoutingProfile.BIKE,
)
_ROUTING_CAPABILITIES = {
    "osm": RoutingCapability(provider="osm", matrix_profiles=_OSRM_MATRIX_PROFILES),
    "photon": RoutingCapability(provider="photon", matrix_profiles=_OSRM_MATRIX_PROFILES),
    # Google currently has direct routing only.  A future matrix adapter must
    # opt in explicitly instead of treating its direct-route profiles as matrix
    # support.
    "google": RoutingCapability(provider="google"),
}


def get_routing_capability(provider: str) -> RoutingCapability:
    """Return matrix capability for the selected provider without fallback."""

    provider_id = provider.lower()
    return _ROUTING_CAPABILITIES.get(provider_id, RoutingCapability(provider=provider_id))


def unsupported_matrix_failure(
    capability: RoutingCapability, profile: RoutingProfile
) -> RoutingFailure:
    """Describe why a selected provider cannot serve a matrix request."""

    if not capability.matrix_profiles:
        return RoutingFailure(
            kind=RoutingFailureKind.UNSUPPORTED_PROVIDER,
            provider=capability.provider,
            profile=profile,
            message="The selected provider does not support travel matrices",
            retryable=False,
        )
    return RoutingFailure(
        kind=RoutingFailureKind.UNSUPPORTED_PROFILE,
        provider=capability.provider,
        profile=profile,
        message="The selected provider does not support this travel-matrix profile",
        retryable=False,
    )
