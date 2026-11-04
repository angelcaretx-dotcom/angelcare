# Roadmap: from records to operations (Phases 22–33)

Written 2026-11-03. Goal: be ready for HHSC and broker (MTM, SafeRide)
trips. Every payer audits the same chain -- **trip booked → assigned →
proven → billed** -- so every phase hangs off the existing `Trip`
record rather than adding a separate tool.

What exists today (Phases 0–21): website booking, trip requests, trips
with driver + vehicle, payers, invoices, claims, company compliance
records, document uploads, staff roles + MFA, audit log, dashboard,
email alerts, nightly encrypted backups. See [architecture.md](architecture.md).

Same rules as every phase so far: one phase = one ADR + tests + docs;
a schema change is migrated in production (`ops/migrate-production.ps1`)
before the push; vendors sit behind an interface.

## Stage A — Safe to carry real Medicaid data

### Phase 22: HIPAA hardening — ✅ done 2026-11-03 (ADR 0027)
- Staff alert email carries no rider details: "New request — log in to view" (customer's own confirmation stays as is).
- `docs/hipaa.md`: every service that touches PHI (Supabase, Vercel, Resend, GitHub backups, future maps vendor) with its BAA status, plan required, and who signed.
- Log who **viewed** a passenger/trip record (today only changes are audited).
- **Exit:** every PHI-holding vendor has a signed BAA or holds no PHI.

## Stage B — Proof of service (the driver side)

### Phase 23: Dispatch foundations — ✅ built 2026-11-03 (ADR 0028)
- `Trip.source` (website, phone, facility, MTM, SafeRide, HHSC, subcontract — AMTS / Gray Stone / American Medical Solutions, private pay) and `Trip.payer`.
- Broker trip number and prior-authorization number on the trip.
- Trip legs: appointment time vs. pickup time; will-call return trips.
- **Day board**: one page per day, trips grouped by driver, unassigned trips on top, quick assign.
- Turn-down log: a trip offered by a broker and declined, with a reason (feeds the SLA numbers in Phase 31).

### Phase 24: Driver app — the day
- **Login with email or phone number** + password (owner decision 2026-11-03). No authenticator app for drivers; instead lockout after repeated failures, idle timeout, and drivers only ever see their own trips.
- Driver logins (a Driver linked to a user account; phone-friendly sign-in).
- Mobile web app at `admin.angelcaretransit.com/driver/` — no app store, works on any smartphone, can be added to the home screen.
- Today's manifest in order; tap an address to open the phone's own maps app for directions.
- Status buttons, each stamped with server time + phone GPS: **En route → Arrived → Picked up → Dropped off**, or **No-show / Cancelled at door** with a reason code.
- New `TripEvent` record: the append-only timeline of a trip (who, what, when, where). Dispatcher sees it live on the day board.

### Phase 25: Driver app — the proof
- Rider signature (or "unable to sign" with reason) at pickup and/or drop-off.
- Odometer at pickup/drop-off → loaded miles; start-of-day odometer → deadhead miles.
- Pre-trip vehicle inspection checklist, once per vehicle per day; a failed item blocks the vehicle.
- Trip proof sheet (printable/PDF) per trip: timestamps, GPS points, signature, miles — what an auditor asks for.

### Phase 26: Recurring trips
- Standing orders: rider, days of week (e.g. dialysis Mon/Wed/Fri), times, start/end dates, payer, prior auth.
- Trips generated automatically N days ahead; skip a date (holiday, hospital stay) without breaking the series.
- Prior-auth expiry warning on the standing order.

### Phase 27: Credential vault + reminders
- Driver credentials as a typed list: license, MVR, background check, drug screen, CPR/first aid, defensive driving, wheelchair securement, plus per-payer required sets (what SafeRide/MTM demand).
- Vehicle insurance added to registration/inspection.
- Company dates entered: SAM (Oct 26, 2027), PEMS revalidation (Dec 2028), Fort Worth VFH license, insurance (Oct 2027).
- **Email reminders at 90 / 60 / 30 / 7 days** and on expiry, via a scheduled job (same mechanism as backups). Expired = can't be assigned (already enforced for license/registration/inspection; extended to all required items).

### Phase 34: Rider accounts + "My trips" (built right after Phase 24)
Numbered 34 so existing phase references stay valid; scheduled second. Owner decisions 2026-11-03:
- **Booking stays account-free.** The account is an optional layer on top of a completed booking, never a gate in front of it.
- A rider's **first booking from a new email** creates an account and emails a **set-password link** (valid 3 days; it doubles as email verification — the account is unusable until the inbox owner clicks it). Later bookings from that email attach to it; no new invite.
- The account sees **only requests made with that email**; never auto-linked to a passenger record (ADR 0006).
- **Facility bookers get no rider account** — a discharge planner's email would bundle many patients' PHI. Facilities get their own account type in Phase 33.
- Riders can: see request/trip status (confirmed pickup time, driver once assigned), book again prefilled, cancel (staff alerted), edit profile (phone, saved addresses, mobility needs).
- Lives on **angelcaretransit.com** ("My trips"), same look as the booking form.
- PHI on screen → strong passwords, lockout, **short idle timeout** (shared family tablets, facility workstations), access logging. Emails stay PHI-free (ADR 0027).

## Stage C — Getting paid

### Phase 28: Broker trip import
- Upload the CSV/Excel exported from the MTM or SafeRide portal → preview → create trips. Re-importing the same file updates, never duplicates (keyed on broker trip number).
- Reconcile report: trips the broker says we did vs. trips we have proof for.
- We work above the broker portals, not instead of them.

### Phase 29: Rates + automatic invoices
- Rate schedule per payer: base rate by service level (ambulatory / wheelchair / stretcher), per loaded mile, wait time, no-show fee, after-hours.
- Invoice amount calculated from the trip's proof (miles, wait) instead of typed.
- Batch: "invoice all completed MTM trips for last week", in that payer's format.
- Prior-auth tracking: authorized trips used vs. remaining.

### Phase 30: Claims output + denials
- Claim lines with HCPCS codes (T2003 and modifiers) per trip.
- Export for TMHP / TexMedConnect (format depends on the submission route — see decisions).
- Denial / appeal queue: denied claims with reason, due date, resubmission history (the claim history model already supports this).

## Stage D — Running the business

### Phase 31: SLA dashboard
- Per payer, per week: acceptance rate, on-time pickup % (from Phase 24 timestamps), no-shows, turn-downs by reason. Red when under target (e.g. 95%).

### Phase 32: Driver pay + trip profit
- Driver pay rule per driver (per trip / per mile / hourly).
- Pay statement per driver per period; 1099 year-end totals for contractors.
- Profit per trip and per payer: revenue − driver pay − mileage cost; loaded vs. deadhead miles.

### Phase 33: Facility CRM + booking portal
- Facilities as accounts: contacts, notes, follow-up reminders, trip history (the 22-facility lead list imported).
- Facility staff log in and book/see their residents' trips; discharge planners stop phoning.

## Order and why

| Order | Phase | Blocks |
|---|---|---|
| 1 | 22 HIPAA | any real Medicaid trip |
| 2 | 23 Dispatch foundations | everything below needs source/payer/auth on the trip |
| 3 | 24 Driver app — day | proof, SLA, mileage billing |
| 3b | 34 Rider accounts | rider self-service |
| 4 | 25 Driver app — proof | audits, mileage-based invoices |
| 5 | 26 Recurring | dialysis volume |
| 6 | 27 Credentials + reminders | contract suspension risk |
| 7 | 28 Broker import | stop retyping broker trips |
| 8 | 29 Rates + invoices | — |
| 9 | 30 Claims output | HHSC billing |
| 10 | 31 SLA · 32 Pay/profit · 33 Facilities | — |

Phases 27 and 28 can move earlier if a contract starts sooner than the driver app is ready.

## Decisions needed (owner)

| # | Decision | Needed by |
|---|---|---|
| 1 | Hosting plans that include a BAA (Supabase, Vercel, Resend) — budget. **Decided 2026-11-03:** strip PHI from staff emails now; owner checks BAA plans/costs before the first Medicaid trip | Phase 22 |
| 2 | Do drivers use their own smartphones? Android, iPhone, or both? **Decided 2026-11-03:** drivers' own phones, mixed Android + iPhone → mobile web app | Phase 24 |
| 3 | Which payer goes live first? **Decided 2026-11-03:** all four at once — MTM, SafeRide, HHSC direct, private/facility. Still needed: a sample trip export file from the MTM and SafeRide portals | Phase 28 |
| 4 | Each payer's rates (from the contracts) | Phase 29 |
| 5 | HHSC claim route: TexMedConnect web entry, or a clearinghouse (837P file)? | Phase 30 |
| 6 | Driver pay model (per trip / per mile / hourly) | Phase 32 |
| 7 | Maps provider for mileage/route estimates (needs a BAA if it receives rider addresses) | Phase 29 |
