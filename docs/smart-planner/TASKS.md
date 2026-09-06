# Smart Planner task backlog

## Documentation foundation

[x] SP-DOC-01 — Inspect the existing TRIP architecture and record verified baseline facts. (completed 2026-09-06)
[x] SP-DOC-02 — Create progressive Smart Planner documentation and session workflow. (completed 2026-09-06)
[x] SP-DOC-03 — Record initial architecture decisions and phase-specific test strategy. (completed 2026-09-06)

## Phase 001 — Routing foundation

[x] SP-001-01 — Establish focused backend test tooling and fixtures for routing/optimization modules. (completed 2026-09-06; [phase](phases/001-routing-foundation.md))
    [x] Select the smallest repository-compatible runner and document the command.
    [x] Add a fixture/mocking pattern for external routing HTTP calls.
[x] SP-001-02 — Define backend optimization value models and `RoutingProvider` protocol. (completed 2026-09-06; `python3 -m pytest`: 8 passed; [phase](phases/001-routing-foundation.md))
    [x] Model travel matrix, location snapshot, and typed capability/failure result.
    [x] Keep existing `BaseMapProvider` direct-route contract compatible.
[x] SP-001-03 — Implement OSRM Table provider and bounded in-memory matrix cache. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 21 passed; [phase](phases/001-routing-foundation.md))
    [x] Support `car`, `foot`, and `bike` profiles used by compatible OSRM endpoints.
    [x] Key cache by provider/profile/coordinate snapshot and validate response shape.
[x] SP-001-04 — Expose an authenticated, non-mutating matrix/diagnostic API. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 30 passed; [phase](phases/001-routing-foundation.md))
    [x] Reuse the authenticated user's selected map provider; do not accept caller-selected provider input or fall back.
    [x] Return typed matrix capability and external-routing failures without persisting data.
[x] SP-001-05 — Add focused tests and Phase 001 verification record. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 31 passed; [phase](phases/001-routing-foundation.md))
    [x] Verify cache, provider parsing, errors, no fallback, and the public direct-route endpoint regression.
    [x] Update state, roadmap, and next-session handoff after validation.

## Phase 002 — Optimize one day

[x] SP-002-01 — Add explicit `TripItem` sequence with migration/backfill. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 34 passed; [phase](phases/002-optimize-day.md))
    [x] Backfill each existing day deterministically by current `time`/`id` order and require a non-null sequence.
    [x] Serialize the field while preserving the existing time-ordered day display and import compatibility.
[x] SP-002-02 — Implement deterministic day ordering and cost comparison. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 39 passed; [phase](phases/002-optimize-day.md))
    [x] Derive the starting order from persisted `TripItem.sequence` with an `id` tie-breaker.
    [x] Calculate deterministic nearest-neighbour plus local improvement exclusively through `RoutingProvider` matrices.
    [x] Return matrix-backed before/after duration and distance costs without mutating items, days, or sequence.
    [x] Preserve coordinate-less items in the proposed order and diagnostics; retain the starting order without a comparison for unavailable or incomplete matrices.
[x] SP-002-03 — Add preview/apply optimize-day APIs and transactional persistence. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 48 passed; [phase](phases/002-optimize-day.md))
    [x] Add authenticated preview and explicit apply endpoints scoped to one accessible day of one trip.
    [x] Resolve only the authenticated user's selected matrix provider and preserve matrix/coordinate diagnostics.
    [x] Require the previewed starting-order snapshot and commit the selected day's sequence atomically only after a complete calculation.
[x] SP-002-04 — Add typed Angular client, Optimize Day UI, manual reorder, and route-summary rerendering. (completed 2026-09-06; `cd backend && .venv/bin/python -m pytest`: 50 passed; `cd src && npm test -- --watch=false`: 6 passed; `cd src && npm run build` passed; [phase](phases/002-optimize-day.md))
[x] SP-002-05 — Validate regression, optimization behavior, and accessibility. (completed 2026-09-06; `cd backend && .venv/bin/python -m pytest`: 50 passed; `cd src && npm test -- --watch=false`: 7 passed; `cd src && npm run build` passed; `git diff --check` passed; [phase](phases/002-optimize-day.md))

## Phase 003 — Trip optimization

[x] SP-003-01 — Add trip-level planner settings and migration. (completed 2026-09-06; `backend/.venv/bin/python -m pytest`: 54 passed; `cd src && npm test -- --watch=false`: 7 passed; `cd src && npm run build` passed; `git diff --check` passed; [phase](phases/003-trip-optimization.md))
    [x] Persist requested days, optional start/end locations, return-to-start, allowed matrix profiles, and objective through a one-to-one trip settings record.
    [x] Backfill compatible defaults for every existing trip, serialize settings, preserve backup import/export, and test migration/API persistence.
[ ] SP-003-02 — Implement geographic allocation and per-day optimization. ([phase](phases/003-trip-optimization.md))
[ ] SP-003-03 — Add whole-trip preview/apply APIs and planning UI. ([phase](phases/003-trip-optimization.md))
[ ] SP-003-04 — Verify multi-day allocation, endpoints, and persistence. ([phase](phases/003-trip-optimization.md))

## Phase 004 — Time budget

[ ] SP-004-01 — Add duration/default policy and day time settings. ([phase](phases/004-time-budget.md))
[ ] SP-004-02 — Make allocation/order budget-aware and report overflow. ([phase](phases/004-time-budget.md))
[ ] SP-004-03 — Expose/edit time estimates and validate budgets. ([phase](phases/004-time-budget.md))

## Phase 005 — Live trip

[ ] SP-005-01 — Add visit-status persistence and travel-mode state. ([phase](phases/005-live-trip.md))
[ ] SP-005-02 — Implement safe remaining-route optimization. ([phase](phases/005-live-trip.md))
[ ] SP-005-03 — Add geolocation/debounce UX and live-trip regression coverage. ([phase](phases/005-live-trip.md))

## Phase 006 — Advanced constraints

[ ] SP-006-01 — Add priority, locks, opening hours, reservations, and breaks model. ([phase](phases/006-advanced-constraints.md))
[ ] SP-006-02 — Extend constraint validation/scoring and explain conflicts. ([phase](phases/006-advanced-constraints.md))
[ ] SP-006-03 — Evaluate an explicit transit matrix provider; do not silently fall back. ([phase](phases/006-advanced-constraints.md))
