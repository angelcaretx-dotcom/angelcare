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
- **Document uploads do not work in production at all** (ADR 0009,
  ADR 0011). Local filesystem storage works for local dev, but the API
  now runs on Vercel's serverless Python runtime, which has no
  writable persistent disk -- not even "until the next deploy" the
  way a container host would allow. A real object storage backend
  (Supabase Storage is the natural fit, already used for the database)
  is required before this feature is usable in production; until then
  attempting a document upload in production will fail. No
  version-chaining either — re-uploading creates a new `Document` row,
  but there's no explicit link to what it replaced.
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
- **`api/` is deployed to production** (ADR 0011): Vercel project
  `angelcare-api` (team `ACT`), database on Supabase via its
  connection pooler. Verified working end to end for real, including a
  real browser submitting the live trip request form on
  angelcaretransit.com and a real admin login. Two things are NOT
  fully production-ready yet, both flagged above and not silently
  broken: document uploads (no object storage backend) and real email
  sending (see below).
- **Production email is not actually sending yet.** `DJANGO_EMAIL_BACKEND`
  is set to the console backend in production (logs instead of
  sending) because no real SMTP credentials have been provided. New
  trip requests are still saved correctly; staff/customer notification
  emails are not actually delivered until real SMTP config (or a
  provider account) is supplied.
- **`web/` is deployed** to Vercel (`angelcare` project, team `ACT`),
  `angelcaretransit.com` DNS points at it, and `NEXT_PUBLIC_API_URL`
  points at the production API above.
