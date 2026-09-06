# Phase 003 — Trip optimization

## Goal

Preview and apply a sensible multi-day allocation and per-day order for selected trip POIs.

## User value

After choosing how many days they have and where each day starts/ends, a user receives a usable multi-day trip rather than manually distributing locations.

## Current project state

Phase 002 supplies ordered days. Existing `Trip` has no planner settings and days only have label/date/notes.

## Scope

- Persist trip-wide planning settings: requested days, start/end locations, return-to-start, allowed profiles, and optimization objective.
- Allocate geographic groups across days using matrix-informed deterministic heuristics, then run the Phase 002 day optimiser.
- Whole-trip before/after preview and explicit transactional apply.
- Planner form and trip summary.

## Out of scope

Visit duration/budget enforcement, live travel, opening hours, priority/locks, transit, and mathematical VRP optimality.

## Architecture impact

Add a planner settings model associated with `Trip`; keep `TripDay` and `TripItem` as persisted itinerary ownership. Allocation strategy is isolated from route-order strategy.

## Backend changes

- Add schema/migration for planner settings and endpoints under the existing trip router.
- Build coordinate snapshot, cluster/allocation heuristic, per-day order, summary, diagnostics, and transactional apply.

## Frontend changes

- Plan Trip form for days/start/end/return/profile choices.
- Preview comparison and explicit Apply/Cancel; day routes have stable distinct colours.

## Data model changes

Trip planner settings; no duplicate POI table. Day creation/assignment must preserve unrelated non-POI items according to an explicit diagnostic policy.

## API changes

`POST /api/trips/{tripId}/optimize` with preview and explicit apply contract. Response includes optimized days and total travel distance/duration.

## Migration requirements

Alembic migration for planner settings; backward-compatible defaults for existing trips.

## Implementation tasks

1. Add/migrate settings and API schemas.
2. Implement allocation and per-day orchestration.
3. Implement preview/apply transaction.
4. Add planner form/summary/route rendering.
5. Validate multi-day fixtures and existing trip flows.

## Tests

- Four-day fixture groups nearby POIs and preserves all eligible POIs.
- Start/end and return-to-start alter travel cost/order as expected.
- Preview/cancel are non-mutating; apply persists/reloads atomically.
- Unsupported/coordinate-less items produce diagnostics.

## Acceptance criteria

- A user can allocate selected POIs across multiple days and apply the result.
- Each planned day has real routes and aggregate metrics.
- No route is silently optimized by a different provider or straight-line metric.
- Existing trips without settings retain existing behavior.

## Risks

Pure geographic clustering can conflict with road topology; matrix-based local refinement is mandatory.

## Dependencies

Phases 001 and 002.

## Completion checklist

- [x] Settings migration/API verified.
- [x] Allocation and apply tests pass.
- [x] UI/build/manual regression complete.
- [x] State/backlog/roadmap/handoff updated.
