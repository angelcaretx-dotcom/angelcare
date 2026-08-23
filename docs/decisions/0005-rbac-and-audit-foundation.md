# ADR 0005: RBAC and audit log foundation

- Status: Accepted
- Date: 2026-08-23

## Context

Every `/admin/` user up to this point has been a Django superuser --
full, unrestricted access, with no way to give a future staff member
narrower access (Section 14: permission-based access, not one generic
admin role) and no record of who changed what (Section 15: centralized
audit trail).

## Decision

- **`accounts/` app**: seeds two Django Groups via a `post_migrate`
  signal (`accounts/roles.py`), not a data migration -- see the
  implementation note below for why.
  - `Dispatcher`: view + change `TripRequest` only.
  - `Administrator`: full `TripRequest` access, plus read-only access
    to `NotificationLog` and `AuditLog`.
  - Deliberately not adding roles like "Billing" or "Fleet Manager"
    yet -- those domains don't exist. Adding a role speculatively
    would violate Rule 2/3 (never invent business structure) as much
    as inventing a fake service would.
  - To make a staff member a Dispatcher: create their `User` with
    `is_staff=True`, then add them to the `Dispatcher` group (via
    `/admin/auth/user/` or the Django shell). The original superuser
    account remains the break-glass admin account.
- **`audit/` app**: a general-purpose `AuditLog` model + `record_change()`
  helper, callable from anywhere (admin, future API surfaces) --
  intentionally not tied to Django's built-in `LogEntry`, which only
  covers admin-made changes. Wired into `TripRequestAdmin.save_model()`
  as the first real usage: every status change is logged with actor,
  before/after, source, and timestamp. `AuditLog` rows are add/edit/
  delete-locked in `/admin/` -- viewable, never alterable.

## Implementation note: why a signal, not a migration

The first attempt seeded roles via a `RunPython` data migration. This
failed on a fresh database (exactly what the test suite creates every
run): Django creates model permissions via its own `post_migrate`
signal, which only fires after *all* migrations in the run complete --
not incrementally after each app. A data migration for `accounts` that
depended on `audit`'s permissions already existing, run in the same
`migrate` invocation as `audit`'s own initial migration, hit a race:
those permissions didn't exist yet. Worse, forcing permission creation
mid-migration crashed on a schema mismatch, because `contenttypes`
itself was still mid-migration at that point (its `name` column removal
migration hadn't run yet), while the real (non-historical) permission-
creation code assumed the current, final schema.

The fix: connect `seed_roles` to `post_migrate` instead (the same
mechanism Django's own permission system uses). By the time any
`post_migrate` receiver runs, every app's schema migrations -- across
the entire project -- are already fully applied, so there's no
ordering hazard. This is now covered by a regression test
(`accounts/tests.py::RoleSeedingTests`) that runs against a fresh test
database on every test run.

## Consequences

- Future domains (passengers, drivers, dispatch, billing) can define
  their own permissions and either extend these two groups or add new
  ones, using the same pattern -- no new RBAC mechanism needed.
- `AuditLog` is ready for any future staff-facing API endpoint, not
  just admin -- `record_change()` doesn't know or care where it's
  called from (`source` just records that).
- Real roles beyond these two remain UNKNOWN / REQUIRES BUSINESS
  DECISION until the domains that need them exist.
