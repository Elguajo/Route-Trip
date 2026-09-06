# Phase 005 — Live trip

## Goal

Provide a travel-focused day view with safe visit statuses, current location, and re-optimization of only the remaining route.

## User value

During a trip, the user sees the next destination and can mark or skip a place without losing the completed part of their day.

## Current project state

Browser one-shot geolocation exists. There is no planner visit status, live UI, location watcher, or remaining-route API.

## Scope

- Persist `planned`, `next`, `visited`, and `skipped` visit status without reusing unrelated existing `TripItemStatusEnum` meanings.
- Start Day UI with current position, next item, ETA, remaining itinerary, and clear planning/live mode boundary.
- Remaining-only optimization from current position; visited order/assignment remains immutable.
- Debounced location updates, deviation threshold, skip/visited triggers, and confirmation-based automatic-visit suggestion.

## Out of scope

Turn-by-turn navigation, background GPS, offline navigation, automatic status mutation, nearby recommendations, and complex constraints not yet implemented.

## Architecture impact

Persist visit state in planner-specific fields/type; keep browser location ephemeral in a dedicated live-trip service/component. `TripOptimizer` accepts a remaining-item snapshot and a start location, never a mutable whole day.

## Backend changes

- Add migration/schemas/status transitions and ownership validation.
- Add `POST /api/trips/{tripId}/optimize-remaining` that rejects mutations to visited items.

## Frontend changes

- Add Live Trip component/state and explicit entry/exit from planning view.
- Use existing geolocation helper as base; add watch/debounce only with clear permission/error UI.

## Data model changes

Planner visit status and optional user-level live preferences such as auto-detection radius. Avoid colliding with existing booking-oriented trip item status enum.

## API changes

Status update endpoint/contract plus `optimize-remaining` request carrying current location and day context; response names preserved/changed item IDs explicitly.

## Migration requirements

Alembic migration with existing items defaulting to `planned` and a compatibility review of shared-trip serialization.

## Implementation tasks

1. Design/migrate independent visit status and transitions.
2. Implement remaining-route calculation and API tests.
3. Build Start Day UI/geolocation/error states.
4. Add status/skip/replan and confirmation flow.
5. Validate that completed items never move.

## Tests

- Mark visited then optimize remaining: visited IDs/order unchanged.
- Skip removes only skipped item from remaining route.
- Permission denied/unavailable geolocation remains usable with last/start location.
- Debounce/deviation policy avoids requests on every GPS update.

## Acceptance criteria

- User can start a day, see next/remaining, visit or skip points, and re-optimize remaining route.
- No automatic status mutation occurs without confirmation.
- Existing planning itinerary is not destroyed by live routing failure.

## Risks

Geolocation is permission/device/network dependent; it must always degrade to an explicit, usable state.

## Dependencies

Phase 002 plus time-aware planning from Phase 004.

## Completion checklist

- [ ] Status migration/transitions verified.
- [ ] Remaining-only invariants tested.
- [ ] Live UX/manual permission cases checked.
- [ ] State/backlog/roadmap/handoff updated.
