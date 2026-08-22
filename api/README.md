# AngelCare Transit — API

Backend for AngelCare Transit. Django + Django REST Framework +
PostgreSQL (SQLite fallback for local dev only). Deploys to DigitalOcean.

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
