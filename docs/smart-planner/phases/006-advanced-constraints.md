# Phase 006 — Advanced constraints

## Goal

Add meaningful planner constraints—priority, locks, opening hours, reservations, and breaks—without weakening deterministic behavior or hiding infeasible plans.

## User value

The planner can preserve a must-see museum, work around a dinner reservation, and explain why a desired itinerary cannot fit.

## Current project state

Previous phases provide explicit sequence, multi-day allocation, time budget, and live state. No advanced constraint data is currently stored.

## Scope

- Priority (`must`, `high`, `normal`, `optional`) and day/position locks.
- Opening-hours representation/parser policy, fixed reservation start/end slots, and optional lunch/break windows.
- Constraint validation, conflict/overflow explanations, and an internal optimization score.
- Explicit feasibility/capability evaluation for future transit matrix provider; implement only if a concrete provider/configuration is approved.

## Out of scope

Perfect global VRP solution, implicit booking creation, weather/AI suggestions, and silent public-transport fallback.

## Architecture impact

Extend `constraints.py` with composable, testable validators and structured violations. Optimizer ordering/allocation receives immutable constraint snapshots. Keep scoring internal until it is accurate enough for UI.

## Backend changes

- Add/migrate planner fields and reservation integration only where existing booking model can be safely referenced.
- Validate time windows and locks before/after heuristic steps; return structured infeasibility explanations.

## Frontend changes

- Constraint editors and clear conflict explanations; lock/priority affordances must remain accessible.
- Preview shows why items move, remain, or cannot fit.

## Data model changes

Planner priority/lock fields, time-window/fixed-event model, optional break preferences, and constraint provenance. Exact representation needs a focused architecture review before migration.

## API changes

Optimization requests/responses gain constraints and violation details; no breaking change to existing trip CRUD.

## Migration requirements

Explicit Alembic migrations with null/default compatibility and a data mapping plan if existing bookings are used as fixed events.

## Implementation tasks

1. Decide constraint model and persistence boundaries.
2. Implement priority/locks and deterministic validation.
3. Add opening hours/fixed events/breaks incrementally with tests.
4. Add structured violations and score.
5. Evaluate transit provider separately; require explicit integration decision.

## Tests

- Must item not silently excluded; optional item may be reported as overflow.
- Locked day/position and reservation time remain unchanged.
- Closed-window placement creates an explainable conflict.
- Infeasible plans preserve current persisted itinerary.

## Acceptance criteria

- Planner respects locks/priorities/time windows or reports why it cannot.
- Conflicts are deterministic and visible to the user.
- Existing MVP planning/live behavior remains compatible.

## Risks

Opening-hours parsing and transit data quality are complex external-domain problems; start with a constrained documented format and capability boundaries.

## Dependencies

Phases 003–005.

## Completion checklist

- [ ] Constraint model/migrations reviewed and verified.
- [ ] Deterministic conflict tests pass.
- [ ] UI explanations/manual regression checked.
- [ ] State/backlog/roadmap/handoff updated.
