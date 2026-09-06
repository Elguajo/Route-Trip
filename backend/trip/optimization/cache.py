"""Process-local cache for immutable routing-matrix results."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from .routing import CoordinateSnapshot, RoutingProfile, TravelMatrix


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    matrix: TravelMatrix


class TravelMatrixCache:
    """A bounded LRU cache with a TTL for successful matrix responses only."""

    def __init__(
        self,
        *,
        max_entries: int = 128,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0")

        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[MatrixCacheKey, _CacheEntry] = OrderedDict()

    def get(
        self,
        provider: str,
        profile: RoutingProfile,
        snapshot: CoordinateSnapshot,
    ) -> TravelMatrix | None:
        key = self._key(provider, profile, snapshot)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return entry.matrix.model_copy(deep=True)

    def set(self, matrix: TravelMatrix) -> None:
        key = self._key(matrix.provider, matrix.profile, matrix.snapshot)
        self._entries[key] = _CacheEntry(
            expires_at=self._clock() + self._ttl_seconds,
            matrix=matrix.model_copy(deep=True),
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    @staticmethod
    def _key(
        provider: str,
        profile: RoutingProfile,
        snapshot: CoordinateSnapshot,
    ) -> "MatrixCacheKey":
        return (
            provider.lower(),
            profile,
            tuple((location.lat, location.lng) for location in snapshot.coordinates),
        )


MatrixCacheKey = tuple[str, RoutingProfile, tuple[tuple[float, float], ...]]
