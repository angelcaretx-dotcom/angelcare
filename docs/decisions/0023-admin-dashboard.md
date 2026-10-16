# ADR 0023: Admin dashboard

- Status: Accepted
- Date: 2026-10-16

## Context

After login, staff landed on Django's stock list of every model. ADR
0016 noted that a real dashboard was possible through Unfold's
`DASHBOARD_CALLBACK` but deliberately left it out of that phase.
Finding out what needed doing (new requests waiting, today's trips,
credentials about to lapse, failed customer emails) meant opening four
or five separate lists and filtering each one.

## Decision

The `/admin/` home page now starts with "what needs attention today",
above Unfold's existing app list and recent-actions panel, which stay
underneath unchanged.

- **Logic:** `accounts/dashboard.py`. `build_dashboard(user, now)` is a
  plain function returning dataclasses, so it's tested without
  rendering. `dashboard_callback` plugs it into Unfold through
  `UNFOLD["DASHBOARD_CALLBACK"]`.
- **Template:** `templates/admin/index.html`, overriding Unfold's (the
  project `DIRS` are checked before app templates, same mechanism as
  the existing login override).

**What it shows.** Only counts and lists of real records, nothing
estimated:

| Section | Source |
|---|---|
| New trip requests to review | `TripRequest` status `new` (count, plus the queue oldest first) |
| Trips today | `Trip` scheduled or in progress, pickup within today |
| Upcoming trips | scheduled `Trip`s from now through the next 7 days |
| Documents awaiting review | `Document` status `pending` |
| Failed emails, last 7 days | `NotificationLog` status `failed` (failures were previously only visible if someone opened that log) |
| Credentials expiring within 30 days, or already expired | active `Driver` licenses, in-use `Vehicle` registration and inspection, active `ComplianceRecord`s |
| Unpaid invoices (count and total), claims awaiting payment, denied claims | `Invoice` status `sent`; `Claim` status `submitted`/`accepted` and `denied` |

Every figure and row links to the matching filtered admin list or
record. Figures that need action (new requests, pending documents,
failed emails, denied claims, expired credentials) are shown in red
when not zero.

**Permissions:** each section appears only if the user has `view`
permission on its model. So a Dispatcher sees the operational sections
but never Billing or Claims figures (ADR 0018/0019 made those
Administrator-only), and doesn't see the email-failure count (they
have no access to `NotificationLog`).

**"Today" means Central time.** A new setting,
`OPERATIONS_TIME_ZONE = "America/Chicago"`, is used for day boundaries
only. Storage, the API, and `TIME_ZONE` stay UTC, so a trip at 11:30 PM
in Texas counts as today rather than tomorrow. Central time covers
nearly all of the confirmed service area (State of Texas). Far-west
Texas (El Paso) is Mountain time; if operations ever run there, this is
the one setting to revisit.

**Windows** (7 days for upcoming trips and failed emails, 30 days for
expiring credentials) are constants at the top of `dashboard.py`.
They're operational defaults, not business facts.

**Styling:** Unfold ships precompiled Tailwind CSS, so a class it
doesn't already use has no styles. The template uses only existing
classes, and `DashboardTemplateTests` fails if any class in the
template is missing from Unfold's stylesheet. That protects against a
silently unstyled dashboard after an Unfold upgrade. The check was
confirmed to catch a planted nonexistent class.

## Not built (deliberately)

- Charts and trends (requests per week, and so on). With today's data
  volume (a handful of real records) they'd be noise. Worth adding when
  there's history to show.
- Per-user customization, or a separate dashboard per role. Permission
  filtering already gives each role the right subset.
- Caching. Each section is a few indexed queries on small tables.

## Consequences

- One page answers "what do I need to do today?" for both roles.
- Failed notification emails, previously a silent problem
  (known-limitations: no retry), are now visible on every login.
- Adding a section means adding to `build_dashboard`, permission-gated
  by the same `_can_view` check, and covering it in
  `accounts/test_dashboard.py`.
