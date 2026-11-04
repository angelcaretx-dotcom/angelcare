# ADR 0030: Administrators manage staff access from the browser

- Status: Accepted
- Date: 2026-11-03

## Context

Access was all-or-nothing by role (ADR 0005): Dispatcher or
Administrator. Only the owner (a superuser) could add staff (ADR 0025),
and giving one dispatcher read access to billing, or switching off
someone who has left, meant Django's raw user screen, where a
permission list of 60+ codenames, plus the "superuser" tick, sit side
by side. The owner asked that administrators be able to grant access
as needed.

## Decision

**Staff access page** (My security → Staff accounts → *Manage access*;
`accounts/access.py`, `security_views.staff_access`):

- **Can sign in** (on/off). Off sets `is_active = False`: the person is
  blocked immediately, including sessions already open. They stay
  listed, so access can be restored.
- **Role**: Dispatcher, Administrator, or none.
- **Access by area**, each No access / View / Edit / Full: trip
  requests, trips & day board, turn-downs, passengers, drivers &
  vehicles, payers, billing, claims, compliance & documents, email
  log, audit log (the two logs are view-only at most). The role is the
  floor ("Role gives: …" is shown per area); a higher level grants that
  extra access to this person only, stored as their own Django
  permissions, so every admin screen, the sidebar and the dashboard
  honour it with no special cases. A lower level has no effect: change
  the role instead.
- Saving needs the administrator's **own password**, and is audited as
  `staff_access_changed` with a readable before/after.

**Who:** superusers and members of the Administrator role
(`can_manage_access`). **Guardrails:** nobody can change their own
access, owner (superuser) accounts can't be changed here, nothing on
the page can make anyone a superuser, and "manage staff" is not a
grantable area, so a Dispatcher can never grant themselves anything.

**Adding staff** (ADR 0025) is opened to administrators too, including
seeding the new account's first one-time codes. **Issuing recovery
codes** for an existing colleague (resetting their second factor) stays
owner-only.

## Consequences

- Administrators can now create other Administrators. That's intended
  (the owner shouldn't be the bottleneck) and audited.
- Access is per area, not per field or per record.
