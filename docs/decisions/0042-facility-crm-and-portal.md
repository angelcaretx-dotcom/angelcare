# ADR 0042: Facility CRM and the facility booking portal

- Status: Accepted
- Date: 2026-11-06

## Context

Roadmap Phase 33 has two goals:
- treat facilities (hospitals, nursing homes, dialysis centers) as
  accounts, with contacts, notes, follow-up reminders and trip history;
- let facility staff book rides and see their residents' rides, so
  discharge planners stop phoning.

The 22-facility lead list is the starting point.

Until now a facility existed only as a booking choice ("a patient or
resident at my facility", ADR 0031). That choice deliberately gets no
rider account.

**Owner decisions, 2026-11-06:**
1. A portal booking arrives as a **ride request the office confirms**,
   not straight onto the schedule.
2. Facility staff see **all their facility's rides**, not only their
   own bookings.
3. Sign-in is a **password plus an emailed 6-digit code** every time.
   Accounts come only by office invitation.
4. Follow-ups due show on the **dashboard and in one morning email** to
   the office.

## Decision

### The CRM (app `facilities`, Admin → Facilities)

- **Facility:**
  - name and kind (hospital, nursing home, assisted living, dialysis,
    rehab, clinic, other);
  - **stage**: Lead → Contacted → Active → Lost;
  - address in parts, phone, email, website;
  - an optional payer, for a facility contract;
  - the **account manager** (a staff login);
  - **next follow-up date** and what it's about;
  - notes.
- **Contacts:** name, title, phone, email, main contact.
- **Activity:** calls, visits, emails, meetings and notes, stamped with
  who logged them and when. They're added on the facility page and
  never edited afterwards.
- The facility page also lists its **portal users** and its **latest
  10 trips**.
- **Follow-ups:**
  - *Follow-ups due* is a list filter: due, next 7 days, none set.
  - A **dashboard panel** lists what's due today or overdue.
  - The daily cron sends one **morning email** listing them.
    `FollowUpDigest` makes it once a day at most.
  - Lost facilities are left out.
  - The email names facilities only, never riders.
- **Import facilities (CSV):** each row becomes a Lead. It reads name,
  kind, address, phone, email, website, notes and one contact. A
  facility already on file with the same name and city is skipped,
  never changed. This is how the 22-facility list goes in.

### Trips linked to a facility

`Trip.facility` and `StandingOrder.facility` are new; a standing order
covers, for example, a dialysis center's regular runs. A trip made
from a portal request takes the request's facility. Standing orders
and return legs copy it. Staff can set it on any trip booked by phone.

### The portal (website `/facility`, API `/api/v1/facility/`)

The portal is built like the rider pages (ADR 0031): Django sessions
through the website's own `/api/` proxy, CSRF on every write, and a
**15-minute idle sign-out** enforced by both the server and the page.

- **Accounts:**
  - Admin → Facility portal users → Add (Administrators) creates the
    login with no password and emails a **set-up link**: 3 days, one
    use.
  - Switch a login off when someone leaves; it stops working on their
    next click.
  - A portal login is not a staff, driver or rider login, and none of
    those can use the portal.
- **Sign-in:**
  1. Email and password. A right password emails a code. Five wrong
     passwords lock the email for 15 minutes.
  2. The **6-digit code**, which lasts 10 minutes and allows 5 tries.
     It uses the website's email codes (ADR 0033) with their per-address
     limits. The pending sign-in is tied to the session, and the code
     must be the one sent to that account for portal sign-in.
- **Rides:** every ride linked to the facility:
  - requests booked by anyone there, with their trips and rides back;
  - rides the office linked to it.

  Each shows the rider's name, status, pickup time, addresses, who
  booked it, and the driver's first name and vehicle once assigned.
- **Book a ride:** the website booking form's fields and checks,
  without the email code (the user is signed in). The facility's
  address is offered for pickup and drop-off. The request arrives as
  `source=facility_portal` and `booked_for=facility` (so no rider
  account is made). The office gets the usual PHI-free alert and
  confirms it as any request.
- **Cancel:** works on portal requests until pickup, the same rule as
  riders. The office gets a "cancelled by the facility" alert. Rides
  the office booked are changed by phone.
- Every sign-in, list view, booking and cancellation is audited.

### Permissions

- **Dispatchers** can view, add and edit facilities and contacts, and
  log activity. They can see portal users.
- **Administrators** also add, edit and switch off portal logins, and
  delete facilities.

Both are areas on the staff access page (ADR 0030).

## Consequences

- **Facility staff now see riders' names and rides online.** That's
  PHI going to the facility, which provides the riders' care, for
  treatment and operations. The same hosting BAA question as the rider
  pages applies (docs/hipaa.md).
- **Changing a ride** means cancel and book again, or call. Office-booked
  rides can only be changed by phone.
- **The 22-facility list isn't in the repository.** Save it as CSV and
  use *Import facilities*.
- Facility contracts are billed through the payer set on the facility
  and its rate card (ADR 0039). There are no facility-specific invoice
  formats yet.
- **Release:** `facilities.0001`, `transportation.0006`, `trips.0009`,
  `verification.0002` and `notifications.0007` change the database.
  Run `ops/migrate-production.ps1` before pushing.
