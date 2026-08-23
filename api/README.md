# AngelCare Transit — API

Backend for AngelCare Transit. Django + Django REST Framework +
PostgreSQL (SQLite fallback for local dev only). Deploys to Fly.io
(Docker), database hosted on Supabase — see
[`docs/decisions/0003-supabase-and-flyio.md`](../docs/decisions/0003-supabase-and-flyio.md).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
cp .env.example .env          # edit as needed; unset DATABASE_URL uses local SQLite
python manage.py migrate
python manage.py createsuperuser   # to access /admin/
python manage.py runserver
```

API is served at `http://localhost:8000/`. Admin at `/admin/`.

## Scripts

- `python manage.py test transportation` — run tests
- `python manage.py check` — Django system checks
- `python manage.py makemigrations` / `migrate` — schema migrations

## Structure

- `config/` — project settings, root URLconf
- `transportation/` — trip request intake domain (Phase 1). Public,
  create-only API at `POST /api/v1/trip-requests/`. Staff review
  submissions via `/admin/`.
- `healthz/` — unauthenticated health check endpoint

Domains beyond `transportation` (organization, passengers, drivers,
vehicles, dispatch, billing, claims, compliance, etc.) are not yet
built — see `../docs/architecture.md` for the planned phase order.

## Environment variables

See `.env.example`. In production, `DJANGO_SECRET_KEY` and
`DATABASE_URL` are required (the app refuses to start without them when
`DJANGO_DEBUG` is off), and `DJANGO_CORS_ALLOWED_ORIGINS` must include
the deployed frontend origin.

## Deployment

Deploys automatically via `.github/workflows/deploy-api.yml` on every
push to `main` that touches `api/` (tests run first; deploy only
happens if they pass). One-time setup (Fly app creation, secrets, and
the `FLY_API_TOKEN` GitHub secret) is documented step-by-step in
[`docs/decisions/0003-supabase-and-flyio.md`](../docs/decisions/0003-supabase-and-flyio.md)
and must be done once by the account owner before the first deploy
will succeed.
