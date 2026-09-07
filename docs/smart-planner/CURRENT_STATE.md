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
- `TripPlanningSettings.from_persisted()` copies a saved `TripPlannerSettings` row into an immutable calculation input. `TripAllocator` uses its first allowed profile and selected provider matrix only, deterministically clusters valid-coordinate POIs across the requested prospective days, honours start/end or return-to-start allocation anchors, and uses the saved duration/distance objective for clustering.
- Every input POI appears exactly once in the non-persisted `TripAllocationResult`; coordinate-less POIs are assigned deterministically and explicitly diagnosed. Provider/profile failures, snapshot mismatches, incomplete matrices, and unavailable distance values return a stable balanced allocation with typed diagnostics and no provider or geodesic fallback.
- Each prospective allocation day invokes the existing Phase 002 `TripOptimizer` for its internal order. This calculation layer adds no route, persistence, API, UI, migration, `TripDay`, `TripItem`, sequence, or settings mutation.
- Authenticated `POST /api/trips/{tripId}/optimize` now returns a non-mutating whole-trip proposal from the saved `TripPlannerSettings`, including POI assignment/day/sequence snapshot, opaque stale-preview token, prospective target days, allocator/day diagnostics, and aggregate matrix-backed totals. It plans only POI-backed `TripItem` rows; unrelated itinerary entries stay assigned and sequenced as-is.
- `POST /api/trips/{tripId}/optimize/apply` requires the preview snapshot/token, rechecks current persisted POI assignments and resolved routing coordinates, settings, selected routing provider, and target days, then recalculates on that provider. It rejects stale or incomplete results, creates only non-empty missing planned days, and atomically updates only eligible POI `day_id`/`sequence`. Unrelated non-POI items retain their assignment and sequence; planned POIs receive sequence slots after any such retained items in a target day.
- The Angular `TripPlannerService` exposes typed whole-trip preview/apply calls. The trip UI shows saved planner inputs, a whole-trip preview with totals, allocation/diagnostics, explicit Apply and Cancel, and reloads the trip after apply before rerendering affected day routes.
- Phase 004 now persists a 60-minute category default, an optional existing `Place.duration` override, and an optional `TripItem.duration` override. `resolve_visit_duration()` reports deterministic item → place → category precedence, including the source of the resolved value.
- Every `TripDay` persists a validated local `start_time`/`end_time` window, defaulted for old and new days to `09:00`–`18:00`. `DayTimeBudget` derives its maximum usable minutes without storing a duplicated budget. Existing category, place, item, and day API contracts serialize and persist the additive fields; Angular category/place/item/day editors expose the default/override/window inputs with matching 0–1,440-minute and same-day-window validation.
- Alembic revision `f1c3d9a8e2b4` backfills `Category.default_duration=60`, `TripDay.start_time="09:00"`, and `TripDay.end_time="18:00"`; it adds nullable `TripItem.duration` and leaves the existing optional `Place.duration` untouched. It makes no routing, allocation, order, preview/apply, or provider-selection change.
- Day and whole-trip planner snapshots now carry the resolved visit estimate. `TripOptimizer` combines the selected provider's complete matrix legs (rounded upward to display minutes) with those visits, returning deterministic local arrival/departure estimates, travel/visit/total/overflow minutes, and an explicit `time_budget_overflow` diagnostic. It does not invent travel for coordinate-less or unavailable matrix legs.
- `TripAllocator` uses the persisted target-day windows (and the compatible 09:00–18:00 default for a not-yet-created day) to rebalance a geographic allocation only when moving a POI strictly reduces matrix-backed total overflow. Eligible POIs are always retained; irreducible overflow stays visible in the non-mutating preview and can only be persisted through the existing explicit Apply flow.
- Whole-trip stale-preview tokens now include target-day time budgets as well as resolved duration snapshots, so a changed day window invalidates the preview. Direct day preview/apply returns the same budget-aware schedule, while `POST /api/completions/route` remains unchanged.
- Day and whole-trip preview cards render the returned matrix-backed schedule as an arrival/departure timeline with per-stop travel and visit minutes, aggregate travel/visit/total minutes, and a prominent overflow alert. The UI never treats a preview as an apply action and explicitly states that overflowed eligible POIs remain present.

## Partially implemented

- Existing direct-route UI and `RouteManagerService` profiles expose `car` and `foot`; the planner's typed client additionally offers backend-supported `bike`.
- Day routing draws independent real route legs and metrics on the map, but does not persist them or produce an itinerary summary.
- Existing selected map provider controls direct route calculation. The matrix endpoint is separate and does not change the direct-route contract.

## Not implemented

- Planner-settings editor (whole-trip preview/apply uses the already persisted settings).
- Drag-and-drop (button and keyboard reorder are implemented).
- Planner settings editor, persisted optimisation summaries, or route segments.
- Live travel mode, visit statuses (`planned`/`next`/`visited`/`skipped`), remaining-route optimization, or location watch/debounce.
- Opening-hours, fixed events, locks, priorities, breaks, and transit matrix integration.
- Broad backend automated coverage. Focused planner frontend tests now cover preview, Apply, diagnostics/errors, keyboard manual reorder, and selected-day route rerendering.

## Known issues

- `RouteManagerService.getProfile()` hard-codes a 5 km heuristic (`car` above it, `foot` otherwise); this is not configurable and must not be reused as planner policy.
- Current route requests are made from the browser per leg and can partially render before a later leg fails; they are not an atomic itinerary calculation.
- The existing UI still displays items by time, so later preview/apply and manual-reorder flows must explicitly opt into sequence ordering instead of changing current reads implicitly.

## Tests status

- `cd backend && python -m pytest` could not start on 2026-09-06 because this shell has no `python` executable. An isolated `backend/.venv` was created with Python 3.12.10; `cd backend && .venv/bin/python -m pytest` now passes 74 tests, including Phase 004 migration defaults/downgrade, duration precedence, derived day window, category/place/item/day API duration-window persistence, the 09:00–18:00 travel-plus-visit overflow fixture, budget-aware reallocation, and existing API persistence, alongside whole-trip preview non-mutation, stale protection for POI routing input/planner settings/selected provider, atomic rollback, diagnostics/coordinate-less POI retention, persisted/reloaded allocation and sequence, and the existing Phase 001/002 regressions.
- `cd src && npm run build` passed on 2026-09-06 (with pre-existing bundle-budget/CommonJS warnings). `cd src && npm test -- --watch=false` runs the configured Karma/Jasmine target and passed 11 focused planner specs in Chrome, including explicit whole-trip preview/Apply/Cancel behavior plus non-mutating time-budget preview display and saved day-window reconciliation. An isolated browser fixture further confirmed Preview leaves persisted assignments untouched, Cancel clears the local preview, and explicit Apply reloads the changed trip and renders routes for the affected days without console errors. `git diff --check` passed.
- The focused backend test command is recorded in `TEST_PLAN.md`; the production dependency manifest remains unchanged.

## Database state

- SQLModel models with Alembic revisions; SQLite initialization/migration is performed at startup.
- Alembic revision `c8a5b1d3e7f2` adds `tripitem.sequence` as non-null. It safely adds the column with a database default, backfills each day in current `time`/`id` order, and avoids recreating the `tripitem` table during upgrade.
- Alembic revision `e4b3f14f9a2c` creates the cascade-deleted one-to-one `tripplannersettings` table and inserts a complete default row for every existing trip without altering `trip`, `tripday`, or `tripitem`.
- Alembic revision `f1c3d9a8e2b4` adds compatible duration and day-window fields without changing existing Place duration data or planner persistence semantics.

## Current active phase

Phase 001 — Routing foundation is complete. Phase 002 — Optimize one day is complete: `SP-002-01` persisted and backfilled `TripItem.sequence` without changing the existing time-ordered display, `SP-002-02` added deterministic non-mutating order/cost calculation, `SP-002-03` exposes preview/apply APIs with atomic selected-day persistence, `SP-002-04` added the typed planner client, visible preview/apply flow, accessible manual order, and selected-day route replacement, and `SP-002-05` verified acceptance criteria and regressions. Phase 003 — Trip optimization is complete: `SP-003-01` added compatible trip-level planner settings, `SP-003-02` added isolated deterministic allocation/order calculation, `SP-003-03` exposed whole-trip preview/apply and explicit planning UI with transactional POI persistence, and `SP-003-04` verified API/UI behaviour and closed the POI-coordinate stale-preview gap. Phase 004 — Time budget is complete: `SP-004-01` persisted durable duration/day-window policy, `SP-004-02` made matrix-backed allocation/order budget-aware, and `SP-004-03` added UI editing plus non-mutating timeline/overflow presentation without changing routing/provider/application contracts.
