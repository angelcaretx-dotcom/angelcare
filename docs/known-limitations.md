# Known Limitations

Tracked honestly so nothing here is discovered by surprise later.

- **RBAC covers two roles only.** `Dispatcher` and `Administrator`
  (see ADR 0005) are the only staff roles that exist — grounded in
  what's actually needed today (reviewing trip requests), not
  speculative job titles. There is still no customer login and no
  driver-facing app; only staff `/admin/` access is role-gated.
- **Audit trail covers status changes only.** `AuditLog` (ADR 0005) is
  general-purpose and ready for other domains, but only `TripRequest`
  and `Passenger` status changes are wired into it so far — other
  edits (e.g. changing a phone number) aren't yet logged.
- **Passenger domain is intentionally minimal** (ADR 0006): no
  authorized representatives, no facility/payer/document relationships,
  no field-level edit history beyond status. Trip requests are never
  auto-linked to a passenger — staff link them manually in `/admin/`.
- **Driver/Vehicle domains are intentionally minimal** (ADR 0007): no
  insurance, background checks, training/certification records,
  maintenance/mileage history, or incident tracking.
- **Trip status is intentionally coarse** (ADR 0008): `SCHEDULED ->
  IN_PROGRESS -> COMPLETED` (+ `CANCELLED`/`NO_SHOW`), not the project
  directive's full example machine (`ARRIVED`, `PASSENGER_ONBOARD`,
  etc.) — those assume a driver-facing app reporting real-time status,
  which doesn't exist. Credential-expiration blocking IS enforced now
  (Driver license, Vehicle registration/inspection) since assignment
  is now a real feature.
- **No recurring trips.** Each `Trip` is one-to-one with one
  `TripRequest`; recurring transportation (Section 4) needs its own
  request/trip pair per occurrence for now.
- **Document uploads now work in production** (ADR 0012): Supabase
  Storage, via its own Storage REST API (not the S3-compatible
  endpoint — those access keys weren't readily locatable in the
  dashboard). Verified end to end for real: a real browser uploading
  through the production admin, confirmed to actually land in the
  Supabase bucket, with a working signed-URL link back to it. Local
  dev still uses plain filesystem storage (ADR 0004). No
  version-chaining still — re-uploading creates a new `Document` row,
  but there's no explicit link to what it replaced.
- **No physical business address published**, per the owner's choice —
  revisit if that changes.
- **Privacy Policy and Terms of Use pages are drafts**, explicitly
  labeled "pending legal review" on the pages themselves. They describe
  actual data handling but are not attorney-reviewed and contain no
  pricing/cancellation/service-level terms (none have been defined).
- **No rate schedule, payer/broker integration, billing, or claims** —
  none of that exists yet.
- **Notifications are email-only, no retry.** A new trip request emails
  staff and the customer once; if the send fails it's logged to
  `NotificationLog` (visible in `/admin/`) but not automatically
  retried. SMS/push are not implemented — the `EmailProvider` interface
  (`notifications/providers/`) is designed so adding them later doesn't
  require touching `NotificationService`'s callers. Emails are now
  branded HTML (ADR 0015) with a plain-text alternative, not plain text
  only.
- **`api/` is deployed to production** (ADR 0011): Vercel project
  `angelcare-api` (team `ACT`), database on Supabase via its
  connection pooler. Verified working end to end for real, including a
  real browser submitting the live trip request form on
  angelcaretransit.com and a real admin login.
- **Production email is configured (Resend SMTP) but not yet fully
  active.** `DJANGO_EMAIL_BACKEND` points at Resend's SMTP relay with a
  real API key, sending from `notifications@angelcaretransit.com`, but
  that domain is still finishing DNS verification on Resend's side as
  of 2026-08-26 — real sends currently fail with "domain is not
  verified" until that completes (no code/config change needed once it
  does; it'll just start working). New trip requests are still saved
  correctly either way — only the notification email is affected.
- **`web/` is deployed** to Vercel (`angelcare` project, team `ACT`),
  `angelcaretransit.com` DNS points at it, and `NEXT_PUBLIC_API_URL`
  points at the production API above.
- **CI (ADR 0013) runs tests on every push but doesn't block deploys.**
  Vercel deploys on push independent of GitHub Actions status; a
  failing test currently means a red X in GitHub, not a stopped
  deploy. Real gating needs branch protection + a pull-request-based
  workflow, which this repo doesn't use yet (direct pushes to `main`).
- **Admin theme (ADR 0016) is visual/navigational only, no custom
  dashboard yet.** `/admin/` uses django-unfold for branding and a
  curated sidebar, but the "Site administration" landing page after
  login still shows Django's stock flat app-list — a real KPI
  dashboard (e.g. "N new trip requests today") is possible via
  Unfold's `DASHBOARD_CALLBACK` but wasn't built, deliberately, to keep
  that phase scoped to theming. `django-unfold` is pinned to `0.96.0`,
  not latest, since newer releases require Django 5.2+ and this
  project is still on 5.1.15.
- **Staff MFA (ADR 0014) has no self-service enrollment or recovery.**
  Every `TOTPDevice` is created out-of-band via
  `manage.py bootstrap_totp <username>`, run by whoever has
  server/database access — there's no in-app "set up your
  authenticator" flow yet, and losing a device means someone with that
  access re-running the command with `--replace`. No backup/static
  recovery codes (django-otp's `otp_static` plugin covers that; not
  added yet, out of scope for the baseline MFA requirement). Applies to
  every staff account (Dispatcher and Administrator), not just
  superusers.
