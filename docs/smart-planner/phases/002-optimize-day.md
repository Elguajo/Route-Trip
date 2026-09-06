# Phase 002 — Optimize one day

## Goal

Let a user preview and apply a deterministic improved order for a single existing day, then manually reorder it while retaining real route metrics.

## User value

The user can turn a disordered day of selected places into a practical visit sequence without moving anything to another day.

## Current project state

Phase 001 provides matrix capability. Existing items are ordered by optional `time`; there is no stable sequence, optimizer, or drag-and-drop implementation.

## Scope

- Explicit persistent item sequence with compatible migration/backfill.
- Fixed-start/fixed-end aware day-order input (initially optional endpoint locations only when existing schema supports it; no full trip settings yet).
- Nearest-neighbour plus local improvement using travel matrix; no global solver.
- Before/after travel summary, non-mutating preview, explicit apply endpoint, and atomic persistence.
- Angular typed client, Optimize Day affordance, accessible manual reorder, recalculation/rerender of that day’s route summary.

## Out of scope

Moving items between days, clustering, day budgets, live state, priorities/locks/opening hours, and auto-selected transport policy.

## Architecture impact

`TripOptimizer` receives a day snapshot and returns ordered item IDs, route segments, cost, and diagnostics. `TripItem.sequence` becomes ordering source; `time` remains a schedule value.

## Backend changes

- Add sequence migration/backfill and update relationships/read models safely.
- Implement deterministic optimize-day, preview, and apply transaction with existing trip ownership enforcement.
- Materialize routes from the selected routing capability after order calculation; failure must leave existing persisted sequence unchanged.

## Frontend changes

- Add typed planner service and visible day preview/apply interaction.
- Add keyboard-accessible drag/reorder according to project UI patterns; then request recalculation without overriding user choice.
- Extend `RouteManagerService` only to replace day-specific route layers and use stable day colour assignments.

## Data model changes

`TripItem.sequence` (non-null after migration) is required. Route segments are calculation response/cache values, not required persistent rows in this phase.

## API changes

`POST /api/trips/{tripId}/optimize-day/{dayId}` supports preview. A separate explicit apply contract is required unless the preview endpoint accepts an explicit `apply` false/true form with unambiguous semantics.

## Migration requirements

Alembic migration/backfill for sequence. Existing day order must be preserved deterministically by current time/id ordering.

## Implementation tasks

1. Persist/serialize explicit sequence and migrate existing rows.
2. Implement optimize-day calculation and route/cost summary.
3. Add preview/apply APIs with tests.
4. Build UI, manual ordering, and route replacement.
5. Run unit/API/frontend/build/manual regression checks.

## Tests

- Five-POI fixture: result cost does not exceed starting order unless diagnostics explain a constraint.
- Sequence migration preserves current stable display order.
- Preview does not mutate; apply persists and reloads.
- Manual reorder remains order after recalculation.
- Invalid coordinates/routing error preserves current itinerary.

## Acceptance criteria

- User can optimize at least five valid POIs in one day.
- Result provides real route geometry and total distance/duration.
- Applying changes only the selected day; no day assignment changes.
- Manual reorder recalculates metrics without restoring optimizer order.
- Existing day/item behavior has regression coverage.

## Risks

Mapping segment geometry to result needs stable item IDs; coordinate-less items must be retained and reported, not silently deleted.

## Dependencies

Phase 001; an explicit item sequence migration.

## Completion checklist

- [ ] Migration and sequence behavior verified.
- [ ] Optimizer/API/UI tests pass.
- [ ] Existing routing and trip flows manually checked.
- [ ] State/backlog/roadmap/handoff updated.
