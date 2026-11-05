# ADR 0035: Standing orders (recurring trips)

- Status: Accepted
- Date: 2026-11-05

## Context

Many NEMT rides repeat: dialysis three times a week, weekly therapy.
Typing each trip by hand is slow, and easy to get wrong.

The owner decided (2026-11-05):
- trips are created **14 days ahead**;
- the rider's **regular driver and vehicle** are given to each trip
  when they're available;
- the ride home has the **same options as a booking** (ADR 0032);
- staff are **warned 14 days before a prior authorization ends**, and
  no trips are created after it ends.

## Decision

**`StandingOrder`** (Operations → Standing orders) holds a repeating
ride:
- the rider, service and route;
- the weekdays, pickup time and optional appointment time, in Texas
  time;
- the ride back: none, at a set time, will-call, or driver waits;
- start date, optional end date, and an **active** box to pause it;
- source, payer (blank means the passenger's payer), and the prior
  authorization number and end date;
- a regular driver and vehicle, and notes copied to each trip.

**Skipped dates** (holiday, hospital stay) are rows on the order. The
series carries on around them.

**Trips** are ordinary trips that dispatch can change like any other.
- Each records the order and date it came from (`standing_order`,
  `standing_date`), and links back to the order from its Record section.
- A database constraint makes each leg once per date, so creating them
  is safe to repeat.
- Creation (`trips/standing.py`) covers the next 14 Texas days, from the
  start date. It never goes past the end date or the authorization's end
  date, and never includes a skipped date or a pickup time that has
  already passed.
- The regular driver and vehicle are kept only if they pass the usual
  trip checks: active, license, registration and inspection valid, and
  not out of service. Otherwise the trip is left unassigned for
  dispatch.
- The ride back uses the same builder as website round trips
  (`returns.return_leg_for`).

**When trips are created:**
- **On save** of an order. Upcoming trips that nobody has touched are
  re-made to match the order.
  - A date where dispatch changed either leg, or a driver started it,
    is **kept as is**, and the admin says how many were kept.
  - A kept trip on a newly skipped date is **cancelled** (audited), not
    deleted.
- **Every morning**: Vercel Cron calls `GET /api/v1/cron/daily/` (renamed from `/cron/standing-orders/` when ADR 0036 added expiry reminders to the same job)
  at 10:00 UTC (about 4–5 AM Texas time).
  - The call must carry `Authorization: Bearer $CRON_SECRET`. Without
    `CRON_SECRET` set, the endpoint refuses to run.
    `ops/set-cron-secret.ps1` creates and sets it.
  - The job tops up every active order. One failing order doesn't stop
    the others.
  - It emails staff once per end date when an authorization ends within
    14 days. The email has the order number and date only: no rider
    name (ADR 0027).
- **By hand**: the *Create upcoming trips now* action on the order list.

The order page shows the upcoming trips, and the authorization's state:
"renew soon" from 14 days before, "ended — no new trips" after.

Dispatchers can view, add and change orders and skipped dates.
Administrators can also delete. Orders appear in the "Trips, day board
& standing orders" access area. Viewing an order is logged (ADR 0027).

## Consequences

- "Untouched" means not saved since it was created (within 2 seconds)
  and no driver-app steps. An edit made through the admin always counts
  as a change.
- A paused or ended order stops making new trips. Trips already
  created stay on the board until dispatch cancels them.
- Vercel Hobby crons run once a day at an approximate time. That's
  fine: the horizon is 14 days.
- Release:
  - `trips.0007` and `notifications.0005` change the database. Run
    `ops/migrate-production.ps1` before pushing.
  - After the push, run `ops/set-cron-secret.ps1` once.
