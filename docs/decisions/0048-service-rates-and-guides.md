# ADR 0048: Rates per service on the rate card, and the two guides

- Status: Accepted
- Date: 2026-11-26

## Context

The owner set the private-pay rates (2026-11-26). They are per service
level: a **booking fee**, a **loading fee**, a **per-mile rate** and a
**wait rate per block of minutes**. The rate card (ADR 0039) had one
per-mile rate and one per-minute wait rate for every service, no
loading fee, and charged the base rate on each ride of a round trip.

The owner decided (2026-11-26):
- **the amounts are not public.** They are entered in the admin and are
  deliberately not written in this repository, the website or these
  documents;
- **administrators add and edit every rate by hand** in the admin;
  nothing is hard-coded;
- on a round trip the **booking and loading fees are charged once**, and
  the miles both ways;
- wait time is **optional**, the **first 15 minutes are free**, and then
  **each started block is charged in full**;
- **no stretcher**, still (ADR 0044): the stretcher rates the owner
  listed are not entered anywhere;
- two guides: one for customers on the website, one for the office in
  the admin.

## Decision

**The rate card** (Billing → Rate cards) now has a section per service
(Ambulatory, Wheelchair, Medical supply & equipment delivery), each
with:
- the **booking fee** (the existing base rate; blank = not billed);
- a **loading fee**;
- its own **per-mile** rate;
- its own **wait rate** per block (not for deliveries, which have no
  ride back).

And for the whole card:
- **Round trips:** "charge booking and loading on the ride back". Ticked
  (the default) bills each ride in full, as before. Unticked charges the
  two fees once for the round trip.
- **Wait time:** the free minutes, the **block length in minutes**, and
  a wait rate used by any service without its own. Each started block
  is charged in full. A block of 1 is the old per-minute charge.
- Mileage: a per-mile rate used by any service without its own, and the
  included miles.

Every new field is optional and falls back to the card-wide rate, so a
card entered before this prices exactly as it did. A price change is
still a new card with a later start date.

**Invoices and emailed prices use the same rules** (`billing/pricing.py`):
- a one-way ride or the ride there: booking fee + loading fee + miles +
  after-hours;
- the ride back: the same, or with no booking and loading fee when the
  card charges them once;
- a wait-and-return ride back adds the wait, in blocks.

The emailed price for a wait-and-return ride says how many minutes of
waiting it covers (the free minutes plus the blocks bought) and the
rate per block after that.

**The owner's rates are a private-pay card**: no payer, the round-trip
box unticked, 15 free minutes, 15-minute blocks, and the four amounts
for ambulatory and for wheelchair.

**Office guide** (Admin → Operations → Office guide). One page, for
every staff member: request, price, confirmation, schedule, assign,
drive, bill, with who does each step and where to click. Billing links
show only to staff who can use them.

**How It Works** (website, `/how-it-works`, in the menu and the
sitemap). The customer's version: request, price by email, confirm,
ride day, getting home; why a waiting driver helps; the facility
portal. **No prices**, and nothing about how drivers and vehicles are
vetted: the owner keeps the rates and the internal rules private
(2026-11-26). Those details are in the Office guide, behind staff login.

## Consequences

- A new **kind** of service (stretcher, for example) is not something an
  administrator can add from the rate card: it also needs the booking
  forms, vehicles and driver credentials to know about it.
- Charges other than the four above (a named extra fee) can't be added
  by an administrator yet.
- **Release:** `billing.0005` adds the rate card columns. Run
  `ops/migrate-production.ps1` before pushing. Then enter the
  private-pay rate card, or no price can be worked out.
