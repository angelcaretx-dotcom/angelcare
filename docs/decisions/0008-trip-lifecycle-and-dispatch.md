# ADR 0008: Trip lifecycle and basic dispatch

- Status: Accepted
- Date: 2026-08-24

## Context

Until now, a `TripRequest` was as far as the system went -- an intake
record with an intake-only status. Drivers and vehicles (ADR 0007)
exist but weren't connected to anything. The project directive's
Section 5 describes a full real-time trip lifecycle: `REQUESTED ->
VERIFIED -> AUTHORIZED -> SCHEDULED -> ASSIGNED -> DRIVER_ACCEPTED ->
EN_ROUTE_TO_PICKUP -> ARRIVED -> PASSENGER_ONBOARD ->
EN_ROUTE_TO_DESTINATION -> ARRIVED_DESTINATION -> COMPLETED` -- but
explicitly flags that machine as an example, not a confirmed
requirement, "before implementation, validate the actual workflow with
AngelCare."

## Decision

Built a `trips` app with a `Trip` model, linked one-to-one to a
`TripRequest`, requiring a `Driver` and `Vehicle` to be assigned at
creation. Status is deliberately coarser than the directive's example:

`SCHEDULED -> IN_PROGRESS -> COMPLETED`, with `CANCELLED`/`NO_SHOW` as
alternates.

The full granular machine (`ARRIVED`, `PASSENGER_ONBOARD`, etc.)
assumes real-time updates from a driver-facing app that doesn't exist.
Building those states now would mean decoration nothing can ever
transition into -- worse than not having them, because it would look
like tracking that isn't actually happening. This coarser set is what
a human dispatcher can genuinely operate by hand in `/admin/` today.
The fuller machine remains the long-term target once live
driver-side tracking exists.

**Credential enforcement, deferred in ADR 0007, is implemented now**:
`Trip.clean()` blocks assignment if the driver isn't Active or their
license is expired, or if the vehicle isn't Active or its registration/
inspection is expired (Section 7/8's explicit requirement). This is
enforceable now because an actual assignment feature exists to enforce
it in -- building this check in ADR 0007 would have been enforcing a
rule against a feature that didn't exist yet.

**Also enforced**: a `Trip` can't be created for a `TripRequest` that
isn't linked to a `Passenger` -- scheduling a trip is committing real
resources to a real person, so that link needs to already exist.

**Side effect, not a separate manual step**: creating a `Trip` updates
the linked `TripRequest.status` to `SCHEDULED` and logs that transition
to the audit trail, so the request and its eventual trip stay
consistent without staff having to remember to update both.

RBAC (ADR 0005): `Dispatcher` gets full view/change/add on `Trip`
(scheduling trips is core dispatch work), `Administrator` adds delete.

## Consequences

- One `TripRequest` maps to at most one `Trip` (`OneToOneField`) --
  recurring trips (Section 4's "recurring transportation" service
  type) aren't built; each occurrence needs its own pair for now.
- Future work (once a driver-facing app or live tracking exists): the
  finer-grained state machine, `IN_PROGRESS` splitting into the fuller
  set, and possibly automatic credential-expiration alerts rather than
  only blocking at assignment time.
