# Next Session

## Current phase

Phase 003 — Trip optimization (SP-003-01 complete)

## Goal

SP-003-01 is verified. Do not begin SP-003-02 unless explicitly requested.

## Already completed

- Repository architecture was inspected.
- Smart Planner progressive documentation, roadmap, phase specifications, test plan, and ADR log were created.
- `SP-001-01` added a focused backend `pytest` setup in `backend/requirements-test.txt` and `backend/pytest.ini`.
- `backend/tests/conftest.py` provides an in-process `httpx.MockTransport` fixture for routing adapters; the OSM direct-route regression test passes without external HTTP.
- `SP-001-02` added provider-neutral matrix contracts under `backend/trip/optimization/`: immutable coordinate snapshots, duration/distance matrices, capability/failure values, and `RoutingProvider`.
- OSM and Photon explicitly advertise matrix support for `car`, `foot`, and `bike`; Google has no matrix capability. The resolver does not substitute providers or calculate straight-line values.
- `SP-001-03` added `OSRMTableRoutingProvider` for OSM/Photon and `TravelMatrixCache`, a bounded TTL/LRU process-local cache. The adapter requests only OSRM Table, preserves null no-route cells, defensively copies cached results, and returns typed routing failures for timeout, HTTP, and malformed responses.
- `SP-001-04` added authenticated `POST /api/completions/matrix`. The endpoint derives the routing provider solely from the authenticated user's selected map provider, shares the process-local matrix cache, writes no data, and returns either a complete matrix or a typed failure. OSM/Photon support `car`/`foot`/`bike`; Google and `transit` return explicit unsupported failures.
- `BaseMapProvider` and `POST /api/completions/route` are unchanged; Phase 001 introduced no database schema changes.
- `SP-001-05` verified all Phase 001 acceptance criteria. `cd backend && .venv/bin/python -m pip install -r requirements-test.txt && .venv/bin/python -m pytest` completed with 31 passed tests; the suite includes an authenticated public direct-route regression and verifies unsupported matrix selections perform no HTTP fallback.
- `SP-002-01` added non-null `TripItem.sequence`, serialized it in full and shared trip reads, and assigned it for normal creation and cross-day moves. Both backup import paths preserve a supplied sequence and deterministically assign one to older imports that lack it.
- Alembic revision `c8a5b1d3e7f2` adds the field without rebuilding `tripitem`, then backfills each day in the existing database's `time`/`id` order. Existing responses still display items by `time` with an `id` tie-breaker.
- `SP-002-02` added `TripOptimizer`, a backend-only, non-mutating single-day calculator. It derives baseline order from `TripItem.sequence`/`id`, calls only the supplied `RoutingProvider` matrix capability, and applies deterministic nearest-neighbour with local improvement.
- `SP-002-03` added authenticated `POST /api/trips/{tripId}/optimize-day/{dayId}` preview and `POST /api/trips/{tripId}/optimize-day/{dayId}/apply` endpoints. Preview writes nothing; apply requires the preview starting-order snapshot, reruns the selected user's matrix-backed calculation, and atomically updates only that day's sequence after a complete result.
- `SP-002-04` added `DayPlannerService` typed Angular preview/apply clients, selected-day preview costs/diagnostics/errors and explicit Apply, plus accessible manual reorder through buttons and `Alt` + arrow keys.
- `SP-002-04` added the minimal authenticated `POST /api/trips/{tripId}/days/{dayId}/reorder` contract. It verifies the complete selected-day item set and atomically writes only that day's sequence; it never changes item time or day assignment.
- Planner reconciliation replaces only the selected local day. `RouteManagerService` now tags/clears only day-specific route layers, assigns stable day colours, and ignores stale responses so an older route render cannot overwrite a later manual/apply rerender.
- `SP-002-05` verified the Phase 002 acceptance criteria and existing direct-route regression. `cd backend && .venv/bin/python -m pytest` passed 50 tests; `cd src && npm test -- --watch=false` passed 7 planner specs; `cd src && npm run build` passed; and `git diff --check` passed. Existing build bundle-budget/CommonJS warnings remain.
- Validation fixed two confirmed UI defects: each repeated day panel now has a unique labelled heading, and an incomplete-matrix preview cannot invoke Apply.
- Optimizer results retain every item. Coordinate-less or invalid-coordinate items stay in their original sequence positions and are returned in diagnostics; unavailable, mismatched, or incomplete matrices preserve the baseline order with no cost comparison.
- Focused migration/API/optimizer tests and the full backend suite passed; `cd backend && .venv/bin/python -m alembic heads` reports `c8a5b1d3e7f2`.
- `SP-003-01` added `TripPlannerSettings`: a cascade-deleted one-to-one setting record with requested days, optional start/end locations, return-to-start, a non-empty unique `car`/`foot`/`bike` allowed-profile list, and duration/distance objective. It does not allocate POIs, compute costs, modify days/items, or add a frontend form.
- Alembic revision `e4b3f14f9a2c` creates `tripplannersettings` and backfills one default row per existing trip (`requested_days=1`, no locations, `return_to_start=false`, `allowed_profiles=["car"]`, `objective="duration"`). It leaves `trip`, `tripday`, and `tripitem` unchanged; downgrade only removes the new table.
- Full and shared trip serialization now include `planner_settings`. Authenticated `GET`/`PUT /api/trips/{tripId}/planner-settings` reads/replaces the complete validated payload; normal new-trip creation and both backup import paths create/preserve the defaults.
- `backend/tests/test_trip_planner_settings.py` covers migration backfill/downgrade, default serialization, API write/reload persistence, new-trip defaults, and profile-list validation. `cd backend && .venv/bin/python -m pytest` passed 54 tests; `cd src && npm test -- --watch=false` passed 7 tests; `cd src && npm run build` passed with existing bundle-budget/CommonJS warnings; `git diff --check` passed; `cd backend && .venv/bin/python -m alembic heads` reports `e4b3f14f9a2c`.

## Next task

`SP-003-01` is complete. Start `SP-003-02` only with explicit direction; keep allocation and per-day orchestration isolated from `TripPlannerSettings` and preserve all Phase 001/002 contracts.

## Files to read

- `AGENTS.md`
- `docs/smart-planner/NEXT_SESSION.md`
- `docs/smart-planner/CURRENT_STATE.md`
- `docs/smart-planner/TASKS.md`
- `docs/smart-planner/phases/003-trip-optimization.md` (only after Phase 003 is authorized)
- `backend/trip/models/models.py`
- `backend/trip/routers/trips.py`
- `backend/trip/alembic/versions/e4b3f14f9a2c_trip_planner_settings.py`

## Files likely to modify

- Phase 003 allocation/orchestration files are not yet determined; preserve all completed Phase 001/002 contracts and the settings API/persistence boundary.

## Important decisions

- Optimisation runs in the backend.
- Matrix capability is a distinct protocol; no direct OSRM dependency in optimizer code.
- Do not silently fall back across selected routing providers.
- `TravelMatrix` preserves the provider/profile and exact immutable coordinate snapshot; `None` cells mean no route, never an air-distance estimate.
- `TripItem.sequence` is persisted and serialized, but it is not client-writable in SP-002-01. Existing display remains ordered by `time`/`id`; later planner APIs may explicitly apply sequence ordering without reusing time as route order.
- The matrix API is a diagnostic/preflight boundary rather than an optimizer API: it uses a caller's authenticated, user-owned provider setting and returns the existing typed matrix result union. It never accepts a provider override.
- The day calculator does not use partial matrix data: an unreachable pair, snapshot mismatch, or typed matrix failure returns the persisted baseline order and diagnostics without a cost comparison. Coordinate-less items remain at their persisted positions and are not silently included in route cost.
- Apply is an explicit compare-and-apply contract: the client returns preview `starting_item_ids`; the server checks that exact sequence order, recalculates rather than trusting a client-supplied optimized order, and commits only a full calculation for the selected day. Stale, malformed, unavailable, incomplete, or mismatched calculations leave all sequences untouched.
- Manual reorder is a separate complete-list transaction scoped to one authorized trip/day. This prevents ordinary item updates from changing planner sequence, validates that no item is added/removed/cross-day, and lets the UI replace only that day from the backend response.
- Planner inputs live in a defaulted one-to-one `TripPlannerSettings` record rather than `Trip` columns. The setting API replaces a complete validated snapshot; its defaults let existing trips and old backup imports keep their prior behavior until whole-trip planning is explicitly invoked.

## Blockers

None.

## Validation commands

- `cd backend && .venv/bin/python -m pytest`
- `cd src && npm run build`
- `cd src && npm test -- --watch=false`
- `git diff --check`
