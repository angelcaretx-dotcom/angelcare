# ADR 0016: Unfold admin theme

- Status: Accepted
- Date: 2026-08-29

## Context

`/admin/` was still Django's default, unbranded admin UI. Every domain
so far (trip requests, passengers, drivers, vehicles, documents,
organization) is managed exclusively through it -- there's no separate
staff frontend. The user explicitly asked for it to look "highly
professional and advanced," not "like old Windows."

## Decision

Adopted **django-unfold**, a Tailwind-based admin theme with real
sidebar navigation, branding, and dark/light mode, rather than
building a custom admin frontend (a far larger, out-of-scope
undertaking) or a lighter-weight CSS-only reskin (rejected as not
meeting "highly professional and advanced" -- see the three options
presented to and chosen from by the user). It reskins Django admin in
place; none of the existing `admin.py` registrations, RBAC (ADR 0005),
or audit trail (ADR 0005) needed to change.

**Pinned to `django-unfold==0.96.0`, not latest.** The current release
(0.104.1) requires Django>=5.2; this project is pinned to Django
5.1.15. Installing latest silently upgraded the local venv to Django
**6.1** as a side effect -- caught before it went anywhere, reverted
immediately. 0.96.0 is the newest release still declaring
`django>=5.1` compatibility (verified via `pip install --dry-run`
across the version range, not guessed). A Django 5.2 LTS upgrade may
be worth doing on its own merits later (5.2 is LTS; 5.1 isn't), but
that's a deliberate, separately-tested decision -- not something to
bundle silently into an admin theme change.

**Combining with MFA (ADR 0014) needed real investigation, not
assumption.** ADR 0014 enforces MFA via `admin.site.__class__ =
OTPAdminSite` (django-otp's documented technique) in
`accounts/apps.py`. That doesn't compose with Unfold: Unfold's
`UnfoldAdminSite.__init__()` does real setup (reading `UNFOLD` settings
to choose a login form) that has to run for the actual instance
`admin.site` becomes -- a monkey-patch after the fact only swaps the
class, not re-runs `__init__`. Read both packages' actual installed
source (`unfold/sites.py`, `django_otp/admin.py`,
`django_otp/forms.py`) before writing any integration code, same
practice as ADR 0014. Result:

- `accounts/admin_site.py::AngelCareAdminSite(UnfoldAdminSite,
  OTPAdminSite)` -- MRO resolves `has_permission()` to `OTPAdminSite`
  (Unfold doesn't define it), everything else to `UnfoldAdminSite`.
  `login_form` and `login_template` are explicitly overridden on top
  of that MRO (see below).
- Registered via Django's **documented** `AdminConfig.default_site`
  mechanism (`accounts/apps.py::AngelCareAdminConfig`, referenced in
  `INSTALLED_APPS` in place of the bare `"django.contrib.admin"`
  string) -- not a monkey-patch this time, since `AngelCareAdminSite`
  needs its own `__init__()` to actually run.
- **A real bug caught by testing, not assumed away**: this alone
  didn't work. Unfold's own `ready()` hook (on the AppConfig that bare
  `"unfold"` resolves to, `DefaultAppConfig`) unconditionally does
  `admin.site = UnfoldAdminSite()`, clobbering `default_site`
  regardless of `INSTALLED_APPS` order.
  `accounts.tests.StaffMfaEnforcementTests.test_admin_site_requires_otp_verification`
  failed with `admin.site` reporting as a plain `UnfoldAdminSite`, not
  `AngelCareAdminSite` -- caught immediately by the existing MFA test,
  not discovered later. Fixed by using Unfold's own documented escape
  hatch for this exact scenario: `"unfold.apps.BasicAppConfig"` in
  `INSTALLED_APPS` instead of the bare string (`BasicAppConfig` has no
  `ready()` override). Added a second, more specific regression test
  (`test_admin_site_is_also_the_unfold_theme`) asserting
  `isinstance(admin.site, AngelCareAdminSite)` directly, so this
  exact failure mode can't silently regress again.
- **Login page**: `OTPAdminSite.login_template` points at its own
  bare, unstyled template, which would otherwise win over Unfold's via
  the MRO above. Reset to `None` on `AngelCareAdminSite`, falling back
  to Django's default `"admin/login.html"` resolution --
  `templates/admin/login.html` (a new project-level template
  directory, added to `TEMPLATES[0]["DIRS"]` so it's checked before
  app-provided templates) is Unfold's own login layout with one field
  added: `otp_token`. Read `django_otp/forms.py` to confirm
  `otp_device` doesn't need to be shown for single-device TOTP setups
  --`clean_otp()` falls back to trying all of a user's devices when
  it's blank, so the extra device-picker/challenge UI django-otp's own
  template shows (for challenge-response device types this project
  doesn't use) isn't needed.
- **Login form styling**: `OTPAdminAuthenticationForm` doesn't know
  about Unfold's Tailwind input classes (neither package is aware of
  the other). `accounts/forms.py::UnfoldOTPAdminAuthenticationForm`
  applies the same class-injection Unfold's own `AuthenticationForm`
  uses on username/password, extended to `otp_token`.

**Branding**: `UNFOLD` settings in `config/settings.py` -- site
title/header, a Material Symbol as a fallback icon, the real site logo
(copied from `web/public/logo.svg` to
`accounts/static/accounts/logo.svg`; a real, sanctioned duplication --
`web/` and `api/` are separate deployments with no shared filesystem,
source of truth stays `web/public/logo.svg`), and a `COLORS.primary`
Tailwind-style ramp anchored at the two real brand hex values also
used on the public site (`--color-brand-blue` at shade 400,
`--color-brand-blue-dark` at 600 -- see `web/src/app/globals.css`),
hand-built around them for the rest (not derived from a design tool,
but visually consistent and exact at its two known points).

**Sidebar navigation** is fully custom (`UNFOLD.SIDEBAR.navigation`),
grouped to match the actual domains -- Operations (Trip Requests,
Trips), People & Fleet (Passengers, Drivers, Vehicles), Compliance
(Documents), Organization, System (Notification Log, Audit Log, Staff
Users, Groups & Roles, the last two gated to superusers only via a
`permission` callback) -- rather than Django's flat, alphabetical
app-list. `show_all_applications: True` keeps every registered model
reachable (including ones not in the curated list, e.g. TOTP devices)
via an "All applications" link, so nothing becomes inaccessible just
because it isn't in the curated groups.

**Small correctness fix found along the way**: logging in without an
explicit `?next=` (e.g. navigating straight to `/admin/login/`) fell
through to Django's own default `LOGIN_REDIRECT_URL`,
`/accounts/profile/` -- a 404, since this project doesn't define that
route. Set `LOGIN_URL`/`LOGIN_REDIRECT_URL` to `/admin/login/` and
`/admin/` respectively.

## Verification

`requirements.txt` diffed explicitly after adding the one new
dependency (`git diff`, not eyeballing) -- the exact mistake that
caused the ADR 0014 production outage, deliberately not repeated.

Full local test suite from a real (Postgres, not SQLite) database: 98
tests, up from 97 (one new regression test), all passing. Also ran
`collectstatic` locally first (matches CI's own established practice
from ADR 0013) since `STORAGES["staticfiles"]` uses WhiteNoise's
manifest storage, which raises rather than degrading gracefully when
a referenced static file (the new logo) hasn't been collected yet --
caught by the test suite itself before this was ever assumed to work.

Real browser verification (Playwright, not just markup review): a
disposable local superuser + confirmed TOTP device, a real TOTP code
computed from the device's own secret (django_otp.oath.TOTP, the same
algorithm the real login flow verifies against), a full login through
the actual form. Screenshots taken of the login page (unauthenticated
state, confirming the otp_token field renders styled and correctly),
the post-login dashboard, and a changelist page (confirming the
curated sidebar navigation, search, and table rendering). The
disposable account was deleted afterward.

## Consequences

- `/admin/` now looks and navigates like a real, modern operations
  dashboard -- sidebar navigation, search, branded login -- while
  every underlying admin behavior (RBAC, audit trail, document
  workflow, MFA) is unchanged.
- The default `admin/index.html` "Site administration" landing page
  still shows Django's stock flat app-list cards -- Unfold supports a
  fully custom dashboard (KPI cards, charts) via
  `UNFOLD.DASHBOARD_CALLBACK`, deliberately not built here to keep
  this phase scoped to theming. Worth doing later once there's a
  concrete set of numbers worth surfacing (e.g. "N new trip requests
  today").
- No self-service device recovery or enrollment UI changed from ADR
  0014 -- this phase is purely visual/navigational.
- `django-unfold` is pinned below its latest release specifically to
  avoid an unplanned Django major-version upgrade; revisit the pin
  once/if a deliberate Django 5.2 upgrade happens.
