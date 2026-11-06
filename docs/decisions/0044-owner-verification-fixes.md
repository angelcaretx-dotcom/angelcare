# ADR 0044: Fixes from the owner's production verification (Nov 4-6)

- Status: Accepted
- Date: 2026-11-06

## Context

The owner tested production from November 4 to 6 and sent a report with
ten findings. This records what changed in the code, and why the other
findings needed no code change.

## Decision

**No stretcher, anywhere (Finding 8).** AngelCare offers ambulatory and
wheelchair transport, plus medical supply and equipment delivery. It
holds no EMS license.
- `ServiceType.STRETCHER` is removed. The website, the facility portal
  and the API no longer offer or accept it.
- The rate card's `base_stretcher`, the vehicle's `stretcher_capable` and
  the stretcher check item are dropped.
- Trip imports still recognise stretcher codes, so they can say clearly
  that the row must be rejected.
- Website text no longer mentions it.

**Claims (Finding 2):**
- The TPI is optional. TMHP retired it in 2021. The `REF*G2` segment is
  sent only when a TPI is entered.
- The 837P files page and the Claims list now explain how to make a
  file: tick the Draft claims, then choose the action "Make an 837P file
  from the selected draft claims". Unfold hides the Action menu while the
  list is empty, which is why it couldn't be found.
- *Add 837P file* now leads to the Draft claims list instead of a 403.

**Trip imports (Finding 3):**
- A missing state is Texas.
- In CSV and Excel files, a title row above the header is skipped. The
  header is the first row filled at least half as wide as the widest of
  the first 20 rows.
- An address written on two lines in one Excel cell is split like a
  one-line address. A ZIP with no state is understood.
- The report counts trips, rides back included, as well as rows.
- On re-import, a row whose trip ID was rejected in an earlier import
  says so, and a person decides again. It isn't skipped, because a
  corrected row may come back.
- A will-call ride back keeps its estimated time, as ADR 0032 designed.
  The leg is marked will-call, and the office sets the time when the
  rider calls.

**Smaller fixes:**
- Help texts no longer say times are "stored in UTC". Staff screens show
  Texas time (Finding 1).
- The booking API refuses a drop-off that is the same as the pickup
  (Finding 7a).
- The dashboard's credentials panel title counts its expired and
  expiring rows (Finding 5). Expired credentials stay listed because
  they block assignments.
- **Admin history** (Django's own record of adds, changes and deletes,
  with each record's name as it was) is viewable by superusers
  (Finding 4).
- The SLA payer table heads its column "No-shows (rate)" (Phase 31).
- The prior-authorization page states the block (ADR 0043).

## Consequences

- **Migrations** `billing.0003`, `claims.0003`, `passengers.0005`,
  `transportation.0007`, `trips.0011` and `vehicles.0004` drop two
  columns and update choices and help texts. Run
  `ops/migrate-production.ps1` before pushing.
- Any existing record that still says `stretcher` keeps the value but
  shows it unlabelled. Production has no trips or passengers, so nothing
  is affected there.
