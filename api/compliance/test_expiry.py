"""Phase 27 (ADR 0036): driver credentials, vehicle insurance, expiry reminders."""

from datetime import date, time, timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from drivers.models import CredentialType, DriverCredential, missing_credentials
from payers.models import Payer
from trips import standing
from trips.models import StandingOrder
from trips.test_dispatch import direct_trip
from trips.tests import make_passenger, make_vehicle

from .expiry import REMINDER_DAYS, expiring_items, send_reminders
from .models import ComplianceRecord, ExpiryReminder

TODAY = date.today()


def bare_driver(**overrides):
    """A driver with no credentials entered yet."""
    from drivers.models import Driver

    defaults = {
        "legal_name": "Robin Diaz",
        "phone": "817-555-0144",
        "employment_type": "employee",
        "license_number": "TX-DL-999",
        "license_expiration_date": TODAY + timedelta(days=400),
    }
    defaults.update(overrides)
    return Driver.objects.create(**defaults)


def credential(driver, kind, expires_on=None, completed_on=None):
    return DriverCredential.objects.create(
        driver=driver,
        credential_type=kind,
        completed_on=completed_on or TODAY - timedelta(days=10),
        expires_on=expires_on,
    )


def give_required(driver, **kwargs):
    for kind in (
        CredentialType.MVR,
        CredentialType.BACKGROUND_CHECK,
        CredentialType.DRUG_SCREEN,
        CredentialType.CPR_FIRST_AID,
        CredentialType.DEFENSIVE_DRIVING,
    ):
        credential(driver, kind, **kwargs)


class MissingCredentialTests(TestCase):
    def test_new_driver_lacks_every_required_credential(self):
        missing = missing_credentials(bare_driver(), service_type="ambulatory")
        self.assertEqual(len(missing), 5)
        self.assertIn("Criminal background check", missing)

    def test_wheelchair_trips_also_need_securement(self):
        driver = bare_driver()
        give_required(driver, expires_on=TODAY + timedelta(days=100))
        self.assertEqual(missing_credentials(driver, service_type="ambulatory"), [])
        self.assertEqual(missing_credentials(driver, service_type="wheelchair"), ["Wheelchair securement (e.g. PASS)"])

    def test_expired_counts_as_missing_and_the_newest_counts(self):
        driver = bare_driver()
        give_required(driver)
        credential(driver, CredentialType.CPR_FIRST_AID, expires_on=TODAY - timedelta(days=1), completed_on=TODAY - timedelta(days=5))
        self.assertEqual(missing_credentials(driver), ["CPR / first aid"])
        credential(driver, CredentialType.CPR_FIRST_AID, expires_on=TODAY + timedelta(days=700), completed_on=TODAY)
        self.assertEqual(missing_credentials(driver), [])

    def test_payer_can_require_more(self):
        driver = bare_driver()
        give_required(driver)
        payer = Payer.objects.create(name="Broker X", payer_type="broker", required_credentials=["wheelchair_securement"])
        self.assertEqual(missing_credentials(driver, payer=payer), ["Wheelchair securement (e.g. PASS)"])


class AssignmentTests(TestCase):
    def test_driver_without_credentials_cannot_be_assigned(self):
        trip = direct_trip(make_passenger(), service_type="ambulatory", driver=bare_driver(), vehicle=make_vehicle())
        with self.assertRaises(ValidationError) as caught:
            trip.full_clean()
        self.assertIn("missing or has expired", str(caught.exception.message_dict["driver"]))

    def test_trips_assigned_before_the_rule_keep_working(self):
        trip = direct_trip(make_passenger(), service_type="ambulatory", driver=bare_driver(), vehicle=make_vehicle())
        trip.save()  # assigned before ADR 0036
        trip.notes = "Gate code 1234"
        trip.full_clean()  # editing it is fine; re-assigning would be checked

    def test_vehicle_needs_current_insurance(self):
        driver = bare_driver()
        give_required(driver)
        for insurance in (None, TODAY - timedelta(days=1)):
            vehicle = make_vehicle(vin=f"1FTEW1EP0MFA{insurance and 1 or 2:05d}", insurance_expiration_date=insurance)
            with self.assertRaises(ValidationError) as caught:
                direct_trip(make_passenger(), service_type="ambulatory", driver=driver, vehicle=vehicle).full_clean()
            self.assertIn("insurance", str(caught.exception.message_dict["vehicle"]).lower())

    def test_standing_order_leaves_an_uncredentialed_regular_driver_off(self):
        order = StandingOrder.objects.create(
            passenger=make_passenger(),
            service_type="ambulatory",
            pickup_address="1 Main St",
            dropoff_address="2 Clinic Rd",
            weekdays=list(range(7)),
            pickup_time=time(9, 0),
            start_date=standing.texas_today() + timedelta(days=1),
            driver=bare_driver(),
        )
        made = standing.generate(order)
        self.assertTrue(made)
        self.assertTrue(all(t.driver_id is None for t in made))


class ReminderTests(TestCase):
    def setUp(self):
        ComplianceRecord.objects.all().delete()  # the company dates from the data migration
        self.driver = bare_driver(license_expiration_date=TODAY + timedelta(days=3000))
        self.cpr = credential(self.driver, CredentialType.CPR_FIRST_AID, expires_on=TODAY + timedelta(days=85))

    def test_each_reminder_point_once(self):
        self.assertEqual(send_reminders(TODAY), 1)  # 85 days left: the 90-day reminder
        self.assertEqual(send_reminders(TODAY), 0)  # same day again: nothing
        self.assertEqual(send_reminders(TODAY + timedelta(days=10)), 0)  # 75 left: still the 90 stage
        self.assertEqual(send_reminders(TODAY + timedelta(days=26)), 1)  # 59 left: 60-day reminder
        self.assertEqual(ExpiryReminder.objects.get().stage, 60)

    def test_missed_days_jump_straight_to_the_current_point(self):
        self.assertEqual(send_reminders(TODAY + timedelta(days=80)), 1)  # 5 left
        self.assertEqual(ExpiryReminder.objects.get().stage, 7)
        self.assertEqual(send_reminders(TODAY + timedelta(days=86)), 1)  # expired
        self.assertEqual(ExpiryReminder.objects.get().stage, 0)

    def test_renewal_starts_afresh(self):
        send_reminders(TODAY + timedelta(days=80))
        renewed = credential(self.driver, CredentialType.CPR_FIRST_AID, expires_on=TODAY + timedelta(days=170), completed_on=TODAY)
        self.assertEqual(send_reminders(TODAY + timedelta(days=81)), 1)
        self.assertTrue(ExpiryReminder.objects.filter(key__startswith=f"credential:{renewed.pk}").exists())

    def test_one_digest_email_without_rider_details(self):
        make_vehicle(  # insurance due in 6 days; inspection in 90 (the factory's default)
            insurance_expiration_date=TODAY + timedelta(days=6),
            registration_expiration_date=TODAY + timedelta(days=300),
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(send_reminders(TODAY), 3)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Robin Diaz — CPR / first aid", body)
        self.assertIn("Insurance", body)
        self.assertIn("Inspection", body)
        self.assertNotIn("Registration", body)
        self.assertIn("3 credentials expiring", mail.outbox[0].subject)

    def test_reminder_points(self):
        self.assertEqual(REMINDER_DAYS, (90, 60, 30, 7, 0))

    @override_settings(CRON_SECRET="s3cret")
    def test_daily_job_sends_them(self):
        response = self.client.get("/api/v1/cron/daily/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.json()["expiry_reminders"], 1)


class CompanyDatesTests(TestCase):
    def test_owner_dates_are_entered_and_covered_by_reminders(self):
        # As corrected by compliance.0004 from the owner's verified records.
        sam = ComplianceRecord.objects.get(name="SAM.gov entity registration")
        self.assertEqual(sam.expiration_date, date(2027, 11, 3))
        self.assertTrue(ComplianceRecord.objects.filter(name__contains="PEMS", expiration_date=date(2028, 12, 3)).exists())
        auto = ComplianceRecord.objects.get(name="Commercial auto insurance")
        self.assertEqual((auto.reference_number, auto.expiration_date), ("WCAN261110", date(2027, 10, 13)))
        self.assertFalse(ComplianceRecord.objects.filter(name__contains="vehicle-for-hire").exists())
        keys = {item.key for item in expiring_items(drivers=False, vehicles=False)}
        self.assertIn(f"compliance:{sam.pk}:2027-11-03", keys)


class AdminTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="admin1", password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name="Administrator"))
        login_with_otp(self.client, username="admin1", password="testpass123")

    def test_driver_page_shows_what_blocks_assignment(self):
        driver = bare_driver()
        credential(driver, CredentialType.MVR, expires_on=TODAY + timedelta(days=200))
        page = self.client.get(f"/admin/drivers/driver/{driver.pk}/change/").content.decode()
        self.assertIn("Credentials check", page)
        self.assertIn("missing", page)
        self.assertIn("OK until", page)

    def test_payer_required_credentials_saved_from_checkboxes(self):
        payer = Payer.objects.create(name="MTM", payer_type="broker")
        response = self.client.post(
            f"/admin/payers/payer/{payer.pk}/change/",
            {
                "name": "MTM",
                "payer_type": "broker",
                "contact_name": "",
                "phone": "",
                "email": "",
                "notes": "",
                "status": "active",
                "required_credentials": ["defensive_driving", "wheelchair_securement"],
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        payer.refresh_from_db()
        self.assertEqual(payer.required_credentials, ["defensive_driving", "wheelchair_securement"])

    def test_credential_added_on_the_driver_page_is_audited(self):
        driver = bare_driver()
        data = {
            "legal_name": driver.legal_name,
            "phone": driver.phone,
            "email": "",
            "employment_type": "employee",
            "license_number": driver.license_number,
            "license_expiration_date": driver.license_expiration_date.isoformat(),
            "status": "active",
            "notes": "",
            "credentials-TOTAL_FORMS": "1",
            "credentials-INITIAL_FORMS": "0",
            "credentials-0-credential_type": "mvr",
            "credentials-0-completed_on": TODAY.isoformat(),
            "credentials-0-expires_on": (TODAY + timedelta(days=365)).isoformat(),
            "credentials-0-reference": "MVR-1",
            "credentials-0-notes": "",
        }
        page = self.client.get(f"/admin/drivers/driver/{driver.pk}/change/").content.decode()
        import re

        for prefix in set(re.findall(r'name="([a-z_-]+)-TOTAL_FORMS"', page)) - {"credentials"}:
            data[f"{prefix}-TOTAL_FORMS"] = "0"
            data[f"{prefix}-INITIAL_FORMS"] = "0"
        response = self.client.post(f"/admin/drivers/driver/{driver.pk}/change/", data)
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.assertTrue(driver.credentials.filter(credential_type="mvr").exists())
        self.assertTrue(AuditLog.objects.filter(action="credential_saved", resource_id=str(driver.pk)).exists())
