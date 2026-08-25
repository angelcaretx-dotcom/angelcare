# ADR 0011: API hosted on Vercel (serverless), not Fly.io

- Status: Accepted
- Date: 2026-08-25
- Supersedes: the Fly.io hosting decision in ADR 0003 (Supabase as the
  database is unchanged)

## Context

ADR 0003 chose Fly.io specifically because its CLI, like DigitalOcean's,
requires a token rather than supporting Vercel-style browser-based
device-code login. In practice, Fly.io hit two separate account-level
blockers in a row: a required payment card, then (after the card was
added) the account being flagged "high risk" and requiring manual
identity verification -- neither of which is something this session
can resolve, and both stalled deployment entirely. The business owner
decided the recurring friction wasn't worth it, given `web/` was
already successfully deployed to Vercel with zero account friction.

## Decision

**`api/` now deploys to Vercel too**, as a second, independent Vercel
project (`angelcare-api`) alongside `web/` (`angelcare`) -- not merged
into one project; they remain two separate deployables per ADR 0001.

Django runs via Vercel's Python runtime (`@vercel/python`), wrapping
the same WSGI application (`wsgi_app.py` -> `config.wsgi.application`)
used locally -- no application code differs between environments, only
how the process is invoked.

**Real tradeoffs, accepted knowingly (flagged in ADR 0003 and again
here, not discovered by surprise):**

- **Serverless, not persistent.** Every request may cold-start a new
  Python process. Acceptable for the current traffic level; would need
  revisiting if response latency becomes a real problem.
- **No working file uploads in production.** The Document domain
  (ADR 0009) writes to local disk, which serverless functions can't
  persist -- not even "until the next deploy" like Fly's ephemeral
  disk would have allowed. This is currently NOT functional in
  production. Needs a real object storage backend (Supabase Storage
  is the natural fit) before it's usable there. Tracked in
  known-limitations.md, not hidden.
- **Production email uses the console backend for now** (logs, sends
  nothing) -- no real SMTP credentials have been provided yet. Also
  tracked in known-limitations.md.
- **Migrations are a manual step**, not a deploy hook (unlike Fly's
  `release_command`). Run `manage.py migrate` against the production
  `DATABASE_URL` by hand (or from a machine with it configured) after
  a schema change, before/after deploying the code that needs it.

## A real infrastructure bug found and fixed along the way

Supabase's **direct** database connection
(`db.<project-ref>.supabase.co:5432`) now resolves to an **IPv6-only**
address. Vercel's Python serverless runtime has no outbound IPv6
route, so every database query failed with `OperationalError: Cannot
assign requested address`. Fixed by switching `DATABASE_URL` to
Supabase's **connection pooler** (PgBouncer, transaction mode,
`aws-0-<region>.pooler.supabase.com:6543`), which is IPv4-compatible
and, being pool-based, is actually the better fit for serverless's
many-short-lived-connections pattern anyway. Two related Django
settings were added for correctness under transaction-mode pooling:
`conn_max_age=0` (no persistent connections -- reusing one across a
frozen/thawed serverless invocation can hand back a dead connection)
and `DISABLE_SERVER_SIDE_CURSORS=True` (PgBouncer transaction mode
doesn't support them). See `config/settings.py`.

## Consequences

- `api/fly.toml` and `.github/workflows/deploy-api.yml` are removed --
  dead config for an abandoned path would mislead a future reader into
  thinking Fly is still the plan.
- The env-var-driven settings design (ADR 0002) is exactly what made
  this pivot possible without touching application code -- only
  deployment config and environment variables changed.
- If Vercel's serverless tradeoffs become a real problem later (cold
  starts, no persistent file storage) the same env-var-driven design
  means a persistent host is a deployment-config change, not a
  rewrite.
