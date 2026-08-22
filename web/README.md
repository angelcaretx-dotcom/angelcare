# AngelCare Transit — Web

Public marketing site and trip request form. Next.js (App Router,
TypeScript, Tailwind CSS v4). Deploys to Vercel.

## Setup

```bash
npm install
cp .env.example .env.local   # then set NEXT_PUBLIC_API_URL to the api/ backend
npm run dev
```

## Scripts

- `npm run dev` — local dev server
- `npm run build` — production build
- `npm run lint` — ESLint
- `npx tsc --noEmit` — type-check

## Structure

- `src/app/` — routes (App Router)
- `src/components/` — shared UI components
- `src/lib/site-config.ts` — real business facts (name, contact, services)
  used across the site — see `../docs/business-decisions-log.md` for
  provenance
- `src/lib/api.ts` — client for the `api/` backend
