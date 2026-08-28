# ADR 0014: Staff MFA for /admin/ (django-otp, TOTP)

- Status: Accepted
- Date: 2026-08-28

## Context

Section 13 of the project directive requires "MFA capability" for staff
access. Until now, every staff/admin account (Dispatcher and
Administrator alike) reached `/admin/` with just a username and
password. `/admin/` is where trip requests, passenger/driver/vehicle
records, and documents (including verified driver licenses and
insurance) are all managed -- a single leaked or guessed password would
be enough to reach all of it.

## Decision

Added `django-otp` (TOTP, RFC 6238 -- standard 30-second, 6-digit codes
compatible with Google Authenticator, Authy, 1Password, etc.) and wired
it in at the framework level rather than per-view:

- `django_otp` + `django_otp.plugins.otp_totp` in `INSTALLED_APPS`,
  `django_otp.middleware.OTPMiddleware` in `MIDDLEWARE` (after
  `AuthenticationMiddleware`, so `request.user` exists first).
- `accounts/apps.py` swaps `admin.site.__class__` to django-otp's
  `OTPAdminSite` in `AppConfig.ready()` -- the package's own documented
  in-place technique. This is a one-line change with no per-app-admin
  changes needed, because every existing `admin.py` in the project
  registers against the shared default `admin.site` instance.
- `OTPAdminSite.has_permission()` requires
  `request.user.is_verified()` in addition to Django's normal
  `is_active and is_staff` check. An authenticated-but-unverified staff
  user is treated exactly like a non-staff user: redirected to the
  login page, not given read-only or degraded access.
- Applies to **every** staff account, not just superusers --
  Dispatcher accounts are real staff logins too, and the directive's
  requirement isn't superuser-specific.

**The bootstrap problem**: `OTPAdminSite.has_permission()` has no
carve-out for a user who doesn't have a device yet -- read directly
from the installed package source
(`django_otp/admin.py`) to confirm this before relying on it. That
means nobody can use an admin page to enroll their *first* device: the
page that would let them do that is itself behind the same check. This
is solved with `manage.py bootstrap_totp <username>`
(`accounts/management/commands/bootstrap_totp.py`), which creates one
confirmed `TOTPDevice` directly via the ORM (no web auth involved) and
prints the `otpauth://` config URL plus an ASCII QR code rendering of
it for scanning into an authenticator app. Idempotent by default
(refuses a second device unless `--replace` is passed, e.g. after a
lost authenticator).

## Verification

Full local test suite run against a real (not SQLite) PostgreSQL
database, from a state that had never seen `django_otp`'s migrations
before (`manage.py migrate` applied its 3 bundled migrations cleanly;
no local `makemigrations` was needed -- the plugin ships its own).

Making `admin.site` an `OTPAdminSite` broke 25 previously-passing tests
across `accounts`, `audit`, `vehicles`, `documents`, `passengers`,
`drivers`, `trips`, and `organization` -- every test that reached
`/admin/` via `client.login()` alone started getting redirected
(302 instead of the expected 200/403). This was the *intended*
behavior showing up, not a bug: those tests were exercising RBAC and
audit-logging concerns unrelated to MFA and needed an equivalent
"assume a verified device" shortcut, the same way `client.login()` is
already a test-only shortcut around a real password check. Added
`accounts/otp_test_utils.py::login_with_otp()` -- django-otp's own
documented test pattern (a confirmed device's `persistent_id` stashed
in the session, exactly what a real OTP challenge leaves behind) --
and replaced `self.client.login(...)` with it at all 21 call sites
across those 8 files. Full suite passed again afterward (93/93,
including 15 accounts tests: 4 new tests directly proving the MFA
enforcement itself -- unverified login rejected from both `/admin/`
and a model list, `login_with_otp` verified to actually grant access
so the other 21 patched call sites can be trusted -- plus 4 covering
`bootstrap_totp` itself).

**A second real bug was caught by that same run, not assumed away**:
`bootstrap_totp`'s QR code, rendered via `qrcode.print_ascii()`, uses
Unicode block characters (U+2584 etc.). On this Windows dev machine's
console codepage (cp1252), writing those to stdout raised
`UnicodeEncodeError` and crashed the command outright -- meaning any
admin actually trying to enroll their first device from a non-UTF-8
terminal (a very plausible default on Windows) would have hit a crash
instead of a QR code. Fixed by rendering the QR into an in-memory
buffer first, then writing it to the real stdout inside a
`try/except UnicodeEncodeError` that falls back to a plain-text notice
-- the `otpauth://` URL is always printed too, so the command stays
fully usable (import-by-URL) even when the QR itself can't be
displayed. Re-verified: the command completes successfully either way.

## Consequences

- Every staff account (Dispatcher and Administrator) now needs a
  confirmed TOTP device to use `/admin/` at all -- there is currently
  no self-service enrollment page; every device is created via
  `bootstrap_totp`, run by whoever has server/database access.
  **Known limitation** (see `docs/known-limitations.md`): this doesn't
  scale past a small number of staff who can each be enrolled
  out-of-band once. A self-service enrollment flow (a logged-in,
  password-verified-but-not-yet-OTP-verified user completing device
  setup themselves) is a reasonable future addition once staff
  headcount makes out-of-band enrollment impractical.
- Losing an authenticator device currently means someone with server
  access re-running `bootstrap_totp --replace` for that user -- there
  is no self-service device recovery (e.g. backup/static codes) yet.
  django-otp ships `otp_static` for exactly that; not added here to
  keep this phase scoped to the directive's baseline "MFA capability"
  requirement rather than the full account-recovery story.
- Production rollout requires actually running `bootstrap_totp` against
  the production database once this deploys, and getting the printed
  QR/URL to the real admin account holder through a channel that isn't
  logged or persisted (the command deliberately warns about this).
