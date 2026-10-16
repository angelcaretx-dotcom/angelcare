# ADR 0021: Backup export, verification, and restore

- Status: Accepted
- Date: 2026-10-15

## Context

"Data export & backup verification" was the next roadmap item after
the domain phases (Phases 12–15). Until now the only copies of
production data were whatever Supabase keeps on its side. Which
Supabase plan is in use, and so what its own backups cover (how often,
for how long, whether point-in-time recovery is available), has never
been checked or recorded. Either way, a backup nobody has restored is
an assumption, not a backup. Uploaded documents live in Supabase
Storage, which database backups don't cover at all.

ADR 0004 (local-first, no cloud lock-in) also applies: the business
should be able to take its data and leave any provider, using its own
tools.

## Decision

New `backups` app (no models) with three management commands, all
built on `backups/archive.py`:

- **`export_backup --output <dir>`** writes one zip:
  `data.json` (Django's JSON serialization of every backed-up row,
  natural keys for users/groups/content types), `files/<name>` (every
  file any `FileField` points at, read through the active storage
  backend, so Supabase in production and the filesystem locally), and
  `manifest.json` (format version, timestamp, applied migrations,
  per-model row counts, SHA-256 and size of every member). On
  PostgreSQL every read happens in one `REPEATABLE READ` transaction,
  so it's a consistent snapshot even if a trip request comes in
  mid-export. If any referenced file can't be read, the export fails
  and writes nothing unless `--allow-missing-files` is given, in which
  case the missing files are listed in the manifest. The zip is written
  under a temporary name and renamed at the end, so a crash can't leave
  a truncated file that looks like a backup.
- **`verify_backup <zip>`** is read-only and never touches the
  database. It checks every checksum, that row counts match the
  manifest, that every model in the archive exists in the code, and
  that every file the data refers to is in the archive (or listed as
  missing).
- **`restore_backup <zip>`** restores into a freshly migrated, empty
  database. It runs verification first, then requires every migration
  the backup was taken with that also exists in this codebase to be
  applied on the target. It refuses if any backed-up table already has
  rows (other than the seeded `Organization` and the role `Group`s),
  restores files (skipping identical ones and refusing to overwrite
  different ones), then loads the data in one transaction and checks
  the row counts again before committing.

**Left out of archives on purpose:**
- `otp_totp` and `otp_static`: staff MFA secrets and backup codes
  (`otp_static` added with ADR 0024). A leaked archive must not also
  give away everyone's second factor. After a restore, one superuser
  re-enrolls with `bootstrap_totp` (ADR 0014) and issues recovery
  codes to everyone else.
- `sessions`: temporary data.
- `contenttypes` and `auth.permission`: `migrate` recreates them on any
  target, and everything that references them goes through natural
  keys, not raw ids.

**Seeded rows:** the Organization seed migration creates a row with a
new random UUID on every fresh database. The restore replaces it with
the archive's row instead of loading a second Organization next to it.
Role groups are merged by name.

**Ghost migrations, found by the restore drill rather than by the
tests:** the local dev database records `accounts.0001_seed_roles`, a
migration that no longer exists in the code (role seeding moved to
`post_migrate`). A freshly migrated target can never have it, so
requiring every recorded migration made the first real drill fail. Only
migrations present in the code are required now. Unknown ones are
reported as warnings, because if the schema really doesn't fit,
`loaddata` fails and the transaction rolls back. Production was likely
migrated during the same period and may record the same migration.

## How verification is done (the restore drill)

`verify_backup` only proves an archive is intact. Proving it restores
means restoring it:

1. Export production (from a machine with the production env vars;
   Vercel's serverless runtime can't run management commands).
2. `verify_backup` the archive.
3. Create a throwaway local database, point `DATABASE_URL` at it, point
   `DJANGO_MEDIA_ROOT` (new optional setting) at a throwaway directory,
   `migrate`, then `restore_backup`.
4. Re-export from the throwaway database and compare `data.json` row
   for row with the original.

The full steps are in `api/README.md`. This drill was run on
2026-10-15 against the local dev database: every row came back
identical, and a second restore into the now-populated database was
refused without writing anything.

**Production drill, 2026-10-15:** a real production export (16 rows
across 6 models; no uploaded documents exist in production yet, so 0
files) passed `verify_backup` and was restored into a throwaway local
database. Re-exporting it gave identical row counts and identical rows.
Production records no ghost migrations. The drill database and files
were deleted afterwards. Document file restore has not yet been
exercised against Supabase Storage with real files, only in CI against
filesystem storage, because production has none to test with. `backups/tests.py` covers the same
round trip in CI, including document files, plus corruption detection,
missing-file handling, and every refusal path.

## Not built (deliberately)

- **Scheduled or automatic backups.** The obvious place, a GitHub
  Actions cron job, is wrong for this repo: it's public, and workflow
  artifacts would put passenger PII somewhere other people could reach
  it. Automation needs private storage first, which is a decision for
  the business owner.
- **Encryption of the archive.** The archive is plain zip. Encrypting
  it means choosing a key and where it's kept, which is not decided
  yet. For now: the export prints a warning, `angelcare-backup-*.zip`
  is gitignored, and storage location is the operator's
  responsibility.
- **Partial or selective restore, and merging into a live database.**
  Restore only goes into an empty database, by design. Merging is
  where silent data corruption happens.

## Consequences

- There is a tested, provider-independent way to get all of
  AngelCare's data out, and an actual proof (not an assumption) that it
  goes back in, including uploaded documents.
- Supabase's own backups are unchanged and still unverified as far as
  this repo knows. This ADR adds an independent layer; it doesn't
  replace them.
- Every new model is backed up automatically (dumpdata covers all
  installed apps not listed in `EXCLUDED_MODELS`), and so is every new
  `FileField` (found by type, not by a hard-coded list).
