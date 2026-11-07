# ADR 0045: Launch checklist, and importing drivers and vehicles

- Status: Accepted
- Date: 2026-11-06

## Context

Every phase on the roadmap is built. What stands between the system and
daily use is data entry and a handful of owner actions, scattered across
a dozen pages and the verification report. The owner asked for two
things (2026-11-06): a page that shows what's still missing, and a way to
enter drivers and vehicles from a spreadsheet instead of one form at a
time.

## Decision

### Launch checklist (Admin → Organization → Launch checklist)

The page is for superusers only, since it spans every area. Each item is
checked against the records every time the page opens, so it updates
itself as data goes in. Each item links to the page where it's fixed.

- **Company:**
  - the legal name includes "LLC";
  - active compliance records exist, and none has expired.
- **Staff and security:**
  - the viewer has MFA backup codes;
  - no staff login is without a role.
- **Payers and rates:**
  - at least one active payer;
  - each active payer has a rate card in effect today.
- **Drivers:**
  - at least one active driver;
  - each driver's license and required credentials are current
    (`missing_credentials`);
  - each driver has a pay plan in effect.
- **Vehicles:**
  - at least one active vehicle;
  - each has insurance, registration and inspection dates that haven't
    passed.
- **Medicaid claims:**
  - Claims settings are complete (`ClaimSettings.missing()`);
  - the Test or Production setting is shown;
  - Medicaid riders have an ID, a birth date, a sex and a home address.
- **Facility portal:** a portal user has signed in.
- **Check yourself:** items the app can't see:
  - BAAs with Supabase, Vercel and Resend;
  - rider email on Google Workspace;
  - Resend shows the domain as verified;
  - the nightly backup's latest run succeeded.

### Import drivers and vehicles (Admin → Drivers & vehicles)

- **The template:** download it from the page as a CSV, fill it in Excel,
  and upload it as CSV or .xlsx. It's read by the trip importer's file
  reader (ADR 0038), so a title row is skipped.
  - **A driver row:** name, phone, email, employment type (employee or
    contractor), license number and expiry. For each credential type,
    the date it was done and its expiry.
  - **A vehicle row:** VIN, year, make, model, plate, wheelchair yes/no,
    seats, and the registration, inspection and insurance expiry dates.
- **Check, then confirm:** every row is checked with the models' own
  validation and shown with its problems. Nothing is saved until
  **Add the N ready rows** is clicked, and then only the rows with no
  problems.
  - The checked file is held in the staff member's session between the
    two steps.
- **Already on file:** a driver with the same license number, or a
  vehicle with the same VIN, is skipped, never changed.
- **Insurance can be left blank.** That keeps an unconfirmed vehicle off
  the policy until someone enters its date: only the 2016 Dodge Grand
  Caravan is confirmed on it (owner report, 2026-11-06).
- Each record made is audited.
- Administrators can import (add permission on drivers, credentials and
  vehicles). Dispatchers can't.

## Consequences

- No database changes.
- The checklist is a snapshot of what the app can see. The "Check
  yourself" items stay unchecked until the owner confirms them outside
  the app.
- The import doesn't update records already on file. Change those on
  their own pages.
