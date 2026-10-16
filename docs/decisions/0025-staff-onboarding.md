# ADR 0025: Adding staff members from the browser

- Status: Accepted
- Date: 2026-10-16

## Context

The owner is the only staff account. Adding a dispatcher meant creating
a user in Django's raw user admin, choosing a password for them, adding
them to the right Group by hand, and then (before ADR 0024) running
`bootstrap_totp` on a server. That's easy to get wrong: a missing group
means no access, a forgotten `is_staff` means no login, and a password
the owner chose and remembers isn't really the employee's own.

## Decision

**"Add a staff member"** (superusers only) on the My security page:
email (which becomes the username), first and last name, and a role:
Dispatcher or Administrator, the two Groups that exist (ADR 0005).
Superusers are still only made by hand. One transaction
(`accounts/onboarding.py`):

- creates a normal `auth.User` with `is_staff`, in that role's Group;
- sets a generated **temporary password**: 16 characters in groups of
  four, from an alphabet without look-alikes (0/o, 1/l/i), because it's
  read off paper; it's checked against the project's password
  validators;
- issues 10 **recovery codes** (ADR 0024) to stand in for the
  authenticator they don't have yet;
- records `staff_member_added` in the audit log (role, never the
  secrets).

Both secrets appear once, on a printable **handover sheet** (sent with
`no-store` caching) that also lists the login address and the
first-login steps. **Nothing is emailed:** email is the channel a
second factor exists to back up, and handing the sheet over in person
keeps the secret and the person together.

**First-login gate** (`accounts/middleware.py`, `StaffSetupMiddleware`).
Once someone is fully logged in (password plus code), the admin
redirects every page to:

1. **Change password,** while their `StaffOnboarding` row (the only new
   table) says the temporary password is still in use. A successful
   change clears it (`AngelCareAdminSite.password_change`) and records
   `staff_temporary_password_replaced`.
2. **Add an authenticator,** while they have no confirmed TOTP device.
   This step needs no flag, and it applies to everyone. It also covers
   someone who logged in with a backup code after losing their phone, so
   nobody keeps working on single-use codes.

Logout is never blocked. The public trip-request API and health check
are outside `/admin/` and are never gated. Existing, fully set-up staff
never see the gate.

## Verification

`accounts/test_onboarding.py` includes the whole journey: a superuser
submits the form and gets the sheet; the new dispatcher logs in with
the sheet's password and a code; every admin page redirects to the
password change; after changing it they're sent to add an
authenticator; after enrolling, the dashboard loads, scoped to the
Dispatcher role (no finance section); and their next login works with
their own password and phone. With the middleware switched off, that
test fails. Also covered: only superusers can add staff, duplicate
emails are refused regardless of capitalization, unknown roles are
refused, logout always works, and existing staff are unaffected.

## Not built (deliberately)

- Email invitations or "set your password" links. See above; also,
  production email delivery (Resend) still isn't confirmed working.
- Editing roles or deactivating staff from this page. Superusers do
  that in Staff Users (the existing user admin): untick "Active" to
  remove access while keeping their history.
- Creating superusers here.

## Consequences

- A new dispatcher can be set up in a couple of minutes, without a
  server, and ends up with a password only they know and their own
  authenticator.
- Deploying this needs `migrate` (the `accounts_staffonboarding` table)
  before the code goes live, because the middleware reads that table on
  every admin request.
