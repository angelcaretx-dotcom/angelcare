# ADR 0029: Driver app — sign-in, manifest, timestamped and located trip steps

- Status: Accepted
- Date: 2026-11-03

## Context

Every payer audits **proof of service**: when the driver arrived, when
the rider got in and out, and where. Until now a trip's status was
whatever a dispatcher typed in the admin. Drivers use their own
phones, a mix of Android and iPhone (owner decision, roadmap), and
sign in with their **email or phone number** (owner decision,
2026-11-03).

## Decision

**A mobile web app at `/driver/`** on the admin domain (`driver_app`),
rendered by Django with its own small inline stylesheet: no app store,
no build step, no extra static files. It can be added to the home
screen. Big buttons, one obvious next action.

**Sign-in** (`driver_app/auth.py`). A driver's login is a plain,
non-staff `auth.User` linked from `Driver.user`. Drivers type the email
or phone on their Driver record (any phone format: digits are
normalized, a leading US `1` dropped) plus their password, and
`DriverLoginBackend` resolves it to exactly one driver with a login
(ambiguous matches fail). The backend only answers calls made with
`identifier=`, so the admin's username login is untouched, and a
driver login can't open the admin (not staff). Inactive, suspended or
terminated drivers can't sign in, and an already signed-in driver is
signed out on their next tap. No authenticator app (owner decision);
instead:

- **Lockout**: 5 failed attempts for an email/phone within 15 minutes
  blocks that identifier for the rest of the window. Failures are
  counted in the audit log (`driver_login_failed`), so this works on
  serverless, where in-memory counters don't survive between requests.
- **Idle timeout**: 60 minutes without a tap signs the driver out.
- Drivers see only **their own** trips, for **today** (Texas day) plus
  anything still in progress; every other trip is a 404.
- Viewing the manifest or a trip is logged like an admin view (ADR 0027).

**Giving a driver a login** (Driver admin page, *Driver app login →
Create app login / Reset password*; Administrators only). Same handover
model as staff onboarding (ADR 0025): a temporary password shown once
on a printable sheet (`no-store`), never emailed or texted, with
first-sign-in steps (choose a password, add to home screen, allow
location). The driver must replace it before seeing any trip.
Refused if another driver has the same email or phone, since sign-in
must know who it is.

**Trip steps** (`trips/events.py`, `TripEvent`). The trip page shows
the rider, mobility notes, dispatch notes, a call button, addresses,
**directions** (a Google Maps link that opens the maps app on both
platforms, to the pickup before pickup and the drop-off after), and
only the step(s) allowed next:

```
(start) → En route → Arrived at pickup → Picked up → Dropped off
                     Arrived at pickup → No-show / Cancelled at the door (reason required)
```

Skipping *En route* is allowed (the driver may already be there).
Each step is a `TripEvent` with **server time** (the phone's clock is
never trusted), the phone's **GPS** latitude/longitude/accuracy,
reason, note and who recorded it. The page asks the browser for a
position on each tap. If location is off or takes over 8 seconds, the
step is **still saved**, flagged "no location", and the driver is told
to turn location on: a driver is never stuck at a door because of GPS.
Out-of-range coordinates are dropped. Steps move the trip's status:
first step → In progress; Dropped off → Completed; No-show → No show;
Cancelled at the door → Cancelled (each audited). A trip cancelled or
completed by dispatch takes no further steps.

`TripEvent` is **append-only**: no add/change/delete in the admin, and
staff roles get view permission only. It's proof of service, so a
mistaken tap is explained in the trip's notes, never edited away.
Dispatchers see the timeline (with map links) on the trip page and the
latest step under each trip on the day board.

## Consequences

- The day board shows progress, but doesn't push updates: refresh to see new steps.
- No signature, odometer/mileage or vehicle inspection yet: Phase 25.
- No offline mode. A tap without signal fails and the driver taps again
  when back in coverage; the time recorded is when it reached the server.
- Locations are PHI (where a rider lives and goes): they live only in
  the database, behind staff login and the audit trail.
- Release: `drivers.0002` and `trips.0004` change the database; run
  `ops/migrate-production.ps1` before pushing.
