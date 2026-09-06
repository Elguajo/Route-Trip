# Current State

_Observed on 2026-09-06; this file reports implementation, not the roadmap._

## Implemented

- TRIP stores user-owned `Place` POIs with coordinates, category, optional `duration`, and visited flag.
- TRIP stores `Trip`, `TripDay`, and `TripItem`. `TripItem.sequence` is a non-null, persisted per-day ordering value; day responses still preserve the existing optional `TripItem.time` order (with `id` as a deterministic tie-breaker).
- New items receive the next sequence in their day, moving an item to another day assigns it the next sequence there, and backup imports preserve an exported sequence or assign one from the imported item order.
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
- `TripOptimizer` performs non-mutating deterministic one-day ordering over an explicit day-item snapshot. It orders the persisted input by `TripItem.sequence` with `id` tie-breaking, then uses the existing selected `RoutingProvider` matrix only for nearest-neighbour plus local improvement.
- Optimizer results return every item ID, a matrix-backed before/after travel-cost comparison, and diagnostics. Items without valid coordinates remain at their original sequence positions and are explicitly reported; they are excluded from the matrix cost. A provider failure, matrix-snapshot mismatch, or any unreachable matrix pair preserves the starting order and returns no comparison.
- `POST /api/trips/{tripId}/optimize-day/{dayId}` previews the authenticated user's accessible existing day without mutating it. It resolves the matrix adapter only from that user's selected map provider and returns the existing deterministic order, cost comparison, and diagnostics.
- `POST /api/trips/{tripId}/optimize-day/{dayId}/apply` requires the preview's `starting_item_ids` snapshot, recalculates on the backend, and commits a new contiguous sequence only for the selected day. It rejects stale snapshots and any unavailable, incomplete, or mismatched matrix before writing; a persistence failure rolls back the whole day update.

## Partially implemented

- Routing profiles in frontend type definitions and `RouteManagerService` expose only `car` and `foot`; backend OSM/Photon already accept `bike`.
- Day routing draws independent real route legs and metrics on the map, but does not persist them or produce an itinerary summary.
- Existing selected map provider controls direct route calculation. The matrix endpoint is separate and does not change the direct-route contract.

## Not implemented

- Automatic day order UI, whole-trip allocation/clustering, manual reorder, or optimisation score.
- Drag-and-drop/manual reorder.
- Trip/day planner settings, start/end locations, time budgets, route segments, or persisted optimisation summaries.
- Live travel mode, visit statuses (`planned`/`next`/`visited`/`skipped`), remaining-route optimization, or location watch/debounce.
- Opening-hours, fixed events, locks, priorities, breaks, and transit matrix integration.
- Broad backend automated coverage and frontend component tests (only Angular test configuration is present). A focused backend routing test baseline now exists.

## Known issues

- `RouteManagerService.getProfile()` hard-codes a 5 km heuristic (`car` above it, `foot` otherwise); this is not configurable and must not be reused as planner policy.
- Current route requests are made from the browser per leg and can partially render before a later leg fails; they are not an atomic itinerary calculation.
- The existing UI still displays items by time, so later preview/apply and manual-reorder flows must explicitly opt into sequence ordering instead of changing current reads implicitly.

## Tests status

- `cd backend && python -m pytest` could not start on 2026-09-06 because this shell has no `python` executable. An isolated `backend/.venv` was created with Python 3.12.10; after `cd backend && .venv/bin/python -m pip install -r requirements-test.txt`, `cd backend && .venv/bin/python -m pytest` passed: 48 tests. Coverage includes direct `POST /api/completions/route` compatibility; OSRM Table success/no-route/malformed/HTTP/timeout; cache and OSM/Photon capability tests; matrix API authentication, selected-provider isolation, supported/unsupported profiles with no HTTP fallback, typed routing failure, and no-mutation coverage; `TripItem.sequence` migration/backfill, API serialization, time-order compatibility, and cross-day move behavior; deterministic one-day ordering, cost comparison, unavailable/incomplete matrices, optional distances, and coordinate-less POIs; and optimize-day API authorization, trip/day ownership, preview non-mutation, selected-provider isolation, explicit snapshot-checked apply, incomplete/unavailable matrix preservation, and transactional rollback.
- Frontend supports `npm run build` and `npm test` through Angular CLI; neither was run in this backend-test setup session.
- The focused backend test command is recorded in `TEST_PLAN.md`; the production dependency manifest remains unchanged.

## Database state

- SQLModel models with Alembic revisions; SQLite initialization/migration is performed at startup.
- Alembic revision `c8a5b1d3e7f2` adds `tripitem.sequence` as non-null. It safely adds the column with a database default, backfills each day in current `time`/`id` order, and avoids recreating the `tripitem` table during upgrade.

## Current active phase

Phase 001 — Routing foundation is complete. Phase 002 — Optimize one day is active: `SP-002-01` persisted and backfilled `TripItem.sequence` without changing the existing time-ordered display, `SP-002-02` added deterministic non-mutating order/cost calculation, and `SP-002-03` exposes preview/apply APIs with atomic selected-day persistence. Frontend and manual reorder work do not exist yet.
