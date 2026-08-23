# AngelCare Transit — API

Backend for AngelCare Transit. Django + Django REST Framework +
PostgreSQL. Fully runnable and testable locally — no cloud account or
provider is required for development. See "Deployment" at the bottom
for production, which is a separate, later concern.

## Setup (local development)

1. Start a local PostgreSQL, matching what production uses (from the
   repo root, requires Docker):

   ```bash
   docker compose up -d
   ```

   No Docker available? The app also runs against SQLite with zero
   extra setup — see `.env.example`. Postgres is preferred so local
   behavior matches production, but it's not a hard requirement to
   start developing.

2. Set up the app:

   ```bash
   python -m venv .venv
   .venv/Scripts/activate        # Windows
   # source .venv/bin/activate   # macOS/Linux

   pip install -r requirements.txt
   cp .env.example .env
   # if using docker compose above, uncomment the DATABASE_URL line in .env
   # pointing at localhost:5432 (see .env.example for the exact value)

   python manage.py migrate
   python manage.py createsuperuser   # to access /admin/
   python manage.py runserver
   ```

API is served at `http://localhost:8000/`. Admin at `/admin/`.

The frontend (`web/`) talks to this exact same API over HTTP
(`NEXT_PUBLIC_API_URL=http://localhost:8000` in `web/.env.local`) —
there is no separate mock/stub backend for local dev. The request path
is identical to production, only the URL differs.

## Scripts

- `python manage.py test transportation` — run tests
- `python manage.py check` — Django system checks
- `python manage.py makemigrations` / `migrate` — schema migrations

## Structure

- `config/` — project settings, root URLconf
- `passengers/` — real passenger records (Phase 4): name, contact,
  emergency contact, mobility profile, status. See
  `docs/decisions/0006-passenger-domain.md` for scope.
- `drivers/`, `vehicles/` — driver and vehicle records (Phase 5). See
  `docs/decisions/0007-driver-vehicle-domains.md` for scope.
- `trips/` — actual scheduled trips (Phase 6): links a `TripRequest`
  (which must already be linked to a `Passenger`) to an assigned
  `Driver` + `Vehicle`. Blocks assignment if the driver/vehicle isn't
  Active or a license/registration/inspection is expired. Creating a
  Trip auto-updates the source `TripRequest`'s status to `scheduled`.
  See `docs/decisions/0008-trip-lifecycle-and-dispatch.md`.
- `transportation/` — trip request intake domain (Phase 1). Public,
  create-only API at `POST /api/v1/trip-requests/`. Staff review
  submissions via `/admin/`, and can optionally link a request to a
  `Passenger` there (autocomplete search) — never automatic.
- `notifications/` — email notifications (Phase 2). `NotificationService`
  + a swappable `EmailProvider` interface (Section 17 pattern) send a
  staff alert and a customer confirmation on every new trip request,
  and log every attempt to `NotificationLog` (visible in `/admin/`) so
  a silent delivery failure is never invisible.
- `audit/` — general-purpose audit trail (Phase 3). `record_change()`
  is called from anywhere a sensitive change happens (currently:
  trip request status changes in `/admin/`) and logs actor, before/
  after, source, and timestamp to `AuditLog`. Entries are view-only in
  `/admin/` — not editable or deletable.
- `accounts/` — staff roles (Phase 3). Seeds `Dispatcher` (view/change
  trip requests) and `Administrator` (full access) as Django Groups
  with real permissions — see `docs/decisions/0005-rbac-and-audit-foundation.md`.
  `python manage.py createsuperuser` is for full admins only. For a
  scoped role: as an existing Administrator/superuser, create a regular
  `User` with `is_staff=True` via `/admin/auth/user/` and add them to
  the `Dispatcher` group.
- `healthz/` — unauthenticated health check endpoint

Domains beyond `transportation` (organization, passengers, drivers,
vehicles, dispatch, billing, claims, compliance, etc.) are not yet
built — see `../docs/architecture.md` for the planned phase order.

## Environment variables

See `.env.example`. Every environment-specific value (secrets, DB,
allowed hosts, CORS origins) comes from environment variables — nothing
provider-specific is hard-coded in application code. In production,
`DJANGO_SECRET_KEY` and `DATABASE_URL` are required (the app refuses to
start without them when `DJANGO_DEBUG` is off).

## Deployment (production — not required for local development)

`fly.toml` and `.github/workflows/deploy-api.yml` configure a Fly.io +
Supabase production deployment. These files are inert during local
development — nothing in `manage.py runserver`, the test suite, or the
app's own code reads or depends on them. They only take effect when the
GitHub Actions workflow actually runs on a push to `main`.

Fly.io/Supabase is the current production choice, not an architectural
requirement — see
[`docs/decisions/0003-supabase-and-flyio.md`](../docs/decisions/0003-supabase-and-flyio.md)
for why, and what it would take to swap it for a different host later
(the app's env-var-driven config is what makes that swap possible
without touching application code).
