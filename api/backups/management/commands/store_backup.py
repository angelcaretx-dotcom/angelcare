"""
Upload an encrypted backup archive to the private Supabase "backups"
bucket and prune old ones -- ADR 0026.

Usage:
    python manage.py store_backup <angelcare-backup-...zip.enc> [--keep-days 30]

Run by the nightly GitHub Actions job after export_backup, encryption,
and a decrypt-and-verify round trip. Output is deliberately minimal: the
job's logs are public (the repository is), so nothing about the data
itself is printed.
"""

from django.core.management.base import BaseCommand, CommandError

from backups.offsite import DEFAULT_BUCKET, OffsiteError, store


class Command(BaseCommand):
    help = "Store an encrypted backup archive in private Supabase Storage and prune old ones."

    def add_arguments(self, parser):
        parser.add_argument("archive")
        parser.add_argument("--bucket", default=DEFAULT_BUCKET)
        parser.add_argument("--keep-days", type=int, default=30)

    def handle(self, archive, bucket, keep_days, **options):
        try:
            result = store(archive, bucket=bucket, keep_days=keep_days)
        except OffsiteError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Stored {result.stored}; removed {len(result.deleted)} older than "
                f"{keep_days} days; {result.kept} backups kept."
            )
        )
