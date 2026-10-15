"""
Check a backup archive is complete and uncorrupted -- ADR 0021.

Usage:
    python manage.py verify_backup <archive.zip>

Read-only: never touches the database. Exits non-zero on any problem.
This proves the archive is intact; only `restore_backup` into a scratch
database proves it actually restores.
"""

from django.core.management.base import BaseCommand, CommandError

from backups.archive import BackupError, verify_archive


class Command(BaseCommand):
    help = "Verify a backup archive's checksums, row counts, and file references."

    def add_arguments(self, parser):
        parser.add_argument("archive", help="Path to an angelcare-backup-*.zip file.")

    def handle(self, archive, **options):
        try:
            result = verify_archive(archive)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc

        manifest = result.manifest
        if not result.ok:
            raise CommandError("Verification FAILED:\n  " + "\n  ".join(result.problems))

        rows = sum(manifest["model_counts"].values())
        self.stdout.write(
            self.style.SUCCESS(
                f"OK: backup from {manifest['created_at']}, {rows} rows across "
                f"{len(manifest['model_counts'])} models, {len(manifest['files'])} files."
            )
        )
        for label, count in manifest["model_counts"].items():
            self.stdout.write(f"  {label}: {count}")
        for missing in manifest.get("missing_files", []):
            self.stdout.write(
                self.style.WARNING(f"  recorded as missing at export: {missing['name']}")
            )
