# ADR 0041: Trip profit and 1099 year-end totals

- Status: Accepted
- Date: 2026-11-06

## Context

Phase 32 asked for a pay statement per driver (built in ADR 0039), plus
two things still missing:
- **1099 year-end totals** for contractor drivers;
- **profit per trip and per payer**: revenue, minus driver pay, minus
  mileage cost.

Everything these need is already recorded:
- each driver's Employee / Contractor field;
- pay plans;
- invoices and rate cards;
- each trip's billable miles.

## Decision

Two read-only reports under **Billing**. Nothing is stored; each opens
as a page and downloads as a CSV.

**Trip profit** (`billing/reports.py`, `profit`; *Billing → Trip
profit*). For completed and no-show trips in a date range (Texas
dates), each trip has:
- **charge**: its invoice, or the rate card's figure when it has no
  invoice yet. This is the same rule the percent-of-charge pay uses.
- **driver pay**: the per-trip, per-mile and percent parts of the
  driver's pay plan on that date.
- **mileage cost**: billable miles × an optional *cost per mile* typed
  on the page. Fuel, wear and insurance aren't recorded anywhere, so
  this isn't a setting.
- **profit** = charge − pay − mileage cost.

Trips are grouped by payer, with private pay as its own row. Each row
shows billed, pay, mileage cost, profit and margin.

**Hourly pay** is earned per day on duty, not per trip. It's summed
from the daily vehicle checks and comes off the total, not off any one
trip.

**A trip missing any number isn't counted.** That includes a trip with
no driver, no pay plan, no miles where miles are needed, or a charge
that can't be worked out. Such trips are listed with the reason; no
number is guessed.

**1099 year-end totals** (`year_end`; *Billing → 1099 year-end
totals*). For a calendar year it lists each **Contractor** driver with
their pay, worked out exactly as the pay statement does. It marks
whether the pay reaches the reporting threshold, and flags trips or
days that couldn't be paid.

- **The threshold defaults to the IRS 1099-NEC floor:**
  - $600 for payments through 2025;
  - $2,000 from 2026, the change made by the 2025 tax law. It's
    inflation-indexed after 2026.

  It can be typed over, and the page says to confirm it with the tax
  preparer.
- **Employees aren't listed.** They get W-2s from payroll.

Both reports need Billing permissions: invoices and pay plans for the
profit report, pay plans for the year-end totals. In practice that
means Administrators. Each view is audited.

## Consequences

- **Year-end totals show pay earned, not cash paid.** The system
  doesn't record payments to drivers. A 1099-NEC reports what was
  *paid* in the year, so the CSV must be checked against payment
  records, especially for late-December trips paid in January.
- **Tax IDs and addresses aren't stored.** They come from each
  contractor's W-9, kept by whoever files the 1099s. No SSNs or EINs
  are kept here.
- **No deadhead miles.** Empty miles between trips aren't known:
  - the odometer is per vehicle-day (ADR 0034);
  - map estimates wait on the maps-provider decision.

  So mileage cost is loaded miles only, and profit is somewhat
  overstated for spread-out days.
- **Charges are what was billed**, not what was collected. Denials and
  short payments (ADR 0040) aren't subtracted.
- No database changes.
