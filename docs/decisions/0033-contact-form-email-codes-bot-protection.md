# ADR 0033: Contact form instead of a public email address; email codes and bot protection on the website forms

- Status: Accepted
- Date: 2026-11-05

## Context

Owner, 2026-11-05: hide the email address from the whole website and
leave only a contact form. Protect the forms from spam with a one-time
email code (OTP) for real people, and a bot-control system.

The owner chose:
- the code on **both** the booking and contact forms;
- **Cloudflare Turnstile** on top of built-in checks.

The published Gmail address was also a HIPAA weak point. Consumer
Gmail can't be covered by a BAA (docs/hipaa.md), and riders were
writing health details to it.

## Decision

**No email address on the website.**
- `siteConfig.email` is gone. The footer, About, Accessibility,
  Privacy and Terms pages link to the contact form, and give the phone
  number where relevant.
- A built-site test fails if any page shows an email address or a
  `mailto:` link.

**Contact form** (`/contact`, `POST /api/v1/contact/`, app `contact`).
- Fields: name, email, optional phone, message.
- Saved as a `ContactMessage`.
- Staff get a "New website message #REF — log in to read" alert with no
  message text, because messages can hold health details (ADR 0027).
- In the admin, under Operations → Website messages, staff read
  messages (each view is logged), reply from their own mailbox and
  mark them Handled.
- Dispatchers can view and change; Administrators can also delete. It
  appears as an area on the staff access page (ADR 0030).

**Email code** (app `verification`, `/api/v1/verify/start|check/`).
- A 6-digit code is emailed. It works for 10 minutes and allows 5
  tries. The email is PHI-free: the code only.
- The correct code earns a **token for that email, valid 2 hours**. The
  booking API (`verification_token`, now required) and the contact API
  only accept a token for the same email.
- The website keeps the token for the browser tab, so fixing a form
  error or booking several rides (facility staff) needs one code.
- Codes and tokens are stored only as hashes.
- Signed-in riders already proved their email, so
  `POST /api/v1/rider/booking-token/` gives them a token for their own
  address without a code.
- Per email address: at most 3 codes per 15 minutes and 10 per day.
  That stops anyone using us to flood someone else's inbox.

**Bot checks before a code is sent** (`verification/bots.py`):
- **Trap field**: a hidden input people never see. If it's filled in,
  the API returns a fake success and sends nothing.
- **Minimum time**: a code can't be requested less than 3 seconds after
  the form opens.
- **Cloudflare Turnstile**: Cloudflare's usually invisible human check.
  It's active only when both keys are set:
  - `NEXT_PUBLIC_TURNSTILE_SITE_KEY` on the website;
  - `DJANGO_TURNSTILE_SECRET_KEY` on the API.

  If Cloudflare can't be reached, the visitor is let through, because
  the code is still required and an outage mustn't stop bookings.
- **Per-IP limit**: 30 code requests or checks an hour (`verify`
  throttle). The forms call the API directly, not through the
  website's proxy, so the limit sees the visitor's own address.

## Consequences

- Booking has one more step for anyone not signed in: type the code
  from their email. Phone bookings are unaffected.
- A booker without email access can't book online. They call, as the
  form already says.
- `EmailVerification` rows hold email addresses. They are short-lived
  in use but aren't purged yet; add a cleanup with the retention work.
- Release:
  - `contact.0001`, `verification.0001` and `notifications.0003` change
    the database; run `ops/migrate-production.ps1` before pushing.
  - Until both deployments finish, an older copy of the website can't
    book (no token). Such bookings fail with a form error.
  - Turnstile starts working once the two keys are added to Vercel.
