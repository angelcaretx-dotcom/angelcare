# ADR 0012: Supabase Storage for document uploads

- Status: Accepted
- Date: 2026-08-26

## Context

ADR 0011 flagged that Document uploads (ADR 0009) don't work in
production at all -- Vercel's serverless runtime has no persistent
disk. Production needs a real object storage backend before this
feature is usable.

## Decision

**`documents/storage.py`: a custom Django `Storage` backend
(`SupabaseStorage`) using Supabase's own Storage REST API directly**,
not the S3-compatible endpoint. The S3-compatible route (via
`django-storages`) was the original plan, but its dedicated access
keys weren't readily locatable in the dashboard. Supabase's own
Storage API works with the same project secret key
(`sb_secret_...`) already available, and is a real, documented API
(the same one Supabase's own client libraries wrap) -- not a hack.

- Configured via `SUPABASE_STORAGE_URL`, `SUPABASE_STORAGE_KEY`,
  `SUPABASE_STORAGE_BUCKET` env vars. When unset (local dev),
  `STORAGES["default"]` falls back to plain `FileSystemStorage` --
  zero cloud dependency for local development, per ADR 0004.
- Bucket (`Documents`) is **private**, not public. `SupabaseStorage.url()`
  requests a short-lived **signed URL** (1 hour) from Supabase per
  access, rather than a plain object URL -- a plain URL would 401 for
  a browser following a link (e.g. from Django admin), since the
  browser can't attach our bearer token.
- `get_available_name()` still checks for collisions and dedupes, the
  same way `FileSystemStorage` does, even though Supabase's object API
  would otherwise silently overwrite on a name collision.

## Verification

Automated tests (`documents/test_storage.py`) mock all HTTP calls --
no real network/credentials needed to run them. Before wiring this
into Django, every operation was verified by hand against the real
Supabase project via curl: upload, download, sign, and delete all
confirmed working, including a signed URL fetched with zero auth
headers (proving it works the way a browser link actually would).
Then verified again through the real system end to end: a real browser
driving the production Django admin, uploading a file through the
`DocumentInline` on a driver's page, confirming the file landed in
Supabase Storage for real (HEAD request against the bucket), and that
the rendered "Currently: ..." link in the admin page is a real, working
signed URL. Test data (driver, document, and the storage file itself)
was cleaned up afterward.

## Consequences

- Document uploads now work in production, closing the second (of two)
  gaps flagged in ADR 0011.
- Coupled to Supabase's specific Storage REST API shape, not a generic
  S3 interface -- if Supabase were ever replaced, this backend would
  need rewriting (unlike an S3-compatible integration, which could
  point at a different S3-compatible provider with just a config
  change). Accepted tradeoff given the credential-discovery friction;
  revisit if Supabase Storage itself ever needs to be replaced.
- No version-chaining still (ADR 0009's existing limitation stands);
  re-uploading creates a new `Document` row and a new file path, but
  the storage backend itself doesn't manage that relationship.
