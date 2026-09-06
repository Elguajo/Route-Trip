# Smart Planner decisions

## ADR-001 — Optimizer lives in the backend

**Status:** Accepted

**Decision:** Implement deterministic calculation, validation, and apply logic under `backend/trip/optimization/`; keep the Angular client as a typed preview/rendering layer.

**Why:** Trip data, authorization, persistence, external routing, and reproducible calculations already meet in the FastAPI backend. A browser-only optimizer would duplicate business logic and make persistence/authorization weaker.

**Alternatives considered:** Client-side optimisation; a new standalone optimization service. Both add an unnecessary boundary for the first version.

## ADR-002 — Separate optimizer routing capability from map search provider

**Status:** Accepted

**Decision:** Retain `BaseMapProvider` for existing geocoding/search/interactive routing and introduce a small optimizer `RoutingProvider` protocol for route/matrix capabilities.

**Why:** Existing map providers do not have a shared matrix contract. Adding an abstract matrix method immediately would force unsupported implementations and couple unrelated map-search concerns to planner internals.

**Alternatives considered:** Direct OSRM calls from `TripOptimizer`; adding `matrix()` to every `BaseMapProvider`. The first prevents provider evolution; the second creates premature unsupported API obligations.

## ADR-003 — Explicit sequence is separate from item time

**Status:** Accepted

**Decision:** Add a persisted sequence field when Phase 002 begins; do not encode optimizer/manual order into `TripItem.time`.

**Why:** Existing `time` is optional, sorted today, and represents schedule data. The planner needs an independently stable user order and later arrival/departure estimates.

## ADR-004 — No hidden provider fallback

**Status:** Accepted

**Decision:** If the selected routing capability cannot supply a matrix, return a typed capability error. Do not silently route/optimise through a different provider or use geodesic distance.

**Why:** Hidden fallback produces surprising costs and violates the requirement that optimization primarily use real travel time. OSRM Table is first for compatible profiles; Google matrix/transit are explicit future work.

## ADR-005 — Matrix diagnostics use the authenticated selected-provider boundary

**Status:** Accepted

**Decision:** Expose `POST /api/completions/matrix` as a non-mutating diagnostic/preflight operation. The request supplies only a profile and coordinate snapshot; the server derives the provider from the authenticated user's persisted map-provider setting. Return the existing `TravelMatrix | RoutingFailure` typed union and share the bounded process-local cache.

**Why:** Phase 002 needs a verifiable route-cost dependency, while a caller-controlled provider would bypass the user's selected routing context. The endpoint gives future planner clients a narrow typed boundary without adding persistence, an optimizer, a Google fallback, or a direct-route contract change.

**Alternatives considered:** Keep the adapter entirely internal until the optimizer API exists; add provider as a request field; extend `POST /api/completions/route`. The first prevents independent capability/preflight diagnostics, the second violates selected-provider ownership, and the third changes an established direct-routing contract.
