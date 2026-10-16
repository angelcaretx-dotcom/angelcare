import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

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
