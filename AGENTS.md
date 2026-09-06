# Smart Planner workflow

Before working on Smart Planner:

1. Read `docs/smart-planner/NEXT_SESSION.md`.
2. Read `docs/smart-planner/CURRENT_STATE.md`.
3. Read the active phase specification in `docs/smart-planner/phases/`.
4. Do not load `MASTER_SPEC.md` unless the active specification or a product question requires it.
5. Update `TASKS.md` after completing work.
6. Update `CURRENT_STATE.md` after verified implementation.
7. Update `NEXT_SESSION.md` before ending the session.
8. Record significant architecture decisions in `DECISIONS.md`.
9. Never mark a task complete without validation.
10. Preserve existing TRIP functionality unless the specification explicitly changes it.

Use the cycle: read active task → inspect relevant code → implement the smallest coherent change → run relevant validation → update the Smart Planner documents. Extend existing TRIP layers before introducing replacements.
