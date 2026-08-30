# ADR 0018: Billing domain

- Status: Accepted
- Date: 2026-08-30

## Context

Billing is next in the roadmap (`docs/architecture.md`) after Payer
(ADR 0017), which explicitly deferred rate schedules, EDI, and
payment/claims logic pending confirmed business facts. Those facts
still haven't been provided -- `docs/business-decisions-log.md` lists
pricing/rate schedules as explicitly UNKNOWN. That's the same
situation Payer, Driver, and Vehicle were built under: no real facts
yet, but a real structural need (recording that a completed trip was
billed, to whom, and for how much) already exists.

## Decision

Built a `billing` app with an `Invoice` model covering only generic,
confirmable structure:

- `trip` (FK to `trips.Trip`, `PROTECT` -- a trip can't disappear
  while it still has billing history)
- `payer` (nullable FK to `payers.Payer` -- null means private pay,
  same convention as `Passenger.payer` from ADR 0017). Captured on the
  invoice itself rather than read live through
  `trip.trip_request.passenger.payer`, so a later edit to a
  passenger's payer doesn't silently rewrite billing history.
- `amount` (`DecimalField`, staff-entered by hand -- there is no rate
  schedule to calculate it from)
- `status` (`draft` / `sent` / `paid` / `void` -- archival-style
  lifecycle, never deleted)
- `issued_date`, `paid_date`, `notes`

**Never auto-created.** A completed `Trip` does not automatically
produce an `Invoice` -- staff create one explicitly via `/admin/`,
the same manual-linking philosophy as `TripRequest.passenger`
(ADR 0006) and `Passenger.payer` (ADR 0017). Inventing an
auto-billing trigger and amount-calculation rule that don't exist yet
would mean fabricating a business process, which this project's
directive explicitly forbids.

**Deliberately NOT built**, because no confirmed business need or real
data exists yet:

- Rate schedules or any automatic amount calculation
- EDI / clearinghouse / billing-vendor integration
- Payment processing (this only records that payment happened, via
  `paid_date` + status, not how)
- Invoice PDF generation or emailing
- Claims (a separate future domain, depends on Billing existing first)

**RBAC** (ADR 0005): Administrator only, full CRUD. Unlike
Driver/Vehicle/Payer, `Dispatcher` gets **no** access at all --
billing is administrative/financial, not dispatch work, and there's
no confirmed need for dispatchers to see it (least privilege).

`trips.TripAdmin` gained `search_fields` (previously absent) so
`InvoiceAdmin` can use `autocomplete_fields` on `trip` -- required by
Django's admin checks on whichever admin the target model is
registered under, not the referencing admin.

## Consequences

- Completed trips can now have a real, auditable billing record
  without any rate schedule or billing vendor having been invented to
  make that possible.
- Claims (still a future domain) has a real `Invoice` to attach to
  once it's built, rather than starting from nothing.
- Rate schedules, EDI/billing-vendor integration, payment processing,
  and claims remain UNKNOWN / REQUIRES BUSINESS DECISION, tracked here
  and in `docs/known-limitations.md` rather than guessed at.
