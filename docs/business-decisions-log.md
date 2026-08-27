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
| 2026-08-22 | Physical address | Not provided — intentionally omitted from public site | Owner, chat |
| 2026-08-22 | Services offered | Ambulatory, Wheelchair, Stretcher transportation | Owner, chat |
| 2026-08-24 | Services offered (addition) | Medical Supply & Equipment Delivery (e.g. wheelchairs, walkers, oxygen equipment, home-care equipment) added as a confirmed 4th service, alongside Ambulatory/Wheelchair/Stretcher | Owner, chat |
| 2026-08-24 | Marketing imagery | Owner-provided banner images (AngelCare-branded, one per service + supporting themes) used as full hero/section banners across the site, unmodified/uncropped aside from responsive sizing | Owner, chat |
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

## Explicitly UNKNOWN (do not invent)

- Payer / broker relationships (Medicaid MCOs, brokers, private pay terms, facility contracts)
- Pricing / rate schedules
- Fleet size, vehicle details
- Driver employment classification (employee vs. contractor)
- Business hours
- Legal entity type / EIN / state registration details
- Insurance and credentialing specifics
- Any regulatory claims (Medicaid enrollment status, state licensure numbers, etc.)

Any of the above appearing anywhere in the app must trace back to an entry
added here first.
