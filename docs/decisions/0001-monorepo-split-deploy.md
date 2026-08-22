# ADR 0001: Monorepo with two independently deployable apps

- Status: Accepted
- Date: 2026-08-22

## Context

AngelCare Transit needs a public marketing site now, and will need a much
larger operations platform (dispatch, drivers, vehicles, billing, claims,
compliance, etc.) over time. The platform must remain maintainable and
replaceable piece-by-piece over a very long time horizon, per the project's
architecture mandate: no single vendor or framework choice should force a
full rewrite later.

## Decision

One git repository, two applications, developed and deployed independently:

- `web/` — Next.js. Public-facing marketing content and the trip request
  form UI. Deploys to Vercel. Contains no business logic beyond form
  validation and presentation; all data is submitted to `api/` over HTTP.
- `api/` — Django + PostgreSQL. Owns the database and all business logic.
  Deploys to DigitalOcean. Organized as domain-oriented Django apps
  (`transportation`, and more added per future phases) rather than one
  monolithic app.

They communicate only through a versioned HTTP API (`/api/v1/...`).

## Consequences

- The frontend can be replaced (different framework, different host)
  without touching the backend, and vice versa — satisfies the
  no-full-rewrite goal for at least this seam.
- Requires CORS configuration on the API for the Vercel domain.
- Requires two deployment pipelines instead of one, and two sets of
  environment configuration.
- Both apps can be developed and tested independently.
