# ADR 0022: Automated tests for web/

- Status: Accepted
- Date: 2026-10-15

## Context

`api/` has had a real test suite since Phase 1, run in CI since ADR 0013.
`web/` had none: CI ran `lint` and `build`, which catch syntax and type
errors but nothing about behavior. The public site is the business's
front door, and the trip request form is its main way of taking in
work. A regression there (a renamed field, a broken date conversion, a
lost error message) would fail silently. The customer sees "something
went wrong", or worse, a request arrives with the wrong pickup time.

## Decision

Two layers, both run in CI on every push.

**1. Component tests: `npm test`** (Vitest + React Testing Library +
jsdom, set up per Next.js's own Vitest guide; config in
`vitest.config.mts`).
- `src/components/TripRequestForm.test.tsx`: the form sends exactly
  the payload the API expects; shows the confirmation (which must say
  "not a confirmed booking"); shows the backend's field errors next to
  the right inputs, with `aria-invalid` and the entered values kept;
  falls back cleanly on a network failure; disables the button while a
  request is in flight; offers exactly the four confirmed services.
- `src/lib/api.test.ts`: endpoint, method, headers, and body; error
  handling for 400s with field errors and for non-JSON error bodies.
- The tests pin `TZ=America/Chicago`. The form turns a local date and
  time into UTC, so the test asserts a concrete value (9:30 AM Central
  on 2026-11-02 becomes 15:30 UTC) rather than recomputing it the way
  the code does. Without the pin, CI (on UTC) and a Texas developer's
  machine would test different things. A run with the pin set to UTC
  was confirmed to fail.

**2. Built-site checks: `npm run test:build`** (config in
`vitest.build.config.mts`). These run after `next build`, against the
prerendered HTML in `.next/server/app/`, which is what visitors and
search engines actually receive. For every route in `sitemap.ts`:
- a non-empty `<title>` (unique across the site) and meta description
- exactly one `<h1>`
- an `alt` attribute on every image
- no internal link to a route or `public/` file that doesn't exist
- **only the real contact details**: every `tel:` and `mailto:` link,
  and every phone-number or email pattern in the visible text, must
  match `site-config.ts`. This applies the project's "never invent
  business facts" rule mechanically.

It also checks that the sitemap and the prerendered pages match exactly
(no page left out of the sitemap, no sitemap entry without a page), and
that `/request` ships the form with every field the API needs.

The checks were tested by planting real mistakes in a temporary build
(a made-up phone number, a link to a nonexistent `/pricing`, a page
removed from the sitemap). All three were caught. The suite fails
loudly if there's no build output, rather than passing with nothing to
check.

**Dependencies:** dev-only, pinned exactly, each release at least two
weeks old at the time of adding. `@types/node` moved from `^20` to
`24.19.0` to match the Node 24 the site actually runs on locally and in
CI; Vitest 5 requires 22 or newer. `@types/jsdom` is needed because
`next build` type-checks every `.ts` file, including `tests/`. Without
it the production build itself failed, which the first planted-mistake
run caught. No production dependencies changed, and
`npm audit --omit=dev` is still clean.

## Not built (deliberately)

- **Browser end-to-end tests (Playwright).** They'd add browser
  downloads to every CI run. The site is fully static apart from one
  form, and the form's behavior is covered by the component tests. Worth
  revisiting if client-side interactivity grows, or to test real
  layout and responsiveness.
- **Automated accessibility auditing (e.g. axe).** The structural
  checks above (h1, alt text) cover the basics. A full audit is a
  separate piece of work.
- **A contract test against `api/`'s real serializer.** The form's field
  names and service types are asserted on the web side only. A
  cross-app check would couple the two apps' test suites, which
  ADR 0001 keeps independent. The API's own tests cover its side.

## Consequences

- A change that breaks the form's payload, its error display, a link,
  the sitemap, or puts an unsourced phone number or email on any page
  now fails CI.
- CI still doesn't block deploys (ADR 0013): Vercel deploys every push
  to `main` in parallel with CI, so a failing test shows up as a red X,
  not a stopped deploy.
