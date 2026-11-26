"""Emailing a ride's price before the ride (ADR 0047)."""

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from facilities.models import Facility
from notifications.models import NotificationLog, NotificationStatus, NotificationType
from notifications.services import NotificationService
from notifications.tests import FailingEmailProvider
from payers.models import Payer
from transportation import distance
from transportation.models import TripRequest, TripRequestStatus
from trash import services as trash
from trips.models import Trip, TripEvent, TripLeg, TripStatus
from trips.returns import build_return_leg
from trips.tests import make_passenger

from . import pricing, quotes
from .models import Quote, RateCard

CENTRAL = ZoneInfo("America/Chicago")
DAY = date(2027, 1, 13)  # a Wednesday
# What the sender ticks on the price page before a price goes out (ADR 0049).
CONFIRMED = {"miles_checked": True, "customer_pays": True}


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

        quotes.send(request, miles=Decimal("10"), wait_minutes=40, user=None, **CONFIRMED)
        body = mail.outbox[0].body
        self.assertIn("covers up to 45 minutes of waiting", body)
        self.assertIn("$8.00 per 15 minutes", body)

    def test_the_two_trips_of_a_wait_and_return_ride_are_invoiced_what_was_quoted(self):
        request = self.request(passenger=make_passenger(), return_ride="wait")
        quote = quotes.send(request, miles=Decimal("10"), wait_minutes=40, user=None, **CONFIRMED)
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

    def test_the_rules_are_explained_one_sentence_each_for_this_service(self):
        booking, loading, mile = "The booking fee is $70.00.", "The loading fee is $55.00.", "Each mile costs $5.00."
        once = "On a round trip the booking fee and the loading fee are charged once, and the miles both ways."
        two_trips = "The driver does not wait: the driver makes two separate trips, one there and one back."
        self.assertEqual(pricing.explain(self.card, self.request()), [booking, loading, mile])
        for ride_back in ("scheduled", "will_call"):
            request = self.request(return_ride=ride_back, return_datetime=at(13) if ride_back == "scheduled" else None)
            self.assertEqual(pricing.explain(self.card, request), [booking, loading, mile, once, two_trips])
        self.assertEqual(pricing.explain(self.card, self.request(return_ride="wait")), [
            booking, loading, mile, once,
            "The driver waits and brings the rider back: the first 15 minutes of waiting are free, "
            "then each started 15 minutes costs $18.00.",
        ])
        # Another service on the same card: its own rates, and no loading fee where the card has none.
        self.assertEqual(pricing.explain(self.card, self.request(service_type="ambulatory")),
                         ["The booking fee is $40.00.", "Each mile costs $3.00."])
        self.assertEqual(pricing.explain(self.card, self.request(service_type="ambulatory", return_ride="will_call")),
                         ["The booking fee is $40.00.", "Each mile costs $3.00.",
                          "On a round trip the booking fee is charged once, and the miles both ways.", two_trips])
        RateCard.objects.update(fees_on_return=True)
        self.assertIn("A round trip is priced as two rides: each pays the booking fee, the loading fee and its miles.",
                      pricing.explain(RateCard.objects.get(), self.request(return_ride="scheduled")))


class ExplainTests(QuoteTestCase):
    """The card every other test uses: included miles, wait by the minute, after-hours, fees on each ride."""

    def setUp(self):
        super().setUp()
        self.card.refresh_from_db()

    def test_included_miles_after_hours_and_a_round_trip_priced_as_two_rides(self):
        booking = "The booking fee is $50.00."
        miles = "The first 5.00 miles are included; each mile after them costs $2.00."
        night = "A ride picked up between 6:00 PM and 6:00 AM (Texas time) adds an after-hours fee of $10.00."
        untimed = "The ride back has no set time, so it has no after-hours fee."
        self.assertEqual(pricing.explain(self.card, self.request()), [booking, miles, night])
        self.assertEqual(pricing.explain(self.card, self.request(return_ride="wait")), [
            booking, miles,
            "A round trip is priced as two rides: each pays the booking fee and its miles.",
            night, untimed,
            "The driver waits and brings the rider back: the first 15 minutes of waiting are free, "
            "then each minute costs $1.00.",
        ])

    def test_a_ride_back_with_no_set_time_is_said_to_have_no_after_hours_fee(self):
        # The sentence follows the price: `quote` leaves the fee off exactly these rides back.
        night = "A ride picked up between 6:00 PM and 6:00 AM (Texas time) adds an after-hours fee of $10.00."
        untimed = "The ride back has no set time, so it has no after-hours fee."
        for extra, said in (({}, False),
                            ({"return_ride": "scheduled", "return_datetime": at(19)}, False),
                            ({"return_ride": "scheduled", "return_datetime": None}, True),
                            ({"return_ride": "will_call"}, True),
                            ({"return_ride": "wait"}, True)):
            request = self.request(**extra)
            rules = pricing.explain(self.card, request)
            self.assertIn(night, rules)
            self.assertEqual(untimed in rules, said, extra)
            _, lines, notes = pricing.quote(request, miles=Decimal("5"))
            self.assertEqual(any("no set time" in note for note in notes), said, extra)
            if said:
                self.assertNotIn(("after_hours", "back"), [(line.kind, line.leg) for line in lines])
        # A card with no after-hours fee has nothing to exempt.
        RateCard.objects.filter(pk=self.card.pk).update(after_hours_fee=Decimal("0"))
        self.card.refresh_from_db()
        self.assertNotIn(untimed, pricing.explain(self.card, self.request(return_ride="will_call")))

    def test_weekends_no_free_minutes_and_rates_the_card_does_not_charge(self):
        RateCard.objects.filter(pk=self.card.pk).update(
            weekends_after_hours=True, after_hours_start=None, after_hours_end=None, wait_free_minutes=0,
            per_mile=Decimal("0"),
        )
        self.card.refresh_from_db()
        rules = pricing.explain(self.card, self.request(return_ride="wait"))
        self.assertIn("Miles are not charged on this card.", rules)
        self.assertIn("A ride picked up on a Saturday or Sunday (Texas time) adds an after-hours fee of $10.00.", rules)
        self.assertIn("The driver waits and brings the rider back: each minute of waiting costs $1.00.", rules)
        # No per-mile rate: the round-trip sentence does not say miles are charged.
        about_miles = ["Miles are not charged on this card."]
        self.assertIn("A round trip is priced as two rides: each pays the booking fee.", rules)
        self.assertEqual([rule for rule in rules if "miles" in rule.lower()], about_miles)
        for each_ride, loading, sentence in (
            (True, Decimal("20.00"), "A round trip is priced as two rides: each pays the booking fee and the loading fee."),
            (False, Decimal("20.00"), "On a round trip the booking fee and the loading fee are charged once."),
            (False, None, "On a round trip the booking fee is charged once."),
        ):
            RateCard.objects.filter(pk=self.card.pk).update(fees_on_return=each_ride, loading_wheelchair=loading)
            self.card.refresh_from_db()
            rules = pricing.explain(self.card, self.request(return_ride="wait"))
            self.assertIn(sentence, rules)
            self.assertEqual([rule for rule in rules if "miles" in rule.lower()], about_miles)
            # The price agrees: no line for miles.
            self.assertNotIn("miles", [line.kind for line in pricing.quote(self.request(return_ride="wait"))[1]])
        RateCard.objects.filter(pk=self.card.pk).update(wait_per_minute=Decimal("0"), after_hours_fee=Decimal("0"))
        self.card.refresh_from_db()
        rules = pricing.explain(self.card, self.request(return_ride="wait"))
        self.assertIn("The driver waits and brings the rider back. Waiting is not charged on this card.", rules)
        self.assertFalse(any("after-hours" in rule for rule in rules))

    def test_the_card_for_a_price_is_refused_for_the_same_reasons_as_the_price(self):
        self.assertEqual(pricing.quote_card(self.request()), self.card)
        with self.assertRaisesMessage(pricing.PricingError, "has no rate for medical supply"):
            pricing.quote_card(self.request(service_type="equipment_delivery"))
        mtm = Payer.objects.create(name="MTM", payer_type="broker")
        with self.assertRaisesMessage(pricing.PricingError, "paid by MTM"):
            pricing.quote_card(self.request(passenger=make_passenger(payer=mtm)))


class SendTests(QuoteTestCase):
    def test_emails_the_price_with_each_part_listed_and_marks_the_request_contacted(self):
        request = self.request()
        quote = quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None, **CONFIRMED)

        self.assertEqual((quote.amount, quote.rate_card, quote.sent_to, quote.delivered),
                         (Decimal("64.80"), self.card, "jamie@example.com", True))
        # What is kept is the office's wording, plus what kind of line each is (ADR 0049).
        self.assertEqual([(line["label"], line["kind"], line["leg"]) for line in quote.lines],
                         [("Wheelchair trip", "base", ""), ("Miles beyond 5.00 included", "miles", "")])

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["jamie@example.com"])
        self.assertIn(f"#{str(request.pk)[:8].upper()}", message.subject)
        html = message.alternatives[0][0]
        for body in (message.body, html):
            self.assertIn(f"#{str(request.pk)[:8].upper()}", body)
            for part in ("Booking fee", "$50.00", "Miles: 7.4 x $2.00 (after the first 5 miles)", "$14.80",
                         "Total price", "$64.80", "One-way ride", "How your price works", "not booked yet"):
                self.assertIn(part, body)
            # The price and its parts (ADR 0049), still nothing about the rider or the trip (ADR 0027).
            for private in ("Jamie", "Main St", "Clinic Rd", "heelchair", "Jan"):
                self.assertNotIn(private, body)
        text = message.body.replace("\r\n", "\n")  # the template's line ends are the checkout's
        self.assertIn("Booking fee: $50.00\n", text)
        self.assertIn("Miles: 7.4 x $2.00 (after the first 5 miles) = $14.80\n", text)
        self.assertIn("Total price: $64.80\n", text)
        self.assertIn("Covers: One-way ride\n", text)

        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.CONTACTED)
        self.assertTrue(AuditLog.objects.filter(resource_type="TripRequest", action="quote_sent",
                                                resource_id=str(request.pk)).exists())
        self.assertTrue(NotificationLog.objects.filter(
            notification_type=NotificationType.CUSTOMER_QUOTE, status=NotificationStatus.SENT,
            related_object_id=str(request.pk)).exists())

    def test_a_wait_and_return_price_says_what_waiting_it_covers(self):
        quotes.send(self.request(return_ride="wait"), miles=Decimal("5"), wait_minutes=45, user=None, **CONFIRMED)
        body = mail.outbox[0].body
        self.assertIn("Round trip, with the driver waiting for you", body)
        self.assertIn("covers up to 45 minutes of waiting", body)
        self.assertIn("$1.00 per minute", body)

    def test_a_failed_email_keeps_the_quote_undelivered_and_the_request_unanswered(self):
        request = self.request()
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED,
                            notifier=NotificationService(email_provider=FailingEmailProvider()))
        self.assertFalse(Quote.objects.get(pk=quote.pk).delivered)
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.NEW)

    def test_a_request_already_scheduled_keeps_its_status(self):
        request = self.request(status=TripRequestStatus.SCHEDULED)
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.SCHEDULED)

    def test_a_cancelled_request_or_one_with_no_email_address_is_not_priced(self):
        for extra, why in (({"status": TripRequestStatus.CANCELLED}, "cancelled"),
                           ({"status": TripRequestStatus.DECLINED}, "declined"),
                           ({"email": ""}, "no email address")):
            with self.assertRaisesMessage(pricing.PricingError, why):
                quotes.send(self.request(**extra), miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

    def test_nothing_is_sent_or_saved_without_both_confirmations(self):
        request = self.request()
        for confirmed in ({}, {"miles_checked": True}, {"customer_pays": True},
                          {"miles_checked": True, "customer_pays": False},
                          {"miles_checked": "yes", "customer_pays": "yes"}):  # only a real True counts
            with self.assertRaisesMessage(pricing.PricingError, "Tick both boxes"):
                quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **confirmed)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="quote_sent").exists())
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.NEW)

    def test_the_audit_row_records_what_the_sender_confirmed(self):
        request = self.request()
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        row = AuditLog.objects.get(resource_type="TripRequest", action="quote_sent", resource_id=str(request.pk))
        self.assertIs(row.after["miles_checked"], True)
        self.assertEqual(row.after["who_pays"], "customer")
        self.assertEqual((row.after["amount"], row.after["miles"]), ("50.00", "5"))

    def test_a_double_click_sends_one_email_and_a_changed_price_sends_another(self):
        request = self.request()
        first = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        again = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (1, 1))
        quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (2, 2))

    def test_a_total_too_large_to_be_right_is_refused(self):
        with self.assertRaisesMessage(pricing.PricingError, "too much to be right"):
            pricing.quote(self.request(), miles=Decimal("99999"))
        self.assertEqual(self.priced(self.request(service_type="ambulatory"), miles=Decimal("5"))[1], Decimal("30.00"))

    def test_a_delivery_is_not_named_in_the_email(self):
        self.card.base_equipment_delivery = Decimal("20.00")
        self.card.save()
        quotes.send(self.request(service_type="equipment_delivery"), miles=Decimal("5"), wait_minutes=0, user=None,
                    **CONFIRMED)
        message = mail.outbox[0]
        self.assertIn("One-way trip", message.body)
        for body in (message.body, message.alternatives[0][0]):
            self.assertNotIn("elivery", body)

    def test_nothing_is_saved_or_sent_when_there_is_no_price(self):
        with self.assertRaises(pricing.PricingError):
            quotes.send(self.request(), miles=None, wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

    def test_the_trips_are_invoiced_what_the_customer_was_told(self):
        request = self.request(passenger=make_passenger(), return_ride="scheduled", return_datetime=at(13))
        quote = quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None, **CONFIRMED)

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
        quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None, **CONFIRMED)
        trip = Trip.objects.create(trip_request=request, scheduled_pickup_datetime=request.requested_datetime,
                                   billable_miles=Decimal("9"))
        self.assertEqual(trip.billable_miles, Decimal("9"))


class ItemisedEmailTests(QuoteTestCase):
    """The customer's email lists each part of the price, and still says nothing about the rider (ADR 0049)."""

    # Things the email must never carry: the test rider, both addresses, the phone, the service
    # level, and the date and times of the rides below (9:00, 1:00 PM and 7:00 PM on Jan 13).
    PRIVATE = ("Jamie", "Chen", "Main St", "Clinic Rd", "Fort Worth", "555-0100",
               "Jan", "Wednesday", "9:00", "1:00", "7:00", "13:00", "19:00", " AM", " PM", "a.m.", "p.m.")
    SERVICE_WORDS = ("wheelchair", "ambulatory", "equipment", "delivery", "supply", "stretcher")

    def sent(self, request, miles="12.4", wait_minutes=0):
        mail.outbox.clear()
        quotes.send(request, miles=Decimal(miles), wait_minutes=wait_minutes, user=None, **CONFIRMED)
        message = mail.outbox[0]
        # The template's line ends are the checkout's: compare on plain "\n".
        return message.subject, message.body.replace("\r\n", "\n"), message.alternatives[0][0]

    def rows(self, request, miles="12.4", wait_minutes=0):
        card, lines, _ = pricing.quote(request, miles=Decimal(miles), wait_minutes=wait_minutes)
        rows = quotes.customer_rows(request, card, lines, wait_minutes)
        # One row per line, and the amounts shown add up to the total.
        self.assertEqual(sum(Decimal(value.lstrip("$")) for _, value in rows), pricing.total(lines))
        return rows

    def by_service(self):
        """The owner's kind of card: each service's own rates, fees once per round trip, wait in 15-minute blocks."""
        RateCard.objects.all().delete()
        return RateCard.objects.create(
            name="Private pay by service", effective_from=date(2027, 1, 1), fees_on_return=False,
            base_ambulatory=Decimal("40.00"), per_mile_ambulatory=Decimal("3.00"), wait_rate_ambulatory=Decimal("8.00"),
            base_wheelchair=Decimal("70.00"), loading_wheelchair=Decimal("55.00"),
            per_mile_wheelchair=Decimal("5.00"), wait_rate_wheelchair=Decimal("18.00"), wait_block_minutes=15,
            after_hours_fee=Decimal("10.00"), after_hours_start=time(18, 0), after_hours_end=time(6, 0),
        )

    def test_a_line_says_what_kind_it_is_and_which_ride_it_belongs_to(self):
        line = pricing.Line("Ride back: miles", Decimal("2"), Decimal("3.00"), kind="miles", leg="back")
        self.assertEqual(line.as_dict(), {"label": "Ride back: miles", "quantity": "2", "unit": "3.00",
                                          "amount": "6.00", "kind": "miles", "leg": "back"})
        # Driver pay lines and the no-show fee have neither.
        self.assertEqual((pricing.Line("Per trip", Decimal(1), Decimal("10")).kind,
                          pricing.Line("Per trip", Decimal(1), Decimal("10")).leg), ("", ""))
        _, lines, _ = pricing.quote(self.request(return_ride="scheduled", return_datetime=at(19)), miles=Decimal("12.4"))
        self.assertEqual([(line.kind, line.leg) for line in lines],
                         [("base", "there"), ("miles", "there"), ("base", "back"), ("miles", "back"),
                          ("after_hours", "back")])

    def test_every_kind_of_line_is_a_row_when_the_card_charges_the_fees_on_each_ride(self):
        RateCard.objects.update(loading_wheelchair=Decimal("20.00"))
        request = self.request(return_ride="scheduled", return_datetime=at(19))
        expected = [
            ("Booking fee, ride there", "$50.00"), ("Loading fee, ride there", "$20.00"),
            ("Miles there: 7.4 x $2.00 (after the first 5 miles)", "$14.80"),
            ("Booking fee, ride back", "$50.00"), ("Loading fee, ride back", "$20.00"),
            ("Miles back: 7.4 x $2.00 (after the first 5 miles)", "$14.80"),
            ("After-hours fee, ride back", "$10.00"),
        ]
        self.assertEqual(self.rows(request), expected)
        _, text, html = self.sent(request)
        for label, value in expected:
            self.assertIn(label, html)
            self.assertIn(value, html)
        self.assertIn("Booking fee, ride there: $50.00\n", text)
        self.assertIn("Miles back: 7.4 x $2.00 (after the first 5 miles) = $14.80\n", text)
        self.assertIn("After-hours fee, ride back: $10.00\n", text)
        self.assertIn("Total price: $179.60\n", text)
        self.assertIn("Covers: Round trip (there and back)\n", text)
        for body in (text, html):
            self.assertIn("A round trip is two rides: each ride has the booking fee and the loading fee, "
                          "and its own miles.", body)

    def test_fees_charged_once_are_not_named_after_a_ride_and_waiting_is_shown_in_blocks(self):
        self.by_service()
        request = self.request(return_ride="wait")
        expected = [("Booking fee", "$70.00"), ("Loading fee", "$55.00"),
                    ("Miles there: 10 x $5.00", "$50.00"), ("Miles back: 10 x $5.00", "$50.00"),
                    ("Waiting time: 2 x 15 minutes at $18.00", "$36.00")]
        self.assertEqual(self.rows(request, miles="10", wait_minutes=40), expected)
        _, text, html = self.sent(request, miles="10", wait_minutes=40)
        self.assertIn("Waiting time: 2 x 15 minutes at $18.00 = $36.00\n", text)
        self.assertIn("Total price: $261.00\n", text)
        for body in (text, html):
            self.assertIn("Round trip, with the driver waiting for you", body)
            self.assertIn("The booking fee covers scheduling the trip and sending a vehicle. "
                          "Miles are the driving miles between your two addresses. "
                          "For a round trip the booking fee and the loading fee are charged once, "
                          "and the miles both ways.", body)
            # The free minutes are said in words, not in the row.
            self.assertIn("The first 15 minutes of waiting are free. This price covers up to 45 minutes of waiting. "
                          "A longer wait is billed at $18.00 per 15 minutes after the ride.", body)

    def test_one_way_after_hours_and_waiting_by_the_minute(self):
        self.assertEqual(self.rows(self.request(requested_datetime=at(19)), miles="5"),
                         [("Booking fee", "$50.00"), ("After-hours fee", "$10.00")])
        self.assertEqual(self.rows(self.request(return_ride="wait"), miles="5", wait_minutes=45)[-1],
                         ("Waiting time: 30 minutes at $1.00", "$30.00"))
        self.assertEqual(self.rows(self.request(return_ride="wait"), miles="5", wait_minutes=16)[-1],
                         ("Waiting time: 1 minute at $1.00", "$1.00"))
        # No miles beyond the included ones: no miles row, and no sentence about miles.
        _, text, _ = self.sent(self.request(), miles="5")
        self.assertIn("The booking fee covers scheduling the trip and sending a vehicle.\n", text)
        self.assertNotIn("driving miles", text)

    def test_a_row_is_never_worded_from_the_offices_label(self):
        lines = [pricing.Line("Wheelchair trip", Decimal(1), Decimal("50.00"), kind="base"),
                 pricing.Line("Stretcher surcharge", Decimal(1), Decimal("5.00"))]
        self.assertEqual(quotes.customer_rows(self.request(), self.card, lines, 0),
                         [("Booking fee", "$50.00"), ("Other charge", "$5.00")])

    def test_nothing_in_the_email_names_the_rider_the_trip_or_the_service(self):
        self.card.base_equipment_delivery = Decimal("20.00")
        self.card.base_ambulatory = Decimal("30.00")
        self.card.save()
        requests = [
            self.request(),
            self.request(service_type="ambulatory", return_ride="scheduled", return_datetime=at(13)),
            self.request(return_ride="scheduled", return_datetime=at(19)),
            self.request(return_ride="will_call"),
            self.request(return_ride="wait"),
            self.request(service_type="equipment_delivery"),
        ]
        for request in requests:
            subject, text, html = self.sent(request, wait_minutes=45)
            for part in (subject, text, html):
                for private in self.PRIVATE:
                    self.assertNotIn(private, part, (request.service_type, request.return_ride))
                for word in self.SERVICE_WORDS:
                    self.assertNotIn(word, part.lower(), (request.service_type, request.return_ride))
            # The subject and the inbox preview line carry no amount and no miles.
            preview = html[:html.index("<table")]
            self.assertIn("Here is the price for the transportation you requested.", preview)
            for part in (subject, preview):
                self.assertNotIn("$", part)
                self.assertNotIn("mile", part.lower())
            self.assertEqual(subject, f"Your price for ride #{str(request.pk)[:8].upper()} — AngelCare Transit")

    def test_the_driver_does_not_wait_paragraph_is_only_for_a_round_trip_without_waiting(self):
        leaves = ("On this booking the driver does not wait for you. The driver leaves after dropping you off, "
                  "and the ride back is a second trip.")
        calls = "When you call for the ride back, there can be a wait if the driver is finishing another ride."
        switch = ("If you would rather have the driver wait for you and bring you straight back, "
                  "tell us and we will send you a new price.")
        for ride_back, shown in (("none", ()), ("wait", ()), ("scheduled", (leaves, switch)),
                                 ("will_call", (leaves, calls, switch))):
            request = self.request(return_ride=ride_back, return_datetime=at(13) if ride_back == "scheduled" else None)
            _, text, html = self.sent(request)
            for body in (text, html):
                self.assertEqual("Your ride back" in body, bool(shown), ride_back)
                for sentence in (leaves, calls, switch):
                    self.assertEqual(sentence in body, sentence in shown, (ride_back, sentence))
            if shown:
                self.assertIn(" ".join(shown) + "\n", text)
            # It makes no promise the system doesn't keep.
            for promise in ("guarantee", "cheaper", "same driver", "save"):
                self.assertNotIn(promise, text.lower())
        self.assertEqual(quotes.no_wait_note(self.request(service_type="equipment_delivery", return_ride="will_call")),
                         "")

    def test_a_delivery_given_a_ride_back_by_staff_is_called_the_round_trip_it_is_priced_as(self):
        # The website refuses this pair; a staff edit of the request can make it. Its trips are
        # billed both ways, so "Covers" must not say one-way above rows for the miles there and back.
        self.card.base_equipment_delivery = Decimal("20.00")
        self.card.save()
        for ride_back, covers in (("none", "One-way trip"), ("scheduled", "Round trip (there and back)"),
                                  ("will_call", "Round trip (there and back)"),
                                  ("wait", "Round trip, with the driver waiting for you")):
            request = self.request(service_type="equipment_delivery", return_ride=ride_back,
                                   return_datetime=at(13) if ride_back == "scheduled" else None)
            subject, text, html = self.sent(request, wait_minutes=45)
            self.assertIn(f"Covers: {covers}\n", text, ride_back)
            for body in (text, html):
                self.assertIn(covers, body, ride_back)
                # One-way or round trip, never both in the same email.
                self.assertEqual("One-way" in body, ride_back == "none", ride_back)
                self.assertEqual("Miles back" in body, ride_back != "none", ride_back)
                self.assertEqual("A round trip is two rides" in body, ride_back != "none", ride_back)
            for part in (subject, text, html):
                for private in self.PRIVATE:
                    self.assertNotIn(private, part, ride_back)
                for word in self.SERVICE_WORDS:
                    self.assertNotIn(word, part.lower(), ride_back)


class QuotedTripTests(QuoteTestCase):
    """A trip made from a request whose price was emailed is invoiced as that price promised (ADR 0049)."""

    def setUp(self):
        super().setUp()
        self.passenger = make_passenger()

    def ride(self, request, *, pickup=None):
        """The request's trips, completed: the ride there, and the ride back when it has one."""
        outbound = Trip.objects.create(
            trip_request=request, scheduled_pickup_datetime=pickup or request.requested_datetime,
            leg=TripLeg.ONE_WAY if request.return_ride == "none" else TripLeg.OUTBOUND,
        )
        if request.return_ride != "none":
            build_return_leg(outbound).save()
        Trip.objects.update(status=TripStatus.COMPLETED)
        return list(Trip.objects.order_by("scheduled_pickup_datetime"))

    def charged(self, trips):
        return sum(pricing.total(pricing.price(trip)[1]) for trip in trips)

    def dearer_card(self, **extra):
        """A newer card in effect on the ride's date, as when the rates go up after the price was emailed."""
        return RateCard.objects.create(
            name="Private pay from Jan 10", effective_from=date(2027, 1, 10),
            base_ambulatory=Decimal("60.00"), base_wheelchair=Decimal("90.00"), per_mile=Decimal("4.00"),
            after_hours_fee=Decimal("25.00"), after_hours_start=time(18, 0), after_hours_end=time(6, 0), **extra,
        )

    def test_a_rate_card_replaced_between_the_email_and_the_ride_does_not_change_the_invoice(self):
        request = self.request(passenger=self.passenger, return_ride="scheduled", return_datetime=at(13))
        quote = quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(quote.amount, Decimal("129.60"))
        newer = self.dearer_card()
        trips = self.ride(request)

        self.assertEqual([pricing.quoted_card(trip) for trip in trips], [self.card, self.card])
        self.assertEqual([pricing.price(trip)[0] for trip in trips], [self.card, self.card])
        self.assertEqual(self.charged(trips), quote.amount)
        # The invoice, and the figure the profit report and percent-of-charge pay use, agree.
        self.assertEqual([pricing.trip_charge(trip) for trip in trips], [Decimal("64.80"), Decimal("64.80")])
        made, skipped = pricing.create_invoices(None, DAY, DAY, user=None)
        self.assertEqual(skipped, [])
        self.assertEqual(sum(invoice.amount for invoice in made), quote.amount)
        self.assertEqual({invoice.rate_card for invoice in made}, {self.card})
        self.assertEqual(sorted((line["kind"], line["leg"]) for invoice in made for line in invoice.lines),
                         [("base", "back"), ("base", "there"), ("miles", "back"), ("miles", "there")])

        # A ride on the same day that was never quoted uses the card in effect, as before.
        walk_in = Trip.objects.create(passenger=self.passenger, service_type="wheelchair", pickup_address="1 Main St",
                                      dropoff_address="2 Clinic Rd", scheduled_pickup_datetime=at(15),
                                      billable_miles=Decimal("12.4"), status=TripStatus.COMPLETED)
        self.assertIsNone(pricing.quoted_card(walk_in))
        self.assertEqual(pricing.price(walk_in)[0], newer)

    def test_with_no_price_emailed_nothing_changes(self):
        request = self.request(passenger=self.passenger, requested_datetime=at(17), return_ride="will_call")
        newer = self.dearer_card()
        trips = self.ride(request)
        for trip in trips:
            trip.billable_miles = Decimal("5")
        self.assertEqual([pricing.quoted_card(trip) for trip in trips], [None, None])
        self.assertEqual([pricing.price(trip)[0] for trip in trips], [newer, newer])
        # The ride back is put on the board two hours later, at 7 PM: after-hours, and charged.
        self.assertEqual([[line.label for line in pricing.price(trip)[1]] for trip in trips],
                         [["Wheelchair trip", "Miles"], ["Wheelchair trip", "Miles", "After-hours"]])

        # A price that could not be delivered promised nothing.
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED,
                    notifier=NotificationService(email_provider=FailingEmailProvider()))
        self.assertFalse(Quote.objects.get().delivered)
        self.assertEqual([pricing.quoted_card(trip) for trip in trips], [None, None])
        self.assertIn("After-hours", [line.label for line in pricing.price(trips[1])[1]])

    def test_a_quoted_ride_back_with_no_set_time_pays_no_after_hours_fee(self):
        # Call when ready: the price had no after-hours fee for the ride back, so the invoice has none.
        request = self.request(passenger=self.passenger, requested_datetime=at(17), return_ride="will_call")
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(quote.amount, Decimal("100.00"))
        there, back = self.ride(request)
        self.assertEqual((back.will_call, pricing.local(back.scheduled_pickup_datetime).hour), (True, 19))
        self.assertEqual([line.label for line in pricing.price(back)[1]], ["Wheelchair trip"])
        self.assertEqual(self.charged([there, back]), quote.amount)

    def test_a_quoted_wait_and_return_ride_back_pays_the_wait_but_no_after_hours_fee(self):
        request = self.request(passenger=self.passenger, requested_datetime=at(17), return_ride="wait")
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=40, user=None, **CONFIRMED)
        self.assertEqual(quote.amount, Decimal("125.00"))  # 50 + 50 + 25 minutes beyond the free 15
        there, back = self.ride(request)
        TripEvent.objects.create(trip=there, event_type="dropped_off", occurred_at=at(17, 30))
        TripEvent.objects.create(trip=back, event_type="picked_up", occurred_at=at(18, 10))
        self.assertEqual([line.label for line in pricing.price(back)[1]],
                         ["Wheelchair trip", "Wait time (40 min, first 15 free)"])
        self.assertEqual(self.charged([there, back]), quote.amount)

    def test_a_quoted_ride_still_pays_after_hours_where_the_price_had_it(self):
        # The ride there at 7 PM and a ride back at a set time in the evening were both priced with the fee.
        request = self.request(passenger=self.passenger, requested_datetime=at(19), return_ride="scheduled",
                               return_datetime=at(21))
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(quote.amount, Decimal("120.00"))
        trips = self.ride(request)
        self.assertEqual([[line.label for line in pricing.price(trip)[1]] for trip in trips],
                         [["Wheelchair trip", "After-hours"], ["Wheelchair trip", "After-hours"]])
        self.assertEqual(self.charged(trips), quote.amount)

    def test_a_ride_back_at_a_set_time_with_no_time_saved_is_invoiced_as_it_was_priced(self):
        # Staff can save "at a set time" with the time left blank. The price treats that ride back
        # as having no set time, and so must the invoice: the trip itself is not marked will-call.
        request = self.request(passenger=self.passenger, requested_datetime=at(17), return_ride="scheduled",
                               return_datetime=None)
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(quote.amount, Decimal("100.00"))
        self.assertNotIn("after_hours", [line["kind"] for line in quote.lines])
        there, back = self.ride(request)
        self.assertEqual((back.will_call, back.driver_waits, pricing.local(back.scheduled_pickup_datetime).hour),
                         (False, False, 19))
        self.assertEqual([line.label for line in pricing.price(back)[1]], ["Wheelchair trip"])
        self.assertEqual(self.charged([there, back]), quote.amount)

    def test_a_ride_back_priced_at_a_set_time_keeps_its_after_hours_fee_when_later_marked_will_call(self):
        # The emailed price included the fee for the 7 PM ride back. Ticking will-call or
        # wait-and-return on the trip afterwards does not take it off the invoice.
        request = self.request(passenger=self.passenger, return_ride="scheduled", return_datetime=at(19))
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(quote.amount, Decimal("110.00"))
        there, back = self.ride(request)
        self.assertEqual(self.charged([there, back]), quote.amount)
        # A wait inside the free minutes, so the wait-and-return ride back can be priced.
        TripEvent.objects.create(trip=there, event_type="dropped_off", occurred_at=at(18, 50))
        TripEvent.objects.create(trip=back, event_type="picked_up", occurred_at=at(19))
        for flag in ("will_call", "driver_waits"):
            Trip.objects.filter(pk=back.pk).update(will_call=False, driver_waits=False)
            Trip.objects.filter(pk=back.pk).update(**{flag: True})
            back = Trip.objects.get(pk=back.pk)
            self.assertEqual(pricing.quoted_card(back), self.card)
            self.assertEqual([line.label for line in pricing.price(back)[1]], ["Wheelchair trip", "After-hours"], flag)
            self.assertEqual(self.charged([there, back]), quote.amount, flag)

    def test_a_ride_back_added_by_hand_to_a_one_way_price_pays_after_hours(self):
        # The emailed price was for a one-way ride. A will-call ride back that staff link to it
        # afterwards was never in that price, so nothing was promised about its after-hours fee.
        request = self.request(passenger=self.passenger)
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        (there,) = self.ride(request)
        back = Trip.objects.create(
            passenger=self.passenger, service_type="wheelchair", pickup_address="2 Clinic Rd",
            dropoff_address="1 Main St", scheduled_pickup_datetime=at(21), leg=TripLeg.RETURN, outbound_trip=there,
            will_call=True, billable_miles=Decimal("5"), status=TripStatus.COMPLETED,
        )
        self.assertEqual(pricing.quoted_card(back), self.card)  # still priced on the card of the emailed price
        self.assertEqual([line.label for line in pricing.price(back)[1]], ["Wheelchair trip", "After-hours"])
        self.assertEqual(pricing.total(pricing.price(there)[1]), quote.amount)

    def test_only_the_newest_delivered_price_counts_and_only_while_its_card_exists(self):
        request = self.request(passenger=self.passenger)
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        (trip,) = self.ride(request)
        self.assertEqual(pricing.quoted_card(trip), self.card)

        # A second price, sent after the rates went up, replaces the first.
        newer = self.dearer_card()
        second = quotes.send(request, miles=Decimal("6"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertEqual(second.rate_card, newer)
        self.assertEqual(pricing.quoted_card(trip), newer)
        # A later one that never arrived does not.
        failed = quotes.send(request, miles=Decimal("7"), wait_minutes=0, user=None, **CONFIRMED,
                             notifier=NotificationService(email_provider=FailingEmailProvider()))
        self.assertFalse(failed.delivered)
        self.assertEqual(pricing.quoted_card(trip), newer)
        # Its card deleted: priced with the card in effect, never with the older price's card.
        Quote.objects.filter(pk=second.pk).update(rate_card=None)
        self.assertIsNone(pricing.quoted_card(trip))
        self.assertEqual(pricing.price(trip)[0], newer)

    def test_a_trip_billed_to_someone_else_than_the_price_was_for_is_priced_as_before(self):
        request = self.request(passenger=self.passenger, requested_datetime=at(17), return_ride="will_call")
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        there, back = self.ride(request)
        self.assertEqual(pricing.quoted_card(back), self.card)

        # The rider turns out to be on a facility's contract: its card, and its after-hours fee.
        contract = Payer.objects.create(name="Example Rehab contract", payer_type="facility_contract")
        contract_card = RateCard.objects.create(
            name="Example Rehab 2027", payer=contract, effective_from=date(2027, 1, 1),
            base_wheelchair=Decimal("45.00"), after_hours_fee=Decimal("10.00"),
            after_hours_start=time(18, 0), after_hours_end=time(6, 0),
        )
        Trip.objects.update(payer=contract)
        there, back = Trip.objects.order_by("scheduled_pickup_datetime")
        self.assertEqual([pricing.quoted_card(trip) for trip in (there, back)], [None, None])
        card, lines = pricing.price(back)
        self.assertEqual((card, [line.label for line in lines]), (contract_card, ["Wheelchair trip", "After-hours"]))

        # A Medicaid or broker payer is never priced from an emailed price, whatever card it names.
        broker = Payer.objects.create(name="MTM", payer_type="broker")
        broker_card = RateCard.objects.create(name="MTM 2027", payer=broker, effective_from=date(2027, 1, 12),
                                              base_wheelchair=Decimal("40.00"))
        older = RateCard.objects.create(name="MTM 2026", payer=broker, effective_from=date(2026, 1, 1),
                                        base_wheelchair=Decimal("35.00"))
        Trip.objects.update(payer=broker)
        Quote.objects.update(rate_card=older)
        there = Trip.objects.get(pk=there.pk)
        self.assertIsNone(pricing.quoted_card(there))
        self.assertEqual(pricing.price(there)[0], broker_card)


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

        sent = self.client.post(self.url(request), {"miles": "12.40", "wait_minutes": "0", "checked_amount": "64.80",
                                                    "miles_checked": "1", "customer_pays": "1", "send": "1"})
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
        response = self.client.post(self.url(request), {"miles": "12.4", "checked_amount": "60.00", "send": "1",
                                                        "miles_checked": "1", "customer_pays": "1"})
        self.assertContains(response, "not the one you checked")
        self.assertNotContains(response, "data-quote-unconfirmed")
        self.assertContains(response, "Total: $64.80")
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

    def zipped(self, **extra):
        """A request whose two addresses carry ZIP codes: downtown Fort Worth to downtown Dallas."""
        return self.request(pickup_address="1 Main St, Fort Worth, TX 76102",
                            dropoff_address="2 Clinic Rd, Dallas, TX 75201", **extra)

    def test_the_email_button_sends_the_miles_in_the_box_never_a_hidden_copy(self):
        self.login("Administrator")
        request = self.zipped(return_ride="wait")
        estimated = distance.estimate(request).miles
        checked = pricing.total(pricing.quote(request, miles=estimated)[1])

        html = self.client.get(self.url(request)).content.decode()
        # A browser posts only the form of the button pressed: the Email button's form must be
        # the one holding the miles box and the wait box, with no second copy of either.
        button = html.index('name="send"')
        form = html[html.rindex("<form", 0, button):html.index("</form>", button)]
        self.assertIn(f'<input type="number" name="miles" step="0.01" min="0" value="{estimated}"', form)
        self.assertIn('<input type="number" name="wait_minutes"', form)
        self.assertIn(f'name="checked_amount" value="{checked}"', form)
        self.assertEqual((html.count('name="miles"'), html.count('name="wait_minutes"')), (1, 1))
        self.assertEqual(form.count("csrfmiddlewaretoken"), 1)
        # "Work out the price" comes first (Enter in a box never sends) and doesn't ask for the two boxes.
        self.assertIn('name="preview" value="1" formnovalidate', form)
        self.assertLess(form.index('name="preview"'), form.index('name="send"'))

        # The map said 33 miles: typed in the box, both boxes ticked, Email pressed without
        # "Work out the price". The new price is shown to check; the estimate's price is not sent.
        response = self.client.post(self.url(request), {
            "miles": "33", "wait_minutes": "", "checked_amount": str(checked), "send": "1",
            "miles_checked": "1", "customer_pays": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not the one you checked: the miles, the wait or the rate card changed")
        self.assertContains(response, "Total: $212.00")  # each way: 50 + 28 miles beyond the 5 included x 2
        self.assertContains(response, 'name="checked_amount" value="212.00"')
        self.assertNotContains(response, "checked>")
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="quote_sent").exists())

        # Checked again at 33 miles, it goes out with the miles that were typed.
        sent = self.client.post(self.url(request), {
            "miles": "33", "wait_minutes": "", "checked_amount": "212.00", "send": "1",
            "miles_checked": "1", "customer_pays": "1"})
        self.assertRedirects(sent, self.request_page(request), fetch_redirect_response=False)
        self.assertEqual((Quote.objects.get().miles, len(mail.outbox)), (Decimal("33.00"), 1))
        self.assertEqual(AuditLog.objects.get(action="quote_sent").after["miles"], "33")

    def test_the_page_opens_with_the_price_worked_out_from_the_estimated_miles(self):
        self.login("Administrator")
        request = self.zipped()
        miles = distance.estimate(request).miles
        expected = pricing.total(pricing.quote(request, miles=miles)[1])

        page = self.client.get(self.url(request))
        self.assertContains(page, "data-quote-estimate")
        self.assertContains(page, f"About {miles} miles one way,")
        self.assertContains(page, "estimated from ZIP codes 76102 and 75201.")
        self.assertContains(page, "This is an estimate, not the driving distance.")
        self.assertNotContains(page, "for the round trip")
        self.assertContains(page, "The miles box below starts with this estimate.")
        self.assertContains(page, f'name="miles" step="0.01" min="0" value="{miles}"')
        # The price is there straight away, with the send form and both boxes unticked.
        self.assertContains(page, f"Total: ${expected}")
        self.assertContains(page, f'name="checked_amount" value="{expected}"')
        self.assertContains(page, '<input type="checkbox" name="miles_checked" value="1" required>')
        self.assertContains(page, '<input type="checkbox" name="customer_pays" value="1" required>')
        self.assertContains(page, "I checked the miles on the map.")
        self.assertContains(page, "The customer pays for this ride. It is not paid by Medicaid, a broker or a "
                                  "facility account.")
        self.assertNotContains(page, "Booked through the facility portal")
        # Opening the page changes nothing: one "viewed" row, no quote, no email, the request still New.
        self.assertEqual(list(AuditLog.objects.filter(resource_id=str(request.pk)).values_list("action", flat=True)),
                         ["viewed"])
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.NEW)

    def test_the_map_button_is_a_link_in_the_administrators_own_browser(self):
        self.login("Administrator")
        request = self.zipped()
        page = self.client.get(self.url(request))
        html = page.content.decode()
        link = html[html.index("<a", html.index("data-quote-miles")):html.index("data-quote-map")]
        self.assertIn('target="_blank" rel="noopener noreferrer"', link)
        self.assertIn("https://www.google.com/maps/dir/?api=1&amp;origin=1%20Main%20St%2C%20Fort%20Worth%2C%20TX%2076102"
                      "&amp;destination=2%20Clinic%20Rd%2C%20Dallas%2C%20TX%2075201&amp;travelmode=driving", link)
        self.assertContains(page, "Open the route in Google Maps")

    def test_a_round_trip_shows_the_miles_both_ways(self):
        self.login("Administrator")
        request = self.zipped(return_ride="scheduled", return_datetime=at(13))
        estimate = distance.estimate(request)
        page = self.client.get(self.url(request))
        self.assertContains(page, f"About {estimate.miles} miles one way ({estimate.miles * 2} for the round trip),")

    def test_no_estimate_shows_why_and_still_offers_the_map(self):
        self.login("Administrator")
        for request, why in ((self.request(), "The pickup address has no ZIP code."),
                             (self.request(pickup_address="1 Main St, Fort Worth, TX 76102",
                                           dropoff_address="2 Clinic Rd, Fort Worth, TX 76102"),
                              "Both addresses are in ZIP code 76102")):
            page = self.client.get(self.url(request))
            self.assertContains(page, "data-quote-no-estimate")
            self.assertContains(page, why)
            self.assertContains(page, "Open the route in Google Maps")
            self.assertContains(page, 'name="miles" step="0.01" min="0" value=""')
            self.assertNotContains(page, "data-quote-price")
            self.assertNotContains(page, "data-quote-error")

    def test_the_miles_of_the_last_price_sent_come_before_the_estimate(self):
        self.login("Administrator")
        request = self.zipped()
        quotes.send(request, miles=Decimal("33"), wait_minutes=0, user=None, **CONFIRMED)
        page = self.client.get(self.url(request))
        self.assertContains(page, 'value="33.00"')
        self.assertContains(page, "starts with the miles of the last price sent")
        self.assertContains(page, "Total: $106.00")  # 50 + 28 miles beyond the 5 included x 2
        self.assertContains(page, "data-quote-estimate")  # the estimate is still shown, to compare

    def test_a_send_without_both_confirmations_sends_nothing_and_shows_the_price_again(self):
        self.login("Administrator")
        request = self.request()
        send = {"miles": "12.40", "wait_minutes": "0", "checked_amount": "64.80", "send": "1"}
        for ticked in ({}, {"miles_checked": "1"}, {"customer_pays": "1"}):
            response = self.client.post(self.url(request), {**send, **ticked})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "data-quote-unconfirmed")
            self.assertContains(response, "Nothing was sent. Tick both boxes first")
            self.assertContains(response, "Total: $64.80")
            self.assertNotContains(response, "not the one you checked")
            # Shown again with both boxes unticked.
            self.assertNotContains(response, "checked>")
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="quote_sent").exists())
        request.refresh_from_db()
        self.assertEqual(request.status, TripRequestStatus.NEW)

        sent = self.client.post(self.url(request), {**send, "miles_checked": "1", "customer_pays": "1"})
        self.assertRedirects(sent, self.request_page(request), fetch_redirect_response=False)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (1, 1))
        row = AuditLog.objects.get(action="quote_sent", resource_id=str(request.pk))
        self.assertEqual((row.after["miles_checked"], row.after["who_pays"]), (True, "customer"))

    def test_a_facility_portal_booking_says_who_booked_it_above_the_confirmations(self):
        self.login("Administrator")
        request = self.request(facility=Facility.objects.create(name="Example Rehab"))
        page = self.client.post(self.url(request), {"miles": "12.4", "preview": "1"})
        self.assertContains(page, "Booked through the facility portal by Example Rehab. "
                                  "If the facility is invoiced for this ride, do not email a price.")
        html = page.content.decode()
        self.assertLess(html.index("data-quote-facility"), html.index('name="miles_checked"'))

    def test_the_rules_panel_explains_the_price_even_before_it_is_worked_out(self):
        self.login("Administrator")
        page = self.client.get(self.url(self.request(return_ride="will_call")))
        self.assertNotContains(page, "data-quote-price")
        self.assertContains(page, "data-quote-rules")
        self.assertContains(page, "How this price is worked out")
        for rule in ("The booking fee is $50.00.",
                     "The first 5.00 miles are included; each mile after them costs $2.00.",
                     "A round trip is priced as two rides",
                     "The driver does not wait: the driver makes two separate trips, one there and one back."):
            self.assertContains(page, rule)

    def test_a_medicaid_or_broker_rider_has_no_price_and_the_page_says_so_at_once(self):
        self.login("Administrator")
        mtm = Payer.objects.create(name="MTM", payer_type="broker")
        for request in (self.request(passenger=make_passenger(payer=mtm)),
                        self.zipped(passenger=make_passenger(payer=mtm))):
            page = self.client.get(self.url(request))
            self.assertContains(page, "data-quote-error")
            self.assertContains(page, "paid by MTM")
            self.assertNotContains(page, "data-quote-price")
            self.assertNotContains(page, "data-quote-rules")
            # Even a forged send with both boxes ticked is refused.
            self.client.post(self.url(request), {"miles": "5", "checked_amount": "50.00", "send": "1",
                                                 "miles_checked": "1", "customer_pays": "1"})
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
        quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        mail.outbox.clear()
        self.assertEqual(self.client.get(self.url(request)).status_code, 403)
        send = {"miles": "5", "wait_minutes": "0", "checked_amount": "50.00", "send": "1",
                "miles_checked": "1", "customer_pays": "1"}
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
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=None, **CONFIRMED)
        self.assertContains(self.client.get("/admin/billing/quote/"), f"#{str(request.pk)[:8].upper()}")
        detail = f"/admin/billing/quote/{quote.pk}/change/"
        self.assertContains(self.client.get(detail), "Wheelchair trip: 1 × $50.00")
        # A price saved before lines said what kind they are (ADR 0049) still shows.
        Quote.objects.filter(pk=quote.pk).update(
            lines=[{"label": "Wheelchair trip", "quantity": "1", "unit": "50.00", "amount": "50.00"}])
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

    def test_a_new_rate_card_starts_with_fees_once_and_fifteen_minute_wait_blocks(self):
        self.login("Administrator")
        form = self.client.get("/admin/billing/ratecard/add/").context["adminform"].form
        self.assertEqual((form["fees_on_return"].value(), form["wait_block_minutes"].value(),
                          form["wait_free_minutes"].value()), (False, 15, 15))

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
        quote = quotes.send(request, miles=Decimal("5"), wait_minutes=0, user=admin, **CONFIRMED)
        entry = trash.send(TripRequest.objects.get(pk=request.pk), user=admin)
        self.assertFalse(Quote.objects.exists())
        trash.restore(entry, user=admin)
        restored = Quote.objects.get(pk=quote.pk)
        self.assertEqual((restored.trip_request_id, restored.amount, restored.sent_by), (request.pk, Decimal("50.00"), admin))
