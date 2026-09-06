# Smart Planner architecture

## Code-grounded baseline

The backend is FastAPI + SQLModel. `Place`, `Trip`, `TripDay`, and `TripItem` live in `backend/trip/models/models.py`; Alembic migrations are in `backend/trip/alembic/versions/`. `TripDay.items` are currently ordered by `TripItem.time`, which is unsuitable as a durable manual/optimizer order because a time is optional and represents scheduling rather than sequence.

`POST /api/completions/route` in `backend/trip/routers/providers.py` delegates through the selected `BaseMapProvider`. OpenStreetMap and Photon providers call OSRM Route; Google calls Google Routes. `RouteManagerService` renders Leaflet route layers and badges; `TripComponent.dayRouting()` currently issues a separate client request for every adjacent pair. `getGeolocationLatLng()` already wraps the browser Geolocation API. There is no optimisation module, matrix endpoint, drag-and-drop reorder, or automated test suite in the repository today.

## Target domain and ownership

| Concept | Current / proposed owner | Notes |
| --- | --- | --- |
| POI | Existing `Place` | Coordinates are the planner input; visit-duration/default policy is added later. |
| Trip | Existing `Trip` plus planner settings | Owns trip-wide locations, profiles, and optimisation policy. |
| Day | Existing `TripDay` plus day settings | Owns day time window and optional endpoint overrides. |
| TripItem | Existing `TripItem` plus planner fields | Owns persistent sequence, status, lock/priority/visit timing in later phases. |
| RouteSegment | Optimiser response/cache value object | Calculated; not a replacement ORM aggregate. |
| TravelMatrix | Optimisation value object/cache | Duration seconds and optional metres for a coordinate snapshot/profile. |
| TripOptimizer | New backend `trip/optimization/` service | Pure orchestration and deterministic heuristics; no HTTP framework code. |
| RoutingProvider | New optimisation capability protocol | Separates `route()` and `matrix()` from map search concerns. |
| LiveTripState | Frontend feature state, persisted visit status | Current browser position stays ephemeral; item status remains durable. |

## Recommended modules

```text
backend/trip/
  optimization/
    models.py          request/response and value models
    routing.py         RoutingProvider protocol, resolver, OSRM Table adapter
    matrix.py          cache and TravelMatrix construction
    optimizer.py       day/trip orchestration and local-improvement heuristic
    constraints.py     budget/lock/status validation (introduced as needed)
    scoring.py         internal score (introduced when meaningful)
  routers/trips.py     authenticated optimisation/apply endpoints

src/src/app/
  types/planner.ts                 API/domain types
  services/trip-optimizer.service.ts
  services/route-manager.service.ts  existing Leaflet renderer, extended not replaced
  components/trip/...                existing trip UI gets entry points first
  components/live-trip/...           introduced only in live-trip phase
```

Do not create a second `Trip`, `Day`, or client-side optimiser. The backend is authoritative for calculation and persistence; the frontend displays a preview and applies the accepted result.

## Data flow

```text
Trip POIs / TripItems + planner settings
  → RoutingProvider.matrix(profile, coordinate snapshot)
  → TravelMatrix cache
  → allocation (whole trip only) + route optimisation
  → constraint validation and optimisation summary
  → preview response
  → explicit apply / transactional persistence of days and item sequence
  → existing API model → RouteManagerService Leaflet layers
```

Live flow is deliberately separate:

```text
browser geolocation + persisted item visit status
  → remaining-item snapshot
  → debounced optimize-remaining request
  → new remaining-order preview/apply; visited items untouched
```

## Existing components to reuse

- `Place` coordinates and category metadata; avoid duplicating POIs into an optimizer table.
- `Trip`, `TripDay`, `TripItem`, existing ownership checks and CRUD routes.
- `BaseMapProvider` selection for interactive route geometry, OpenStreetMap/Photon OSRM route support, and Google Routes support.
- `RouteManagerService` for Leaflet geometry, route badges, route identifiers, and clearing layers.
- `TripMapService`, `tripDayMarker`, Leaflet layers, marker clusters, and GPX rendering.
- `getGeolocationLatLng()` for one-shot location; a watch/debounce layer belongs to the live phase.
- Alembic, SQLModel/Pydantic schema pattern, Angular signals, `ApiService`, and the existing trip component.

## New versus modified boundaries

| Boundary | Change |
| --- | --- |
| Map-provider layer | Keep existing `BaseMapProvider` for search and interactive route rendering. Add a narrowly scoped optimisation routing capability instead of expanding every map provider with speculative matrix methods. |
| OSRM integration | Add an OSRM Table adapter behind `RoutingProvider`; reuse the provider's known OSRM profile mapping, but do not expose OSRM details to optimizer callers. |
| Persistence | Add planner settings and an explicit `TripItem` sequence only in the phases that need them. Never infer sequence from an arrival time. |
| Trip APIs | Add preview/validation endpoints in the trip router, using existing authentication/ownership checks; applying must be transactional. |
| Frontend | Add typed planner API client and UI controls incrementally; extend `RouteManagerService` for stable day styling rather than replace its Leaflet rendering. |

## Routing-provider compatibility

The current selected map provider has route support, but not every provider has a matrix capability: OSM and Photon currently use OSRM route endpoints; Google uses Google Routes. Phase 001 introduces explicit matrix capability detection. The first optimizer matrix implementation is OSRM Table for compatible OSRM profiles (`car`, `foot`, `bike`). Google Routes Matrix and transit are later provider implementations, not hidden fallbacks. A request that lacks a compatible matrix capability must return a typed, actionable error rather than silently optimise using a different provider or straight-line distance.

## Persistence model sequence

1. **Phase 001:** no planner business data; ephemeral bounded route/matrix caching only.
2. **Phase 002:** `TripItem.sequence` (or equivalent) with migration/backfill; route preview/apply needs a stable manual order.
3. **Phase 003:** trip-level planner settings and day assignment persistence required for full-trip application.
4. **Phase 004:** day time settings and POI/item visit duration.
5. **Phase 005:** visit statuses and live-only preferences, preserving already visited items.
6. **Phase 006:** priority, locks, fixed slots, opening-hours data, breaks, and constraint provenance.

## Failure and consistency boundaries

- Validate coordinates and provider capability before calling external routing.
- Treat route/matrix calls as fallible and time-bounded. Return diagnostics without modifying the saved itinerary.
- Cache keys include provider/profile and a stable coordinate snapshot; changed coordinates produce a new key.
- Apply an accepted optimisation in one database transaction. Preview responses are not persisted.
- Keep route geometry cacheable/replaceable; persisted planning correctness must not depend on geometry cache availability.
- Existing route endpoint behavior remains compatible while new endpoints are added.

## Verification architecture

Add backend unit tests for routing adapters, matrix cache, deterministic day optimisation, validation, and apply transaction; API tests for ownership/schema/failure behavior; Angular unit tests for typed client/route rendering state; browser/manual checks for map and reorder UI. See [`TEST_PLAN.md`](TEST_PLAN.md).
