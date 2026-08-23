# Known Limitations

Tracked honestly so nothing here is discovered by surprise later.

- **RBAC covers two roles only.** `Dispatcher` and `Administrator`
  (see ADR 0005) are the only staff roles that exist — grounded in
  what's actually needed today (reviewing trip requests), not
  speculative job titles. There is still no customer login and no
  driver-facing app; only staff `/admin/` access is role-gated.
- **Audit trail covers trip request status changes only.** `AuditLog`
  (ADR 0005) is general-purpose and ready for other domains, but only
  `TripRequest` status changes are wired into it so far — other admin
  edits (e.g. changing a phone number) aren't yet logged.
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
