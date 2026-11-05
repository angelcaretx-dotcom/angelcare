# ADR 0031: Rider accounts and "My trips" on the website

- Status: Accepted
- Date: 2026-11-04

## Context

The owner asked that a one-time booking create an account and email a
link to set a password, so riders can track their rides. Owner
decisions (2026-11-03, roadmap Phase 34): the account is offered on
every first booking from a new email; riders can see request status,
book again prefilled, cancel and edit their profile; it lives on
angelcaretransit.com. Plus three conditions:

1. **Booking stays account-free.** The account is an optional layer on
   top of a completed booking, never a gate in front of it.
2. **Facility bookers get no rider account.** A discharge planner's
   email would collect many patients' requests.
3. **Short idle timeout.** Riders and caregivers share tablets and
   facility computers.

## Decision

**Offering the account** (`riders/services.py`, `offer_account_for`,
called after every website booking, never able to fail it). The
booking form asks **"Who is this ride for?"** (myself / someone I care
for / a patient or resident at my facility; `TripRequest.booked_for`).
Facility bookings get nothing. Otherwise the first booking from a new
email (case-insensitive) creates a non-staff user with **no usable
password** plus a `RiderAccount` (name, phone, mobility needs copied
from the booking), and emails a **set-up link**. Later bookings from
that email attach to it; an unused invite is re-sent at most once a day.

**The link is the email verification.** It's Django's password-reset
token: valid 3 days (`PASSWORD_RESET_TIMEOUT`), single-use (void once
a password is set), and tied to the user's email. The account can't be
signed into until its owner uses the link, so typing someone else's
email into the form gives the typist nothing. The invite and reset
emails carry only the link: no name, trip or address (ADR 0027).
"Forgot password" answers the same whether or not the email has an
account, and re-sends the invite to unverified accounts.

**What an account sees**: the requests made **with its email**, except
facility-booked ones. It is never linked to a `Passenger` record (ADR
0006), because the email's owner may be a caregiver. For each request:
the reference, a rider-worded status (Received / Contacted / Confirmed
/ On the way / Completed / Missed / Declined / Cancelled), requested or
confirmed pickup time, addresses, and once assigned the driver's
**first name** and the vehicle. **Cancel** works until the pickup time
while the trip hasn't started: it cancels the request and its trip,
audits both (`source="rider_portal"`), and emails staff a PHI-free
alert. **Profile**: name, phone, mobility needs, up to 10 saved
addresses, used to prefill the booking form. **Book again** prefills
route and service from an earlier ride.

**Where it runs.** API: `riders/api.py` under `/api/v1/rider/` (JSON,
Django session auth). Pages: Next.js under `angelcaretransit.com/account`
(My trips, profile, sign in, set/reset/forgot password), `noindex`,
excluded from the sitemap and disallowed in robots.txt. The website
**proxies `/api/*` to the API** (a Next.js rewrite to
`NEXT_PUBLIC_API_URL`), so the rider pages call the API same-origin
and the session cookie belongs to angelcaretransit.com: no cross-site
cookies, no CORS credentials. The rewrite adds back the trailing slash
Django expects. The public booking form still posts to the API
directly, as before.

**Security.**
- CSRF on every write, **including sign-in and set-password** (no login
  CSRF): the page fetches the token cookie from `/csrf/` and sends it
  back as `X-CSRFToken`. The website's origins are in
  `CSRF_TRUSTED_ORIGINS`.
- Sign-in lockout after 5 failures per email in 15 minutes, counted in
  the audit log (`audit/lockout.py`, now shared with the driver app).
  The sign-in endpoints have their own, roomier rate limit
  (`rider_auth`, 300/hour) because behind the proxy the shared
  anonymous limit could be shared by everyone.
- **15-minute idle timeout**, enforced twice: the server session
  expiry slides on each request, and the page itself signs out and
  clears trip details from the screen after 15 minutes without
  activity.
- Rider views are logged like staff views (ADR 0027). Staff and driver
  logins are not rider sessions.
- A `localStorage` hint ("this browser has signed in") only decides
  whether the public booking form bothers asking the API who is signed
  in. It grants nothing.

**Staff side.** Admin → People → Rider accounts: look, resend an
unused set-up link, switch an account off (Administrators). Not created
or edited by staff. Dispatchers can view; it's an area on the staff
access page (ADR 0030).

## Consequences

- One account per email. A caregiver booking for two relatives sees both;
  that's what "requests made with this email" means, and it's their own booking history.
- Changing a ride online = cancel and book again (or call).
- Riders' requests now surface on the website, so the same BAA question
  as the rest of the stack applies (docs/hipaa.md), with Vercel serving
  the pages.
- Release: `notifications.0002`, `transportation.0004` and
  `riders.0001` change the database; run `ops/migrate-production.ps1`
  before pushing.
