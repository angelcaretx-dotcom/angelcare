"""Emailing a ride's price before the ride (ADR 0047)."""

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from notifications.models import NotificationLog, NotificationStatus, NotificationType
from notifications.services import NotificationService
from notifications.tests import FailingEmailProvider
from payers.models import Payer
from transportation.models import TripRequest, TripRequestStatus
from trash import services as trash
from trips.models import Trip, TripEvent, TripLeg, TripStatus
from trips.returns import build_return_leg
from trips.tests import make_passenger

from . import pricing, quotes
from .models import Quote, RateCard

CENTRAL = ZoneInfo("America/Chicago")
DAY = date(2027, 1, 13)  # a Wednesday


def at(hour, minute=0, day=DAY):
    return datetime.combine(day, time(hour, minute), tzinfo=CENTRAL)


class QuoteTestCase(TestCase):
    def setUp(self):
        # No payer: the private-pay card.
        self.card = RateCard.objects.create(
            name="Private pay 2027", effective_from=date(2027, 1, 1),
            base_ambulatory=Decimal("30.00"), base_wheelchair=Decimal("50.00"),
            per_mile=Decimal("2.00"), included_miles=Decimal("5"), wait_per_minute=Decimal("1.00"),
            after_hours_fee=Decimal("10.00"), after_hours_start=time(18, 0), after_hours_end=time(6, 0),
        )

    def request(self, **extra):
        defaults = {
            "full_name": "Jamie Chen", "phone": "817-555-0100", "email": "jamie@example.com",
            "pickup_address": "1 Main St, Fort Worth, TX", "dropoff_address": "2 Clinic Rd, Fort Worth, TX",
            "requested_datetime": at(9), "service_type": "wheelchair",
        }
        defaults.update(extra)
        return TripRequest.objects.create(**defaults)

    def priced(self, request, **kwargs):
        card, lines, notes = pricing.quote(request, **kwargs)
        return [(line.label, line.amount) for line in lines], pricing.total(lines), notes


class PriceTests(QuoteTestCase):
    def test_one_way_is_base_plus_miles_beyond_the_included_ones(self):
        lines, total, notes = self.priced(self.request(), miles=Decimal("12.4"))
        self.assertEqual(lines, [("Wheelchair trip", Decimal("50.00")),
                                 ("Miles beyond 5.00 included", Decimal("14.80"))])
        self.assertEqual(total, Decimal("64.80"))
        self.assertEqual(notes, [])

    def test_a_per_mile_card_needs_the_miles(self):
        with self.assertRaisesMessage(pricing.PricingError, "one-way miles"):
            pricing.quote(self.request())

    def test_a_round_trip_is_two_rides_each_with_its_own_after_hours(self):
        request = self.request(return_ride="scheduled", return_datetime=at(19))
        lines, total, notes = self.priced(request, miles=Decimal("5"))
        self.assertEqual(lines, [("Ride there: wheelchair trip", Decimal("50.00")),
                                 ("Ride back: wheelchair trip", Decimal("50.00")),
                                 ("Ride back: after-hours", Decimal("10.00"))])
        self.assertEqual(total, Decimal("110.00"))
        self.assertEqual(notes, [])

    def test_a_ride_back_with_no_set_time_has_no_after_hours_fee_and_says_so(self):
        lines, total, notes = self.priced(self.request(return_ride="will_call"), miles=Decimal("3"))
        self.assertEqual(total, Decimal("100.00"))
        self.assertEqual(len(notes), 1)
        self.assertIn("no set time", notes[0])

    def test_a_waiting_driver_adds_the_wait_after_the_free_minutes(self):
        request = self.request(return_ride="wait")
        lines, total, notes = self.priced(request, miles=Decimal("5"), wait_minutes=45)
        self.assertEqual(lines[-1], ("Wait time (45 min, first 15 free)", Decimal("30.00")))
        self.assertEqual(total, Decimal("130.00"))
        self.assertTrue(any("driver app" in note for note in notes))
        self.assertEqual(pricing.covered_wait_minutes(request, self.card, 45), 45)
        # No expected wait typed: the price covers the free minutes only.
        self.assertEqual(self.priced(request, miles=Decimal("5"))[1], Decimal("100.00"))
        self.assertEqual(pricing.covered_wait_minutes(request, self.card, 0), 15)
        self.assertIsNone(pricing.covered_wait_minutes(self.request(), self.card, 0))

    def test_no_card_no_rate_and_program_payers_have_no_price(self):
        with self.assertRaisesMessage(pricing.PricingError, "No rate card for private pay"):
            pricing.quote(self.request(requested_datetime=at(9, day=date(2026, 12, 1))), miles=Decimal("5"))
        with self.assertRaisesMessage(pricing.PricingError, "has no rate for medical supply"):
            pricing.quote(self.request(service_type="equipment_delivery"), miles=Decimal("5"))
        mtm = Payer.objects.create(name="MTM", payer_type="broker")
        covered = self.request(passenger=make_passenger(payer=mtm))
        with self.assertRaisesMessage(pricing.PricingError, "paid by MTM"):
            pricing.quote(covered, miles=Decimal("5"))


class ServiceRateTests(QuoteTestCase):
    """A card with each service's own rates, wait in blocks, and the fees once per round trip (ADR 0048)."""

    def setUp(self):
        RateCard.objects.create(
            name="Private pay by service", effective_from=date(2027, 1, 1), fees_on_return=False,
            base_ambulatory=Decimal("40.00"), per_mile_ambulatory=Decimal("3.00"),
            wait_rate_ambulatory=Decimal("8.00"),
            base_wheelchair=Decimal("70.00"), loading_wheelchair=Decimal("55.00"),
            per_mile_wheelchair=Decimal("5.00"), wait_rate_wheelchair=Decimal("18.00"),
            wait_block_minutes=15,
        )
        self.card = RateCard.objects.get()

    def test_each_service_has_its_own_booking_loading_and_per_mile_rate(self):
        lines, total, _ = self.priced(self.request(), miles=Decimal("10"))
        self.assertEqual(lines, [("Wheelchair trip", Decimal("70.00")), ("Loading", Decimal("55.00")),
                                 ("Miles", Decimal("50.00"))])
        self.assertEqual(total, Decimal("175.00"))
        lines, total, _ = self.priced(self.request(service_type="ambulatory"), miles=Decimal("10"))
        self.assertEqual(lines, [("Ambulatory trip", Decimal("40.00")), ("Miles", Decimal("30.00"))])
        self.assertEqual(total, Decimal("70.00"))

    def test_a_round_trip_pays_booking_and_loading_once_and_miles_both_ways(self):
        request = self.request(return_ride="scheduled", return_datetime=at(13))
        lines, total, _ = self.priced(request, miles=Decimal("10"))
        self.assertEqual(lines, [("Wheelchair trip", Decimal("70.00")), ("Loading", Decimal("55.00")),
                                 ("Ride there: miles", Decimal("50.00")), ("Ride back: miles", Decimal("50.00"))])
        self.assertEqual(total, Decimal("225.00"))

    def test_wait_is_free_for_fifteen_minutes_then_each_started_block_is_charged(self):
        request = self.request(service_type="ambulatory", return_ride="wait")
        for minutes, blocks in ((0, 0), (15, 0), (16, 1), (30, 1), (31, 2), (40, 2), (45, 2), (46, 3)):
            self.assertEqual(self.card.wait_blocks(minutes), blocks, minutes)
        lines, total, _ = self.priced(request, miles=Decimal("10"), wait_minutes=40)
        self.assertEqual(lines[-1], ("Wait time (40 min, first 15 free, 15-minute blocks)", Decimal("16.00")))
        self.assertEqual(total, Decimal("116.00"))  # 40 booking + 30 + 30 miles + 2 blocks x 8
        self.assertEqual(pricing.covered_wait_minutes(request, self.card, 40), 45)
        self.assertEqual(pricing.covered_wait_minutes(request, self.card, 0), 15)

        quotes.send(request, miles=Decimal("10"), wait_minutes=40, user=None)
        body = mail.outbox[0].body
        self.assertIn("covers up to 45 minutes of waiting", body)
        self.assertIn("$8.00 per 15 minutes", body)

    def test_the_two_trips_of_a_wait_and_return_ride_are_invoiced_what_was_quoted(self):
        request = self.request(passenger=make_passenger(), return_ride="wait")
        quote = quotes.send(request, miles=Decimal("10"), wait_minutes=40, user=None)
        self.assertEqual(quote.amount, Decimal("261.00"))  # 70 + 55 + 50 + 50 + 2 blocks x 18

        outbound = Trip.objects.create(trip_request=request, leg=TripLeg.OUTBOUND,
                                       scheduled_pickup_datetime=request.requested_datetime)
        back = build_return_leg(outbound)
        back.save()
        TripEvent.objects.create(trip=outbound, event_type="dropped_off", occurred_at=at(9, 40))
        TripEvent.objects.create(trip=back, event_type="picked_up", occurred_at=at(10, 20))
        Trip.objects.update(status=TripStatus.COMPLETED)

        there = pricing.total(pricing.price(Trip.objects.get(pk=outbound.pk))[1])
        home = pricing.price(Trip.objects.get(pk=back.pk))[1]
        self.assertEqual(there, Decimal("175.00"))
        # The ride back pays no second booking or loading fee: its miles and the wait only.
        self.assertEqual([(line.label, line.amount) for line in home],
                         [("Miles", Decimal("50.00")),
                          ("Wait time (40 min, first 15 free, 15-minute blocks)", Decimal("36.00"))])
        self.assertEqual(there + pricing.total(home), quote.amount)


class SendTests(QuoteTestCase):
    def test_emails_the_total_only_and_marks_the_request_contacted(self):
        request = self.request()
        quote = quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None)

        self.assertEqual((quote.amount, quote.rate_card, quote.sent_to, quote.delivered),
                         (Decimal("64.80"), self.card, "jamie@example.com", True))
        self.assertEqual(quote.lines[0]["label"], "Wheelchair trip")

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["jamie@example.com"])
        self.assertIn(f"#{str(request.pk)[:8].upper()}", message.subject)
        html = message.alternatives[0][0]
        for body in (message.body, html):
            self.assertIn("$64.80", body)
            self.assertIn("One-way ride", body)
            self.assertIn("not booked yet", body)
            # The total and the reference only (ADR 0027): nothing about the rider or the trip.
            for private in ("Jamie", "Main St", "Clinic Rd", "heelchair", "Jan"):
                self.assertNotIn(private, body)
        self.assertIn("Total price: $64.80", message.body)

        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.CONTACTED)
        self.assertTrue(AuditLog.objects.filter(resource_type="TripRequest", action="quote_sent",
                                                resource_id=str(request.pk)).exists())
        self.assertTrue(NotificationLog.objects.filter(
            notification_type=NotificationType.CUSTOMER_QUOTE, status=NotificationStatus.SENT,
            related_object_id=str(request.pk)).exists())

    def test_a_wait_and_return_price_says_what_waiting_it_covers(self):
        quotes.send(self.request(return_ride="wait"), miles=Decimal("5"), wait_minutes=45, user=None)
        body = mail.outbox[0].body
        self.assertIn("Round trip, with the driver waiting for you", body)
        self.assertIn("covers up to 45 minutes of waiting", body)
        self.assertIn("$1.00 per minute", body)

    def test_a_failed_email_keeps_the_quote_undelivered_and_the_request_unanswered(self):
        request = self.request()
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None,
                            notifier=NotificationService(email_provider=FailingEmailProvider()))
        self.assertFalse(Quote.objects.get(pk=quote.pk).delivered)
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.NEW)

    def test_a_request_already_scheduled_keeps_its_status(self):
        request = self.request(status=TripRequestStatus.SCHEDULED)
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None)
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.SCHEDULED)

    def test_nothing_is_saved_or_sent_when_there_is_no_price(self):
        with self.assertRaises(pricing.PricingError):
            quotes.send(self.request(), miles=None, wait_minutes=0, user=None)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

    def test_the_trips_are_invoiced_what_the_customer_was_told(self):
        request = self.request(passenger=make_passenger(), return_ride="scheduled", return_datetime=at(13))
        quote = quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None)

        outbound = Trip.objects.create(trip_request=request, leg=TripLeg.OUTBOUND,
                                       scheduled_pickup_datetime=request.requested_datetime)
        self.assertEqual(outbound.billable_miles, Decimal("12.40"))
        back = build_return_leg(outbound)
        self.assertEqual(back.billable_miles, Decimal("12.40"))
        back.save()

        Trip.objects.update(status=TripStatus.COMPLETED)
        charged = sum(pricing.total(pricing.price(trip)[1]) for trip in Trip.objects.all())
        self.assertEqual(charged, quote.amount)
        self.assertEqual(charged, Decimal("129.60"))

    def test_miles_typed_on_the_trip_are_kept(self):
        request = self.request(passenger=make_passenger())
        quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None)
        trip = Trip.objects.create(trip_request=request, scheduled_pickup_datetime=request.requested_datetime,
                                   billable_miles=Decimal("9"))
        self.assertEqual(trip.billable_miles, Decimal("9"))


class AdminTests(QuoteTestCase):
    def login(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")
        return user

    @staticmethod
    def url(request):
        return f"/admin/billing/quote/send/{request.pk}/"

    @staticmethod
    def request_page(request):
        return f"/admin/transportation/triprequest/{request.pk}/change/"

    def test_administrator_checks_the_price_then_emails_it(self):
        self.login("Administrator")
        request = self.request()

        page = self.client.get(self.url(request))
        self.assertContains(page, "1 Main St, Fort Worth, TX")
        self.assertNotContains(page, "data-quote-price")
        self.assertTrue(AuditLog.objects.filter(resource_type="TripRequest", action="viewed",
                                                resource_id=str(request.pk)).exists())

        preview = self.client.post(self.url(request), {"miles": "12.4", "preview": "1"})
        self.assertContains(preview, "Total: $64.80")
        self.assertContains(preview, "Miles beyond 5.00 included")
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

        sent = self.client.post(self.url(request), {"miles": "12.40", "wait_minutes": "0",
                                                    "checked_amount": "64.80", "send": "1"})
        self.assertRedirects(sent, self.request_page(request), fetch_redirect_response=False)
        quote = Quote.objects.get()
        self.assertEqual((quote.amount, quote.miles, quote.sent_by.username),
                         (Decimal("64.80"), Decimal("12.40"), "administrator"))
        self.assertEqual(len(mail.outbox), 1)

        page = self.client.get(self.request_page(request))
        self.assertContains(page, "$64.80 to jamie@example.com")
        self.assertContains(page, "Email the price")
        # The page offers the last miles again, and lists what was sent.
        again = self.client.get(self.url(request))
        self.assertContains(again, 'value="12.40"')
        self.assertContains(again, "data-quote-sent")

    def test_a_price_that_is_not_the_one_checked_is_shown_again_instead_of_sent(self):
        self.login("Administrator")
        request = self.request()
        response = self.client.post(self.url(request), {"miles": "12.4", "checked_amount": "60.00", "send": "1"})
        self.assertContains(response, "not the one you checked")
        self.assertContains(response, "Total: $64.80")
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

    def test_what_cannot_be_priced_is_explained_on_the_page(self):
        self.login("Administrator")
        response = self.client.post(self.url(self.request()), {"miles": "", "preview": "1"})
        self.assertContains(response, "data-quote-error")
        self.assertContains(response, "one-way miles")
        self.assertNotContains(response, "data-quote-price")

    def test_dispatchers_cannot_see_or_send_prices(self):
        self.login("Dispatcher")
        request = self.request()
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None)
        mail.outbox.clear()
        self.assertEqual(self.client.get(self.url(request)).status_code, 403)
        send = {"miles": "5", "wait_minutes": "0", "checked_amount": "50.00", "send": "1"}
        self.assertEqual(self.client.post(self.url(request), send).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.get("/admin/billing/quote/").status_code, 403)
        page = self.client.get(self.request_page(request))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "$50.00")
        self.assertNotContains(page, "Email the price")

    def test_sent_quotes_are_a_read_only_record(self):
        self.login("Administrator")
        request = self.request()
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None)
        self.assertContains(self.client.get("/admin/billing/quote/"), f"#{str(request.pk)[:8].upper()}")
        detail = f"/admin/billing/quote/{quote.pk}/change/"
        self.assertContains(self.client.get(detail), "Wheelchair trip: 1 × $50.00")
        self.assertEqual(self.client.post(detail, {"amount": "1.00"}).status_code, 403)
        self.assertEqual(self.client.get("/admin/billing/quote/add/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/billing/quote/{quote.pk}/delete/").status_code, 403)
        quote.refresh_from_db()
        self.assertEqual(quote.amount, Decimal("50.00"))

    def test_the_office_guide_is_for_all_staff_and_links_billing_only_for_those_who_bill(self):
        guide = "/admin/organization/organization/guide/"
        self.login("Dispatcher")
        page = self.client.get(guide)
        self.assertContains(page, "Assign a driver and a vehicle")
        self.assertContains(page, "/admin/trips/trip/board/")
        self.assertNotContains(page, "/admin/billing/ratecard/")
        self.client.logout()
        self.login("Administrator")
        page = self.client.get(guide)
        self.assertContains(page, "/admin/billing/ratecard/")
        self.assertContains(page, "/admin/billing/invoice/create-batch/")

    def test_administrator_sets_each_services_rates_on_the_card(self):
        self.login("Administrator")
        page = f"/admin/billing/ratecard/{self.card.pk}/change/"
        self.assertContains(self.client.get(page), "Loading fee")
        self.client.post(page, {
            "name": "Private pay 2027", "payer": "", "effective_from": "2027-01-01", "effective_to": "",
            "base_ambulatory": "30.00", "base_wheelchair": "50.00", "base_equipment_delivery": "",
            "loading_wheelchair": "55.00", "per_mile_wheelchair": "5.00", "wait_rate_wheelchair": "18.00",
            "per_mile": "2.00", "included_miles": "5", "wait_free_minutes": "15", "wait_block_minutes": "15",
            "wait_per_minute": "1.00", "no_show_fee": "0", "after_hours_fee": "10.00",
            "after_hours_start": "18:00", "after_hours_end": "06:00", "notes": "",
        })
        self.card.refresh_from_db()
        self.assertEqual(
            (self.card.loading_wheelchair, self.card.per_mile_wheelchair, self.card.wait_rate_wheelchair,
             self.card.wait_block_minutes, self.card.fees_on_return),
            (Decimal("55.00"), Decimal("5.00"), Decimal("18.00"), 15, False),
        )
        self.assertTrue(AuditLog.objects.filter(resource_type="RateCard", action="changed").exists())
        self.assertContains(self.client.get("/admin/billing/ratecard/"), "$50.00 + $55.00 loading + $5.00/mi")

    def test_a_deleted_request_takes_its_quotes_to_the_trash_and_brings_them_back(self):
        admin = self.login("Administrator")
        request = self.request()
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=admin)
        entry = trash.send(TripRequest.objects.get(pk=request.pk), user=admin)
        self.assertFalse(Quote.objects.exists())
        trash.restore(entry, user=admin)
        restored = Quote.objects.get(pk=quote.pk)
        self.assertEqual((restored.trip_request_id, restored.amount, restored.sent_by), (request.pk, Decimal("50.00"), admin))
