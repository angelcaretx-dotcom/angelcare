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
| **Resend** (sends the automatic emails) | Staff alerts and rider confirmations, both now reference-number only (ADR 0027). The price email staff send (ADR 0047, 0049) adds dollar amounts: each fee, the miles and the rate, the wait, and the total. No name, address, date, time or service level in any of them, but the fee amounts can hint at the service. Still reveals that an email address booked medical transport | Minimal | _to confirm_ — ask support: "Will you sign a BAA?" | | |
| **GitHub** (code + nightly backup job) | The backup job reads the database and uploads an encrypted file; the code repo holds no rider data | Briefly, during backup | _to confirm_ | | |
| **Hotmail / Outlook.com** (angelcaretransit@hotmail.com) | Staff alerts (no PHI now) — but **replies to customers** sent from it would carry PHI | If staff email riders from it | Consumer accounts generally can't be covered | Consider a business email with a BAA | |
| **Gmail** (angelcaretx@gmail.com — the working inbox; no longer published on the website since ADR 0033, which replaced it with a contact form whose messages stay in the admin) | Whatever riders email to it, and staff replies | Yes, if riders write in | **Consumer Gmail: Google won't sign a BAA for it** | **Google Workspace** on angelcaretransit.com (e.g. dispatch@angelcaretransit.com) offers a BAA — the long-term home for rider email, and can replace Hotmail for alerts too | |
| **Maps provider** (none today; future) | Nothing: the system sends addresses to no map company. Miles for a price are estimated inside the system from the two ZIP codes, using a public Census table kept with the code (ADR 0049). A provider that works out exact miles would receive pickup and drop-off addresses | No, none is in use. It would be "Yes" for any provider added later | A provider needs a signed BAA before it is added. The free service considered in 2026-11 forbids personal data and signs none | | |
| **Google Maps, opened by a person** (not a service the system calls) | Two links open Google Maps in the user's own browser or maps app: the driver app's directions link (ADR 0029) and the price page's "Open the route in Google Maps" button (ADR 0049). Clicking one shows Google the address or the two addresses, with no name and no reference. The server sends Google nothing | Addresses only, when a person clicks | Consumer Google Maps: no BAA | Owner's call whether staff and drivers may use it; the alternative is typing the address into a map by hand, which shows a map company the same thing | |

Services that hold **no** PHI and need no BAA: Namecheap (DNS only),
the public website pages themselves.

## What the system already does

- **Staff logins require MFA** (password + authenticator code) — ADR 0014, 0024.
- **Role-based access**: dispatchers see only what their role allows — ADR 0005.
- **Audit trail** of every change, and (ADR 0027) of every view of a rider's record, list search or dashboard. Admin → Audit logs, filter action = "viewed".
- **No PHI in any automatic email** (staff alerts and rider confirmations carry a reference number only) — ADR 0027. The staff alert has a button to the price page and never an amount. The price email staff send from the admin (ADR 0047, 0049) carries the reference number, each part of the price with its amount (fees, miles and rate, wait), the total, and whether it is one way or a round trip: no name, address, date, time or service level. Its subject line shows no amount and no miles. The owner accepted (2026-11-26) that the fee amounts can hint at the service level.
- **No address leaves the system for a map.** The miles on the price page are an estimate from the two ZIP codes, worked out on the server with no outside call (ADR 0049). The map button is a link the staff member opens in their own browser (see the Google Maps row above).
- **A price can't be emailed until the sender confirms the customer pays** (not Medicaid, a broker or a facility account) and that the miles were checked. Both answers are kept in the audit log (ADR 0049). A rider already linked to a Medicaid or broker payer is refused by the system (ADR 0047).
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
