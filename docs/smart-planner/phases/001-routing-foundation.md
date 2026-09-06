# Phase 001 — Routing foundation

## Goal

Add a provider-neutral backend travel-matrix capability backed first by OSRM Table, with cache/error behavior suitable for later deterministic optimization. Existing direct route rendering must remain unchanged.

## User value

This is an enabling phase: later Optimize Day can compare real travel times reliably instead of distance-by-air estimates.

## Current project state

Direct route calls exist at `POST /api/completions/route`; OSM and Photon call OSRM Route, Google calls Google Routes. No Table API, optimizer, cache, or tests exists. See [`CURRENT_STATE.md`](../CURRENT_STATE.md).

## Scope

- Optimization value models: locations, matrix, capability/failure result.
- A `RoutingProvider` protocol separate from existing map search providers.
- OSRM Table adapter for compatible `car`, `foot`, and `bike` OSRM endpoints.
- Bounded, in-memory matrix cache keyed by provider/profile/coordinate snapshot.
- Authenticated non-mutating matrix/diagnostic API, only if its contract is useful to Phase 002.
- Focused backend test tooling and mocked external HTTP tests.

## Out of scope

- Reordering, day/trip optimization, persistence changes, UI plan controls, route summaries, DB cache, Google Matrix, transit, and any fallback to straight-line distance.

## Architecture impact

Add `backend/trip/optimization/` with routing protocol/OSRM adapter/matrix cache. Existing `BaseMapProvider` and `/api/completions/route` must retain their contract. Provider capability is explicit.

## Backend changes

- Add models and resolver for travel-matrix calculations.
- Derive OSRM Table endpoints/profile mapping from verified existing OSM/Photon routing behavior; centralize duplicate endpoint knowledge only if the small change is justified.
- Bound cache size/TTL and ensure response mutation cannot corrupt cached data.
- Map external failure, malformed response, unsupported profile, and unsupported provider to typed API errors.

## Frontend changes

None required. A minimal typed diagnostic client is optional only if a non-mutating endpoint is exposed and has a Phase 002 consumer.

## Data model changes

None. Cache is process-local and reconstructible.

## API changes

Potential authenticated read-only matrix/diagnostic route. Its request must include profile and coordinates; result must include durations in seconds, optional distances in metres, and provider capability metadata. Final path and models are decided from existing router conventions during implementation.

## Migration requirements

None.

## Implementation tasks

1. Add backend test runner/fixtures and record the command.
2. Define models/protocol and capability resolver.
3. Implement OSRM Table request/parsing and cache.
4. Add a narrow API boundary, if required by Phase 002 integration.
5. Test direct-route compatibility, cache behavior, malformed/error responses, and unsupported providers.

## Tests

- Mocked OSRM Table success, no-route cells, malformed schema, HTTP timeout/error.
- Deterministic cache key/hit/miss behavior for coordinate or profile changes.
- Compatible OSM/Photon profiles and explicit unsupported matrix capability.
- Existing direct `/api/completions/route` regression.

## Acceptance criteria

- A caller can request a valid duration matrix in seconds for compatible OSRM profiles without knowing OSRM details.
- Equivalent requests are cached; changed profile/coordinates miss the cache.
- Unsupported provider/profile and routing failures are actionable and non-destructive.
- Existing direct route API behavior remains compatible.
- Focused automated tests pass.

## Risks

- Public OSRM instances may impose request limits; cache and test mocks are required. Production endpoint configuration/rate policy may need a later deployment decision.
- Current Google provider lacks a shared matrix capability; no hidden fallback is allowed.

## Dependencies

Existing provider selection, `httpx`, FastAPI authentication, and backend test tooling selected in SP-001-01.

## Completion checklist

- [x] Models/protocol implemented and tested.
- [x] OSRM Table adapter/cache implemented and tested.
- [x] API boundary tested.
- [x] Existing public `POST /api/completions/route` regression checked.
- [x] `TASKS.md`, `CURRENT_STATE.md`, `ROADMAP.md`, and `NEXT_SESSION.md` updated with observed results.
