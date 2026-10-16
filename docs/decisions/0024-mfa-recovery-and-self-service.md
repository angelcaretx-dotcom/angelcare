# ADR 0024: MFA backup codes, self-service authenticators, and recovery

- Status: Accepted
- Date: 2026-10-16

## Context

ADR 0014 made TOTP mandatory for every staff account, but left the
whole device lifecycle on the command line: every authenticator was
created with `manage.py bootstrap_totp`, run by someone with server
and database access. A lost or replaced phone locked that person out
until such a person was found, and there were no backup codes at all.
For a small business without a dedicated IT person, that's an
operational risk: losing the owner's phone could mean losing admin
access entirely.

## Decision

Built on django-otp's own plugins, with no new auth mechanism:

**1. Backup codes** (`django_otp.plugins.otp_static`, newly installed;
its migrations add two tables). Each staff member can generate 10
single-use codes from their **My security** page (new sidebar item
under "Account"). Generating again replaces the old set. The codes are
shown once, on a page sent with `Cache-Control: no-store`, and are
never written to logs or the audit trail. At login they go into the
same code box as the 6-digit code. Codes are 8 base32 characters, so
they can never look like a TOTP code, which keeps the login form's
device routing (ADR 0014 postscript) unambiguous: a mistyped
authenticator code is never counted against, or throttles, backup
codes. They're accepted however they're retyped (capital letters,
spaces, dashes). The phone-only numeric keyboard hint was removed from
the code box, since backup codes contain letters.

**2. Self-service authenticators.** On the same page, a staff member
can add an authenticator (for example a new phone) by scanning an
inline SVG QR code (made by the `qrcode` library already in use; no
image service, nothing leaves the server), or by typing the setup key.
It's activated only after they enter a correct code from it. Until
then it's an unconfirmed django-otp device, which login ignores, and
an abandoned attempt is discarded the next time they start. They can
also remove an authenticator, but never their last one. New
enrollments are labeled "AngelCare Transit" in authenticator apps
(`OTP_TOTP_ISSUER`).

**3. Recovery for a locked-out colleague.** A superuser can issue
recovery codes for another staff member from the "Locked-out
colleagues" section of their own My security page. These are the same
single-use codes, generated on that person's behalf, to be handed over
in person or by phone. The colleague logs in with one, then adds a new
authenticator themselves. This is superuser-only because only
superusers manage accounts here: the Administrator role has no user
permissions (ADR 0005), and anyone who can issue a second factor for
someone else must already be trusted with their account.

**Re-authentication:** creating backup codes, removing an
authenticator, and issuing recovery codes all require the current
password, so an unattended, logged-in browser isn't enough. Adding an
authenticator is protected by having to enter a code from it.

**Audit:** every change is written to `AuditLog`:
`mfa_backup_codes_generated`, `mfa_recovery_codes_issued` (actor is
the superuser, resource is the colleague), `mfa_authenticator_added`,
and `mfa_authenticator_removed`. Codes and secrets are never included.

**Dashboard:** anyone with no backup codes sees a red notice at the top
of the admin home page linking to My security, until they create some.

`bootstrap_totp` stays for the one case it's still needed: the very
first superuser on a brand-new deployment, who has nobody to issue them
codes.

## Verification

`accounts/test_mfa.py` drives everything through the **real login
form** (the shortcut helper that hid the 1.7.4 login outage is only
used to set up already-logged-in users). It covers: a backup code
logging in once, typed in mixed case with a dash, and being rejected
the second time; a backup code working with no authenticator at all
(the lost-phone case); wrong authenticator codes not throttling backup
codes; adding a new phone end to end, then logging in with its next
code (reusing the code that confirmed it is rightly rejected by
django-otp's replay protection); refusals for wrong passwords, for
removing the last authenticator, for removing someone else's, and for
Administrators issuing recovery codes; and a full recovery from lost
phone to superuser-issued code to the colleague enrolling a
replacement phone. The CSS-class check (ADR 0023) now covers every
project admin template and caught a missing `font-mono` class, which
would have shown codes in a proportional font.

## Deployment

The two new tables must exist **before** this code goes live. At
login, django-otp checks every installed device type, so code that
knows about backup codes but has no table for them would break every
login. Release order: run `migrate` against production, then push.

## Not built (deliberately)

- WebAuthn and passkeys, and SMS codes. A new factor type is a bigger
  decision; TOTP plus backup codes is the usual baseline.
- Self-service recovery by email. Email is exactly the channel a
  second factor exists to back up, so it would weaken MFA.
- Forced backup-code setup. The dashboard notice nudges instead of
  blocking work.

## Consequences

- A lost phone is no longer an emergency: backup code, then add a new
  phone, all in the browser.
- Server access is only needed for the very first superuser on a new
  deployment.
