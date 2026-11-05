import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import EmailVerification

START = "/api/v1/verify/start/"
CHECK = "/api/v1/verify/check/"


def code_from(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


class CodeFlowTests(TestCase):
    def setUp(self):
        cache.clear()  # throttle counters
        self.addCleanup(cache.clear)
        self.client = APIClient()

    def start(self, email="Pat@Example.com", **extra):
        data = {"email": email, "purpose": "contact", "elapsed_ms": 9000, **extra}
        return self.client.post(START, data, format="json")

    def test_code_emailed_then_exchanged_for_a_token(self):
        response = self.start()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["pat@example.com"])
        code = code_from(message)
        self.assertIn(code, message.body)
        # The code itself is never stored.
        self.assertNotIn(code, EmailVerification.objects.get().code_hash)

        checked = self.client.post(
            CHECK, {"verification_id": response.json()["verification_id"], "code": code}, format="json"
        )
        self.assertEqual(checked.status_code, 200, checked.content)
        token = checked.json()["token"]
        self.assertTrue(services.token_is_valid(token, "PAT@example.com"))
        self.assertFalse(services.token_is_valid(token, "someone@example.com"))
        # Single use.
        again = self.client.post(
            CHECK, {"verification_id": response.json()["verification_id"], "code": code}, format="json"
        )
        self.assertEqual(again.status_code, 400)

    def test_five_wrong_codes_and_the_code_is_dead(self):
        verification_id = self.start().json()["verification_id"]
        code = code_from(mail.outbox[0])
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(services.MAX_ATTEMPTS):
            response = self.client.post(CHECK, {"verification_id": verification_id, "code": wrong}, format="json")
            self.assertEqual(response.status_code, 400)
        response = self.client.post(CHECK, {"verification_id": verification_id, "code": code}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["detail"])

    def test_expired_code_and_expired_token(self):
        verification_id = self.start().json()["verification_id"]
        EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.post(
            CHECK, {"verification_id": verification_id, "code": code_from(mail.outbox[0])}, format="json"
        )
        self.assertEqual(response.status_code, 400)

        token = services.issue_token("pat@example.com")
        EmailVerification.objects.update(token_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(services.token_is_valid(token, "pat@example.com"))

    def test_codes_per_address_are_capped(self):
        for _ in range(services.MAX_CODES_PER_15_MINUTES):
            self.assertEqual(self.start().status_code, 200)
        response = self.start()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), services.MAX_CODES_PER_15_MINUTES)

    def test_trap_field_gets_a_fake_success_and_no_email(self):
        response = self.start(company_website="http://spam.example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerification.objects.exists())

    def test_too_fast_or_untimed_is_refused(self):
        self.assertEqual(self.start(elapsed_ms=400).status_code, 400)
        response = self.client.post(START, {"email": "pat@example.com", "purpose": "contact"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(TURNSTILE_SECRET_KEY="test-secret")
class TurnstileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def start(self, token):
        return APIClient().post(
            START,
            {"email": "pat@example.com", "purpose": "booking", "elapsed_ms": 9000, "turnstile_token": token},
            format="json",
        )

    @patch("verification.bots.requests.post")
    def test_cloudflare_decides(self, post):
        post.return_value.json.return_value = {"success": False}
        self.assertEqual(self.start("bad").status_code, 400)
        post.return_value.json.return_value = {"success": True}
        self.assertEqual(self.start("good").status_code, 200)
        self.assertEqual(post.call_args.kwargs["data"]["secret"], "test-secret")

    def test_without_turnstile_only_a_few_codes_an_hour(self):
        # The widget couldn't load (ad-blocker, work network): still let a
        # person through, but cap it per visitor.
        from .bots import NO_TURNSTILE_PER_HOUR

        for n in range(NO_TURNSTILE_PER_HOUR):
            response = APIClient().post(
                START,
                {"email": f"p{n}@example.com", "purpose": "contact", "elapsed_ms": 9000},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.start("").status_code, 400)
        self.assertEqual(len(mail.outbox), NO_TURNSTILE_PER_HOUR)

    @patch("verification.bots.requests.post", side_effect=__import__("requests").ConnectionError)
    def test_cloudflare_outage_falls_back_to_the_email_code(self, post):
        self.assertEqual(self.start("any").status_code, 200)
        self.assertEqual(len(mail.outbox), 1)


class BookingNeedsConfirmedEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_booking_without_or_with_someone_elses_token_is_refused(self):
        from transportation.tests import valid_payload

        client = APIClient()
        for token in ("", services.issue_token("other@example.com")):
            response = client.post(
                "/api/v1/trip-requests/", valid_payload(verification_token=token), format="json"
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("verification_token", response.json())
        # One confirmation covers several bookings for a while (facilities).
        token = services.issue_token("jane@example.com")
        for _ in range(2):
            response = client.post(
                "/api/v1/trip-requests/", valid_payload(verification_token=token), format="json"
            )
            self.assertEqual(response.status_code, 201)
