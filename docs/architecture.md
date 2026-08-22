# Architecture

See also: [ADR 0001](decisions/0001-monorepo-split-deploy.md),
[ADR 0002](decisions/0002-hosting-and-stack.md),
[business-decisions-log.md](business-decisions-log.md),
[known-limitations.md](known-limitations.md).

## Shape

Two independently deployable apps in one repo:

```
web/   Next.js (TypeScript, Tailwind) — public site + trip request form UI
       -> deploys to Vercel
api/   Django + DRF + PostgreSQL      — owns the database, all business logic
       -> deploys to DigitalOcean
```

`web/` calls `api/` only over HTTP, at a versioned path (`/api/v1/...`).
No shared code, no shared database access from the frontend, no direct
coupling beyond that HTTP contract. Either side can be replaced without
rewriting the other.

## Domain organization (api/)

The backend is organized as domain-oriented Django apps, not one
monolithic app. Currently:

- `transportation/` — trip request intake (Phase 1: capture and store a
  request; not yet the full trip lifecycle / dispatch state machine)

Future domains, added only when there's a confirmed business need
(never speculatively): organization, passengers, drivers, vehicles,
dispatch/trips, payers/brokers, billing, claims, compliance, documents,
notifications, audit. Each gets its own app, its own models, and talks
to other domains through explicit interfaces — not by reaching into
another app's models directly once the domains get more coupled logic.

Third-party vendors (maps, SMS, email, payments, broker APIs) are to be
isolated behind an `integrations/` layer when they're introduced — never
called directly from domain logic. None exist yet in Phase 1.

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
