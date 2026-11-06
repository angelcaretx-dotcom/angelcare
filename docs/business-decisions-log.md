# Business Decisions Log

Source of truth for confirmed AngelCare Transit business facts, so nothing
in the codebase or content is invented. Every entry below was provided
directly by the business owner in conversation. Anything not listed here
is UNKNOWN and must not be assumed.

| Date | Fact | Value | Source |
|---|---|---|---|
| 2026-08-22 | Legal/display business name | AngelCare Transit | Owner, chat |
| 2026-08-22 | Domain | angelcaretransit.com (Namecheap) | Owner, chat |
| 2026-08-22 | Service area | Full state of Texas | Owner, chat |
| 2026-08-22 | Business phone | 817-766-9228 | Owner, chat |
| 2026-08-22 | Business email | angelcaretx@gmail.com | Owner, chat |
| 2026-11-03 | Staff notification email (new trip request alerts) | angelcaretransit@hotmail.com | Owner, chat |
| 2026-08-22 | Physical address | Not provided — intentionally omitted from public site | Owner, chat |
| 2026-08-22 | Services offered | Ambulatory, Wheelchair, Stretcher transportation | Owner, chat |
| 2026-08-24 | Services offered (addition) | Medical Supply & Equipment Delivery (e.g. wheelchairs, walkers, oxygen equipment, home-care equipment) added as a confirmed 4th service, alongside Ambulatory/Wheelchair/Stretcher | Owner, chat |
| 2026-08-24 | Marketing imagery | Owner-provided banner images (AngelCare-branded, one per service + supporting themes) used as full hero/section banners across the site, unmodified/uncropped aside from responsive sizing | Owner, chat |
| 2026-08-27 | Marketing imagery (placed) | 8 images placed live: homepage hero + discharge banner, and one per matching service on the Services page (Ambulatory, Wheelchair, Equipment Delivery — no image used for Stretcher, none of the 8 depict it) — see `web/public/marketing/` | Owner, chat |
| 2026-08-22 | Logo (superseded) | An earlier PNG (red/maroon hands + cyan "ACT" wordmark) was sent first but replaced before use | Owner, chat |
| 2026-08-22 | Logo (current) | Official vector mark `angelcaretransite-logo.svg`, blue/teal abstract "act" symbol — stored at `web/public/logo.svg`, used as-is, explicitly confirmed as the replacement | Owner, chat |
| 2026-08-22 | Brand colors (derived from current logo) | Blue `#4fb1e4` / teal `#6bc9cd` (decorative, from the SVG artwork); accessible text/button variants `#2d6f91` / `#2f7377` (chosen separately to pass WCAG AA 4.5:1 on white — see `web/src/app/globals.css`) | Derived, not separately confirmed |
| 2026-08-22 | GitHub repo visibility | Public (explicitly chosen, kept despite recommendation to go private) | Owner, chat |
| 2026-08-22 | Frontend hosting | Vercel — deployed, project `angelcare` under team `ACT` (act-c1d1), domain angelcaretransit.com attached | Owner, chat |
| 2026-08-22 | Backend hosting (superseded #1) | DigitalOcean — replaced before deployment; doctl has no non-interactive login and the owner didn't want to share a full-account token | Owner, chat |
| 2026-08-22 | Backend hosting (superseded #2) | Fly.io — replaced after deployment attempts hit a required payment card, then an account flagged "high risk" requiring manual verification; too much recurring friction | Owner, chat |
| 2026-08-25 | Backend hosting (current) | Vercel (project `angelcare-api`, team `ACT`), Python serverless runtime — deployed, verified working end to end (real trip request via the live site, real admin login). See ADR 0011 | Owner, chat |
| 2026-08-25 | Database (current) | Supabase Postgres, via its connection pooler (not the direct connection — that's IPv6-only, unreachable from Vercel's runtime). Production schema fully migrated | Owner, chat |
| 2026-08-22 | Backend stack | Python / Django + PostgreSQL (unchanged by the hosting swap) | Owner, chat |
| 2026-08-26 | Production email provider | Resend, SMTP relay, sending from `notifications@angelcaretransit.com` (domain verification with Resend in progress as of this date — real sends will start working automatically once it completes, no config change needed) | Owner, chat |
| 2026-08-26 | Production file storage | Supabase Storage (bucket `Documents`, private), accessed via Supabase's own Storage REST API — verified working end to end for real (upload/download/sign/delete). See ADR 0012 | Owner, chat |
| 2026-08-31 | EIN | 93-312-2590 | Owner, chat |
| 2026-11-04 | Wait time (wait-and-return) | $1 per minute while the driver waits, after the first 15 minutes free (confirmed 2026-11-05). Private-pay and facility riders only; Medicaid broker (MTM, SafeRide) and HHSC trips are paid at the payer's own rates. Billing from Phase 29 rates | Owner, chat |
| 2026-11-05 | Email address on the website | None: removed from every page; people write through the contact form, which confirms their email with a one-time code (ADR 0033). angelcaretx@gmail.com stays the business email, unpublished | Owner, chat |
| 2026-11-05 | Website spam protection | Emailed one-time code on the booking and contact forms, plus Cloudflare Turnstile and built-in bot checks (ADR 0033) | Owner, chat |
| 2026-11-05 | Prices on the website | None: no rates (wait time included) are published on the website | Owner, chat |
| 2026-11-05 | Rider signature | At pickup, on the driver's phone; "unable to sign" with a reason is allowed (ADR 0034) | Owner, chat |
| 2026-11-05 | Mileage | Odometer at the start and end of each vehicle's day only, not per trip (ADR 0034) | Owner, chat |
| 2026-11-05 | Failed vehicle inspection | Vehicle blocked until an Administrator marks it fixed (ADR 0034) | Owner, chat |
| 2026-11-05 | Trip proof sheet | PDF created on download, any time; never stored (ADR 0034) | Owner, chat |
| 2026-11-05 | Standing orders | Trips created 14 days ahead; regular driver/vehicle assigned when available; ride back with the same options as booking; prior-auth warning 14 days before its end, no trips after it (ADR 0035) | Owner, chat |
| 2026-11-05 | Required driver credentials | Every driver: MVR check, criminal background check, drug and alcohol screen, CPR/first aid, defensive driving; plus wheelchair securement for wheelchair/stretcher trips. Payers can require more, set per payer (ADR 0036) | Owner, chat |
| 2026-11-05 | Expiry reminders | To the office only, at 90/60/30/7 days and on expiry (ADR 0036) | Owner, chat |
| 2026-11-05 | Trip intake | Format-agnostic importer for any sender's PDF/Excel/CSV, reviewed before anything is scheduled; imports create ambulatory and wheelchair trips only (stretcher rows go to review); files never go to an outside extraction/OCR service without a BAA (ADR 0038) | Owner, chat |
| 2026-11-05 | Rates and driver pay | Administrators enter and edit payer rate cards (incl. private pay) and driver pay plans in the admin; drivers can be paid per trip, per mile, hourly and/or a % of the trip charge; trip miles from the import file or typed by staff; invoices made by batch as drafts to check; after-hours window set per rate card (ADR 0039) | Owner, chat |
| 2026-11-05 | Claims submission (Phase 30) | TexMedConnect directly, no clearinghouse: generate 837P to the TMHP EDI companion guide and submit to TMHP; handle 277CA rejections, the Remittance & Status report and denials/appeals; track the 95-day filing deadline per claim | Owner, chat |
| 2026-11-05 | Who sets which rates | Medicaid (T2003) and broker rates are program/contract-set; only private-pay and facility rate cards come from the owner, entered directly in the admin | Owner, chat |
| 2026-11-05 | Broker sample files | Can't be supplied until a broker onboards us (no portal access); the universal intake (ADR 0038) covers it, and real broker layouts become saved mappings later | Owner, chat |
| 2026-11-05 | Stretcher rate | Leave the stretcher base rate blank on every rate card: stretcher transport isn't sold (no EMS license) | Owner, chat |
| 2026-11-05 | Company dates (corrected from verified records) | SAM.gov registration renews Nov 3, 2027 (reset Nov 4, 2026 by the NAICS 492110 update); PEMS revalidation due Dec 3, 2028; commercial auto insurance WCAN261110, Incline Casualty, 10/13/2026 to 10/13/2027; no Fort Worth vehicle-for-hire license yet (ADR 0036) | Owner, chat |
| 2026-11-06 | Facility portal and CRM (Phase 33) | Portal bookings arrive as ride requests the office confirms; facility staff see all their facility's rides; sign-in is password plus an emailed 6-digit code, accounts by office invitation only; facility follow-ups due show on the dashboard and in one morning email to the office (ADR 0042) | Owner, chat |
| 2026-11-06 | Prior authorizations | Block trips under an authorization that's used up, out of its dates, or for another rider/payer; authorizations running low (3 or fewer trips left, or ending within 14 days) show on the dashboard and in the morning office email (ADR 0043) | Owner, chat |

## Explicitly UNKNOWN (do not invent)

- Payer / broker relationships (Medicaid MCOs, brokers, private pay terms, facility contracts)
- Pricing / rate schedules (except wait time: $1/minute, see the table above)
- Fleet size, vehicle details
- Driver employment classification (employee vs. contractor)
- Business hours
- Legal entity type / state registration details (EIN is now confirmed — see the table above)
- Insurance and credentialing specifics beyond the required driver credentials and the dates above (insurer, policy numbers, coverage amounts, each broker's own requirement list)
- Any regulatory claims (Medicaid enrollment status, state licensure numbers, etc.)

Any of the above appearing anywhere in the app must trace back to an entry
added here first.
