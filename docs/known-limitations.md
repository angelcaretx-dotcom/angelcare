# Known Limitations (Phase 1)

Tracked honestly so nothing here is discovered by surprise later.

- **No audit trail yet.** `TripRequest` has `created_at`/`updated_at`
  only. Status changes made by staff in `/admin/` are not individually
  logged with actor/timestamp/reason. A centralized audit system
  (Section 15 of the project directive) is planned for a later phase,
  once there are actual staff-facing state transitions worth auditing
  beyond simple admin edits.
- **No authentication/RBAC beyond Django's built-in admin auth.** There
  is no customer login, no driver app, no permission model yet — only
  Django staff accounts guarding `/admin/`.
- **No physical business address published**, per the owner's choice —
  revisit if that changes.
- **Privacy Policy and Terms of Use pages are drafts**, explicitly
  labeled "pending legal review" on the pages themselves. They describe
  actual data handling but are not attorney-reviewed and contain no
  pricing/cancellation/service-level terms (none have been defined).
- **No rate schedule, payer/broker integration, billing, or claims** —
  none of that exists yet; the site currently only captures and stores
  a transportation request for staff to follow up on manually.
- **DigitalOcean deployment is not yet provisioned** — `api/` runs
  locally (SQLite fallback) and has not been deployed. Production
  `DJANGO_SECRET_KEY`/`DATABASE_URL`/`DJANGO_CORS_ALLOWED_ORIGINS`
  need to be set when that happens (see `api/.env.example`).
- **Vercel deployment is not yet connected** to angelcaretransit.com's
  DNS — `web/` has not been deployed or pointed at the domain.
