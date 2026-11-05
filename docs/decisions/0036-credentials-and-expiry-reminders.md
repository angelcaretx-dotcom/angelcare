# ADR 0036: Driver credentials, vehicle insurance, company dates and expiry reminders

- Status: Accepted
- Date: 2026-11-05

## Context

Until now only the driver's license and the vehicle's registration and
inspection had dates, and they blocked assignment when expired (ADR
0008). Brokers and HHSC also check:
- that drivers hold current screening and training;
- that vehicles are insured.

The company's own registrations lapse too. Nothing reminded anyone
before a date passed.

The owner decided (2026-11-05):
- **Every driver needs:**
  - an MVR check;
  - a criminal background check;
  - a drug and alcohol screen;
  - CPR / first aid;
  - defensive driving.

  **Wheelchair securement** training is needed for wheelchair and
  stretcher trips.
- Reminders go to the **office only**.
- The **company dates** from the roadmap are entered.
- **Per-payer required lists** are built now.

## Decision

**Driver credentials** (`drivers.DriverCredential`, on the driver's
admin page).
- Each row has a type, date done, optional expiry, reference and note.
- Renewing adds a row: the newest of each type is the current one, and
  older rows are history.
- The scanned paper goes in the driver's Documents, as before.
- A **Credentials check** box on the driver page shows each required
  item as OK, expired or missing.
- Adding or deleting a credential is audited.

**Payer requirements** (`Payer.required_credentials`): checkboxes on the
payer page for credentials that payer demands on top of the company-wide
set.

**Vehicle insurance** (`Vehicle.insurance_expiration_date`) sits beside
registration and inspection.

**Assignment rule** (`Trip.clean`; `drivers.missing_credentials`). When
a driver is put on a trip, or its service type or payer changes, it's
refused if the driver lacks, or has expired:
- any company-wide credential;
- wheelchair securement, for a wheelchair or stretcher trip;
- anything that payer requires.

When a vehicle is put on a trip, it's refused if its insurance date is
missing or past. The check runs **only on a new assignment**, so trips
assigned before this rule still save. Standing orders (ADR 0035) leave
a regular driver or vehicle that fails the check off the trip, for
dispatch to assign.

**One list of expiring things** (`compliance/expiry.py`) covers:
- active drivers' licenses and current credentials;
- in-use vehicles' registration, inspection and insurance;
- active company compliance records.

It feeds the admin dashboard's "expiring" panel, and the **daily
reminder email**.
- The email goes to the office address at **90, 60, 30 and 7 days**
  before expiry, and **on or after** expiry.
- It's one digest a day, sent only when something reached a new point.
  `ExpiryReminder` remembers the last point sent per item and expiry
  date. A missed day doesn't skip a reminder, nothing is sent twice, and
  a renewal starts afresh.
- It names drivers, vehicles and company records only, never riders.
- It runs in the same Vercel Cron job as standing orders, now
  `/api/v1/cron/daily/`.

**Company dates** (data migrations `compliance.0003`, corrected by
`0004` from the owner's verified records the same day):

| Record | Expiry |
| --- | --- |
| SAM.gov registration | Nov 3, 2027 (reset Nov 4, 2026 by the NAICS 492110 update) |
| PEMS revalidation | Dec 3, 2028 |
| Commercial auto insurance | Oct 13, 2027 (WCAN261110, Incline Casualty, policy 10/13/2026 to 10/13/2027) |

There is no Fort Worth vehicle-for-hire license yet, so it has no
record. `0004` only changes records still as `0003` left them.

## Consequences

- **Before assigning, enter each driver's credentials and each
  vehicle's insurance date.** Until then, new assignments of those
  drivers and vehicles are refused with a message saying what's missing.
- Credentials that don't expire (left blank) never trigger reminders.
- Release: these change the database; run `ops/migrate-production.ps1`
  before pushing:
  - `drivers.0003`, `payers.0002`, `vehicles.0003`;
  - `compliance.0002`, `0003` and `0004`;
  - `notifications.0006`.
