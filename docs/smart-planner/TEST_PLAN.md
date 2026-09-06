# Smart Planner test plan

## Principles

Validation is incremental and must prove that a planner result is deterministic, non-destructive until applied, and compatible with existing TRIP behavior. Network calls must be mocked in automated tests; OSRM/Google availability is not a test oracle.

## Focused backend command

```bash
cd backend
python3 -m venv .venv # once
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

`backend/tests/conftest.py` provides `mock_routing_http`, which replaces provider `httpx.AsyncClient` instances with `httpx.MockTransport`. Adapter tests must use it rather than call routing providers over the network.

## Unit tests

- Routing adapter: profile/coordinate validation, OSRM Table parsing, timeout/error mapping, and capability reporting.
- Matrix cache: equivalent request hit, profile/coordinate snapshot miss, and stale/error behavior.
- Optimizer: deterministic ordering, never accepting a worse route without a documented constraint reason, 2-opt/local-improvement behavior, and empty/invalid coordinates.
- Constraints: time budget, visit duration defaults, start/end, locks, fixed events, priorities, and skipped/visited handling as introduced.
- Scoring: component penalties and stable score for a fixed input once Phase 006 introduces it.

## Backend API tests

- Authentication/ownership of every optimize/apply endpoint.
- Schema validation, unsupported matrix capability, routing failure, and no mutation on preview/failure.
- Transactional apply: day assignment/order/settings are persisted together or not at all.
- Existing trip, item, and direct `/api/completions/route` endpoints remain compatible.

## Frontend tests

- Typed API service serializes planner requests and exposes errors.
- Preview is not applied prematurely; accepted response updates client state only after server apply.
- Drag-and-drop sends/preserves explicit order and recalculates route summary.
- Route layer state clears/replaces only the intended day’s routes.
- Live status controls exclude skipped/visited items from remaining optimization.

## Integration tests

- Fixed fixture of at least five POIs: optimize a day, compare before/after cost, render all route legs.
- Full trip: allocate across days, include start/end, respect budget or return overflow diagnostics, then apply and reload.
- Routing service failure for one leg/matrix does not delete or partially persist an itinerary.
- Provider capability test confirms unsupported matrix provider is explicit rather than silently using another provider.

## Regression and manual UX tests

- POI CRUD, categories, existing trip/day/item CRUD, map markers/clusters, GPX, imports, sharing, and direct routing.
- Desktop and mobile layout for plan controls and day route legs; keyboard-accessible reorder/control behavior.
- Verify day colour/geometry/metrics, manual reorder, route removal, and browser geolocation denied/unavailable states.
- Live mode: confirm before marking visited, skip a POI, then ensure completed items do not move after re-optimization.

## Phase exit baseline

For each phase, run its focused backend and frontend tests, the frontend build, `git diff --check`, and relevant manual regression. Record commands actually run and outcomes in `CURRENT_STATE.md`; do not claim a suite passed if it was not run.
