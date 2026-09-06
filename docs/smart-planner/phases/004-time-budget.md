# Phase 004 — Time budget and visit duration

## Goal

Make trip allocation and day order account for visit duration as well as travel time, and clearly report overflow.

## User value

The planner stops proposing impossible days such as fourteen hours of visits inside a 09:00–18:00 window.

## Current project state

Phase 003 plans by travel cost but has no persisted day time window, visit duration policy, or constraint validation.

## Scope

- Editable POI/item estimated visit duration and configurable category defaults.
- Day start/end time, endpoint overrides where needed, and maximum usable duration.
- Budget-aware allocation/order with arrival/departure estimates and overflow diagnostics.
- Planning UI settings and day timeline/summary.

## Out of scope

Opening hours, reservations, lunch rules, priority/locks, live state, and automatic transport selection.

## Architecture impact

Add narrow duration/settings models and `constraints.py` validation. The optimizer must calculate total day time as travel + visits; it must not encode a budget into display-only frontend logic.

## Backend changes

- Migrate durable duration/settings fields with defaults preserving existing behavior.
- Add default-duration resolver by existing category and explicit override precedence.
- Return arrivals/departures/overflow reasons in preview; do not silently drop items.

## Frontend changes

- Edit duration and day window; show time line, totals, and actionable overflow.

## Data model changes

Place default/override policy, optional item override, and day planning time settings. Exact field placement must follow code inspection before migration.

## API changes

Extend optimization request/response with time window, duration assumptions, arrival/departure estimates, and unallocated/overflow diagnostics.

## Migration requirements

Explicit Alembic revision(s), default/backfill behavior, and compatibility tests for existing POIs/items/days.

## Implementation tasks

1. Choose/migrate settings ownership with backward-compatible defaults.
2. Implement duration resolution and budget validation.
3. Integrate overflow-aware allocation/order.
4. Add timeline/settings UI.
5. Test budgets, defaults, and persisted overrides.

## Tests

- A 09:00–18:00 fixture counts travel + visits and reports overflow.
- Explicit duration overrides category default.
- Must-not-drop policy is deferred; normal eligible overflow stays visible as unallocated/over-budget.
- Existing no-settings trip remains readable/editable.

## Acceptance criteria

- Planner does not silently present an over-budget day as feasible.
- Visit duration appears in daily total and schedule estimates.
- User can change input durations/time window and receive deterministic recalculation.

## Risks

Ambiguous day timezone/date handling: use existing trip-day date conventions and document any needed timezone decision before implementation.

## Dependencies

Phase 003.

## Completion checklist

- [ ] Migrations and defaults verified.
- [ ] Budget/overflow tests pass.
- [ ] UI and regression checks pass.
- [ ] State/backlog/roadmap/handoff updated.
