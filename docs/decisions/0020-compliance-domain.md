# ADR 0020: Compliance domain

- Status: Accepted
- Date: 2026-08-31

## Context

Compliance is the last domain on the original roadmap
(`docs/architecture.md`), after Claims (ADR 0019). No real AngelCare
regulatory facts have been confirmed --
`docs/business-decisions-log.md` explicitly lists legal entity
type/EIN/state registration, insurance and credentialing specifics,
and any regulatory claims (Medicaid enrollment status, state
licensure numbers, etc.) as UNKNOWN. Same situation every other
recent domain was built under.

## Decision

Built a `compliance` app with a `ComplianceRecord` model covering only
generic, confirmable structure:

- `name` (free text, e.g. "General Liability Insurance")
- `record_type` (License / Permit / Insurance Policy / Certification /
  Registration / Other -- generic categories, the same kind of
  non-invented classification as `PayerType` on `Payer`; NOT a list of
  AngelCare's real licenses/policies)
- `issuing_authority`, `reference_number` (both optional free text)
- `issued_date`, `expiration_date`, `status`
  (active/expired/inactive), `notes`

This is distinct from the per-`Driver` license and per-`Vehicle`
registration/inspection already tracked in their own domains
(ADR 0007) -- `ComplianceRecord` is for business-level regulatory
items, not per-person or per-vehicle credentials.

**Reuses the existing `documents.Document` framework (ADR 0009)** for
supporting evidence (the actual license/policy PDF) via
`DocumentInline`/`DocumentUploaderAdminMixin` -- the exact same
pattern `DriverAdmin`/`VehicleAdmin` already use. No new file-handling
code was needed.

**Deliberately NOT built**, because no confirmed business need or real
data exists yet:

- Any real AngelCare license numbers, policy numbers, EIN, or
  registration details
- Automatic expiration-based blocking of anything (Driver/Vehicle
  block trip assignment on expired credentials because assignment is
  a real, existing feature to block; there's no equivalent action a
  business-level compliance record would block yet)
- Regulatory-body integration (e.g. checking Medicaid enrollment
  status via an API)
- Renewal reminders/notifications

**RBAC** (ADR 0005): `Dispatcher` gets view-only, `Administrator` gets
full CRUD -- same reasoning as Driver/Vehicle/Payer (reference
information, not day-to-day dispatch work, and not financial like
Billing/Claims).

## Consequences

- AngelCare now has a real place to record its actual licenses,
  policies, and registrations (with supporting documents) once staff
  enter them -- without any of that data having been invented to make
  the domain exist.
- Completes the domain list from the original Phase 0 roadmap
  (`docs/architecture.md`'s "future domains" list is now empty; any
  further domain is added only when a new confirmed need arises).
- Real AngelCare regulatory facts remain UNKNOWN / REQUIRES OFFICIAL
  SOURCE, tracked in `docs/business-decisions-log.md` rather than
  guessed at.
