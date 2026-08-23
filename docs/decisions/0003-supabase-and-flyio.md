# ADR 0003: Supabase (Postgres) + Fly.io (app) instead of DigitalOcean, deployed via GitHub Actions

- Status: Accepted
- Date: 2026-08-22
- Supersedes: the DigitalOcean hosting decision in ADR 0002 (Django + PostgreSQL as the *stack* is unchanged; only *where it runs* changed)

## Context

ADR 0002 chose DigitalOcean App Platform for hosting `api/`. In practice,
neither DigitalOcean's `doctl` nor Fly.io's `flyctl` support a
browser-based, non-interactive login (unlike Vercel's CLI, which uses an
OAuth device-code flow that needs no pasted secret). Both require either
a pasted API token or, for Fly, email/password. The business owner
preferred not to hand a full-account cloud provider token to the
assistant operating this session.

## Decision

- **Database**: Supabase-managed PostgreSQL. Only a scoped Postgres
  connection string (`DATABASE_URL`) was needed to set this up --
  materially narrower than a cloud account's management API token, and
  it was provided directly by the business owner rather than requiring
  any CLI login.
- **App hosting**: Fly.io, running the same Dockerfile built for ADR
  0002 (no application changes -- Django + DRF is unchanged).
- **Deployment mechanism**: GitHub Actions (`.github/workflows/deploy-api.yml`),
  authenticated with a Fly.io deploy token stored as a GitHub repo
  secret (`FLY_API_TOKEN`). The business owner creates this token and
  adds it to GitHub directly -- the assistant never handles it. This
  also means every future deploy happens automatically on push to
  `main`, with tests gating the deploy (see the workflow's `test` job).

## One-time setup (must be done outside this session, by the account owner)

Run from the `api/` directory, after `fly auth login` (interactive,
opens a browser):

```bash
fly apps create angelcare-api

fly secrets set \
  DJANGO_SECRET_KEY="<generate a real one, e.g. via: python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\">" \
  DATABASE_URL="<the Supabase connection string>" \
  DJANGO_ALLOWED_HOSTS="angelcare-api.fly.dev" \
  DJANGO_CORS_ALLOWED_ORIGINS="https://angelcaretransit.com,https://www.angelcaretransit.com" \
  DJANGO_SECURE_SSL_REDIRECT="True" \
  --app angelcare-api

fly tokens create deploy -a angelcare-api
# copy the printed token
```

Then, on GitHub: repo -> Settings -> Secrets and variables -> Actions ->
New repository secret -> name it `FLY_API_TOKEN`, paste the token from
the last command.

After that, every push to `main` touching `api/` deploys automatically.
The first deploy can also be triggered manually from the Actions tab
("Deploy to Fly.io" -> Run workflow).

## Consequences

- One extra moving part (GitHub Actions) versus a direct CLI deploy,
  but it's the standard, auditable way to do CI/CD anyway -- arguably
  better for the 100-year-maintainability goal than an ad hoc local
  deploy would have been.
- Fly.io and Supabase are both isolated behind standard interfaces
  (Docker + `DATABASE_URL`) exactly as ADR 0002 intended -- swapping
  either later doesn't touch application code.
- `fly.toml` (app config) is committed; no secrets are committed
  anywhere -- they live only in Fly's secret store and the GitHub
  Actions secret.
