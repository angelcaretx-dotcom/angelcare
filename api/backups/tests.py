import io
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import Group, User
from django.core import serializers
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.mfa import generate_backup_codes
from audit.models import AuditLog
from claims.tests import make_claim
from compliance.models import ComplianceRecord
from documents.models import Document
from organization.models import Organization
from passengers.models import Passenger

from . import offsite
from .archive import (
    MERGED_SEEDED_MODELS,
    BackupError,
    export_archive,
    restore_archive,
    verify_archive,
)

TEMP_MEDIA_ROOT = tempfile.mkdtemp()


def snapshot(labels) -> dict:
    """Every row of each model, serialized with natural keys, order-independent."""
    result = {}
    for label in labels:
        queryset = apps.get_model(label)._default_manager.all()
        rows = json.loads(
            serializers.serialize(
                "json", queryset, use_natural_foreign_keys=True, use_natural_primary_keys=True
            )
        )
        result[label] = sorted(json.dumps(row, sort_keys=True) for row in rows)
    return result


def wipe_to_fresh_database(manifest):
    """
    Leave the database as `migrate` would on a brand-new deployment:
    every backed-up table empty except the seeded roles, plus a seeded
    Organization with a different id than the archived one.
    """
    remaining = [
        apps.get_model(label)
        for label in manifest["model_counts"]
        if label not in MERGED_SEEDED_MODELS
    ]
    # PROTECT foreign keys (e.g. Trip.vehicle) block deleting a parent
    # before its children; keep sweeping until every table is empty.
    while remaining:
        blocked = []
        for model in remaining:
            try:
                model._default_manager.all().delete()
            except ProtectedError:
                blocked.append(model)
        if len(blocked) == len(remaining):
            raise AssertionError(f"Can't empty: {blocked}")
        remaining = blocked
    Organization.objects.create(legal_name="AngelCare Transit", phone="817-766-9228")


def rewrite_member(archive: Path, member: str, transform):
    """Rewrite one member of a zip in place (to simulate corruption)."""
    with zipfile.ZipFile(archive) as zf:
        contents = {name: zf.read(name) for name in zf.namelist()}
    contents[member] = transform(contents[member])
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in contents.items():
            zf.writestr(name, data)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class BackupArchiveTests(TestCase):
    def setUp(self):
        self.output_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, TEMP_MEDIA_ROOT, ignore_errors=True)

        self.staff = User.objects.create_user(
            username="admin1", password="testpass123", is_staff=True
        )
        self.staff.groups.add(Group.objects.get(name="Administrator"))
        TOTPDevice.objects.create(user=self.staff, name="phone", confirmed=True)
        self.backup_codes = generate_backup_codes(self.staff, actor=self.staff)

        claim = make_claim()
        self.driver = claim.invoice.trip.driver
        self.document = Document.objects.create(
            content_object=self.driver,
            document_type="driver_license",
            file=SimpleUploadedFile("license.pdf", b"real pdf bytes"),
            uploaded_by=self.staff,
            verified_by=self.staff,
            status="verified",
        )
        ComplianceRecord.objects.create(name="General Liability", record_type="insurance")
        AuditLog.objects.create(
            actor=self.staff,
            action="status_change",
            resource_type="trips.trip",
            resource_id=str(claim.invoice.trip.id),
            before={"status": "scheduled"},
            after={"status": "completed"},
            source="admin",
        )

    def test_round_trip_restores_every_row_and_file_exactly(self):
        archive, manifest = export_archive(self.output_dir)
        self.assertTrue(verify_archive(archive).ok)
        labels = list(manifest["model_counts"])
        before = snapshot(labels)
        file_name = self.document.file.name

        wipe_to_fresh_database(manifest)
        Path(TEMP_MEDIA_ROOT, file_name).unlink()
        self.assertEqual(Passenger.objects.count(), 0)

        restore_archive(archive)

        self.assertEqual(snapshot(labels), before)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Path(TEMP_MEDIA_ROOT, file_name).read_bytes(), b"real pdf bytes")
        restored_user = User.objects.get(username="admin1")
        self.assertTrue(restored_user.check_password("testpass123"))
        self.assertTrue(restored_user.groups.filter(name="Administrator").exists())

    def test_archive_covers_every_domain_and_excludes_mfa_secrets_and_sessions(self):
        self.client.login(username="admin1", password="testpass123")  # creates a session
        archive, manifest = export_archive(self.output_dir)
        counts = manifest["model_counts"]
        for label in [
            "auth.user", "auth.group", "passengers.passenger", "drivers.driver",
            "vehicles.vehicle", "transportation.triprequest", "trips.trip",
            "payers.payer", "billing.invoice", "claims.claim", "documents.document",
            "compliance.compliancerecord", "organization.organization", "audit.auditlog",
        ]:
            self.assertGreaterEqual(counts.get(label, 0), 1, label)
        for label in counts:
            self.assertFalse(
                label.startswith(("otp_totp.", "otp_static.", "sessions.", "contenttypes.")), label
            )
            self.assertNotEqual(label, "auth.permission")
        with zipfile.ZipFile(archive) as zf:
            data = zf.read("data.json")
            self.assertNotIn(b"otpauth", data)
            for code in self.backup_codes:
                self.assertNotIn(code.encode(), data)
            self.assertIn("files/" + self.document.file.name, zf.namelist())

    def test_verify_detects_a_corrupted_file(self):
        archive, _ = export_archive(self.output_dir)
        rewrite_member(archive, "files/" + self.document.file.name, lambda b: b + b"x")
        problems = verify_archive(archive).problems
        self.assertTrue(any("checksum mismatch" in p for p in problems), problems)

    def test_verify_detects_edited_data(self):
        archive, _ = export_archive(self.output_dir)
        rewrite_member(archive, "data.json", lambda b: b.replace(b"Morgan Lee", b"Someone Else"))
        problems = verify_archive(archive).problems
        self.assertTrue(any("data.json checksum" in p for p in problems), problems)

    def test_verify_rejects_a_non_backup_file(self):
        not_a_zip = self.output_dir / "notes.zip"
        not_a_zip.write_text("hello")
        with self.assertRaises(BackupError):
            verify_archive(not_a_zip)

    def test_export_refuses_when_a_referenced_file_is_missing(self):
        Path(TEMP_MEDIA_ROOT, self.document.file.name).unlink()
        with self.assertRaisesMessage(BackupError, "couldn't be read from storage"):
            export_archive(self.output_dir)
        self.assertEqual(list(self.output_dir.iterdir()), [])

    def test_export_can_record_missing_files_instead(self):
        Path(TEMP_MEDIA_ROOT, self.document.file.name).unlink()
        archive, manifest = export_archive(self.output_dir, allow_missing_files=True)
        self.assertEqual([m["name"] for m in manifest["missing_files"]], [self.document.file.name])
        self.assertTrue(verify_archive(archive).ok)

    def test_restore_refuses_a_database_that_already_has_data(self):
        archive, _ = export_archive(self.output_dir)
        passengers_before = Passenger.objects.count()
        with self.assertRaisesMessage(BackupError, "already has data"):
            restore_archive(archive)
        self.assertEqual(Passenger.objects.count(), passengers_before)

    def test_restore_refuses_when_target_lacks_backup_migrations(self):
        archive, manifest = export_archive(self.output_dir)
        wipe_to_fresh_database(manifest)
        # Simulate a target migrated with older code than the backup.
        latest = max(name for app, name in manifest["applied_migrations"] if app == "trips")
        MigrationRecorder.Migration.objects.filter(app="trips", name=latest).delete()

        with self.assertRaisesMessage(BackupError, f"trips.{latest}"):
            restore_archive(archive)
        self.assertEqual(Passenger.objects.count(), 0)

    def test_restore_only_warns_about_migrations_this_codebase_never_had(self):
        # e.g. a "ghost" migration recorded in the source database whose
        # file was later removed -- a fresh target can never apply it.
        archive, manifest = export_archive(self.output_dir)

        def add_ghost_migration(raw):
            data = json.loads(raw)
            data["applied_migrations"].append(["accounts", "0001_seed_roles"])
            return json.dumps(data).encode()

        rewrite_member(archive, "manifest.json", add_ghost_migration)
        wipe_to_fresh_database(manifest)
        _, warnings = restore_archive(archive)
        self.assertEqual(
            warnings,
            ["Backup records migration accounts.0001_seed_roles, which this codebase doesn't have."],
        )
        self.assertEqual(Passenger.objects.count(), manifest["model_counts"]["passengers.passenger"])

    def test_commands_export_and_verify(self):
        out = io.StringIO()
        call_command("export_backup", output=str(self.output_dir), stdout=out)
        self.assertIn("PII", out.getvalue())
        (archive,) = self.output_dir.glob("angelcare-backup-*.zip")

        out = io.StringIO()
        call_command("verify_backup", str(archive), stdout=out)
        self.assertIn("OK: backup from", out.getvalue())

        rewrite_member(archive, "data.json", lambda b: b + b" ")
        with self.assertRaisesMessage(CommandError, "Verification FAILED"):
            call_command("verify_backup", str(archive), stdout=io.StringIO())


class FakeSupabaseStorage:
    """In-memory stand-in for the Supabase Storage REST endpoints offsite.py uses."""

    def __init__(self, buckets=None):
        self.buckets = buckets if buckets is not None else {}  # name -> {"public": bool, "objects": {}}

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def _path(self, url):
        return url.split("/storage/v1/", 1)[1]

    def get(self, url, **kwargs):
        name = self._path(url).removeprefix("bucket/")
        if name in self.buckets:
            return self.Response(200, {"id": name, "public": self.buckets[name]["public"]})
        return self.Response(404, {})

    def post(self, url, headers=None, json=None, data=None, **kwargs):
        path = self._path(url)
        if path == "bucket":
            self.buckets[json["id"]] = {"public": json["public"], "objects": {}}
            return self.Response(200, {})
        if path.startswith("object/list/"):
            bucket = path.removeprefix("object/list/")
            return self.Response(200, [{"name": n} for n in sorted(self.buckets[bucket]["objects"])])
        bucket, name = path.removeprefix("object/").split("/", 1)
        self.buckets[bucket]["objects"][name] = data.read()
        return self.Response(200, {"Key": f"{bucket}/{name}"})

    def delete(self, url, json=None, **kwargs):
        bucket = self._path(url).removeprefix("object/")
        for name in json["prefixes"]:
            self.buckets[bucket]["objects"].pop(name, None)
        return self.Response(200, [])


@override_settings(SUPABASE_STORAGE_URL="https://example.supabase.co", SUPABASE_STORAGE_KEY="test-key")
class OffsiteStoreTests(TestCase):
    NOW = datetime(2026, 11, 2, 8, 0, tzinfo=dt_timezone.utc)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeSupabaseStorage()
        for method in ("get", "post", "delete"):
            patcher = mock.patch(f"backups.offsite.requests.{method}", getattr(self.fake, method))
            patcher.start()
            self.addCleanup(patcher.stop)

    def archive(self, when):
        path = self.tmp / f"angelcare-backup-{when:%Y%m%dT%H%M%SZ}.zip.enc"
        path.write_bytes(b"encrypted bytes")
        return path

    def test_creates_a_private_bucket_and_stores_the_archive(self):
        result = offsite.store(self.archive(self.NOW), now=self.NOW)
        self.assertEqual(self.fake.buckets["backups"]["public"], False)
        self.assertEqual(list(self.fake.buckets["backups"]["objects"]), [result.stored])
        self.assertEqual(self.fake.buckets["backups"]["objects"][result.stored], b"encrypted bytes")

    def test_prunes_backups_older_than_the_retention_window(self):
        seeded_at = self.NOW - timedelta(days=41)  # seed without pruning anything yet
        for days in (40, 31, 29, 1):
            offsite.store(self.archive(self.NOW - timedelta(days=days)), now=seeded_at)
        result = offsite.store(self.archive(self.NOW), now=self.NOW, keep_days=30)
        kept = sorted(self.fake.buckets["backups"]["objects"])
        self.assertEqual(len(result.deleted), 2)
        self.assertEqual(len(kept), 3)
        self.assertTrue(all(offsite.backup_time(n) >= self.NOW - timedelta(days=30) for n in kept))

    def test_never_deletes_the_newest_backup_even_if_old(self):
        # A job that was broken for weeks must not prune away the last good backup.
        old = self.NOW - timedelta(days=90)
        offsite.store(self.archive(old), now=old)
        self.fake.buckets["backups"]["objects"]["unrelated-notes.txt"] = b"x"
        result = offsite.store(self.archive(old + timedelta(seconds=1)), now=self.NOW)
        self.assertEqual(len(result.deleted), 1)
        self.assertIn(result.stored, self.fake.buckets["backups"]["objects"])
        self.assertIn("unrelated-notes.txt", self.fake.buckets["backups"]["objects"])  # not ours: untouched

    def test_refuses_a_public_bucket(self):
        self.fake.buckets["backups"] = {"public": True, "objects": {}}
        with self.assertRaisesMessage(offsite.OffsiteError, "public"):
            offsite.store(self.archive(self.NOW), now=self.NOW)
        self.assertEqual(self.fake.buckets["backups"]["objects"], {})

    def test_bucket_that_already_exists_but_wasnt_found_at_first_is_fine(self):
        # Supabase can answer both "not found" and "already exists" with 400.
        real_get = self.fake.get
        calls = []

        def flaky_get(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return self.fake.Response(400, {"statusCode": "404", "error": "Bucket not found"})
            return real_get(url, **kwargs)

        self.fake.buckets["backups"] = {"public": False, "objects": {}}
        real_post = self.fake.post

        def post(url, **kwargs):
            if url.endswith("/storage/v1/bucket"):
                return self.fake.Response(400, {"statusCode": "409", "error": "Duplicate"})
            return real_post(url, **kwargs)

        with mock.patch("backups.offsite.requests.get", flaky_get), mock.patch("backups.offsite.requests.post", post):
            result = offsite.store(self.archive(self.NOW), now=self.NOW)
        self.assertIn(result.stored, self.fake.buckets["backups"]["objects"])

    def test_bucket_failure_names_supabases_reason(self):
        refused = self.fake.Response(400, {"statusCode": "403", "error": "Unauthorized", "message": "secret stuff"})
        with mock.patch("backups.offsite.requests.get", return_value=refused), mock.patch(
            "backups.offsite.requests.post", return_value=refused
        ):
            with self.assertRaises(offsite.OffsiteError) as caught:
                offsite.store(self.archive(self.NOW), now=self.NOW)
        self.assertIn("HTTP 400 (Unauthorized)", str(caught.exception))
        self.assertNotIn("secret stuff", str(caught.exception))

    def test_refuses_unencrypted_or_misnamed_files(self):
        plain = self.tmp / "angelcare-backup-20261102T080000Z.zip"
        plain.write_bytes(b"PK")
        with self.assertRaises(offsite.OffsiteError):
            offsite.store(plain, now=self.NOW)
        self.assertEqual(self.fake.buckets, {})


class QuietCommandTests(TestCase):
    @override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
    def test_verbosity_zero_prints_nothing(self):
        out_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        out = io.StringIO()
        call_command("export_backup", output=str(out_dir), verbosity=0, stdout=out)
        (archive,) = out_dir.glob("angelcare-backup-*.zip")
        call_command("verify_backup", str(archive), verbosity=0, stdout=out)
        self.assertEqual(out.getvalue(), "")
