# ADR 0015: Branded HTML email formatting

- Status: Accepted
- Date: 2026-08-28

## Context

Every outbound email (staff new-trip-request alert, customer trip
request confirmation -- see `notifications/`, ADR from Phase 2) was
plain text only, generated from a bare `.txt` template with no
branding, no visual hierarchy, and no clickable admin link (the staff
email's "review in admin" line was a relative path, not a real URL --
useless pasted into a browser from an email client). The user
explicitly asked for "highly professional email format for each
conversation or type of notification."

## Decision

Every notification type now sends **multipart/alternative** email: the
existing plain-text body (kept, not replaced -- still the accessible
and spam-filter-friendly fallback every transactional email should
carry) plus a new branded HTML rendering as the preferred part.

- `notifications/providers/base.py`: `EmailProvider.send()` gained an
  optional `body_html` parameter. `DjangoEmailProvider` now builds an
  `EmailMultiAlternatives` message and calls `attach_alternative(html,
  "text/html")` when HTML is given, instead of the old bare
  `send_mail()`. This is additive to the interface (existing text-only
  callers still work with `body_html=None`), so no other vendor
  integration would break if one existed.
- `notifications/templates/notifications/email_base.html`: one shared
  layout every email extends -- table-based HTML (required for
  cross-client email rendering; flexbox/grid are not reliable in
  Outlook's Word rendering engine), inline styles only (email clients
  strip `<style>` blocks or `class` selectors unpredictably), a
  preheader block (the hidden inbox-preview snippet), branded header
  (brand-blue-dark `#2d6f91` background, matching `web/`'s existing
  `--color-brand-blue-dark` token) and a footer with phone/email/
  website links and an automated-message disclaimer.
- **No embedded logo image.** `web/public/logo.svg` is the only logo
  asset that exists, and SVG `<img>` support is inconsistent across
  email clients (classic Win32 Outlook, still common in NEMT-adjacent
  back-office environments, renders a broken-image icon for it). A
  styled text wordmark in the header is guaranteed to render
  identically everywhere and was judged more "professional" than a
  logo that might visibly break for some recipients. Revisit if a PNG
  export of the logo is added later.
- `notifications/templates/notifications/_detail_table.html` and
  `_button.html`: small shared partials (a label/value row table, and
  a table-based "bulletproof-ish" CTA button) so the two content
  templates stay declarative rather than repeating table markup.
- New settings: `SITE_URL` and `WEBSITE_URL`
  (`config/settings.py`), env-overridable via `DJANGO_SITE_URL` /
  `DJANGO_WEBSITE_URL`, defaulting to the real current production
  values (`https://angelcare-api.vercel.app`,
  `https://www.angelcaretransit.com`) so the staff email's "Review in
  Admin" button is a genuine, working absolute link without requiring
  new production configuration.

## Verification

Full `notifications` test suite (10 tests, up from 6): existing tests
updated for the new `body_html` parameter on the `EmailProvider`
interface; new tests assert every send carries an HTML alternative,
that the staff HTML includes the trip details and a real (not
relative-only) admin URL, that the customer HTML keeps the 911/
emergency disclaimer, and -- critically -- one test exercises the
real `DjangoEmailProvider` against Django's `locmem` test backend and
inspects `message.alternatives` directly, proving the email is
actually multipart/alternative and not just that a mock recorded the
right kwargs.

Both templates were also rendered with representative data
(`render_to_string`, matching the actual context `NotificationService`
builds) and screenshotted with Playwright for a real visual check, not
an assumption that inline-CSS table markup "should" render correctly.

Full backend suite: 97/97 passing.

## Consequences

- Every future notification type gets the shared branded layout for
  free by extending `email_base.html` -- consistent look without
  re-deriving it per type.
- HTML email is inherently harder to guarantee pixel-perfect across
  every client than plain text; the table+inline-style approach here
  targets broad compatibility (Outlook included) rather than modern
  CSS convenience, which is the standard, deliberate tradeoff for
  transactional email.
- `SITE_URL`/`WEBSITE_URL` are new required-in-spirit settings for any
  future email that needs a real link; defaults mean nothing breaks
  today, but a future domain change needs these two env vars updated
  alongside it (documented in `.env.example`).
