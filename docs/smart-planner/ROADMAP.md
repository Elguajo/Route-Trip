# Smart Planner roadmap

| Phase | Outcome | Depends on | Status |
| --- | --- | --- | --- |
| [001 — Routing foundation](phases/001-routing-foundation.md) | Provider-neutral matrix capability and safe routing cache foundation. | Existing provider routing | Complete (verified 2026-09-06) |
| [002 — Optimize one day](phases/002-optimize-day.md) | Deterministic day ordering, preview/apply, explicit sequence, real route display. | 001 | Planned |
| [003 — Trip optimization](phases/003-trip-optimization.md) | Allocate points across days and persist an accepted whole-trip plan. | 001, 002 | Planned |
| [004 — Time budget](phases/004-time-budget.md) | Visit durations and daily time-budget-aware planning. | 003 | Planned |
| [005 — Live trip](phases/005-live-trip.md) | Travel mode, statuses, and safe remaining-route replanning. | 002, 004 | Planned |
| [006 — Advanced constraints](phases/006-advanced-constraints.md) | Locks, priority, opening hours, reservations, breaks, score. | 003–005 | Planned |

Phases deliver independently usable behavior and must preserve pre-existing TRIP behavior. A phase advances to complete only after its acceptance criteria and validation are recorded in `TASKS.md`, `CURRENT_STATE.md`, and `NEXT_SESSION.md`.
