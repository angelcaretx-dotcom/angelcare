# Architecture

See also: [ADR 0001](decisions/0001-monorepo-split-deploy.md),
[ADR 0002](decisions/0002-hosting-and-stack.md),
[ADR 0003](decisions/0003-supabase-and-flyio.md),
[ADR 0004](decisions/0004-local-first-development.md),
[ADR 0005](decisions/0005-rbac-and-audit-foundation.md),
[ADR 0006](decisions/0006-passenger-domain.md),
[ADR 0007](decisions/0007-driver-vehicle-domains.md),
[ADR 0008](decisions/0008-trip-lifecycle-and-dispatch.md),
[ADR 0009](decisions/0009-document-domain.md),
[ADR 0010](decisions/0010-organization-domain.md),
[ADR 0011](decisions/0011-vercel-hosted-api.md),
[ADR 0012](decisions/0012-supabase-storage.md),
[ADR 0013](decisions/0013-ci-pipeline.md),
[ADR 0014](decisions/0014-staff-mfa.md),
[ADR 0015](decisions/0015-branded-html-emails.md),
[ADR 0016](decisions/0016-unfold-admin-theme.md),
[ADR 0017](decisions/0017-payer-broker-domain.md),
[ADR 0018](decisions/0018-billing-domain.md),
[ADR 0019](decisions/0019-claims-domain.md),
[business-decisions-log.md](business-decisions-log.md),
[known-limitations.md](known-limitations.md).

## Shape

Two independently deployable apps in one repo:

```
web/   Next.js (TypeScript, Tailwind) — public site + trip request form UI
       -> deploys to Vercel
api/   Django + DRF + PostgreSQL      — owns the database, all business logic
       -> deploys to Vercel (serverless), database on Supabase (ADR 0011)
```

`web/` calls `api/` only over HTTP, at a versioned path (`/api/v1/...`).
No shared code, no shared database access from the frontend, no direct
coupling beyond that HTTP contract. Either side can be replaced without
rewriting the other.

## Domain organization (api/)

The backend is organized as domain-oriented Django apps, not one
monolithic app. Currently:

- `passengers/` — (Phase 4) real, standalone passenger records —
  see ADR 0006 for what's included and what's deliberately deferred.
- `drivers/`, `vehicles/` — (Phase 5) driver and vehicle records —
  see ADR 0007 for what's included and what's deliberately deferred.
- `trips/` — (Phase 6) `Trip`: links a reviewed `TripRequest` to an
  assigned `Driver`+`Vehicle` with a real (if intentionally coarse)
  status lifecycle, and enforces the credential-expiration blocking
  ADR 0007 deferred. See ADR 0008.
- `documents/` — (Phase 7) `Document`: generic file attachment (via
  Django's ContentType framework) to any model, with a verify/reject
  review workflow. Currently used by Driver/Vehicle to back up their
  credential expiration dates. See ADR 0009.
- `organization/` — (Phase 8) `Organization`: single record for
  AngelCare Transit itself, seeded from confirmed business facts.
  Does not replace `web/src/lib/site-config.ts` -- see ADR 0010.
- `payers/` — (Phase 12) `Payer`: structural funding-source record
  (Medicaid MCO / Broker / Private Pay / Facility Contract / Other),
  optionally linked from `Passenger`. No rate schedules, billing, or
  claims logic yet -- see ADR 0017.
- `billing/` — (Phase 13) `Invoice`: structural bill for one completed
  `Trip` (amount entered by staff, no rate schedule), optionally
  linked to a `Payer`. Never auto-created. No EDI, payment processing,
  or claims yet -- see ADR 0018.
- `claims/` — (Phase 14) `Claim`: record that an `Invoice` was
  submitted to a `Payer` for reimbursement (required, unlike Invoice's
  optional payer). Multiple claims per invoice (FK, not OneToOne) --
  denial/resubmission keeps real history. Never auto-created. No EDI,
  clearinghouse integration, or appeals workflow -- see ADR 0019.
- `transportation/` — trip request intake (Phase 1: capture and store a
  request; not yet the full trip lifecycle / dispatch state machine).
  `TripRequest.passenger` optionally links to a `passengers.Passenger`,
  set by staff only, never auto-matched on submission.
- `notifications/` — (Phase 2) `NotificationService` + a swappable
  `EmailProvider` interface, and a `NotificationLog` audit trail of
  every send attempt. This is the first realized instance of the
  vendor-isolation pattern below: `transportation` calls
  `NotificationService`, never an email vendor's SDK directly. Every
  email is branded HTML with a plain-text alternative (Phase 10) — see
  ADR 0015.
- `audit/` — (Phase 3) general-purpose `AuditLog` + `record_change()`,
  usable by any domain app, not tied to Django admin specifically.
- `accounts/` — (Phase 3) seeds staff roles as Django Groups
  (`Dispatcher`, `Administrator`) with explicit permissions — see
  ADR 0005. Also (Phase 9) enforces TOTP-based MFA for every staff
  account on `/admin/`, via django-otp — see ADR 0014. Also (Phase 11)
  hosts `AngelCareAdminSite`, the combined Unfold + MFA admin site
  class registered as `admin.site` itself — see ADR 0016.

Future domains, added only when there's a confirmed business need
(never speculatively): compliance. Each gets its own
app, its own models, and talks to other
domains through explicit interfaces — not by reaching into another
app's models directly once the domains get more coupled logic.

Third-party vendors (maps, SMS, email, payments, broker APIs) are
isolated behind an interface when they're introduced — never called
directly from domain logic. `notifications/providers/` is the first
example (an `EmailProvider` ABC); the same pattern applies to maps, SMS,
payments, etc. as they're added.

## Data conventions

- Timestamps stored in UTC (`USE_TZ = True`, `TIME_ZONE = "UTC"`),
  converted to local time only at presentation.
- Primary keys on business records are UUIDs (not sequential integers),
  since request IDs are returned to an unauthenticated public client.
- Status fields use `TextChoices` enums, not free-form strings.

## What Phase 1 deliberately does NOT include

- Authentication / RBAC (nothing behind a login yet — the only public
  surface is the create-only trip request endpoint; admin uses Django's
  built-in auth for staff)
- The full trip lifecycle state machine (REQUESTED -> ... -> COMPLETED)
  — `transportation.TripRequestStatus` is a much simpler intake-only
  status, by design (see the model's docstring)
- A centralized audit log — trip requests currently only have
  `created_at`/`updated_at`; see known-limitations.md
- Any payer/broker, billing, claims, compliance, or dispatch logic

These are future phases, not oversights — see the phase list in the
Phase 0 report / project history.
