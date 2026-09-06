# Next Session

## Current phase

Phase 002 — Optimize one day

## Goal

Add explicit `TripItem` sequence with a compatible migration/backfill before implementing day-order optimization.

## Already completed

- Repository architecture was inspected.
- Smart Planner progressive documentation, roadmap, phase specifications, test plan, and ADR log were created.
- `SP-001-01` added a focused backend `pytest` setup in `backend/requirements-test.txt` and `backend/pytest.ini`.
- `backend/tests/conftest.py` provides an in-process `httpx.MockTransport` fixture for routing adapters; the OSM direct-route regression test passes without external HTTP.
- `SP-001-02` added provider-neutral matrix contracts under `backend/trip/optimization/`: immutable coordinate snapshots, duration/distance matrices, capability/failure values, and `RoutingProvider`.
- OSM and Photon explicitly advertise matrix support for `car`, `foot`, and `bike`; Google has no matrix capability. The resolver does not substitute providers or calculate straight-line values.
- `SP-001-03` added `OSRMTableRoutingProvider` for OSM/Photon and `TravelMatrixCache`, a bounded TTL/LRU process-local cache. The adapter requests only OSRM Table, preserves null no-route cells, defensively copies cached results, and returns typed routing failures for timeout, HTTP, and malformed responses.
- `SP-001-04` added authenticated `POST /api/completions/matrix`. The endpoint derives the routing provider solely from the authenticated user's selected map provider, shares the process-local matrix cache, writes no data, and returns either a complete matrix or a typed failure. OSM/Photon support `car`/`foot`/`bike`; Google and `transit` return explicit unsupported failures.
- `BaseMapProvider` and `POST /api/completions/route` are unchanged. No database schema has changed.
- `SP-001-05` verified all Phase 001 acceptance criteria. `cd backend && .venv/bin/python -m pip install -r requirements-test.txt && .venv/bin/python -m pytest` completed with 31 passed tests; the suite includes an authenticated public direct-route regression and verifies unsupported matrix selections perform no HTTP fallback.

## Next task

Start `SP-002-01`: inspect `phases/002-optimize-day.md` and the current `TripItem` persistence/read paths, then add an explicit sequence with migration/backfill. Preserve existing time ordering and do not begin optimizer, preview/apply API, or frontend work in this task.

## Files to read

- `AGENTS.md`
- `docs/smart-planner/CURRENT_STATE.md`
- `docs/smart-planner/phases/002-optimize-day.md`
- `docs/smart-planner/TASKS.md`
- `backend/trip/models/models.py`
- `backend/trip/routers/trips.py`
- `backend/alembic/versions/`

## Files likely to modify

- `backend/trip/models/models.py`
- `backend/trip/routers/trips.py`
- `backend/alembic/versions/`
- `backend/tests/`
- `docs/smart-planner/{TASKS,CURRENT_STATE,NEXT_SESSION}.md`

## Important decisions

- Optimisation runs in the backend.
- Matrix capability is a distinct protocol; no direct OSRM dependency in optimizer code.
- Do not silently fall back across selected routing providers.
- `TravelMatrix` preserves the provider/profile and exact immutable coordinate snapshot; `None` cells mean no route, never an air-distance estimate.
- Add persistent sequence only in Phase 002; Phase 001 has no business-data migration.
- The matrix API is a diagnostic/preflight boundary rather than an optimizer API: it uses a caller's authenticated, user-owned provider setting and returns the existing typed matrix result union. It never accepts a provider override.

## Blockers

None.

## Validation commands

- `cd backend && .venv/bin/python -m pip install -r requirements-test.txt && .venv/bin/python -m pytest` (create the ignored local environment once with `python3 -m venv .venv`; 31 passed on 2026-09-06)
- `cd src && npm run build`
- `git diff --check`
