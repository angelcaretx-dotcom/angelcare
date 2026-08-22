# ADR 0002: Hosting and stack choice

- Status: Accepted
- Date: 2026-08-22

## Context

Needed a concrete stack and hosting target to start Phase 1. Options
considered: Node/TypeScript (NestJS) backend vs. Python/Django backend;
AWS/Azure vs. DigitalOcean vs. Render/Railway for hosting; single deploy
target vs. split frontend/backend.

## Decision

- Backend: **Django + PostgreSQL**, chosen by the business owner. Mature,
  batteries-included (admin, ORM, migrations), long track record, common
  in healthcare-adjacent backends.
- Frontend: **Next.js**, deployed to **Vercel** (owner already intends to
  use Vercel).
- Backend hosting: **DigitalOcean**, run as a Dockerized service with a
  managed PostgreSQL instance. Chosen over AWS/Azure to avoid operational
  overhead not currently justified by scale, and over generic shared
  hosting because Django+PostgreSQL requires a real application server.
  Docker keeps the deployment portable to another host later if needed.

## Consequences

- The backend is not deployable to Vercel directly (Vercel's serverless
  model doesn't fit a persistent Django app well) — this is why the split
  in ADR 0001 exists.
- DigitalOcean-specific deployment config (e.g. App Platform spec or
  droplet provisioning scripts) will live under `api/deploy/`, isolated
  from application code, so switching hosts later only touches that
  directory.
- Secrets (DB credentials, `SECRET_KEY`, etc.) are supplied via
  environment variables only, never committed — see `api/.env.example`.
