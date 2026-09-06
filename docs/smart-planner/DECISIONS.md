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

## ADR-006 — Introduce sequence without changing the existing display contract

**Status:** Accepted

**Decision:** `TripItem.sequence` is persisted and serialized in SP-002-01, but is not accepted from normal create/update clients. Current day reads remain ordered by `time` and then `id`; create, cross-day move, and old backup-import paths assign a stable sequence automatically.

**Why:** This establishes a safe, durable planner ordering value while preserving today's time-based UI and API behavior. Later explicit planner/manual-reorder contracts can use sequence without overloading schedule time or silently changing existing displays.

**Alternatives considered:** Immediately change every day read to sequence order; accept arbitrary sequence on the existing item update endpoint. The first is a visible compatibility change before preview/apply semantics exist; the second exposes a partial reordering API without transaction or validation guarantees.

## ADR-007 — Day calculation requires a complete matrix and preserves unlocatable items

**Status:** Accepted

**Decision:** A single-day calculation may compare and propose an optimized route only when the returned matrix exactly matches the requested coordinate snapshot and contains a duration for every distinct ordered pair. If the provider fails, the matrix is incomplete, or the snapshot differs, return the persisted sequence order with diagnostics and no cost comparison. Items without valid coordinates remain at their current sequence positions, are listed in diagnostics, and are excluded from the matrix-backed cost.

**Why:** A partial table cannot support an honest all-pairs optimization or before/after comparison. Treating `null` cells as estimates would violate the Phase 001 routing contract, while dropping POIs would make an eventual apply operation silently lose itinerary intent.

**Alternatives considered:** Optimize around missing cells and report partial totals; estimate unavailable legs; omit coordinate-less items from the result. These produce ambiguous or misleading costs and make later apply behavior unsafe.

## ADR-008 — Apply recalculates a previewed sequence from an explicit snapshot

**Status:** Accepted

**Decision:** Preview returns its persisted starting item-ID order. Apply requires that exact order, recalculates the result on the server from the authenticated user's selected matrix provider, and writes only when the calculation has a complete matrix-backed comparison. The proposed order itself is never accepted from the client.

**Why:** The snapshot prevents applying a stale preview, while server-side recalculation keeps travel costs and provider selection authoritative. Requiring a complete calculation ensures unavailable, incomplete, or mismatched matrices cannot partially alter itinerary intent.

**Alternatives considered:** Apply a client-supplied optimized order; rerun calculation without a preview snapshot; persist a partial result. Those options permit stale or unverified changes and weaken the no-fallback/no-partial-cost contract.

## ADR-009 — Manual reorder has a dedicated complete-day transaction

**Status:** Accepted

**Decision:** Add a separate authenticated `POST /api/trips/{tripId}/days/{dayId}/reorder` contract that accepts every selected-day item ID exactly once and atomically writes contiguous `TripItem.sequence` values for that day only. Keep `TripItem.time`, day assignment, and the ordinary item-update API unchanged.

**Why:** Manual ordering is a multi-row consistency operation, not an attribute edit. A full scoped snapshot prevents accidental cross-day moves or dropped items while allowing the Angular client to reconcile just the changed day and recalculate its tagged route layers.

**Alternatives considered:** Make `sequence` writable on the existing item endpoint; reorder client-side without persistence; submit partial move operations. Each either permits transient/inconsistent order, blurs the time/sequence boundary, or weakens server-side day scoping.

## ADR-010 — Trip planner settings are a one-to-one defaulted record

**Status:** Accepted

**Decision:** Store Phase 003 planner inputs in a cascade-deleted one-to-one `TripPlannerSettings` record rather than adding columns to `Trip`. Backfill every existing trip with one default record: one requested day, no start/end, no return-to-start, `car` profile, and duration objective. Full and shared trip serialization expose the setting object; an authenticated complete-payload `GET`/`PUT /api/trips/{tripId}/planner-settings` boundary owns updates.

**Why:** The settings are planner-specific, optional for legacy trip behavior, and will evolve independently of core trip metadata. A separate row leaves the established `Trip` schema and create/update contract intact, gives each old trip deterministic values, and lets Phase 003 orchestration consume a single validated input snapshot without starting allocation or route computation.

**Alternatives considered:** Add nullable settings columns to `Trip`; create settings only when a user first opens the planner; accept partial fields on the ordinary trip update endpoint. Those approaches either mix planner policy into a stable base model, leave legacy trips with ambiguous configuration, or weaken validation/ownership of a coherent settings payload.

## ADR-011 — Allocation is an isolated, complete-matrix calculation that delegates ordering

**Status:** Accepted

**Decision:** Copy saved `TripPlannerSettings` into an immutable calculation snapshot, select its first allowed profile deterministically, and perform prospective multi-day grouping only from a complete matrix supplied by the already-selected routing provider. Use duration or distance exactly as selected for allocation; start/end (or return-to-start) are matrix anchors. Pass each prospective group to the existing Phase 002 `TripOptimizer` for internal order. Do not write or create `TripDay`/`TripItem`, sequence, settings, APIs, or route-provider fallbacks in this layer.

**Why:** The allocation strategy needs planner policy but must remain independently testable and safe to reuse by a later preview/apply transaction. A complete matrix avoids partial or estimated clustering, while delegating per-day order preserves one source of truth for the established Phase 002 contract.

**Alternatives considered:** Let allocation import and mutate ORM trip/day rows; use straight-line clustering when a matrix is unavailable; select any supported profile or provider; reimplement the day ordering heuristic. These options would blur calculation and persistence, hide routing behaviour, or fork tested ordering logic.

## ADR-012 — Whole-trip apply snapshots POI positions and retains unmanaged itinerary items

**Status:** Accepted

**Decision:** Whole-trip preview returns every eligible POI-backed `TripItem` as an `(item_id, day_id, sequence)` snapshot plus an opaque token derived from that snapshot, persisted planner settings, the selected routing provider, and current target-day set. Apply requires both values, recalculates through the existing `TripAllocator`, and atomically creates only non-empty missing days and updates only eligible POI assignments/sequences. Non-POI itinerary items are not allocated, moved, or resequenced; when sharing a target day, POIs are written after their retained sequence slots.

**Why:** The compare-and-apply boundary must reject stale assignments and settings rather than silently applying a newly recalculated plan the user never reviewed. POI allocation also cannot disturb bookings, notes, or generic itinerary entries that are outside the Phase 003 POI policy. Keeping those rows untouched gives a clear diagnostic policy while retaining every eligible POI, including coordinate-less ones.

**Alternatives considered:** Accept only an item-ID list; trust a client-supplied proposed allocation; apply the newest settings without invalidating the preview; resequence or move every item in a target day. These weaken stale protection, duplicate backend authority, or modify unrelated itinerary data.
