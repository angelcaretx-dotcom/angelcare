# ADR 0017: Payer/Broker domain

- Status: Accepted
- Date: 2026-08-29

## Context

The roadmap (`docs/architecture.md`) has listed payers/brokers as a
future domain since Phase 1, and ADR 0006 explicitly deferred payer
information on `Passenger` pending this domain's existence. No real
AngelCare Transit payer/broker facts have been provided, though --
`docs/business-decisions-log.md` lists "Payer / broker relationships
(Medicaid MCOs, brokers, private pay terms, facility contracts)" as
explicitly UNKNOWN. That's the same situation Driver/Vehicle domains
were built under (ADR 0007): no real fleet/employment facts existed
either, and the answer there was to build the generic structure without
inventing specifics, not to wait indefinitely.

## Decision

Built a `payers` app with a `Payer` model covering only generic,
industry-standard structure:

- `name`, `payer_type` (Medicaid MCO / Broker / Private Pay / Facility
  Contract / Other -- generic NEMT funding-source categories, the same
  kind of non-invented classification as `EmploymentType` on `Driver`;
  NOT a list of AngelCare's real payer relationships)
- `contact_name`, `phone`, `email` (all optional)
- `notes`, and `status` (active/inactive -- archival, matching every
  other domain's never-delete-historical-records principle)

**`Passenger.payer`** is a new nullable FK, staff-set only via
`/admin/` (autocomplete widget, same pattern as
`TripRequest.passenger` from ADR 0006). Not required, not auto-set --
a passenger's funding source isn't always known at intake.

**Deliberately NOT built**, because no confirmed business need or real
data exists yet:

- Rate schedules / pricing (`docs/business-decisions-log.md`: pricing
  is explicitly UNKNOWN)
- Contract terms, authorization/eligibility verification workflows
- EDI or any billing-vendor integration (no Billing domain exists yet)
- Claims
- Real payer names or AngelCare's actual relationships -- the seeded
  `payer_type` choices are generic categories, not confirmed facts

RBAC (ADR 0005) extended the same way Driver/Vehicle were:
`Dispatcher` gets view-only (payer records are administrative, not
day-to-day dispatch work); `Administrator` gets full CRUD. Neither is
speculative -- both roles already exist and the same least-privilege
reasoning already applies to Driver/Vehicle.

## Consequences

- `Passenger` records can now note a funding source without any real
  payer data having been invented to make that possible.
- Billing and Claims (still future domains) have a real `Payer` to
  attach to once they're built, rather than starting from nothing.
- Rate schedules, EDI/billing integration, authorization/eligibility
  verification, and claims remain UNKNOWN / REQUIRES BUSINESS
  DECISION, tracked here and in `docs/known-limitations.md` rather
  than guessed at.
