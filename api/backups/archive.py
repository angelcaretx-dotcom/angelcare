"""
Provider-independent backup archives: export, verify, restore (ADR 0021).

One archive is a single zip file:

    manifest.json   format version, creation time, applied migrations,
                    per-model row counts, SHA-256 of every member
    data.json       every backed-up row (Django's JSON serialization,
                    natural keys for users/groups/content types)
    files/<name>    every file a FileField points at (e.g. Document
                    uploads), under its exact storage name

It deliberately doesn't depend on Supabase (or any host) being the one
to keep or restore it -- it's plain JSON + the original files, readable
without this codebase at all if it ever came to that.

What's left out, on purpose (EXCLUDED_MODELS):
- contenttypes / auth.permission: recreated by `migrate` on any target;
  everything referencing them uses natural keys instead of raw ids.
- sessions: ephemeral.
- otp_totp: staff MFA secrets. A stolen archive must not also hand over
  everyone's second factor; after a restore, staff re-enroll via
  `manage.py bootstrap_totp` (ADR 0014).
"""

import hashlib
import io
import json
import os
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import django
from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import connection, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

FORMAT_VERSION = 1

EXCLUDED_MODELS = ["contenttypes", "auth.permission", "sessions", "otp_totp"]

# Rows `migrate` itself creates on a fresh database. A restore target is
# allowed to already contain these (and only these):
# - organization.organization is replaced by the archive's copy (the
#   seed migration creates one with a fresh random UUID, so loading the
#   archive's row alongside it would leave two Organization records);
# - auth.group is merged by natural key (name), so seeded roles are
#   updated in place to the archive's permissions.
REPLACED_SEEDED_MODELS = {"organization.organization"}
MERGED_SEEDED_MODELS = {"auth.group"}

MANIFEST_NAME = "manifest.json"
DATA_NAME = "data.json"
FILES_PREFIX = "files/"


class BackupError(Exception):
    pass


@dataclass
class VerifyResult:
    manifest: dict
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_fields_by_model() -> dict[str, list[str]]:
    """{"app.model": [file field names]} for every installed model with one."""
    result = {}
    for model in apps.get_models():
        names = [f.name for f in model._meta.fields if isinstance(f, models.FileField)]
        if names:
            result[model._meta.label_lower] = names
    return result


def _referenced_file_names(records: list[dict]) -> set[str]:
    file_fields = _file_fields_by_model()
    names = set()
    for record in records:
        for field_name in file_fields.get(record["model"], []):
            value = record["fields"].get(field_name)
            if value:
                names.add(value)
    return names


def export_archive(output_dir, *, allow_missing_files: bool = False) -> tuple[Path, dict]:
    """
    Write a new archive into output_dir. Returns (archive path, manifest).

    All database reads happen in one transaction; on PostgreSQL it's
    REPEATABLE READ, so the export is a single consistent snapshot even
    if (say) a trip request is submitted mid-export.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = timezone.now()

    # Only the outermost transaction can set its isolation level (inside
    # an existing one, e.g. a TestCase, this is a savepoint instead).
    outermost = not connection.in_atomic_block
    with transaction.atomic():
        if outermost and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        buffer = io.StringIO()
        call_command(
            "dumpdata",
            exclude=EXCLUDED_MODELS,
            natural_foreign=True,
            natural_primary=True,
            indent=2,
            stdout=buffer,
        )
        applied = sorted(MigrationRecorder(connection).applied_migrations().keys())

    data = buffer.getvalue().encode("utf-8")
    records = json.loads(data)

    files, missing, contents = [], [], {}
    for name in sorted(_referenced_file_names(records)):
        try:
            with default_storage.open(name, "rb") as fh:
                content = fh.read()
        except Exception as exc:  # backend-specific (OSError, HTTPError, ...)
            missing.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        contents[name] = content
        files.append(
            {
                "name": name,
                "archive_path": FILES_PREFIX + name,
                "size": len(content),
                "sha256": _sha256(content),
            }
        )

    if missing and not allow_missing_files:
        listed = "\n".join(f"  {m['name']} ({m['error']})" for m in missing)
        raise BackupError(
            f"{len(missing)} referenced file(s) couldn't be read from storage:\n{listed}\n"
            "Nothing was written. Re-run with --allow-missing-files to export anyway "
            "(they'll be listed in the manifest)."
        )

    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": created_at.isoformat(),
        "django_version": django.get_version(),
        "database_vendor": connection.vendor,
        "applied_migrations": [list(m) for m in applied],
        "excluded_models": EXCLUDED_MODELS,
        "model_counts": dict(sorted(Counter(r["model"] for r in records).items())),
        "data": {"path": DATA_NAME, "size": len(data), "sha256": _sha256(data)},
        "files": files,
        "missing_files": missing,
    }

    final_path = output_dir / f"angelcare-backup-{created_at:%Y%m%dT%H%M%SZ}.zip"
    # Written under a temporary name and renamed at the end, so a crash
    # mid-write never leaves a truncated file that looks like a backup.
    partial_path = final_path.with_suffix(".zip.partial")
    with zipfile.ZipFile(partial_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(DATA_NAME, data)
        for entry in files:
            zf.writestr(entry["archive_path"], contents[entry["name"]])
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    os.replace(partial_path, final_path)
    return final_path, manifest


def verify_archive(path) -> VerifyResult:
    """
    Check an archive is complete and internally consistent, without
    touching the database: every checksum, every row count, and every
    file the data refers to.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"Not a readable zip archive: {exc}") from exc

    with zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            raise BackupError(f"Corrupt archive member: {bad_member}")
        members = set(zf.namelist())
        if MANIFEST_NAME not in members:
            raise BackupError("Archive has no manifest.json -- not an AngelCare backup.")
        manifest = json.loads(zf.read(MANIFEST_NAME))
        result = VerifyResult(manifest=manifest)

        if manifest.get("format_version") != FORMAT_VERSION:
            raise BackupError(
                f"Unsupported format_version {manifest.get('format_version')!r} "
                f"(this code reads version {FORMAT_VERSION})."
            )

        if DATA_NAME not in members:
            result.problems.append("data.json is missing.")
            return result
        data = zf.read(DATA_NAME)
        if _sha256(data) != manifest["data"]["sha256"]:
            result.problems.append("data.json checksum doesn't match the manifest.")
        try:
            records = json.loads(data)
        except json.JSONDecodeError as exc:
            result.problems.append(f"data.json isn't valid JSON: {exc}")
            return result

        counts = dict(Counter(r.get("model") for r in records))
        if counts != manifest["model_counts"]:
            result.problems.append(
                f"Row counts in data.json {counts} don't match the manifest "
                f"{manifest['model_counts']}."
            )
        for label in counts:
            try:
                apps.get_model(label)
            except (LookupError, ValueError, TypeError):
                result.problems.append(f"data.json contains unknown model {label!r}.")

        archived = {}
        for entry in manifest["files"]:
            archived[entry["name"]] = entry
            if entry["archive_path"] not in members:
                result.problems.append(f"File missing from archive: {entry['name']}")
                continue
            content = zf.read(entry["archive_path"])
            if len(content) != entry["size"] or _sha256(content) != entry["sha256"]:
                result.problems.append(f"File checksum mismatch: {entry['name']}")

        known_missing = {m["name"] for m in manifest.get("missing_files", [])}
        for name in sorted(_referenced_file_names(records)):
            if name not in archived and name not in known_missing:
                result.problems.append(f"Data refers to a file not in the archive: {name}")

    return result


def restore_archive(path) -> tuple[dict, list[str]]:
    """
    Restore an archive into the current (freshly migrated, empty)
    database and default file storage. Returns (manifest, warnings).

    Refuses rather than merging: if any backed-up table already holds
    rows (other than the seeded ones above), nothing is written.
    """
    result = verify_archive(path)
    if not result.ok:
        raise BackupError("Archive failed verification:\n  " + "\n  ".join(result.problems))
    manifest = result.manifest

    # Only migrations that exist in this codebase are required on the
    # target. A database can record "ghost" migrations whose files were
    # later removed or renamed (the local dev database had one,
    # accounts.0001_seed_roles, from before role seeding moved to
    # post_migrate -- found by a real restore drill), and a fresh target
    # can never apply those. Unknown ones are reported, not fatal: if
    # the schema genuinely doesn't fit, loaddata fails and rolls back.
    applied = set(MigrationRecorder(connection).applied_migrations().keys())
    on_disk = set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
    backup_migrations = [tuple(m) for m in manifest["applied_migrations"]]
    not_applied = [m for m in backup_migrations if m in on_disk and m not in applied]
    warnings = [
        f"Backup records migration {app}.{name}, which this codebase doesn't have."
        for app, name in backup_migrations
        if (app, name) not in on_disk and (app, name) not in applied
    ]
    if not_applied:
        listed = ", ".join(f"{app}.{name}" for app, name in not_applied)
        raise BackupError(
            f"Target database is missing migrations the backup was taken with: {listed}. "
            "Run `manage.py migrate` (with code at least as new as the backup) first."
        )

    seeded = REPLACED_SEEDED_MODELS | MERGED_SEEDED_MODELS
    occupied = [
        label
        for label in manifest["model_counts"]
        if label not in seeded and apps.get_model(label)._default_manager.exists()
    ]
    if occupied:
        raise BackupError(
            "Target database already has data in: " + ", ".join(sorted(occupied)) + ". "
            "Restore only into a freshly migrated, empty database (create no users first)."
        )

    with zipfile.ZipFile(path) as zf:
        # Files first: they're idempotent (identical existing files are
        # skipped), so a failure here leaves the database untouched.
        for entry in manifest["files"]:
            content = zf.read(entry["archive_path"])
            name = entry["name"]
            if default_storage.exists(name):
                with default_storage.open(name, "rb") as fh:
                    if _sha256(fh.read()) != entry["sha256"]:
                        raise BackupError(
                            f"Storage already has a different file at {name}; not overwriting."
                        )
                continue
            saved = default_storage.save(name, ContentFile(content))
            if saved != name:
                raise BackupError(f"Storage saved {name} as {saved}; expected the exact name.")

        data = zf.read(DATA_NAME)

    fd, data_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with transaction.atomic():
            for label in REPLACED_SEEDED_MODELS & set(manifest["model_counts"]):
                apps.get_model(label)._default_manager.all().delete()
            call_command("loaddata", data_path, verbosity=0)

            for label, expected in manifest["model_counts"].items():
                actual = apps.get_model(label)._default_manager.count()
                if label in MERGED_SEEDED_MODELS:
                    ok = actual >= expected
                else:
                    ok = actual == expected
                if not ok:
                    raise BackupError(
                        f"After loading, {label} has {actual} rows; the backup has {expected}. "
                        "Rolled back."
                    )
    finally:
        os.unlink(data_path)

    return manifest, warnings
