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
   python manage.py collectstatic --noinput   # required -- see note below
   python manage.py createsuperuser   # to access /admin/
   python manage.py bootstrap_totp <username>   # required -- see MFA note below
   python manage.py runserver
   ```

   **`collectstatic` is required even for local dev, not just
   production** (ADR 0016): the admin theme's logo is resolved via
   `django.templatetags.static.static()` directly in
   `config/settings.py` (not the `{% static %}` template tag), which
   always goes through `STORAGES["staticfiles"]` (WhiteNoise's
   manifest storage) regardless of `DJANGO_DEBUG` -- every `/admin/`
   page, including the login page, 500s without having run this at
   least once. Re-run it after adding or changing any static file.

API is served at `http://localhost:8000/`. Admin at `/admin/`.

**`/admin/` requires MFA (ADR 0014)**: a password alone isn't enough --
every staff account needs a confirmed TOTP device. `createsuperuser`
doesn't create one, so `bootstrap_totp` above is required before you
can actually log into `/admin/` locally; it prints a QR code (and the
raw `otpauth://` URL, if your terminal can't render the QR) to scan
into an authenticator app (Google Authenticator, Authy, 1Password,
etc.).

The frontend (`web/`) talks to this exact same API over HTTP
(`NEXT_PUBLIC_API_URL=http://localhost:8000` in `web/.env.local`) —
there is no separate mock/stub backend for local dev. The request path
is identical to production, only the URL differs.

## Scripts

- `python manage.py test transportation` — run tests
- `python manage.py check` — Django system checks
- `python manage.py makemigrations` / `migrate` — schema migrations
- `python manage.py bootstrap_totp <username> [--replace]` — enroll (or
  re-enroll) a staff account's MFA device; required before that account
  can log into `/admin/` — see `docs/decisions/0014-staff-mfa.md`

## Structure

- `config/` — project settings, root URLconf
- `organization/` — single `Organization` record for AngelCare Transit
  itself (Phase 8), seeded from `docs/business-decisions-log.md`. Does
  not replace `web/`'s own static config — see
  `docs/decisions/0010-organization-domain.md`.
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
- `documents/` — file attachments (Phase 7) for any model via Django's
  ContentType framework, with a pending/verified/rejected review
  workflow. Upload from the Driver/Vehicle admin page directly (an
  inline); verify/reject in the standalone Documents list. Local
  filesystem storage in dev; production uses Supabase Storage
  (`documents/storage.py`) — verified working end to end for real, see
  `docs/decisions/0009-document-domain.md` and
  `docs/decisions/0012-supabase-storage.md`.
- `transportation/` — trip request intake domain (Phase 1). Public,
  create-only API at `POST /api/v1/trip-requests/`. Staff review
  submissions via `/admin/`, and can optionally link a request to a
  `Passenger` there (autocomplete search) — never automatic.
- `notifications/` — email notifications (Phase 2). `NotificationService`
  + a swappable `EmailProvider` interface (Section 17 pattern) send a
  staff alert and a customer confirmation on every new trip request,
  and log every attempt to `NotificationLog` (visible in `/admin/`) so
  a silent delivery failure is never invisible. Every email is branded
  HTML (with a plain-text alternative) — see
  `docs/decisions/0015-branded-html-emails.md`. Adding a new
  notification type: add a `NotificationType` choice, a `.txt` +
  `.html` template pair under `templates/notifications/` (the `.html`
  extends `email_base.html`), and a `_send_to_...` method in
  `NotificationService`.
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
  the `Dispatcher` group. Also (Phase 9) enforces TOTP-based MFA on
  every staff account for `/admin/` access — see
  `docs/decisions/0014-staff-mfa.md`. New accounts need
  `manage.py bootstrap_totp <username>` run once before they can log in.
  Also (Phase 11) hosts `AngelCareAdminSite`
  (`accounts/admin_site.py`) — combines django-unfold's admin theme
  with MFA into the actual `admin.site` instance, registered via
  `accounts/apps.py::AngelCareAdminConfig` — see
  `docs/decisions/0016-unfold-admin-theme.md`. Sidebar navigation and
  branding are configured in `config/settings.py`'s `UNFOLD` dict.
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

Deployed to **Vercel** (project `angelcare-api`, team `ACT`), running
via its Python serverless runtime (`wsgi_app.py`, `vercel.json`) —
database on **Supabase**, connected through its connection pooler
(required: Supabase's direct connection is IPv6-only, which Vercel's
runtime can't reach — see ADR 0011). Verified working end to end,
including a real trip request submitted through the live site and a
real admin login.

Vercel/Supabase is the current production choice, not an architectural
requirement — see
[`docs/decisions/0011-vercel-hosted-api.md`](../docs/decisions/0011-vercel-hosted-api.md)
for why (it replaced an earlier Fly.io attempt that hit repeated
account-verification friction), and what it would take to swap it for
a different host later (the app's env-var-driven config, unchanged
since ADR 0002, is what makes that swap possible without touching
application code).

**Known gaps in production** (see known-limitations.md): document
uploads don't work (no object storage backend configured yet — Vercel
has no persistent disk), and email sending uses the console backend
(no real SMTP credentials provided yet, so notifications log instead
of sending).

To deploy a change: `vercel deploy --prod --scope act-c1d1` from the
repo root (for `web/`) or from `api/` (for the API). Environment
variables are managed via `vercel env` — see ADR 0011 for what's set.
After a schema change, run `manage.py migrate` against the production
`DATABASE_URL` by hand; there's no automatic migrate-on-deploy hook.

After deploying ADR 0014 (staff MFA) for the first time, every existing
staff account needs `manage.py bootstrap_totp <username>` run once
against the production database before they can log into `/admin/`
again — the printed QR/URL contains a live secret, so hand it to the
account holder over a channel that isn't logged or persisted (not
email, not a ticket comment).
