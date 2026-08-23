# ADR 0006: Passenger domain

- Status: Accepted
- Date: 2026-08-23

## Context

Every trip request has, until now, been a disconnected one-off record
-- no concept of "the same person requesting transportation again."
Section 3 of the project directive describes a fairly rich Passenger
concept (legal/preferred name, contact info, emergency contact,
mobility requirements, authorized representatives, facility
relationships, payer information, documents, notes, status, history).

## Decision

Built a `passengers` app with a `Passenger` model covering only what's
grounded in real, current need:

- Legal name, preferred name, phone, email
- Emergency contact name/phone (simple fields, not a sub-model --
  important safety information for NEMT, low complexity to add)
- Preferred service type (reuses the existing, already-confirmed
  `ServiceType` choices from `transportation`) + free-text mobility
  notes
- Notes, and `status` (active/inactive -- archival, matching the
  project's never-delete-historical-records principle)

**Deliberately NOT built**, because no dependent domain or confirmed
need exists yet:

- Authorized representatives (a real sub-model, but no confirmed
  workflow exists for someone booking on a passenger's behalf)
- Facility relationships (no Facility domain)
- Payer information (no Payer/Broker domain -- explicitly deferred
  since ADR-level architecture)
- Documents (no Document domain)
- Field-level history/versioning of Passenger edits (only status
  changes are audited, matching the same scope decision made for
  TripRequest in ADR 0005)

**`TripRequest.passenger`** is a new nullable FK, staff-set only via
`/admin/` (autocomplete widget, backed by `Passenger.search_fields`).
Explicitly NOT auto-matched on submission by phone/email -- doing that
without real deduplication logic (fuzzy matching, merge/conflict
handling) risks silently attaching a request to the wrong person's
record, which is worse than leaving it unlinked for a human to review.

RBAC (ADR 0005) extended: `Dispatcher` gets view/change/add on
`Passenger` (day-to-day use); `Administrator` additionally gets delete.
Neither is speculative -- both roles already existed and already
needed to touch passenger data as part of handling trip requests.

## Consequences

- Enables repeat-customer recognition and a real mobility profile per
  person, without having invented a business process (like
  representative-booking) that hasn't been confirmed to exist.
- Future domains (dispatch/trips, billing) attach to `Passenger`, not
  to a `TripRequest`'s inline fields -- `TripRequest` remains what it
  actually is: an intake record, optionally linked to a passenger once
  staff have reviewed it.
- Authorized representatives, facility/payer/document relationships
  remain UNKNOWN / REQUIRES BUSINESS DECISION, tracked here rather than
  guessed at.
