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
| 2026-08-22 | Logo | Provided (red/maroon hands + cyan "ACT" wordmark) — stored at `web/public/logo.png`, used as-is | Owner, chat |
| 2026-08-22 | Brand colors (derived from logo) | Maroon `#A6353A`, Cyan `#1CADE4` | Derived, not separately confirmed |
| 2026-08-22 | GitHub repo visibility | Public (explicitly chosen, kept despite recommendation to go private) | Owner, chat |
| 2026-08-22 | Frontend hosting | Vercel | Owner, chat |
| 2026-08-22 | Backend/DB hosting | DigitalOcean | Owner, chat |
| 2026-08-22 | Backend stack | Python / Django + PostgreSQL | Owner, chat |

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
