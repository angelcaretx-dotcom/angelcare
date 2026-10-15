"""
Restore a backup archive into an empty database -- ADR 0021.

Usage:
    python manage.py migrate
    python manage.py restore_backup <archive.zip>

The target must be freshly migrated and otherwise empty (don't create a
superuser first); anything else is refused before a single row or file
is written. Staff MFA devices aren't in backups, so afterwards each
staff member needs `python manage.py bootstrap_totp <username>`.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from backups.archive import BackupError, restore_archive


class Command(BaseCommand):
    help = "Restore a backup archive into a freshly migrated, empty database."

    def add_arguments(self, parser):
        parser.add_argument("archive", help="Path to an angelcare-backup-*.zip file.")

    def handle(self, archive, **options):
        db = settings.DATABASES["default"]
        self.stdout.write(f"Restoring into {db.get('HOST') or ''}/{db['NAME']} ...")
        try:
            manifest, warnings = restore_archive(archive)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc

        for warning in warnings:
            self.stdout.write(self.style.WARNING(warning))
        rows = sum(manifest["model_counts"].values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Restored backup from {manifest['created_at']}: {rows} rows, "
                f"{len(manifest['files'])} files. Row counts re-checked after loading."
            )
        )
        self.stdout.write(
            "Staff MFA devices aren't included in backups: run "
            "`manage.py bootstrap_totp <username>` for each staff account."
        )
