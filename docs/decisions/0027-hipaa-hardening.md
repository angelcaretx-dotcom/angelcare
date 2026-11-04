# ADR 0027: HIPAA hardening — PHI-free staff alerts, view logging, BAA checklist

- Status: Accepted
- Date: 2026-11-03

## Context

Rider names, phone numbers, addresses and appointment details are
protected health information (PHI) once AngelCare carries Medicaid
trips. Two gaps stood out before the first such trip:

1. The **staff alert email** for a new trip request contained everything
   the rider typed: name, phone, email, both addresses, service type
   (e.g. wheelchair) and free-text notes (often a diagnosis, such as
   "dialysis"). That email passes through Resend and lands in a
   consumer Hotmail inbox, and none of those parties is covered by a
   business associate agreement (BAA) today.
2. The audit log (ADR 0005) recorded who **changed** a record, but not
   who **looked** at one. HIPAA's access-control and audit expectations
   cover reads: "who opened this rider's record" must be answerable.

The owner decided (2026-11-03, see roadmap): strip PHI from staff
emails now, and check BAA plans and costs with each vendor before the
first Medicaid trip.

## Decision

**Staff alert carries no PHI.** Subject: `New trip request #AB12CD34 —
log in to view`. Body: the reference (first 8 characters of the
request's id), requested pickup time, submitted time, and a button to
the request in the admin. Service type is left out too, because
"wheelchair" or "stretcher" says something about the rider's health.
Staff already have to log in to act on a request, so nothing is lost.
A test feeds a request full of identifying details and asserts none of
them reach the subject, text or HTML.

**The rider's confirmation carries no PHI either** (owner decision,
same day). Resend has no confirmed BAA, so the confirmation says
"Hello," (no name), confirms the request is not a booking, gives the
reference number and the 911 warning, and asks the rider to call with
that reference. No name, phone, addresses, pickup time or service type.
"Reply to this email" was dropped too, to steer conversations to the
phone instead of an inbox without a BAA. A test asserts the same
identifying details never reach it. This is reversible if Resend signs
a BAA.

**View logging** (`audit/admin_mixins.py`, `AccessLoggedAdminMixin`) on
every admin holding PHI: trip requests, passengers, trips, invoices,
claims and the notification log (customer email addresses).

- Opening a record page (GET, 200) → `viewed`, with the record id.
- Opening a list page → `viewed_list`, with the search/filter used
  (searching for a name is a look at that person), capped at 500
  characters.
- Opening the admin home page → `viewed` on `Dashboard`, since its
  panels list riders' names.
- Saves are not double-logged (they already record `status_changed`
  etc.), and a missing record (redirect) isn't logged.

Entries land in the existing `AuditLog`, which can't be edited or
deleted from the admin. Filter by action "viewed" to answer "who looked
at what".

**BAA checklist** in [docs/hipaa.md](../hipaa.md): every service that
stores or carries PHI, what it holds, and its BAA status. The owner
fills in the status from each vendor.

## Consequences

- The audit log grows with every page view: several rows per working
  hour per staff member. That's small for Postgres; if it ever matters,
  old `viewed` rows can be archived, never silently dropped.
- The log records views in the **admin** only. The driver app
  (Phase 24) must log its own reads when it shows rider details.
- Even a PHI-free email says that this address booked medical
  transport, so a BAA-covered email provider is still the clean
  long-term answer.
- Removing PHI from email doesn't make the system HIPAA-compliant on
  its own. Signed BAAs with the database, hosting and email vendors,
  plus the policies in hipaa.md, are still required before Medicaid
  trips. That's an owner action, not code.
