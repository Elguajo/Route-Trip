"""Provider-neutral travel-matrix contracts for Smart Planner."""

from .cache import TravelMatrixCache
from .osrm_table import OSRMTableRoutingProvider
from .routing import (
    CoordinateSnapshot,
    RoutingCapability,
    RoutingFailure,
    RoutingFailureKind,
    RoutingProfile,
    RoutingProvider,
    TravelLocation,
    TravelMatrix,
    TravelMatrixRequest,
    TravelMatrixResult,
    get_routing_capability,
    unsupported_matrix_failure,
)

__all__ = [
    "CoordinateSnapshot",
    "OSRMTableRoutingProvider",
    "RoutingCapability",
    "RoutingFailure",
    "RoutingFailureKind",
    "RoutingProfile",
    "RoutingProvider",
    "TravelLocation",
    "TravelMatrixCache",
    "TravelMatrix",
    "TravelMatrixRequest",
    "TravelMatrixResult",
    "get_routing_capability",
    "unsupported_matrix_failure",
]
