"""
Write a full backup archive (database rows + uploaded files) -- ADR 0021.

Usage:
    python manage.py export_backup --output <directory>

Runs against whatever DATABASE_URL / storage settings are active, so
pointing it at production means running it from a machine with the
production env vars set (Vercel's serverless runtime can't run
management commands). The archive contains passenger PII and password
hashes: keep it out of the repo (it's gitignored by name) and anywhere
shared.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from backups.archive import BackupError, export_archive


class Command(BaseCommand):
    help = "Export every backed-up row and uploaded file into one verifiable zip archive."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Directory to write the archive into.")
        parser.add_argument(
            "--allow-missing-files",
            action="store_true",
            help="Export even if some referenced files can't be read from storage "
            "(they're recorded in the manifest instead of failing).",
        )

    def handle(self, output, allow_missing_files, **options):
        db = settings.DATABASES["default"]
        self.stdout.write(f"Exporting from {db.get('HOST') or db['NAME']} ...")
        try:
            path, manifest = export_archive(output, allow_missing_files=allow_missing_files)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc

        rows = sum(manifest["model_counts"].values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {path} ({path.stat().st_size:,} bytes): "
                f"{rows} rows across {len(manifest['model_counts'])} models, "
                f"{len(manifest['files'])} files."
            )
        )
        for missing in manifest["missing_files"]:
            self.stdout.write(self.style.WARNING(f"  missing file: {missing['name']}"))
        self.stdout.write(
            "This archive contains passenger PII and password hashes -- store it "
            "somewhere private, never in the repo."
        )
