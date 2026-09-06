"""OSRM Table adapter for provider-neutral travel-matrix requests."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

import httpx

from .cache import TravelMatrixCache
from .routing import (
    CoordinateSnapshot,
    MatrixRows,
    RoutingCapability,
    RoutingFailure,
    RoutingFailureKind,
    RoutingProfile,
    TravelMatrix,
    TravelMatrixResult,
    get_routing_capability,
    unsupported_matrix_failure,
)


# Separate from BaseMapProvider's direct Route URLs: optimizer callers depend
# only on this adapter and the provider-neutral RoutingProvider protocol.
OSRM_TABLE_ENDPOINTS: dict[str, dict[RoutingProfile, str]] = {
    "osm": {
        RoutingProfile.CAR: "https://routing.openstreetmap.de/routed-car/table/v1/driving",
        RoutingProfile.FOOT: "https://routing.openstreetmap.de/routed-foot/table/v1/driving",
        RoutingProfile.BIKE: "https://routing.openstreetmap.de/routed-bike/table/v1/driving",
    },
    "photon": {
        RoutingProfile.CAR: "https://routing.openstreetmap.de/routed-car/table/v1/driving",
        RoutingProfile.FOOT: "https://routing.openstreetmap.de/routed-foot/table/v1/driving",
        RoutingProfile.BIKE: "https://routing.openstreetmap.de/routed-bike/table/v1/driving",
    },
}


class OSRMTableRoutingProvider:
    """Fetch one selected compatible provider's matrix using OSRM Table only."""

    def __init__(
        self,
        provider: str,
        *,
        cache: TravelMatrixCache | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._provider = provider.lower()
        self._capability = get_routing_capability(self._provider)
        self._cache = cache or TravelMatrixCache()
        self._timeout_seconds = timeout_seconds

    @property
    def capability(self) -> RoutingCapability:
        return self._capability

    async def get_matrix(
        self,
        snapshot: CoordinateSnapshot,
        profile: RoutingProfile,
    ) -> TravelMatrixResult:
        if not self.capability.supports_matrix(profile):
            return unsupported_matrix_failure(self.capability, profile)

        endpoint = OSRM_TABLE_ENDPOINTS.get(self._provider, {}).get(profile)
        if endpoint is None:
            return RoutingFailure(
                kind=RoutingFailureKind.UNSUPPORTED_PROVIDER,
                provider=self._provider,
                profile=profile,
                message="The selected provider has no OSRM Table endpoint",
                retryable=False,
            )

        cached = self._cache.get(self._provider, profile, snapshot)
        if cached is not None:
            return cached

        coordinates = ";".join(
            f"{location.lng},{location.lat}" for location in snapshot.coordinates
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(
                    f"{endpoint}/{coordinates}",
                    params={"annotations": "duration,distance"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            return self._unavailable_failure(profile, "OSRM Table request timed out", retryable=True)
        except httpx.HTTPStatusError as exc:
            return self._unavailable_failure(
                profile,
                f"OSRM Table request failed with status {exc.response.status_code}",
                retryable=exc.response.status_code >= 500,
            )
        except httpx.HTTPError as exc:
            return self._unavailable_failure(
                profile, f"OSRM Table request failed: {exc}", retryable=True
            )
        except (TypeError, ValueError):
            return self._malformed_failure(profile, "OSRM Table response is not valid JSON")

        try:
            matrix = self._parse_response(payload, snapshot, profile)
        except (TypeError, ValueError) as exc:
            return self._malformed_failure(profile, f"Malformed OSRM Table response: {exc}")

        self._cache.set(matrix)
        return matrix.model_copy(deep=True)

    def _parse_response(
        self,
        payload: object,
        snapshot: CoordinateSnapshot,
        profile: RoutingProfile,
    ) -> TravelMatrix:
        if not isinstance(payload, Mapping):
            raise ValueError("expected an object")
        if payload.get("code") != "Ok":
            message = payload.get("message")
            if isinstance(message, str) and message:
                raise ValueError(f"OSRM returned {payload.get('code')}: {message}")
            raise ValueError(f"OSRM returned {payload.get('code')!r} instead of 'Ok'")

        size = len(snapshot.coordinates)
        durations = self._parse_rows(payload.get("durations"), "durations", size)
        raw_distances = payload.get("distances")
        distances = (
            None
            if raw_distances is None
            else self._parse_rows(raw_distances, "distances", size)
        )
        return TravelMatrix(
            provider=self._provider,
            profile=profile,
            snapshot=snapshot,
            durations_s=durations,
            distances_m=distances,
        )

    @staticmethod
    def _parse_rows(raw: object, name: str, size: int) -> MatrixRows:
        if not isinstance(raw, list) or len(raw) != size:
            raise ValueError(f"{name} must have {size} rows")

        rows = []
        for row in raw:
            if not isinstance(row, list) or len(row) != size:
                raise ValueError(f"{name} rows must each have {size} cells")
            cells = []
            for cell in row:
                if cell is None:
                    cells.append(None)
                elif isinstance(cell, bool) or not isinstance(cell, (int, float)):
                    raise ValueError(f"{name} cells must be numbers or null")
                elif not isfinite(cell) or cell < 0:
                    raise ValueError(f"{name} cells must be finite non-negative numbers")
                else:
                    cells.append(float(cell))
            rows.append(tuple(cells))
        return tuple(rows)

    def _unavailable_failure(
        self,
        profile: RoutingProfile,
        message: str,
        *,
        retryable: bool,
    ) -> RoutingFailure:
        return RoutingFailure(
            kind=RoutingFailureKind.PROVIDER_UNAVAILABLE,
            provider=self._provider,
            profile=profile,
            message=message,
            retryable=retryable,
        )

    def _malformed_failure(self, profile: RoutingProfile, message: str) -> RoutingFailure:
        return RoutingFailure(
            kind=RoutingFailureKind.MALFORMED_RESPONSE,
            provider=self._provider,
            profile=profile,
            message=message,
            retryable=False,
        )
