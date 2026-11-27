"""
Discounts on an emailed price (ADR 0051), and the owner's corrections to
the price email (one miles row for a round trip, the real distance on a
card with included miles, a second press of the Email button).

A discount is one more entry in a quote's saved lines: nothing here needs
a database change. No test holds a real key or reaches the network
(`PaymentTestMixin`). Every name, address, rate and amount is made up.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.messages import constants as message_levels
from django.core import mail

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from trips.models import Trip, TripEvent, TripLeg, TripStatus
from trips.returns import build_return_leg
from trips.tests import make_passenger

from . import payments, pricing, quotes
from .models import Invoice, InvoiceStatus, Payment, Quote, RateCard
from .pricing import Discount
from .test_checkout import GENERIC, FlowTestCase
from .test_quotes import CONFIRMED, DAY, at

REASON = "Thank-you for the church group booking"
RIDE_BACKS = ("none", "scheduled", "will_call", "wait")


def dollars(value, reason=REASON) -> Discount:
    return Discount(pricing.DISCOUNT_DOLLARS, Decimal(value), reason)


def percent(value, reason=REASON) -> Discount:
    return Discount(pricing.DISCOUNT_PERCENT, Decimal(value), reason)


def amount_of(shown: str) -> Decimal:
    """The value of an email row as a number: "$50.00" -> 50.00, "-$6.48" -> -6.48."""
    return Decimal(shown.replace("$", "").replace(",", ""))


class DiscountTestCase(FlowTestCase):
    """
    Two made-up cards. The default one charges the fees on each ride and
    includes 5 miles; `fees_once()` swaps in the other kind: the fees once
    for a round trip, no included miles, waiting in 15-minute blocks.
    """

    def fees_once(self) -> RateCard:
        RateCard.objects.all().delete()
        self.card = RateCard.objects.create(
            name="Private pay by service", effective_from=date(2027, 1, 1), fees_on_return=False,
            base_wheelchair=Decimal("70.00"), loading_wheelchair=Decimal("55.00"),
            per_mile_wheelchair=Decimal("5.00"), wait_rate_wheelchair=Decimal("18.00"), wait_block_minutes=15,
            no_show_fee=Decimal("25.00"),
        )
        return self.card

    def booking(self, ride_back="none", **extra):
        """A request linked to a passenger, so that its trips can be made and billed."""
        return self.request(passenger=make_passenger(), return_ride=ride_back,
                            return_datetime=at(13) if ride_back == "scheduled" else None, **extra)

    def email(self, request, discount=None, *, miles="12.4", wait_minutes=0):
        """Send the price; returns (quote, subject, text, html)."""
        mail.outbox.clear()
        quote = self.sent(request, miles=miles, wait_minutes=wait_minutes, discount=discount)
        message = mail.outbox[-1]
        return quote, message.subject, message.body.replace("\r\n", "\n"), message.alternatives[0][0]

    def ride(self, request, *, waited=None, status=TripStatus.COMPLETED):
        """The booking's trips, done: the ride there and, for a round trip, the ride back."""
        outbound = Trip.objects.create(
            trip_request=request, scheduled_pickup_datetime=request.requested_datetime,
            leg=TripLeg.ONE_WAY if request.return_ride == "none" else TripLeg.OUTBOUND,
        )
        if request.return_ride != "none":
            back = build_return_leg(outbound)
            back.save()
            if waited is not None:
                TripEvent.objects.create(trip=outbound, event_type="dropped_off", occurred_at=at(9, 30))
                TripEvent.objects.create(trip=back, event_type="picked_up",
                                         occurred_at=at(9 + (30 + waited) // 60, (30 + waited) % 60))
        Trip.objects.update(status=status)
        return list(Trip.objects.order_by("scheduled_pickup_datetime"))

    @staticmethod
    def clear_rides():
        """Remove the trips and invoices made so far, so one test can run several bookings through a day."""
        Invoice.objects.all().delete()
        TripEvent.objects.all().delete()
        Trip.objects.filter(outbound_trip__isnull=False).delete()
        Trip.objects.all().delete()

    def invoiced(self):
        """Create the day's private-pay invoices: (the invoices oldest ride first, what was skipped)."""
        made, skipped = pricing.create_invoices(None, DAY, DAY, user=None)
        return sorted(made, key=lambda invoice: invoice.trip.scheduled_pickup_datetime), skipped

    def login(self, group="Administrator"):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")
        return user

    @staticmethod
    def page(request) -> str:
        return f"/admin/billing/quote/send/{request.pk}/"


class DiscountPriceTests(DiscountTestCase):
    def test_a_dollar_discount_is_one_negative_line_and_the_amount_is_the_total_after_it(self):
        quote = self.sent(self.request(), discount=dollars("20"))
        self.assertEqual(quote.amount, Decimal("44.80"))  # 50 + 14.80 - 20
        self.assertEqual(quote.lines[-1], {
            "label": "Discount", "quantity": "1", "unit": "-20.00", "amount": "-20.00", "kind": "discount", "leg": "",
            "percent": "", "reason": REASON,
        })
        # The other lines are exactly those of the price without a discount.
        _, plain, _ = pricing.quote(quote.trip_request, miles=Decimal("12.4"))
        self.assertEqual(quote.lines[:-1], [line.as_dict() for line in plain])
        self.assertEqual(sum(Decimal(line["amount"]) for line in quote.lines), quote.amount)

    def test_a_percentage_is_of_the_whole_price_and_keeps_what_was_typed(self):
        quote = self.sent(self.request(return_ride="wait"), miles="5", wait_minutes=45, discount=percent("12.5"))
        # 50 + 50 + 30 minutes of waiting = 130.00; 12.5% of it is 16.25.
        self.assertEqual(quote.amount, Decimal("113.75"))
        entry = quote.lines[-1]
        self.assertEqual((entry["amount"], entry["percent"], entry["kind"], entry["leg"], entry["label"]),
                         ("-16.25", "12.5", "discount", "", "Discount"))
        self.assertEqual(self.sent(self.request(), discount=percent("10.00")).lines[-1]["percent"], "10")

    def test_a_percentage_is_rounded_to_cents_half_up(self):
        # 0.01% of $50.00 is half a cent: half-up makes it one cent, never nothing.
        _, lines, _ = pricing.quote(self.request(), miles=Decimal("5"), discount=percent("0.01"))
        self.assertEqual((lines[-1].amount, pricing.total(lines)), (Decimal("-0.01"), Decimal("49.99")))
        # 0.5% of $64.80 is 32.4 cents: 32.
        _, lines, _ = pricing.quote(self.request(), miles=Decimal("12.4"), discount=percent("0.5"))
        self.assertEqual(lines[-1].amount, Decimal("-0.32"))

    def test_the_whole_price_can_be_taken_off(self):
        for discount in (percent("100"), dollars("64.80")):
            quote = self.sent(self.request(), discount=discount)
            self.assertEqual(quote.amount, Decimal("0.00"))
            self.assertTrue(quote.delivered)
            self.assertIn("Total price: $0.00", mail.outbox[-1].body)

    def test_the_limit_on_a_price_is_on_the_total_after_the_discount(self):
        # 50 + 10,000 miles beyond the 5 included x $2 = $20,050: over the limit by $50.
        with self.assertRaisesMessage(pricing.PricingError, "too much to be right"):
            pricing.quote(self.request(), miles=Decimal("10005"))
        _, lines, _ = pricing.quote(self.request(), miles=Decimal("10005"), discount=dollars("50"))
        self.assertEqual(pricing.total(lines), pricing.MAX_QUOTE)
        with self.assertRaisesMessage(pricing.PricingError, "too much to be right"):
            pricing.quote(self.request(), miles=Decimal("10005"), discount=dollars("49.99"))

    def test_every_discount_that_can_not_be_given_is_refused_with_its_reason_and_nothing_is_sent(self):
        request = self.request()
        refused = (
            (dollars("0"), "The discount must be greater than 0."),
            (dollars("-5"), "The discount must be greater than 0."),
            (percent("0"), "The discount must be greater than 0."),
            (percent("100.01"), "A percentage discount can be at most 100."),
            (percent("150"), "A percentage discount can be at most 100."),
            (percent("10.125"), "A percentage discount can have at most 2 decimal places."),
            (dollars("10.001"), "A dollar discount can have at most 2 decimal places."),
            (dollars("64.81"), "The discount ($64.81) is more than the price before the discount ($64.80)."),
            (dollars("5", reason=""), "Give the reason for the discount."),
            (dollars("5", reason="   "), "Give the reason for the discount."),
            (percent("5", reason="x" * 201), "The reason for the discount can be at most 200 characters."),
            (Discount("euros", Decimal("5"), REASON), "Choose $ or % for the discount."),
            (Discount("$", "5", REASON), "The discount must be a number."),
            (Discount("%", Decimal("NaN"), REASON), "The discount must be greater than 0."),
            (("$", Decimal("5"), REASON), "Choose $ or % for the discount."),  # not a Discount at all
        )
        for discount, why in refused:
            with self.assertRaisesMessage(pricing.PricingError, why):
                pricing.quote(request, miles=Decimal("12.4"), discount=discount)
            with self.assertRaisesMessage(pricing.PricingError, why):
                quotes.send(request, miles=Decimal("12.4"), wait_minutes=0, user=None, discount=discount, **CONFIRMED)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="quote_sent").exists())
        # A percentage too small to take a cent off a $30.00 price is refused, not sent as "-$0.00".
        cheap = self.request(service_type="ambulatory")
        with self.assertRaisesMessage(pricing.PricingError, "This discount comes to $0.00, so nothing would be taken off."):
            quotes.send(cheap, miles=Decimal("5"), wait_minutes=0, user=None, discount=percent("0.01"), **CONFIRMED)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        # At the edges it is allowed: 200 characters, two decimals.
        _, lines, _ = pricing.quote(request, miles=Decimal("12.4"), discount=percent("99.99", reason="x" * 200))
        self.assertEqual(lines[-1].extra["reason"], "x" * 200)

    def test_a_price_with_nothing_to_charge_is_still_refused_before_any_discount(self):
        RateCard.objects.update(base_wheelchair=Decimal("0"), per_mile=Decimal("0"))
        with self.assertRaisesMessage(pricing.PricingError, "Nothing to charge"):
            pricing.quote(self.request(), miles=Decimal("5"), discount=percent("10"))

    def test_the_audit_row_records_the_type_the_value_the_dollars_and_the_reason(self):
        request = self.request()
        quote = self.sent(request, discount=percent("10", reason="  Thank-you   for the church group booking "))
        row = AuditLog.objects.get(action="quote_sent", resource_id=str(request.pk))
        self.assertEqual(row.after["discount"], {"type": "percent", "value": "10", "amount": "6.48", "reason": REASON})
        self.assertEqual((row.after["amount"], row.after["quote"]), ("58.32", str(quote.pk)))

        other = self.request()
        self.sent(other, discount=dollars("20"))
        row = AuditLog.objects.get(action="quote_sent", resource_id=str(other.pk))
        self.assertEqual(row.after["discount"], {"type": "dollars", "value": "20", "amount": "20.00", "reason": REASON})
        # A price without a discount says nothing about one.
        plain = self.request()
        self.sent(plain)
        self.assertNotIn("discount", AuditLog.objects.get(action="quote_sent", resource_id=str(plain.pk)).after)


class DiscountEmailTests(DiscountTestCase):
    PRIVATE = ("Jamie", "Chen", "Main St", "Clinic Rd", "Fort Worth", "555-0100", "Jan", "Wednesday",
               "9:00", "1:00", "13:00", " AM", " PM")
    SERVICE_WORDS = ("wheelchair", "ambulatory", "equipment", "delivery", "stretcher")

    def test_the_email_shows_the_discount_after_the_parts_and_never_the_reason(self):
        for fees_once in (False, True):
            if fees_once:
                self.fees_once()
            for ride_back in RIDE_BACKS:
                for discount, row in ((dollars("20"), "Discount"), (percent("10"), "Discount (10%)")):
                    case = (fees_once, ride_back, row)
                    request = self.request(return_ride=ride_back,
                                           return_datetime=at(13) if ride_back == "scheduled" else None)
                    wait = 45 if ride_back == "wait" else 0
                    quote, subject, text, html = self.email(request, discount, wait_minutes=wait)

                    card, lines, _ = pricing.quote(request, miles=Decimal("12.4"), wait_minutes=wait, discount=discount)
                    rows = quotes.customer_rows(request, card, lines, wait, Decimal("12.4"))
                    off = -lines[-1].amount
                    # The discount is the last row, and the rows still add up to the total.
                    self.assertEqual(rows[-1], (row, f"-${off:,.2f}"), case)
                    self.assertEqual(sum(amount_of(value) for _, value in rows), quote.amount, case)
                    self.assertEqual(sum(1 for label, _ in rows if label.startswith("Discount")), 1, case)

                    self.assertIn(f"{row}: -${off:,.2f}\nTotal price: ${quote.amount:,.2f}\n", text, case)
                    self.assertLess(html.index(row), html.index("Total price"), case)
                    self.assertIn(f"-${off:,.2f}", html, case)
                    for part in (subject, text, html):
                        self.assertNotIn(REASON, part, case)
                        for word in ("Thank-you", "church", "group booking", "reason", "Reason", "promotion", "coupon"):
                            self.assertNotIn(word, part, case)
                        for private in self.PRIVATE:
                            self.assertNotIn(private, part, case)
                        for word in self.SERVICE_WORDS:
                            self.assertNotIn(word, part.lower(), case)
                    # The subject says nothing about money, with or without a discount.
                    self.assertNotIn("$", subject)
                    self.assertNotIn("iscount", subject)

    def test_a_price_without_a_discount_has_no_discount_row(self):
        _, subject, text, html = self.email(self.request())
        for part in (subject, text, html):
            self.assertNotIn("iscount", part)

    def test_what_the_notification_service_is_given_holds_no_reason(self):
        seen = {}

        class Recorder:
            def send_quote(self, quote, **context):
                seen.update(context)
                return True

        quotes.send(self.request(), miles=Decimal("12.4"), wait_minutes=0, user=None, discount=percent("10"),
                    notifier=Recorder(), **CONFIRMED)
        self.assertEqual(seen["rows"][-1], ("Discount (10%)", "-$6.48"))
        self.assertNotIn(REASON, repr(seen))


class DiscountPaymentTests(DiscountTestCase):
    """Online payment (ADR 0050): Stripe is asked for the total after the discount, and told nothing about it."""

    def test_the_amount_asked_at_the_provider_is_the_total_after_the_discount(self):
        self.payments_on()
        quote = self.sent(self.request(), discount=percent("10"))
        self.assertEqual(quote.amount, Decimal("58.32"))
        self.assertIn("/pay/", self.bodies(mail.outbox[-1]))
        self.assertContains(self.client.get(self.pay_path(quote)), "$58.32")

        payment = self.opened(quote)
        self.assertEqual(payment.amount_cents, 5832)
        (asked,) = self.created()
        # Exactly what a session is always opened with: the amount, a reference, the quote's id.
        self.assertEqual(set(asked), {"amount_cents", "reference", "quote_id", "idempotency_key", "success_url",
                                      "cancel_url", "expires_at"})
        self.assertEqual((asked["amount_cents"], asked["quote_id"]), (5832, str(quote.pk)))
        self.assertEqual(asked["reference"], payments.reference(quote.trip_request_id))
        # Not the price before the discount, not the discount, and not a word about either.
        for value in asked.values():
            self.assertNotIn(value, (6480, 648, "6480", "648", "64.80", "6.48", "10", "10%"))
        said = repr(self.provider.calls) + repr(self.provider.sessions)
        for word in (REASON, "iscount", "church", "percent", "Thank"):
            self.assertNotIn(word, said)

        self.provider.pay(payment.session_id)
        self.assertEqual(self.apply(payment), "paid")
        payment.refresh_from_db()
        self.assertEqual(payment.paid_cents, 5832)
        # The customer's "we received your payment" email states the amount paid, not the discount.
        received = self.bodies(mail.outbox[-1])
        self.assertIn("$58.32", received)
        self.assertNotIn(REASON, received)

    def test_a_discount_that_leaves_less_than_fifty_cents_gets_no_pay_link(self):
        self.payments_on()
        for discount, total in ((dollars("64.31"), "0.49"), (dollars("64.80"), "0.00"), (percent("100"), "0.00")):
            quote = self.sent(self.request(), discount=discount)
            self.assertEqual(quote.amount, Decimal(total))
            message = mail.outbox[-1]
            for body in (message.body, message.alternatives[0][0]):
                self.assertNotIn("/pay/", body)
                self.assertNotIn("Pay securely online", body)
                self.assertIn("Your ride is not booked yet.", body)
                self.assertIn("817-766-9228", body)
            self.assertIn("To book it at this price, call 817-766-9228", message.body)
            # The link, were someone to build it, is refused: on the page and on the button.
            self.assertContains(self.client.get(self.pay_path(quote)), GENERIC)
            self.assertContains(self.press(quote), GENERIC)
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(Payment.objects.exists())
        # Fifty cents is the least a card can pay: from there the link is offered.
        quote = self.sent(self.request(), discount=dollars("64.30"))
        self.assertEqual(quote.amount, Decimal("0.50"))
        self.assertIn("/pay/", self.bodies(mail.outbox[-1]))


class DiscountInvoiceTests(DiscountTestCase):
    """A ride that goes as quoted is invoiced exactly the emailed price, the discount included."""

    def test_the_invoices_total_the_emailed_price_for_each_ride_back_and_each_kind_of_card(self):
        for fees_once in (False, True):
            for ride_back in RIDE_BACKS:
                for discount in (dollars("20"), percent("10")):
                    case = (fees_once, ride_back, discount.kind)
                    self.clear_rides()
                    if fees_once:
                        self.fees_once()
                    request = self.booking(ride_back)
                    wait = 40 if ride_back == "wait" else 0
                    quote = self.sent(request, miles="10", wait_minutes=wait, discount=discount)
                    trips = self.ride(request, waited=wait if ride_back == "wait" else None)

                    made, skipped = self.invoiced()
                    self.assertEqual(skipped, [], case)
                    self.assertEqual(len(made), len(trips), case)
                    self.assertEqual(sum(invoice.amount for invoice in made), quote.amount, case)
                    # The whole discount is on the invoices, once: in the dollars the customer was shown.
                    on_invoices = [Decimal(line["amount"]) for invoice in made for line in invoice.lines
                                   if line["kind"] == "discount"]
                    self.assertEqual(sum(on_invoices), Decimal(quote.lines[-1]["amount"]), case)
                    for invoice in made:
                        self.assertEqual(invoice.status, InvoiceStatus.DRAFT, case)
                        self.assertEqual(sum(Decimal(line["amount"]) for line in invoice.lines), invoice.amount, case)
                        # An invoice's discount line carries no reason and no percentage: only the share.
                        for line in invoice.lines:
                            self.assertNotIn("reason", line, case)
                    # The profit report and percent-of-charge driver pay see the discounted charge.
                    self.assertEqual(sum(pricing.trip_charge(trip) for trip in trips), quote.amount, case)
                    Invoice.objects.all().delete()
                    self.assertEqual(sum(pricing.trip_charge(trip) for trip in trips), quote.amount, case)

    def test_the_first_trip_takes_the_discount_and_the_ride_back_only_what_is_left(self):
        self.fees_once()
        # Fees once: the first trip's part is 70 + 55 + 50 miles = 175, the ride back's is its 50 miles.
        request = self.booking("scheduled")
        quote = self.sent(request, miles="10", discount=dollars("30"))
        self.assertEqual(pricing.discount_split(quote.lines), (Decimal("30.00"), Decimal("0.00")))
        there, back = self.ride(request)
        self.assertEqual(pricing.total(pricing.price(there)[1]), Decimal("145.00"))
        self.assertEqual([line.kind for line in pricing.price(back)[1]], ["miles"])  # no discount line at all
        # The same whichever invoice is made first: the shares come from the saved price alone.
        self.assertEqual(pricing.total(pricing.price(back)[1]), Decimal("50.00"))

    def test_a_discount_larger_than_the_first_trips_part_carries_over_to_the_ride_back(self):
        self.fees_once()
        request = self.booking("scheduled")
        quote = self.sent(request, miles="10", discount=dollars("200"))
        self.assertEqual(quote.amount, Decimal("25.00"))
        self.assertEqual(pricing.discount_split(quote.lines), (Decimal("175.00"), Decimal("25.00")))
        there, back = self.ride(request)
        self.assertEqual(pricing.total(pricing.price(there)[1]), Decimal("0.00"))
        self.assertEqual([(line.label, line.amount) for line in pricing.price(back)[1]],
                         [("Miles", Decimal("50.00")), ("Discount", Decimal("-25.00"))])

        made, skipped = self.invoiced()
        # The first trip has nothing left to bill, and the page says why; the ride back bills the rest.
        self.assertEqual([(trip.pk, why) for trip, why in skipped],
                         [(there.pk, "Nothing to bill: the discount on the emailed price covers this ride.")])
        self.assertEqual([(invoice.trip_id, invoice.amount) for invoice in made], [(back.pk, Decimal("25.00"))])
        self.assertEqual(sum(invoice.amount for invoice in made), quote.amount)

    def test_the_wait_is_charged_on_the_ride_back_so_it_counts_on_that_side(self):
        self.fees_once()
        request = self.booking("wait")
        # 70 + 55 + 50 + 50 + 2 blocks of waiting x 18 = 261; the first trip's part is 175.
        quote = self.sent(request, miles="10", wait_minutes=40, discount=dollars("230"))
        self.assertEqual(quote.amount, Decimal("31.00"))
        self.assertEqual(pricing.discount_split(quote.lines), (Decimal("175.00"), Decimal("55.00")))
        there, back = self.ride(request, waited=40)
        self.assertEqual(pricing.total(pricing.price(there)[1]), Decimal("0.00"))
        self.assertEqual(pricing.total(pricing.price(back)[1]), Decimal("31.00"))  # 50 + 36 - 55
        # No invoice is ever negative, whatever the discount.
        for trip in (there, back):
            self.assertGreaterEqual(pricing.total(pricing.price(trip)[1]), 0)

    def test_the_whole_price_taken_off_leaves_nothing_to_bill(self):
        request = self.booking("scheduled")
        quote = self.sent(request, miles="10", discount=percent("100"))
        self.assertEqual(quote.amount, Decimal("0.00"))
        self.ride(request)
        made, skipped = self.invoiced()
        self.assertEqual(made, [])
        self.assertEqual({why for _, why in skipped},
                         {"Nothing to bill: the discount on the emailed price covers this ride."})

    def test_a_ride_that_differs_from_the_price_is_charged_in_full_on_top_of_the_same_dollars(self):
        # 10% of the emailed $159.60 is $15.96, and stays $15.96 when the driver waits longer.
        request = self.booking("wait")
        quote = self.sent(request, miles="12.4", wait_minutes=45, discount=percent("10"))
        self.assertEqual(quote.lines[-1]["amount"], "-15.96")
        self.ride(request, waited=75)
        made, _ = self.invoiced()
        discounts = [Decimal(line["amount"]) for invoice in made for line in invoice.lines if line["kind"] == "discount"]
        self.assertEqual(sum(discounts), Decimal("-15.96"))
        # 30 more minutes at $1.00, charged in full.
        self.assertEqual(sum(invoice.amount for invoice in made), quote.amount + Decimal("30.00"))

    def test_a_ride_shorter_than_the_price_never_makes_an_invoice_negative(self):
        request = self.booking()
        self.sent(request, miles="12.4", discount=dollars("60"))  # 64.80 - 60
        (trip,) = self.ride(request)
        # The miles on the trip are corrected down after the ride: 50.00 is left to charge.
        Trip.objects.filter(pk=trip.pk).update(billable_miles=Decimal("5"))
        lines = pricing.price(Trip.objects.get(pk=trip.pk))[1]
        self.assertEqual([(line.label, line.amount) for line in lines],
                         [("Wheelchair trip", Decimal("50.00")), ("Discount", Decimal("-50.00"))])

    def test_a_no_show_gets_no_discount(self):
        RateCard.objects.update(no_show_fee=Decimal("25.00"))
        request = self.booking("scheduled")
        self.sent(request, miles="10", discount=percent("50"))
        trips = self.ride(request, status=TripStatus.NO_SHOW)
        for trip in trips:
            self.assertEqual([(line.label, line.amount) for line in pricing.price(trip)[1]],
                             [("No-show fee", Decimal("25.00"))])
        made, skipped = self.invoiced()
        self.assertEqual(([invoice.amount for invoice in made], skipped), ([Decimal("25.00")] * 2, []))
        self.assertNotIn("discount", [line["kind"] for invoice in made for line in invoice.lines])

    def test_a_ride_back_added_by_hand_to_a_discounted_one_way_price_gets_none_of_it(self):
        request = self.booking()
        quote = self.sent(request, miles="5", discount=dollars("10"))
        (there,) = self.ride(request)
        back = Trip.objects.create(
            passenger=there.passenger, service_type="wheelchair", pickup_address="2 Clinic Rd",
            dropoff_address="1 Main St", scheduled_pickup_datetime=at(13), leg=TripLeg.RETURN, outbound_trip=there,
            billable_miles=Decimal("5"), status=TripStatus.COMPLETED,
        )
        self.assertEqual(pricing.total(pricing.price(there)[1]), quote.amount)
        self.assertEqual([line.label for line in pricing.price(back)[1]], ["Wheelchair trip"])

    def test_prices_sent_before_discounts_are_priced_exactly_as_before(self):
        request = self.booking("scheduled")
        quote = self.sent(request, miles="12.4")
        self.assertIsNone(pricing.discount_entry(quote.lines))
        self.assertEqual(pricing.discount_split(quote.lines), (Decimal("0"), Decimal("0")))
        trips = self.ride(request)
        before = [[line.as_dict() for line in pricing.price(trip)[1]] for trip in trips]
        self.assertNotIn("discount", [line["kind"] for lines in before for line in lines])
        self.assertEqual(sum(pricing.total(pricing.price(trip)[1]) for trip in trips), quote.amount)
        # A price saved before lines said what kind they are (before ADR 0049) changes nothing either.
        Quote.objects.filter(pk=quote.pk).update(
            lines=[{"label": line["label"], "quantity": line["quantity"], "unit": line["unit"],
                    "amount": line["amount"]} for line in quote.lines])
        self.assertEqual(pricing.discount_split(Quote.objects.get(pk=quote.pk).lines), (Decimal("0"), Decimal("0")))
        self.assertEqual([[line.as_dict() for line in pricing.price(trip)[1]] for trip in trips], before)
        # And a request that was never sent a price has no discount to look for.
        walk_in = Trip.objects.create(passenger=make_passenger(), service_type="wheelchair", pickup_address="1 Main St",
                                      dropoff_address="2 Clinic Rd", scheduled_pickup_datetime=at(15),
                                      billable_miles=Decimal("12.4"), status=TripStatus.COMPLETED)
        self.assertEqual(pricing.total(pricing.price(walk_in)[1]), Decimal("64.80"))


class DiscountAdminTests(DiscountTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def post(self, request, **fields):
        return self.client.post(self.page(request), {"miles": "12.4", **fields})

    def test_the_page_has_the_discount_boxes_in_the_one_form(self):
        request = self.request()
        html = self.client.get(self.page(request)).content.decode()
        button = html.index('name="preview"')
        form = html[html.rindex("<form", 0, button):html.index("</form>", button)]
        self.assertIn('<input type="number" name="discount" step="0.01" min="0" value=""', form)
        self.assertIn('<select name="discount_kind"', form)
        self.assertIn('<option value="$" selected>$</option>', form)
        self.assertIn('<option value="%">%</option>', form)
        self.assertIn('<input type="text" name="discount_reason" maxlength="200" value=""', form)
        self.assertEqual((html.count('name="discount"'), html.count('name="discount_reason"')), (1, 1))
        # Opening the page never starts with a discount, even when the last price sent had one.
        self.sent(request, discount=percent("10"))
        again = self.client.get(self.page(request))
        self.assertContains(again, 'name="discount" step="0.01" min="0" value=""')
        self.assertNotContains(again, "data-quote-discount>")
        self.assertContains(again, "Total: $64.80")

    def test_work_out_the_price_shows_the_lines_then_the_discount_then_the_total(self):
        request = self.request()
        page = self.post(request, discount="10", discount_kind="%", discount_reason=REASON, preview="1")
        self.assertContains(page, "Total: $58.32")
        self.assertContains(page, 'name="checked_amount" value="58.32"')
        self.assertContains(page, "Email $58.32 to jamie@example.com")
        html = page.content.decode()
        order = [html.index(part) for part in ("Miles beyond 5.00 included", "Price before the discount", "$64.80",
                                                "Discount (10%)", "-$6.48", "data-quote-total", "$58.32</td>")]
        self.assertEqual(order, sorted(order))
        self.assertContains(page, f"Reason (not shown to the customer): {REASON}")
        # The boxes keep what was typed.
        self.assertContains(page, 'name="discount" step="0.01" min="0" value="10"')
        self.assertContains(page, '<option value="%" selected>%</option>')
        self.assertContains(page, f'name="discount_reason" maxlength="200" value="{REASON}"')
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))

        page = self.post(request, discount="20", discount_kind="$", discount_reason=REASON, preview="1")
        self.assertContains(page, "Total: $44.80")
        self.assertContains(page, "-$20.00")
        self.assertContains(page, ">Discount</td>")  # a dollar discount shows no percentage

    def test_the_checked_price_is_emailed_with_its_discount(self):
        request = self.request()
        sent = self.post(request, discount="10", discount_kind="%", discount_reason=REASON, checked_amount="58.32",
                         miles_checked="1", customer_pays="1", send="1")
        self.assertRedirects(sent, f"/admin/transportation/triprequest/{request.pk}/change/",
                             fetch_redirect_response=False)
        quote = Quote.objects.get()
        self.assertEqual((quote.amount, quote.lines[-1]["kind"], quote.lines[-1]["percent"], quote.lines[-1]["reason"]),
                         (Decimal("58.32"), "discount", "10", REASON))
        self.assertIn("Discount (10%): -$6.48", mail.outbox[0].body)
        self.assertEqual(AuditLog.objects.get(action="quote_sent").after["discount"]["amount"], "6.48")
        # The request page and the list of prices show the lower amount, nothing else new.
        self.assertContains(self.client.get(f"/admin/transportation/triprequest/{request.pk}/change/"),
                            "$58.32 to jamie@example.com")

    def test_a_discount_changed_without_working_the_price_out_again_is_shown_not_sent(self):
        request = self.request()
        # The price checked had 10% off ($58.32). The discount box now says 20%, and Email is pressed.
        response = self.post(request, discount="20", discount_kind="%", discount_reason=REASON,
                             checked_amount="58.32", miles_checked="1", customer_pays="1", send="1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not the one you checked: the miles, the wait, the discount or the rate card")
        self.assertContains(response, "Total: $51.84")
        self.assertContains(response, 'name="checked_amount" value="51.84"')
        self.assertNotContains(response, "checked>")  # both boxes unticked again
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        # The same when the discount is taken away, or added to a price checked without one.
        for fields, checked, total in (({}, "58.32", "64.80"),
                                       ({"discount": "5", "discount_kind": "$", "discount_reason": REASON},
                                        "64.80", "59.80")):
            response = self.post(request, checked_amount=checked, miles_checked="1", customer_pays="1", send="1",
                                 **fields)
            self.assertContains(response, "not the one you checked")
            self.assertContains(response, f"Total: ${total}")
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="quote_sent").exists())

        # Checked again at 20%, it goes out.
        sent = self.post(request, discount="20", discount_kind="%", discount_reason=REASON,
                         checked_amount="51.84", miles_checked="1", customer_pays="1", send="1")
        self.assertEqual(sent.status_code, 302)
        self.assertEqual(Quote.objects.get().amount, Decimal("51.84"))

    def test_a_discount_that_can_not_be_given_is_explained_and_nothing_is_priced_or_sent(self):
        request = self.request()
        send = {"miles_checked": "1", "customer_pays": "1", "send": "1", "checked_amount": "64.80"}
        refused = (
            ({"discount": "0", "discount_reason": REASON}, "The discount must be greater than 0."),
            ({"discount": "-3", "discount_reason": REASON}, "The discount must be greater than 0."),
            ({"discount": "100.01", "discount_kind": "%", "discount_reason": REASON},
             "A percentage discount can be at most 100."),
            ({"discount": "10.125", "discount_kind": "%", "discount_reason": REASON},
             "A percentage discount can have at most 2 decimal places."),
            ({"discount": "10.001", "discount_kind": "$", "discount_reason": REASON},
             "A dollar discount can have at most 2 decimal places."),
            ({"discount": "64.81", "discount_reason": REASON},
             "The discount ($64.81) is more than the price before the discount ($64.80)."),
            ({"discount": "5"}, "Give the reason for the discount."),
            ({"discount": "5", "discount_reason": "   "}, "Give the reason for the discount."),
            ({"discount": "5", "discount_reason": "x" * 201}, "The reason can be at most 200 characters."),
            ({"discount": "five", "discount_reason": REASON}, "Discount: Enter a number."),
            ({"discount": "5", "discount_kind": "euros", "discount_reason": REASON}, "Discount: choose $ or %."),
        )
        for fields, why in refused:
            for pressed in ({"preview": "1"}, send):
                response = self.post(request, **fields, **pressed)
                self.assertEqual(response.status_code, 200, fields)
                self.assertContains(response, why)
                # No price is shown, so there is no Email button to press.
                self.assertNotContains(response, "data-quote-price")
                self.assertNotContains(response, 'name="send"')
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="quote_sent").exists())

    def test_a_reason_typed_without_a_discount_is_ignored(self):
        request = self.request()
        page = self.post(request, discount="", discount_reason=REASON, preview="1")
        self.assertContains(page, "Total: $64.80")
        self.assertNotContains(page, "data-quote-discount>")
        sent = self.post(request, discount_reason=REASON, checked_amount="64.80", miles_checked="1",
                         customer_pays="1", send="1")
        self.assertEqual(sent.status_code, 302)
        quote = Quote.objects.get()
        self.assertEqual((quote.amount, pricing.discount_entry(quote.lines)), (Decimal("64.80"), None))
        self.assertNotIn(REASON, repr(quote.lines) + repr(AuditLog.objects.get(action="quote_sent").after))

    def test_the_price_record_shows_the_discount_and_its_reason_to_staff(self):
        quote = self.sent(self.request(), discount=percent("10"))
        detail = self.client.get(f"/admin/billing/quote/{quote.pk}/change/")
        self.assertContains(detail, "Wheelchair trip: 1 × $50.00")
        self.assertContains(detail, "Discount (10%): <strong>-$6.48</strong>")
        self.assertContains(detail, f"Reason: {REASON}")
        self.assertNotContains(detail, "$-6.48")
        other = self.sent(self.request(), discount=dollars("20"))
        detail = self.client.get(f"/admin/billing/quote/{other.pk}/change/")
        self.assertContains(detail, "Discount: <strong>-$20.00</strong>")
        # The list shows the lower amount and nothing else new.
        listing = self.client.get("/admin/billing/quote/")
        self.assertContains(listing, "58.32")
        self.assertNotContains(listing, REASON)

    def test_an_invoice_shows_its_share_of_the_discount(self):
        request = self.booking()
        self.sent(request, discount=dollars("20"))
        self.ride(request)
        (invoice,), _ = self.invoiced()
        page = self.client.get(f"/admin/billing/invoice/{invoice.pk}/change/")
        self.assertContains(page, "Discount: <strong>-$20.00</strong>")
        self.assertNotContains(page, REASON)

    def test_dispatchers_can_not_discount_because_they_can_not_send_prices(self):
        self.client.logout()
        self.login("Dispatcher")
        request = self.request()
        response = self.post(request, discount="10", discount_kind="%", discount_reason=REASON, checked_amount="58.32",
                             miles_checked="1", customer_pays="1", send="1")
        self.assertEqual(response.status_code, 403)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (0, 0))


class EmailRowTests(DiscountTestCase):
    """The owner's corrections to the price email (2026-11-26)."""

    MILES_ROW = re.compile(r"^Miles[^:]*: (?:[\d.]+ miles, first [\d.]+ included: )?([\d.]+) x \$([\d,.]+)$")

    def rows(self, request, miles, wait_minutes=0, discount=None):
        card, lines, _ = pricing.quote(request, miles=Decimal(miles), wait_minutes=wait_minutes, discount=discount)
        rows = quotes.customer_rows(request, card, lines, wait_minutes, Decimal(miles))
        self.assertEqual(sum(amount_of(value) for _, value in rows), pricing.total(lines), (miles, rows))
        # No miles row is ever printed whose numbers don't multiply out.
        for label, value in rows:
            if label.startswith("Miles"):
                found = self.MILES_ROW.match(label)
                self.assertIsNotNone(found, label)
                self.assertEqual(pricing.total([pricing.Line("", Decimal(found[1]), amount_of(found[2]))]),
                                 amount_of(value), label)
        return rows

    def test_a_round_trip_has_one_miles_row_for_both_ways(self):
        self.fees_once()
        request = self.request(return_ride="scheduled", return_datetime=at(13))
        self.assertEqual(self.rows(request, "21.3"), [
            ("Booking fee", "$70.00"), ("Loading fee", "$55.00"), ("Miles, round trip: 42.6 x $5.00", "$213.00")])
        _, _, text, html = self.email(request, miles="21.3")
        self.assertIn("Miles, round trip: 42.6 x $5.00 = $213.00\n", text)
        self.assertIn("Miles, round trip: 42.6 × $5.00", html)
        for body in (text, html):
            self.assertNotIn("Miles there", body)
            self.assertNotIn("Miles back", body)
        # What is saved, and what the office reads on the price page, still has a line for each ride.
        self.assertEqual([(line["kind"], line["leg"]) for line in Quote.objects.get().lines],
                         [("base", ""), ("loading", ""), ("miles", "there"), ("miles", "back")])
        # A one-way price keeps its row.
        self.assertEqual(self.rows(self.request(), "21.3")[-1], ("Miles: 21.3 x $5.00", "$106.50"))

    def test_the_one_row_multiplies_out_for_odd_miles_or_each_ride_keeps_its_own(self):
        self.fees_once()
        request = self.request(return_ride="will_call")
        # 7.35 miles at $5.00 is $36.75 each way, and 14.7 x $5.00 is exactly $73.50: one row.
        self.assertIn(("Miles, round trip: 14.7 x $5.00", "$73.50"), self.rows(request, "7.35"))
        # At $2.50 each way is $18.375, charged $18.38: twice that is $36.76, but 14.7 x $2.50 is $36.75.
        RateCard.objects.update(per_mile_wheelchair=Decimal("2.50"))
        rows = self.rows(request, "7.35")
        self.assertEqual(rows[2:], [("Miles there: 7.35 x $2.50", "$18.38"), ("Miles back: 7.35 x $2.50", "$18.38")])
        # Whatever the miles, the rows add up and every miles row multiplies out (checked in `rows`).
        for miles in ("0.01", "0.33", "1.15", "3.33", "7.35", "9.99", "12.4", "21.3", "33.33", "101.07"):
            for rate in ("0.35", "1.15", "2.00", "2.50", "3.33"):
                RateCard.objects.update(per_mile_wheelchair=Decimal(rate))
                self.rows(request, miles)

    def test_two_prices_per_mile_keep_two_rows(self):
        lines = [pricing.Line("Ride there: miles", Decimal("10"), Decimal("3.00"), kind="miles", leg="there"),
                 pricing.Line("Ride back: miles", Decimal("10"), Decimal("3.25"), kind="miles", leg="back")]
        self.assertEqual(quotes.customer_rows(self.request(return_ride="will_call"), self.card, lines, 0),
                         [("Miles there: 10 x $3.00", "$30.00"), ("Miles back: 10 x $3.25", "$32.50")])

    def test_included_miles_do_not_hide_the_distance(self):
        # The default card includes 5 miles and charges the fees on each ride.
        self.assertEqual(self.rows(self.request(), "21.3"), [
            ("Booking fee", "$50.00"), ("Miles: 21.3 miles, first 5 included: 16.3 x $2.00", "$32.60")])
        round_trip = self.request(return_ride="scheduled", return_datetime=at(13))
        self.assertEqual(self.rows(round_trip, "21.3"), [
            ("Booking fee, ride there", "$50.00"), ("Booking fee, ride back", "$50.00"),
            ("Miles, round trip: 42.6 miles, first 10 included: 32.6 x $2.00", "$65.20")])
        _, _, text, html = self.email(round_trip, miles="21.3")
        self.assertIn("Miles, round trip: 42.6 miles, first 10 included: 32.6 x $2.00 = $65.20\n", text)
        self.assertIn("Miles, round trip: 42.6 miles, first 10 included: 32.6 × $2.00", html)
        # Inside the included miles there is no miles row at all.
        self.assertEqual(self.rows(self.request(), "4"), [("Booking fee", "$50.00")])
        # Without the distance to hand, the row says only what is charged: never numbers that don't fit.
        card, lines, _ = pricing.quote(self.request(), miles=Decimal("21.3"))
        self.assertEqual(quotes.customer_rows(self.request(), card, lines, 0)[-1], ("Miles: 16.3 x $2.00", "$32.60"))
        self.assertEqual(quotes.customer_rows(self.request(), card, lines, 0, Decimal("25"))[-1],
                         ("Miles: 16.3 x $2.00", "$32.60"))

    def test_fees_on_each_ride_keep_two_rows_and_fees_charged_once_have_one(self):
        RateCard.objects.update(loading_wheelchair=Decimal("20.00"))
        request = self.request(return_ride="will_call")
        labels = [label for label, _ in self.rows(request, "12.4")]
        self.assertEqual(labels[:4], ["Booking fee, ride there", "Loading fee, ride there",
                                      "Booking fee, ride back", "Loading fee, ride back"])
        self.fees_once()
        labels = [label for label, _ in self.rows(request, "12.4")]
        self.assertEqual(labels[:2], ["Booking fee", "Loading fee"])
        self.assertEqual(sum(1 for label in labels if "fee" in label), 2)

    def test_covers_comes_straight_after_the_reference(self):
        for ride_back, covers in (("none", "One-way ride"), ("will_call", "Round trip (there and back)"),
                                  ("wait", "Round trip, with the driver waiting for you")):
            request = self.request(return_ride=ride_back)
            _, _, text, html = self.email(request)
            reference = f"#{str(request.pk)[:8].upper()}"
            self.assertIn(f"Reference: {reference}\nCovers: {covers}\n\nBooking fee", text, ride_back)
            self.assertEqual(text.count("Covers:"), 1)
            self.assertEqual(html.count("Covers"), 1)
            order = [html.index(part) for part in (reference, "Covers", covers, "Booking fee", "Total price")]
            self.assertEqual(order, sorted(order), ride_back)

    def test_the_ride_back_paragraph_says_each_thing_once(self):
        note = quotes.no_wait_note(self.request(return_ride="scheduled", return_datetime=at(13)))
        self.assertEqual(note, "On this booking the driver does not wait for you: the ride back is a separate trip. "
                               "If you would rather have the driver wait and bring you straight back, "
                               "tell us and we will send you a new price.")
        note = quotes.no_wait_note(self.request(return_ride="will_call"))
        self.assertEqual(note, "On this booking the driver does not wait for you: the ride back is a separate trip. "
                               "When you call for the ride back, there can be a wait if the driver is finishing "
                               "another ride. If you would rather have the driver wait and bring you straight back, "
                               "tell us and we will send you a new price.")
        for request in (self.request(), self.request(return_ride="wait"),
                        self.request(service_type="equipment_delivery", return_ride="will_call")):
            self.assertEqual(quotes.no_wait_note(request), "")
        # It no longer repeats what "How your price works" says, and still promises nothing.
        _, _, text, _ = self.email(self.request(return_ride="will_call"))
        self.assertEqual(text.count("two rides"), 1)
        for gone in ("second trip", "leaves after dropping you off", "cheaper", "save", "guarantee", "same driver"):
            self.assertNotIn(gone, text)

    def test_a_second_press_sends_nothing_and_says_so_to_its_caller(self):
        request = self.request()
        first = self.sent(request)
        self.assertIs(first.already_sent, False)
        again = self.sent(request)
        self.assertEqual(again.pk, first.pk)
        self.assertIs(again.already_sent, True)
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (1, 1))
        # A different price is a new email, and a failed one is not "already sent".
        self.assertIs(self.sent(request, miles="20").already_sent, False)
        self.assertEqual(len(mail.outbox), 2)

    def test_a_second_press_of_the_email_button_shows_no_second_success_message(self):
        self.login()
        request = self.request()
        send = {"miles": "12.4", "checked_amount": "64.80", "miles_checked": "1", "customer_pays": "1", "send": "1"}
        first = self.client.post(self.page(request), send, follow=True)
        self.assertEqual([(m.level, m.message) for m in first.context["messages"]],
                         [(message_levels.SUCCESS, "$64.80 emailed to jamie@example.com.")])
        second = self.client.post(self.page(request), send, follow=True)
        self.assertEqual([(m.level, m.message) for m in second.context["messages"]], [(
            message_levels.INFO,
            "This price was already emailed to jamie@example.com a moment ago. It was not sent again.")])
        self.assertEqual((Quote.objects.count(), len(mail.outbox)), (1, 1))
        self.assertEqual(AuditLog.objects.filter(action="quote_sent").count(), 1)
