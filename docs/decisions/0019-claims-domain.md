# ADR 0019: Claims domain

- Status: Accepted
- Date: 2026-08-30

## Context

Claims is next in the roadmap (`docs/architecture.md`) after Billing
(ADR 0018), which explicitly noted Claims as a future domain that
would attach to a real `Invoice` once one existed. No real AngelCare
claims process has been confirmed -- no billing vendor, no
clearinghouse, no EDI format, nothing in
`docs/business-decisions-log.md`. Same situation Payer and Billing
were built under.

## Decision

Built a `claims` app with a `Claim` model covering only generic,
confirmable structure:

- `invoice` (FK to `billing.Invoice`, `PROTECT`) -- which bill this
  claim is for
- `payer` (FK to `payers.Payer`, `PROTECT`, **required** -- unlike the
  optional payer on `Invoice`/`Passenger`, a claim only makes sense
  once there's someone to submit it to)
- `claim_number` (blank -- the payer's own reference, filled in once
  known)
- `amount_claimed`, `amount_paid` (staff-entered; no rate schedule or
  automatic reconciliation)
- `status` (`submitted` / `accepted` / `denied` / `paid`)
- `submitted_date`, `response_date`, `notes`

**FK, not OneToOne, to `Invoice`.** A real claim can get denied and
resubmitted -- forcing one Claim per Invoice would mean either
destroying that history or bolting on a workaround. Multiple `Claim`
rows for one `Invoice` is the honest shape.

**Never auto-created.** Marking an `Invoice` as `sent` doesn't create
a `Claim` -- staff create one explicitly via `/admin/`, same
manual-linking philosophy as `TripRequest.passenger` (ADR 0006),
`Passenger.payer` (ADR 0017), and `Invoice` itself (ADR 0018).

**Deliberately NOT built**, because no confirmed business need or real
data exists yet:

- EDI (837/835) file generation or parsing
- Clearinghouse / billing-vendor integration
- Automatic claim-status polling
- Appeals workflow
- Remittance advice parsing / auto-reconciliation of `amount_paid`

**RBAC** (ADR 0005): Administrator only, full CRUD -- same as Billing.
`Dispatcher` gets no access; claims are financial, not dispatch work.

## Consequences

- A denied-and-resubmitted claim has real history instead of being
  silently overwritten.
- Compliance (still the last remaining future domain) has real
  Payer/Billing/Claims records to reference once it's built.
- EDI, clearinghouse integration, appeals, and remittance parsing
  remain UNKNOWN / REQUIRES BUSINESS DECISION, tracked here and in
  `docs/known-limitations.md` rather than guessed at.
