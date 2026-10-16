# ADR 0026: Nightly encrypted backups

- Status: Accepted
- Date: 2026-10-16

## Context

ADR 0021 built a verified backup archive (`export_backup`,
`verify_backup`, `restore_backup`) and proved a production restore, but
someone had to remember to run it, and the zip was plain text. It
deliberately left out scheduling, because the obvious scheduler, GitHub
Actions artifacts, would publish passenger data from a public repo,
and it left out encryption pending a key decision.

The owner chose (2026-10-16): store backups in a **private bucket in
the existing Supabase project**, **daily**, encrypted either way.

## Decision

**`.github/workflows/backup.yml`** runs nightly at 08:17 UTC (3:17 AM
Central in summer, 2:17 AM in winter) and on demand ("Run workflow").
Each run:

1. **Exports** with the ADR 0021 command against production (session
   pooler, port 5432), so it's the same consistent snapshot, including
   document files from Supabase Storage. Settings run with DEBUG off,
   as in production. The email backend is set to Django's dummy one,
   since otherwise the production settings refuse to start without SMTP
   credentials. A local rehearsal of the job caught exactly that.
2. **Encrypts** with OpenSSL: AES-256-CBC, salted, with the key derived
   from a passphrase by PBKDF2 over 600,000 iterations. It then
   **decrypts a copy, checks it's byte-for-byte identical, and runs
   `verify_backup` on it**, so an archive that can't be opened is never
   uploaded. CBC has no built-in integrity tag; the archive's own
   SHA-256 manifest (ADR 0021) covers integrity after decryption. Only
   the encrypted file leaves the runner.
3. **Stores** it with the new `manage.py store_backup`
   (`backups/offsite.py`) in a Supabase Storage bucket named `backups`.
   The bucket is created private if missing, and the command **refuses
   to upload if it's ever public**. Archives older than 30 days are
   deleted, but **the newest is never deleted**, so a job that has been
   failing for weeks can't prune away the last good backup. Files in the
   bucket that aren't backups are never touched. The upload is
   confirmed by listing the bucket afterwards.

**Public logs.** The repository, and so its Actions logs, are public.
Every step that touches data discards its output and prints only a
generic line ("Export OK." / "Export failed (run ... locally for
details)."). Even an error message could leak something: a missing
document's file name might contain a person's name. `export_backup`
and `verify_backup` are now completely silent at `-v 0`. Secrets are
GitHub Actions secrets, which are masked in logs and never given to
fork pull requests.

**Failure alerts:** GitHub emails the account behind the schedule when
a scheduled run fails. There's no other alerting.

**The passphrase** is generated once by a setup script on the owner's
machine, shown once, and stored only as a GitHub secret (write-only)
and in the owner's password manager. If it's lost, every backup is
lost too. The script won't save anything until the owner types back
the end of the passphrase to confirm they've recorded it.

## Restoring from a nightly backup

In the Supabase dashboard, go to Storage, then the `backups` bucket,
and download the newest `angelcare-backup-...zip.enc`. Decrypt it with
OpenSSL (Git for Windows includes it), verify, then restore as in
ADR 0021. The exact commands are in `api/README.md`, under "Backups".

## Not built (deliberately)

- **A second provider.** A copy outside Supabase would survive losing
  the Supabase account itself. That was a deliberate trade-off for no
  new accounts or cost. The archive format is provider-independent, so
  adding a second destination later is one more upload step.
- **A dashboard indicator for "last backup".** The job is in GitHub,
  and the admin has no access to the bucket's state without new
  plumbing. GitHub's failure emails are the signal for now.
- **Encryption with a public key** (age or GPG), where the job could
  encrypt but not decrypt. That's stronger if the GitHub secret leaked,
  but adds tooling the owner would have to manage. Worth revisiting.

## Consequences

- Backups happen every night without anyone remembering, are verified
  before they're kept, and are unreadable without the passphrase,
  including to Supabase and to anyone reading the public logs.
- GitHub disables scheduled workflows in repositories with no activity
  for 60 days. If the repo goes quiet, re-enable the workflow in the
  Actions tab, or push any commit.
