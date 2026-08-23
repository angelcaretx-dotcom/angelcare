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
  none of that exists yet.
- **Notifications are email-only, plain text, no retry.** A new trip
  request emails staff and the customer once; if the send fails it's
  logged to `NotificationLog` (visible in `/admin/`) but not
  automatically retried. SMS/push are not implemented — the
  `EmailProvider` interface (`notifications/providers/`) is designed so
  adding them later doesn't require touching `NotificationService`'s
  callers.
- **`api/` is not yet deployed to production.** This is expected and
  not blocking anything — see ADR 0004: production hosting is a
  deliberately separate, later step from development, never a
  prerequisite for it. The full workflow (form -> API -> Postgres ->
  admin) is proven working locally end to end. When production
  deployment does happen: Supabase database is already live and
  migrated; the Fly app still needs its one-time setup (`fly apps
  create`, `fly secrets set`, `FLY_API_TOKEN` GitHub secret) done by
  the account owner — see `docs/decisions/0003-supabase-and-flyio.md`.
  Until then, `web/`'s trip request form has no live backend to submit
  to in *production* specifically (local development is unaffected).
- **`web/` is deployed** to Vercel (`angelcare` project, team `ACT`) and
  `angelcaretransit.com` DNS points at it. `NEXT_PUBLIC_API_URL` is not
  yet set in Vercel's environment variables — needs to be set once the
  API is deployed (above), pointing at wherever it ends up.
