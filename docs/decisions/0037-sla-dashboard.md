# ADR 0037: SLA dashboard

- Status: Accepted
- Date: 2026-11-05

## Context

Brokers grade transportation providers on two things:
- **acceptance**: how many offered trips we take (SafeRide and MTM
  expect about 95%);
- **on-time pickup**.

They act on the grades with fewer offers, or removal from the network.
Until now nobody here could see those numbers before the broker did.
The data already exists: trips, driver-app timestamps (ADR 0029) and
logged turn-downs (ADR 0028).

## Decision

**Operations → On-time & SLA** (`/admin/trips/trip/sla/`,
`trips/sla.py`). Anyone who can view trips can open it. It shows one
week, Monday to Sunday in Texas time, with links to the previous and
next weeks.

**By payer table.** One row per payer, plus an all-payers total. A trip
or turn-down without a payer is grouped by its source, e.g. "SafeRide".
- **Trips** scheduled that week, with completed, no-shows (count and
  share of rides that happened or should have) and cancelled.
- **On time:** the driver tapped *Arrived at pickup* (or *Picked up*, if
  arrival was skipped) no later than **15 minutes** after the scheduled
  pickup.
  - Will-call rides have no fixed time and aren't measured.
  - Completed or no-show trips with no arrival tap appear as **No arrival
    tap**, so gaps in proof are visible rather than silently dropped.
- **Acceptance** = trips taken on that week ÷ (taken + turned down),
  using creation and offer dates.
  - A round trip counts once: return legs are left out.
  - Trips made by standing orders (ADR 0035) aren't offers.
- **Red** when on-time or acceptance is below **95%**.

**Two side panels:**
- turn-downs by reason that week;
- the last 8 weeks' totals (trips, on-time %, acceptance %), each
  linking to its week.

The 15-minute window and the 95% target are constants
(`ON_TIME_GRACE`, `TARGET_PERCENT`). They are industry defaults, not
contract terms. Change them if a broker's contract says otherwise.

## Consequences

- The numbers are only as good as the drivers' taps and the logged
  turn-downs. A turn-down that isn't logged makes acceptance look better
  than the broker's figure.
- Acceptance uses our creation date as the offer date. Once broker
  import arrives (Phase 28), the broker's own offer date can replace it.
- Computed on each page view, with no stored figures and no database
  change. That's fine at this size. Revisit if it gets slow.
