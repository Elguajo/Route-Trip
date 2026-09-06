# Current State

_Observed on 2026-09-06; this file reports implementation, not the roadmap._

## Implemented

- TRIP stores user-owned `Place` POIs with coordinates, category, optional `duration`, and visited flag.
- TRIP stores `Trip`, `TripDay`, and `TripItem`; days currently return items ordered by optional `TripItem.time`.
- The authenticated backend exposes `POST /api/completions/route` for two or more coordinates.
- OpenStreetMap and Photon map providers call OSRM Route for `car`, `foot`, and `bike`; Google maps uses Google Routes and also supports transit for direct routes.
- The Angular `RouteManagerService` renders GeoJSON route geometry and distance/duration Leaflet badges. `TripComponent` can calculate a route for current adjacent day items through one request per segment.
- Browser geolocation is wrapped by `getGeolocationLatLng()`.
- Database schema is managed through Alembic; application startup runs migrations.
- Backend focused tests use `pytest` from `backend/requirements-test.txt`; `mock_routing_http` replaces routing HTTP with `httpx.MockTransport`. The first regression test captures the existing OSM direct Route request contract without network access.
- `backend/trip/optimization/` defines immutable coordinate snapshots and travel matrices (durations in seconds, optional distances in metres), a separate `RoutingProvider` protocol, explicit matrix capability, and typed matrix failures. OSM and Photon advertise `car`/`foot`/`bike`; Google and unknown selections report no matrix capability without fallback.
- `OSRMTableRoutingProvider` implements that protocol for the selected OSM or Photon provider only. It calls OSRM Table for the compatible profile, preserves null unreachable cells, and maps HTTP errors, timeouts, and malformed responses to `RoutingFailure`; it neither selects another provider nor estimates travel by straight line.
- `TravelMatrixCache` is process-local, bounded LRU storage with TTL. Its exact key is provider/profile/ordered coordinates, it caches only successful matrices, and callers receive a defensive result copy.
- `POST /api/completions/matrix` is an authenticated, non-mutating diagnostic boundary for the caller's selected map provider. It accepts a profile and two or more coordinates, uses the shared process-local `TravelMatrixCache`, and returns either a full matrix (provider, profile, immutable coordinate snapshot, durations in seconds, optional distances in metres, and `null` no-route cells) or an actionable typed `RoutingFailure`; unsupported selection/profile performs no HTTP fallback.

## Partially implemented

- Routing profiles in frontend type definitions and `RouteManagerService` expose only `car` and `foot`; backend OSM/Photon already accept `bike`.
- Day routing draws independent real route legs and metrics on the map, but does not persist them or produce an itinerary summary.
- Existing selected map provider controls direct route calculation. The matrix endpoint is separate and does not change the direct-route contract.

## Not implemented

- Optimizer service or optimizer API.
- Automatic day order, whole-trip allocation/clustering, preview/apply workflow, or optimisation score.
- Stable item sequence and drag-and-drop reorder.
- Trip/day planner settings, start/end locations, time budgets, route segments, or persisted optimisation summaries.
- Live travel mode, visit statuses (`planned`/`next`/`visited`/`skipped`), remaining-route optimization, or location watch/debounce.
- Opening-hours, fixed events, locks, priorities, breaks, and transit matrix integration.
- Broad backend automated coverage and frontend component tests (only Angular test configuration is present). A focused backend routing test baseline now exists.

## Known issues

- `RouteManagerService.getProfile()` hard-codes a 5 km heuristic (`car` above it, `foot` otherwise); this is not configurable and must not be reused as planner policy.
- Current route requests are made from the browser per leg and can partially render before a later leg fails; they are not an atomic itinerary calculation.
- Item display and persistence depend on time ordering, so manual spatial ordering cannot safely be represented yet.

## Tests status

- `cd backend && python -m pytest` could not start on 2026-09-06 because this shell has no `python` executable. An isolated `backend/.venv` was created with Python 3.12.10; after `cd backend && .venv/bin/python -m pip install -r requirements-test.txt`, `cd backend && .venv/bin/python -m pytest` passed: 31 tests. Coverage includes direct `POST /api/completions/route` compatibility; OSRM Table success/no-route/malformed/HTTP/timeout; cache and OSM/Photon capability tests; and matrix API authentication, selected-provider isolation, supported/unsupported profiles with no HTTP fallback, typed routing failure, and no-mutation coverage.
- Frontend supports `npm run build` and `npm test` through Angular CLI; neither was run in this backend-test setup session.
- The focused backend test command is recorded in `TEST_PLAN.md`; the production dependency manifest remains unchanged.

## Database state

- SQLModel models with Alembic revisions; SQLite initialization/migration is performed at startup.
- No Smart Planner tables, columns, or migrations exist.

## Current active phase

Phase 001 — Routing foundation is complete. Its focused backend test infrastructure, provider-neutral routing-matrix contracts, OSRM Table adapter, bounded in-memory cache, authenticated non-mutating matrix diagnostic API, and direct-route regression are verified. Phase 002 — Optimize one day is next.
