# Smart Planner development context

This directory is the progressive, spec-driven workspace for evolving this fork of TRIP into a Smart Route Planner. It is intentionally separate from implementation code and from the upstream product documentation.

## Reading order

At the start of a Smart Planner session, read only:

1. [`NEXT_SESSION.md`](NEXT_SESSION.md)
2. [`CURRENT_STATE.md`](CURRENT_STATE.md)
3. the active file under [`phases/`](phases/)

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for an affected boundary, [`DECISIONS.md`](DECISIONS.md) for a non-obvious constraint, and [`MASTER_SPEC.md`](MASTER_SPEC.md) only for product-scope questions. `MASTER_SPEC.md` is the product source of truth and must not be duplicated into phase files.

## Document roles

| File | Purpose |
| --- | --- |
| `MASTER_SPEC.md` | Stable product requirements, scope, and acceptance criteria. |
| `ARCHITECTURE.md` | Code-grounded target architecture and integration boundaries. |
| `ROADMAP.md` | Ordered delivery phases and their outcomes. |
| `phases/*.md` | The self-contained specification for one implementation phase. |
| `TASKS.md` | Live, checkable execution backlog. |
| `CURRENT_STATE.md` | Verified facts about what works now. |
| `NEXT_SESSION.md` | Compact continuation handoff. |
| `DECISIONS.md` | Lightweight ADR log. |
| `TEST_PLAN.md` | Cross-phase verification strategy. |

## Update rules

- Mark a task `[x]` only after its stated validation has passed.
- Update `CURRENT_STATE.md` only with observed implementation state, not planned behavior.
- Before ending an implementation session, make `NEXT_SESSION.md` accurate.
- Keep phase files scoped to their phase; link to the Master Spec rather than copy it.
