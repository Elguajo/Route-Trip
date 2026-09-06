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
- The Angular `DayPlannerService` provides typed preview/apply clients and a scoped manual-reorder client. The selected-day panel shows preview costs, diagnostics, explicit Apply only when a complete matrix is available, actionable errors, and a manual sequence list with buttons plus `Alt` + arrow-key movement. Each day panel has a unique labelled heading for assistive technology.
- `POST /api/trips/{tripId}/days/{dayId}/reorder` atomically accepts the complete unique item-ID set for that accessible non-archived day and updates only its `sequence`. It rejects missing or cross-day IDs and never changes `time` or an item's day.
- Planner Apply and manual reorder reconcile only the selected local day. `RouteManagerService` replaces only that day's tagged layers, retains all other route layers, uses a stable day colour, and suppresses stale route responses; its selected-day summary is recalculated in persisted sequence order.
- `TripPlannerSettings` is a one-to-one, cascade-deleted trip record for requested days, optional start/end coordinates, return-to-start, allowed routing profiles, and duration/distance objective. Existing and new trips have compatible defaults: one requested day, no endpoints, no return, `car`, and duration objective.
- Authenticated trip readers receive `planner_settings` in full and shared trip serialization. `GET`/`PUT /api/trips/{tripId}/planner-settings` reads or replaces a complete validated settings payload; it does not allocate POIs, calculate routes, or alter days/items.
- The user backup export/import paths preserve planner settings and assign defaults for older backups that lack them.

## Partially implemented

- Existing direct-route UI and `RouteManagerService` profiles expose `car` and `foot`; the planner's typed client additionally offers backend-supported `bike`.
- Day routing draws independent real route legs and metrics on the map, but does not persist them or produce an itinerary summary.
- Existing selected map provider controls direct route calculation. The matrix endpoint is separate and does not change the direct-route contract.

## Not implemented

- Whole-trip allocation/clustering or optimisation score.
- Drag-and-drop (button and keyboard reorder are implemented).
- Day planner settings, time budgets, route segments, or persisted optimisation summaries.
- Live travel mode, visit statuses (`planned`/`next`/`visited`/`skipped`), remaining-route optimization, or location watch/debounce.
- Opening-hours, fixed events, locks, priorities, breaks, and transit matrix integration.
- Broad backend automated coverage. Focused planner frontend tests now cover preview, Apply, diagnostics/errors, keyboard manual reorder, and selected-day route rerendering.

## Known issues

- `RouteManagerService.getProfile()` hard-codes a 5 km heuristic (`car` above it, `foot` otherwise); this is not configurable and must not be reused as planner policy.
- Current route requests are made from the browser per leg and can partially render before a later leg fails; they are not an atomic itinerary calculation.
- The existing UI still displays items by time, so later preview/apply and manual-reorder flows must explicitly opt into sequence ordering instead of changing current reads implicitly.

## Tests status

- `cd backend && python -m pytest` could not start on 2026-09-06 because this shell has no `python` executable. An isolated `backend/.venv` was created with Python 3.12.10; `cd backend && .venv/bin/python -m pytest` now passes 54 tests, including planner-settings migration backfill/rollback, API serialization/persistence, preview/apply isolation, scoped manual-reorder success, invalid-snapshot preservation, provider diagnostics, and the existing OSM direct-route regression.
- `cd src && npm run build` passed on 2026-09-06 (with pre-existing bundle-budget/CommonJS warnings). `cd src && npm test -- --watch=false` runs the configured Karma/Jasmine target and passed 7 focused planner specs in Chrome, including keyboard reorder, selected-day rerendering, and blocking Apply when diagnostics report no complete matrix. `git diff --check` passed.
- The focused backend test command is recorded in `TEST_PLAN.md`; the production dependency manifest remains unchanged.

## Database state

- SQLModel models with Alembic revisions; SQLite initialization/migration is performed at startup.
- Alembic revision `c8a5b1d3e7f2` adds `tripitem.sequence` as non-null. It safely adds the column with a database default, backfills each day in current `time`/`id` order, and avoids recreating the `tripitem` table during upgrade.
- Alembic revision `e4b3f14f9a2c` creates the cascade-deleted one-to-one `tripplannersettings` table and inserts a complete default row for every existing trip without altering `trip`, `tripday`, or `tripitem`.

## Current active phase

Phase 001 — Routing foundation is complete. Phase 002 — Optimize one day is complete: `SP-002-01` persisted and backfilled `TripItem.sequence` without changing the existing time-ordered display, `SP-002-02` added deterministic non-mutating order/cost calculation, `SP-002-03` exposes preview/apply APIs with atomic selected-day persistence, `SP-002-04` added the typed planner client, visible preview/apply flow, accessible manual order, and selected-day route replacement, and `SP-002-05` verified acceptance criteria and regressions. Phase 003 is in progress: `SP-003-01` added compatible trip-level planner settings and its migration/API tests; allocation and whole-trip optimisation have not begun.
