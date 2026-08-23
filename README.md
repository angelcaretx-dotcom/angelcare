# AngelCare Transit

Non-Emergency Medical Transportation (NEMT) platform for AngelCare Transit,
serving the state of Texas.

This repository is a monorepo containing two independently deployable
applications:

| Path | What it is | Deploys to |
|---|---|---|
| [`web/`](web/) | Public marketing site + trip request form (Next.js) | Vercel |
| [`api/`](api/) | Backend API and future operations platform (Django + PostgreSQL) | Fly.io (DB: Supabase) |

See [`docs/`](docs/) for architecture, decisions, and business-requirements
records.

## Contact

- Phone: 817-766-9228
- Email: angelcaretx@gmail.com
- Service area: State of Texas

## Local development

Everything runs and is fully testable locally — no cloud account or
provider is required for development. `web/` and `api/` each run as
plain local dev servers, talking to each other over `localhost` exactly
as they will in production (only the URLs differ, via environment
variables). `docker-compose.yml` at the repo root brings up a local
PostgreSQL matching production; see [`api/README.md`](api/README.md)
and [`web/README.md`](web/README.md) for full setup instructions.

Production deployment (Vercel for `web/`, Fly.io + Supabase for `api/`)
is a separate, later concern — see [`docs/`](docs/) for how and why.
