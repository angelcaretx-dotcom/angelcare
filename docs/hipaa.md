# HIPAA checklist

Owner's working list for carrying Medicaid / broker trips. Rider name +
anything about their trip or health = protected health information
(PHI). Every outside service that stores or carries PHI needs a signed
**business associate agreement (BAA)** before real riders' data goes
through it. See [ADR 0027](decisions/0027-hipaa-hardening.md).

**Rule:** don't start Medicaid trips until every "Yes" row in the first
table has a signed BAA, or the PHI has been moved off that service.

## Services that touch PHI

Fill in "BAA status" from each vendor. Plans and prices change, so
confirm with the vendor directly; don't rely on this file.

| Service | What it holds | PHI? | BAA status | Plan needed / cost | Signed (date, by) |
|---|---|---|---|---|---|
| **Supabase** (database + file storage) | Every rider, trip, request, uploaded document; encrypted nightly backups | Yes | _to confirm_ | | |
| **Vercel** (runs the API and website) | Rider data passes through it on every request (including the website's "My trips" pages and `/api` proxy, ADR 0031); server logs | Yes | _to confirm_ | | |
| **Resend** (sends the automatic emails) | Staff alerts and rider confirmations, both now reference-number only (ADR 0027). Still reveals that an email address booked medical transport | Minimal | _to confirm_ — ask support: "Will you sign a BAA?" | | |
| **GitHub** (code + nightly backup job) | The backup job reads the database and uploads an encrypted file; the code repo holds no rider data | Briefly, during backup | _to confirm_ | | |
| **Hotmail / Outlook.com** (angelcaretransit@hotmail.com) | Staff alerts (no PHI now) — but **replies to customers** sent from it would carry PHI | If staff email riders from it | Consumer accounts generally can't be covered | Consider a business email with a BAA | |
| **Gmail** (angelcaretx@gmail.com — the working inbox and public contact address) | Whatever riders email to it, and staff replies | Yes, if riders write in | **Consumer Gmail: Google won't sign a BAA for it** | **Google Workspace** on angelcaretransit.com (e.g. dispatch@angelcaretransit.com) offers a BAA — the long-term home for rider email, and can replace Hotmail for alerts too | |
| **Maps provider** (future, Phase 29) | Pickup/drop-off addresses | Yes | — | | |

Services that hold **no** PHI and need no BAA: Namecheap (DNS only),
the public website pages themselves.

## What the system already does

- **Staff logins require MFA** (password + authenticator code) — ADR 0014, 0024.
- **Role-based access**: dispatchers see only what their role allows — ADR 0005.
- **Audit trail** of every change, and (ADR 0027) of every view of a rider's record, list search or dashboard. Admin → Audit logs, filter action = "viewed".
- **No PHI in any automatic email** (staff alerts and rider confirmations carry a reference number only) — ADR 0027.
- **Encrypted in transit** (HTTPS everywhere, TLS email).
- **Encrypted backups**, private bucket, 30-day retention — ADR 0026. The passphrase is only in the owner's password manager and GitHub secrets.
- **MFA secrets and backup codes are excluded from backups** — ADR 0021.

## Owner actions (not code)

- [ ] Get BAA status and cost from Supabase, Vercel, Resend, GitHub; fill in the table.
- [ ] Move rider email to Google Workspace on angelcaretransit.com (sign Google's BAA there); retire Gmail/Hotmail for rider conversations. Until then, keep rider details to the phone.
- [ ] Written HIPAA policies: privacy, security, breach notification, who the privacy/security officer is.
- [ ] Staff HIPAA training, recorded per staff member (compliance records can hold the certificate).
- [ ] Remove staff access the same day someone leaves (Admin → Users → untick "Active").
- [ ] Review the audit log of views monthly; look for anyone opening records they had no trip reason to open.
