# ADR 0028: Dispatch foundations — standalone trips, sources, legs, day board, turn-downs

- Status: Accepted
- Date: 2026-11-03

## Context

ADR 0008 modelled a Trip as "a website request with a driver and
vehicle": `trip_request` was required (one-to-one), and so were
`driver` and `vehicle`. That fit a business whose only intake was the
website form. The roadmap (Phases 23–33) brings trips from MTM,
SafeRide, HHSC prior authorizations, facilities, phone calls,
subcontract partners (AMTS, Gray Stone, American Medical Solutions)
and, from Phase 26, recurring standing orders. None of those has a
website request, and a broker trip usually arrives *before* anyone
knows who will drive it. The owner chose to run all four payer streams
from the start (roadmap decision 3).

## Decision

**The Trip is the central record and stands on its own** (`trips/models.py`).

- It carries its own `passenger`, `service_type`, `pickup_address`,
  `dropoff_address`. `trip_request` becomes optional. When a trip *is*
  made from a website request, anything left blank is copied from it
  (`fill_from_request`, on `clean()` and `save()`), so the old
  workflow (pick the request, assign, save) still works unchanged.
- `driver` and `vehicle` are optional. An unassigned trip is valid and
  waits on the day board. A trip can't be **In progress** or
  **Completed** without both. The credential checks from ADR 0007/0008
  still run whenever either is set.
- `source` (`TripSource`): website, phone, facility, MTM, SafeRide,
  HHSC, subcontract (+ `subcontract_partner`, required), private pay.
  Where the trip came from, separate from who pays.
- `payer` (defaults to the passenger's payer), `broker_trip_number`,
  `prior_authorization_number`. A broker number is unique **per
  source** (a partial unique constraint ignoring blanks), so the same
  MTM trip can't be entered twice; Phase 28's import relies on this.
- Legs: `leg` (outbound / return / one way), `appointment_datetime`,
  `outbound_trip` (a return leg points at its outbound trip;
  `outbound.return_trip` goes the other way), and `will_call` (return
  only: the rider phones when ready, so the pickup time is an
  estimate). A return can't be picked up before its outbound.
- Passenger, addresses and service type are blank-able in the database
  only so existing rows could be migrated; `clean()` requires them.
  The migration backfills every existing trip from its request
  (passenger, addresses, service type, payer; source "website" or
  "phone" from the request's own source).

**Day board** (`trips/board.py`, Operations → Day board): one Texas day
(`OPERATIONS_TIME_ZONE`, not UTC) on one page, with previous/next/today
and a date picker. **Needs a driver** lists unassigned scheduled trips
in pickup order, each with a quick-assign form (driver + vehicle
dropdowns of active ones). Below that, one card per driver with their
trips and statuses. Quick assign runs the same `full_clean()` as the
trip form, so an expired license or inspection is refused with the
reason shown. Each assignment is audited as `assigned` (before/after
driver and vehicle), and assignment changes made in the trip form are
audited the same way. Opening the board is audited as a view of
`DayBoard` for that date (ADR 0027), since it lists riders' names.
Dispatchers can use the board and assign; staff without trip access
get 403.

**Turn-down log** (`TurnDown`, Operations → Turn-downs): a broker trip
offered to us and declined: source, payer, broker trip number, when
offered, requested pickup, service type, a reason (no driver, no
suitable vehicle, outside area, too short notice, rate, other), notes,
and who recorded it (set automatically). It holds **no rider details**
by design: the broker's number is enough to reconcile, and a declined
trip shouldn't leave PHI behind. Phase 31's SLA dashboard computes the
acceptance rate from trips vs. turn-downs per source. Dispatchers can
add and edit; only Administrators delete.

## Consequences

- Everything that displayed `trip.trip_request.full_name` (dashboard,
  invoice/claim search) now uses `trip.passenger`.
- `Trip.trip_request` no longer guarantees a request exists; code must
  not assume it. The request's status still flips to Scheduled when a
  trip is created from it.
- The day board has no drag-and-drop, no conflict warning when a
  driver's trips overlap, and no route optimization. Those wait until
  the driver app (Phase 24) supplies real timings.
- Release: this changes the database (`trips.0002`, `trips.0003`). Run
  `ops/migrate-production.ps1` before pushing.
