# ADR 0004: Local-first, provider-independent development

- Status: Accepted
- Date: 2026-08-23

## Context

Phase 1 development drifted toward treating production hosting (first
DigitalOcean, then Fly.io) as something blocking further progress,
including asking the business owner for a payment card before local
functionality was even fully proven out. The business owner corrected
this: production hosting is a separate, later concern from
development, and no cloud provider or payment method should ever be a
prerequisite for developing or testing this application.

An audit at this point confirmed the codebase was already
architecturally sound on this point (ADR 0001-0003's env-var-driven
config meant zero application code was actually coupled to Fly.io --
only `api/fly.toml` and the GitHub Actions deploy workflow were, and
neither affects local development). The gap was tooling and docs, not
architecture:

- No local PostgreSQL was set up; local dev had been using the SQLite
  fallback only, untested against real Postgres.
- Docs (`api/README.md`, root `README.md`) led with production hosting
  before local setup, reinforcing the wrong priority.

## Decision

1. **`docker-compose.yml`** at the repo root provides a local PostgreSQL
   matching production, for anyone with Docker available.
2. Local development docs now lead with local setup; production
   deployment is explicitly labeled as a separate, later section.
3. **Verification standard going forward**: before any phase is
   considered complete, its full workflow must be proven locally end
   to end (browser -> frontend -> API -> real Postgres -> admin/staff
   view), not just unit-tested. Cloud deployment is validated
   separately, afterward, never as a gate on local completeness.
4. No feature, fix, or test may require a cloud account, API token, or
   payment method to develop or verify locally. If a future
   integration (e.g. a mapping or SMS provider) has no free/local way
   to test, that must be flagged explicitly as a decision point before
   building against it, not discovered after the fact.

## Consequences

- Anyone (a new engineer, a future agent session) can clone this repo
  and have a fully working, fully tested local environment without
  contacting any cloud provider.
- Production hosting choices (Vercel, Fly.io, Supabase -- ADR 0002/3)
  remain valid and unchanged; this ADR only changes when and how they
  enter the workflow, not what they are.
- Slightly more local setup (a Postgres instance) than SQLite-only
  development would need, in exchange for local behavior actually
  matching production.
