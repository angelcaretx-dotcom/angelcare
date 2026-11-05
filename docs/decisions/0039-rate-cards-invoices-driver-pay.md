# ADR 0039: Rate cards, calculated invoices and driver pay

- Status: Accepted
- Date: 2026-11-05

## Context

Until now invoice amounts were typed by hand (ADR 0018), and driver pay
wasn't recorded. The owner asked (2026-11-05) for an admin-managed
system: administrators add, update and edit **rates for customers** and
**drivers' pay**. Nothing is to be hard-coded.

The owner decided:
- drivers can be paid **per trip, per mile, hourly and as a percentage
  of the trip charge**, in any mix per driver;
- **trip miles come from the import file or are typed by staff**;
- invoices are made by a **batch button as drafts to check**;
- **after-hours is set on each rate card**.

## Decision

**Rate cards** (`billing.RateCard`, Billing → Rate cards,
Administrators only). One card per payer per period: a start date and
an optional end date. A card with **no payer is the private-pay rate**.
It holds:
- a base rate per service level, where a blank means "not billed";
- a per-mile rate after any included miles;
- free wait minutes, then a per-minute rate. Wait-and-return rides
  only, measured by the driver app from the outbound drop-off to the
  return pickup;
- a no-show fee;
- an after-hours fee, with that card's own window and weekend rule, on
  the Texas clock.

A price change is a **new card with a later start date**. Two cards for
the same payer can't start on the same day.

**Trip miles** (`Trip.billable_miles`):
- filled from an import file's miles column (ADR 0038);
- otherwise typed on the trip page.

The odometer is per vehicle-day only (ADR 0034).

**Pricing** (`billing/pricing.py`). The card is the one for the trip's
payer that applies on the trip's Texas date:
- a **completed** trip pays base + miles + after-hours + wait;
- a **no-show** pays the no-show fee;
- nothing else is billed.

If a number the card needs is missing, the trip **isn't billed**, and
the reason is shown. Missing numbers include: miles on a per-mile card,
a wait the driver app didn't record, a service with no rate, and no card
at all.

**Invoices** (Billing → Create invoices). Pick a payer, or private pay,
and a date range. Every completed or no-show trip that isn't invoiced
yet becomes a **Draft** invoice. Each invoice records:
- its amount;
- the card it used;
- **how it was calculated**, line by line.

Trips that couldn't be billed are listed with the reason. A trip is
invoiced once, unless its invoice is voided. A later rate change never
rewrites an invoice. Hand-entered invoices still work. Creation is
audited.

**Driver pay plans** (`billing.DriverPayPlan`, Billing → Driver pay
plans, Administrators only). Per driver per period, in any mix:
- a flat amount per trip, optionally a different one for wheelchair
  trips;
- per billable mile;
- hourly, from the start-of-day vehicle check to the end-of-day
  odometer;
- a percentage of the trip charge: the trip's invoice, or what its rate
  card gives.

Completed and no-show trips earn pay.

**Pay statement** (Billing → Driver pay statement). Pick a driver and a
date range to see each trip's pay lines, each day's hours and the total,
with **CSV download**. Gaps are flagged as "not paid" rather than
guessed, for example missing miles or a day with no end odometer.
Nothing is stored. Viewing is audited.

Every rate card and pay plan add or change is audited with its values.
Rates and pay are visible to Administrators only.

## Consequences

- Rate cards hold the payers' real contract numbers; administrators
  enter them. No price appears in the code or on the website.
- Wait time stays 15 minutes free then $1/minute for private and
  facility cards (business decision, 2026-11-04/05), entered on those
  cards. Broker cards set their own.
- Still to come:
  - from Phase 29: invoices in each payer's own format, and prior-auth
    usage tracking;
  - from Phase 32: 1099 year-end totals and profit per trip.
- Release: `billing.0002` and `trips.0008` change the database. Run
  `ops/migrate-production.ps1` before pushing.
