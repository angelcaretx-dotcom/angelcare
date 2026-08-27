# ADR 0013: CI pipeline (GitHub Actions)

- Status: Accepted
- Date: 2026-08-27

## Context

Every phase so far has been verified by manually running the test
suite locally before committing. That's real verification (Section 26
of the project directive), but it depends on that manual step actually
happening every time, by whoever's making the change -- there was no
automated check at all. Vercel auto-deploys on every push to `main`
regardless of whether anything was tested, so a broken commit could go
live with nothing catching it first.

## Decision

`.github/workflows/ci.yml`, two jobs, on every push and PR to `main`:

- **`api-tests`**: a real PostgreSQL 16 service container (not
  SQLite -- matches local practice per ADR 0004/local dev conventions),
  `manage.py check`, `makemigrations --check --dry-run` (verifies no
  model change was left without a migration -- would have caught
  nothing new here, but it's a real class of mistake worth guarding
  automatically going forward), `migrate`, then the full test suite.
- **`web-checks`**: `npm run lint`, `tsc --noEmit`, `npm run build`.

**Honest limitation, not oversold**: this does not *block* Vercel's
deploy. Vercel's GitHub integration deploys on push independent of
other status checks; true gating would require branch protection rules
plus a pull-request-based workflow (this repo currently pushes
directly to `main`). This CI surfaces pass/fail clearly and quickly in
GitHub either way, and is the foundation for adding real gating later
if/when a PR-based workflow is adopted.

## Verification

Before writing the workflow file, the exact sequence it runs
(`check` -> `makemigrations --check` -> `migrate` -> `test`) was run
locally against a genuinely fresh, empty PostgreSQL database (not the
existing dev database with prior state) to simulate what the GitHub
Actions runner actually starts with. All 85 tests passed from that
clean state before the workflow was committed.

## Consequences

- Every future phase gets this check automatically -- no longer
  dependent on remembering to run it manually.
- `makemigrations --check` specifically protects against a model
  change being committed without its migration, which would otherwise
  only surface as a runtime error in production.
- Real blocking (PR + branch protection) remains a deliberate future
  step, not implemented here without being asked for.
