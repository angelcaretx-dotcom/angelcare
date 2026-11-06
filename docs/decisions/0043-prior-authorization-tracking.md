# ADR 0043: Prior authorization tracking

- Status: Accepted
- Date: 2026-11-06

## Context

Roadmap Phase 29 still listed "prior-auth tracking: authorized trips
used vs. remaining". A trip carried its authorization number as plain
text. Only standing orders knew an end date (ADR 0035), and nothing
counted how many trips had been used.

**Owner decisions, 2026-11-06:**
- When an authorization is used up or out of dates, **block** further
  trips under it.
- When one runs low, show it on the **dashboard and in the morning
  email** to the office.

## Decision

**Prior authorizations** (`trips.PriorAuthorization`; Admin →
Operations → Prior authorizations). Each record holds:
- the number exactly as the payer issued it, unique per payer;
- the rider and the payer;
- the first and last day it covers;
- how many trips it covers;
- **how the payer counts them:**
  - each one-way trip, where there and back is 2 (the default);
  - or each round trip, where there and back is 1.

Dispatchers can add and edit them, since entering an authorization is
part of booking. Changes are audited.

**Usage** (`trips/authorizations.py`):
- A trip counts when it carries the number (matched ignoring case) and
  is for the same rider. Cancelled trips don't count; scheduled,
  in-progress, completed and no-show trips do.
- On a round-trip authorization, return legs aren't counted.
- The authorization page lists every trip counted and shows *used / of
  / left*. The trip page shows the same line beside the number.

**The block.** Trip validation (`Trip.clean`) refuses a trip under a
tracked authorization that:
- is for another payer or another rider;
- has a pickup, on its Texas date, outside the covered dates;
- has no trips left.

The check runs only when the number, rider, payer, pickup time or
status changes. That way trips already booked under it keep saving,
for example when dispatch assigns a driver. Every path that validates
a trip gets this check, so it applies to:
- the admin;
- the day board;
- the ride back;
- the trip import.

**Standing orders** stop creating trips at the first date with no room
under the authorization. A day with a ride back needs room for both
legs on a per-leg authorization. This is the same as their existing
stop at the authorization's end date.

**A number not entered as a prior authorization isn't tracked or
blocked.** That keeps existing trips and payers that don't need
authorizations working unchanged.

**Alerts.** An authorization is **running low** when it has 3 or fewer
trips left, or ends within 14 days.
- **The dashboard** lists running-low authorizations, with the rider's
  name.
- **The daily cron** emails the office once when one starts running
  low, and once when it's used up or has ended. Extending or renewing
  it resets that.
- **The email has no authorization number and no rider name.** It
  shows only the record reference (`PA-…`), the payer, what's left and
  the end date (ADR 0027).

## Consequences

- An authorization only protects against unpaid rides once someone
  enters it. The office should enter each one as it arrives.
- A standing order's own "authorization ends" date still works on its
  own. When both are set, the earlier limit wins.
- Overlapping authorizations with the same number for one payer aren't
  possible (the number is unique per payer). A renewal with the same
  number is an edit: extend the dates and trips.
- **Release:** `trips.0010` and `notifications.0008` change the
  database. Run `ops/migrate-production.ps1` before pushing.
