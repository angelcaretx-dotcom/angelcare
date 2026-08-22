# AngelCare Transit

Non-Emergency Medical Transportation (NEMT) platform for AngelCare Transit,
serving the state of Texas.

This repository is a monorepo containing two independently deployable
applications:

| Path | What it is | Deploys to |
|---|---|---|
| [`web/`](web/) | Public marketing site + trip request form (Next.js) | Vercel |
| [`api/`](api/) | Backend API and future operations platform (Django + PostgreSQL) | DigitalOcean |

See [`docs/`](docs/) for architecture, decisions, and business-requirements
records.

## Contact

- Phone: 817-766-9228
- Email: angelcaretx@gmail.com
- Service area: State of Texas

## Local development

See [`web/README.md`](web/README.md) and [`api/README.md`](api/README.md)
for setup instructions for each app.
