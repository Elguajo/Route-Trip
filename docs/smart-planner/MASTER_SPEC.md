# Smart Route Planner — Master Spec

## Product goal

Evolve this fork of `itskovacs/trip` from a POI manager with a manual trip itinerary into a deterministic Smart Trip Planner. A user saves interesting places, selects a trip and its constraints, optimizes it into sensible days and visiting order, adjusts it, then uses the plan while travelling.

```text
Saved POIs → Smart Planning → Days → Optimized routes → Live trip
```

The primary quality criterion is practical: do not send the user far away when a nearby selected POI can be visited first.

## Product constraints and principles

- Extend before rewrite. Preserve POIs, categories, Trips, Days, Trip Items, Leaflet, marker clustering, GPX, existing routes, users, imports, map providers, and unrelated UI behavior.
- Use real road/path routes and travel time, not straight-line geometry, as the routing source of truth.
- Optimisation is deterministic; an LLM must never calculate a route.
- Reuse existing routing and map layers where appropriate, but do not bind the optimizer directly to a concrete provider.
- Do not introduce a mathematical global optimum, turn-by-turn navigation, a maps engine, live traffic, transit, marketplace features, or AI planning into the first version.
- Persist only an accepted plan. A preview must not mutate a trip.
- Planning state and live-travel state are different modes and must not be conflated.

## Core domain

- **POI**: existing saved `Place`; later gains a configurable estimated visit duration and priority.
- **Trip / Day / TripItem**: existing itinerary aggregate. Optimisation assigns items to days and preserves an explicit item order independent of display time.
- **RouteSegment**: an immutable calculated leg between two planned items, including routing profile, distance, duration, and real geometry.
- **TravelMatrix**: duration (and when available distance) matrix for an ordered snapshot of locations and one transport profile.
- **TripOptimizer**: service that assembles matrices, clusters/allocates where needed, creates an itinerary, and validates constraints.
- **RoutingProvider**: backend capability with route and matrix operations; OSRM Table is the first matrix implementation.
- **LiveTripState**: non-destructive current-position and visit-status state for a day in progress.

## Functional requirements

### Routing and day optimisation

- Render a real route, distance, duration, and walking/car/bike indication between all itinerary points.
- `Optimize Day` reorders only unprotected items in one day and reduces or reasonably minimises travel time.
- Manual reordering remains authoritative; recalculate route, distance, travel duration, arrival/departure estimates, and day duration afterward.
- Cache equivalent route and matrix requests; invalidate naturally when the profile or coordinate snapshot changes.

### Whole-trip planning

- `Optimize Trip` distributes selected POIs across a requested number of days, groups nearby places, then optimises each day.
- Honour trip start and end location, optional return to start, allowed transport modes, and a user-configurable walking preference.
- Show an optimisation preview (before/after travel time and distance) and apply only after confirmation.
- Support a practical heuristic: matrix → geographic clustering → day allocation → nearest-neighbour/local improvement → constraint validation → overflow adjustment. Global optimality is not required.

### Time and constraints

- A day has start/end time, start/end location, transport configuration, and maximum usable time.
- Total day time is travel time + visit durations + later optional breaks.
- POIs have editable estimated visit duration; absent values use configurable category defaults.
- Later constraints include priority (`must`, `high`, `normal`, `optional`), fixed day/position/time locks, opening hours, reservations, and breaks. Must-visit POIs are never silently dropped.

### Live trip

- `Start Day` introduces a travel-oriented view with current location, next destination, ETA, and remaining itinerary.
- Reuse browser geolocation. Visit status is `planned`, `next`, `visited`, or `skipped`.
- Visited places remain fixed. Skip/visited/manual changes/location deviation may re-optimise only the remaining route, with debounce/threshold protection.
- Automatic visit detection may suggest a status change within a configurable radius; it never silently changes it.

### APIs and persistence

- Planned APIs: `POST /api/trips/{tripId}/optimize`, `POST /api/trips/{tripId}/optimize-day/{dayId}`, and `POST /api/trips/{tripId}/optimize-remaining`.
- Optimisation responses contain days and total distance/travel duration. Applying a response persists day assignment, item order, profiles, arrival/departure estimates, and route summaries; geometry may be cached or recalculated.
- Every durable model change has an explicit Alembic migration and compatible read/write schemas.

## UX principles

- Let a user go from saved places to a usable plan with a small number of choices: days, locations, time window, transport, and Optimize.
- Surface route legs in the day view with mode icon, distance, duration, and a distinct route colour for each day.
- Keep manual control visible: users can drag/reorder and retain their choice.
- Explain constraint warnings and preserve an itinerary if a routing call fails.

## Release scope

### MVP 1 — day routing and order

Real route lines, route metrics, foot/car/bike, `Optimize Day`, automatic order, manual drag-and-drop, and recalculation.

### MVP 2 — whole-trip planning

`Optimize Trip`, allocation by day, geographic clustering, start/end locations, daily time budget, and visit duration.

### MVP 3 — live trip

Start Day, current location, visited/skipped, optimize remaining, and controlled replanning.

### Phase 2

Opening hours, fixed reservations, lunch/breaks, priority, locks, score/comparison, and public transport.

### Phase 3

Nearby suggestions, weather-aware planning, optional AI explanations/preferences, collaboration, offline data, and mobile/PWA travel improvements.

## Acceptance criteria

1. A user selects at least five POIs and can optimise a day.
2. The order changes only when it is not worse under the chosen travel-cost metric.
3. Every routable adjacent pair has real route geometry, distance, and duration.
4. Manual reorder recalculates metrics and does not silently restore automatic order.
5. Whole-trip planning allocates points across multiple days while respecting start/end locations and time budget, or reports an actionable overflow.
6. Applying an optimisation persists it; cancelling does not.
7. Live replanning never changes completed itinerary items; skipped items are excluded.
8. A routing failure does not destroy an existing itinerary.
9. Existing TRIP behavior continues to work.
