"""
Invoices for a ride that was paid online (ADR 0050): the paid price is
final, so the invoices repeat that price's own saved lines, and only
waiting beyond what was paid for is billed after the ride.

No test holds a real key or reaches the network (`PaymentTestMixin`, via
`FlowTestCase`). Every name, address, rate and amount is made up.
"""

from datetime import date
from decimal import Decimal

from django.db import connection

from payers.models import Payer
from trips.models import Trip, TripEvent, TripLeg, TripStatus
from trips.returns import build_return_leg

from . import checkout, pricing
from .models import Invoice, InvoiceStatus, PaymentStatus, Quote, RateCard
from .test_discounts import DiscountTestCase, dollars, percent
from .test_quotes import at


class PaidRideTestCase(DiscountTestCase):
    def setUp(self):
        super().setUp()
        self.payments_on()

    def paid_booking(self, ride_back="none", *, miles="10", wait_minutes=0, discount=None, **extra):
        """A booking whose emailed price the customer paid online: (request, quote, payment)."""
        request = self.booking(ride_back, **extra)
        quote = self.sent(request, miles=miles, wait_minutes=wait_minutes, discount=discount)
        payment = self.paid(quote)
        self.assertEqual(payment.paid_cents, int(quote.amount * 100))
        return request, quote, payment

    @staticmethod
    def shown(line: dict) -> tuple:
        return tuple(line[name] for name in ("label", "quantity", "unit", "amount", "kind", "leg"))

    def note(self, request, payment) -> str:
        return (f"Paid online ${payment.paid:,.2f} on {pricing.local(payment.paid_at):%b %d, %Y} "
                f"(reference #{str(request.pk)[:8].upper()}).")


class PaidInvoiceTests(PaidRideTestCase):
    def test_the_invoices_of_a_paid_booking_total_exactly_the_amount_paid(self):
        for fees_once in (False, True):
            for ride_back in ("none", "scheduled", "will_call", "wait"):
                case = (fees_once, ride_back)
                self.clear_rides()
                if fees_once:
                    self.fees_once()
                wait = 40 if ride_back == "wait" else 0
                request, quote, payment = self.paid_booking(ride_back, wait_minutes=wait)
                trips = self.ride(request, waited=wait if ride_back == "wait" else None)

                made, skipped = self.invoiced()
                self.assertEqual(skipped, [], case)
                self.assertEqual(len(made), len(trips), case)
                self.assertEqual(sum(invoice.amount for invoice in made), payment.paid, case)
                self.assertEqual(sum(invoice.amount for invoice in made), quote.amount, case)
                # Every line of the paid price is on exactly one invoice, as it was paid.
                self.assertEqual(sorted(self.shown(line) for invoice in made for line in invoice.lines),
                                 sorted(self.shown(line) for line in quote.lines), case)
                # Still Drafts: nothing is marked Paid by itself.
                self.assertEqual({invoice.status for invoice in made}, {InvoiceStatus.DRAFT}, case)
                self.assertEqual({invoice.paid_date for invoice in made}, {None}, case)
                self.assertEqual({invoice.rate_card for invoice in made}, {self.card}, case)
                # The payment is noted once: on the first invoice of the booking.
                self.assertEqual([invoice.notes for invoice in made],
                                 [self.note(request, payment), ""][:len(made)], case)
                # The profit report and percent-of-charge driver pay use the same figures.
                Invoice.objects.all().delete()
                self.assertEqual(sum(pricing.trip_charge(trip) for trip in trips), payment.paid, case)

    def test_each_trip_gets_its_own_part_of_the_paid_price(self):
        self.fees_once()
        request, quote, _ = self.paid_booking("wait", wait_minutes=40)
        self.assertEqual(quote.amount, Decimal("261.00"))  # 70 + 55 + 50 + 50 + 2 blocks x 18
        there, back = self.ride(request, waited=40)
        # The ride there: the lines with no ride named or named "there". The fees are charged once, so all of them.
        self.assertEqual([(line.label, line.amount, line.kind, line.leg) for line in pricing.price(there)[1]],
                         [("Wheelchair trip", Decimal("70.00"), "base", ""), ("Loading", Decimal("55.00"), "loading", ""),
                          ("Ride there: miles", Decimal("50.00"), "miles", "there")])
        # The ride back: the lines named "back", and the wait.
        self.assertEqual([(line.label, line.amount, line.kind, line.leg) for line in pricing.price(back)[1]],
                         [("Ride back: miles", Decimal("50.00"), "miles", "back"),
                          ("Wait time (40 min, first 15 free, 15-minute blocks)", Decimal("36.00"), "wait", "")])

    def test_waiting_longer_than_was_paid_for_adds_exactly_the_extra_blocks(self):
        self.fees_once()
        # Paid: 40 minutes expected, which is 2 blocks and covers up to 45 minutes.
        request, quote, payment = self.paid_booking("wait", wait_minutes=40)
        there, back = self.ride(request, waited=61)
        lines = pricing.price(back)[1]
        # 61 minutes is 4 blocks: 2 more than were paid for, at the $18.00 that was paid per block.
        self.assertEqual([(line.label, line.quantity, line.unit, line.amount) for line in lines][-1],
                         ("Extra waiting time (16 min beyond the 45 paid for)", Decimal(2), Decimal("18.00"),
                          Decimal("36.00")))
        made, skipped = self.invoiced()
        self.assertEqual(skipped, [])
        self.assertEqual(sum(invoice.amount for invoice in made), payment.paid + Decimal("36.00"))
        self.assertEqual(made[0].amount, Decimal("175.00"))  # the ride there is untouched
        self.assertEqual({invoice.status for invoice in made}, {InvoiceStatus.DRAFT})

        # One minute into the next block is one block; the last minute paid for is none.
        for waited, extra in ((45, 0), (46, 1), (60, 1), (76, 3)):
            Invoice.objects.all().delete()
            back.events.all().delete()
            there.events.all().delete()
            TripEvent.objects.create(trip=there, event_type="dropped_off", occurred_at=at(9, 30))
            TripEvent.objects.create(trip=back, event_type="picked_up",
                                     occurred_at=at(9 + (30 + waited) // 60, (30 + waited) % 60))
            charged = sum(pricing.total(pricing.price(trip)[1]) for trip in (there, back))
            self.assertEqual(charged, payment.paid + extra * Decimal("18.00"), waited)

    def test_a_shorter_wait_changes_nothing_and_nothing_is_refunded(self):
        self.fees_once()
        request, quote, payment = self.paid_booking("wait", wait_minutes=40)
        there, back = self.ride(request, waited=5)
        self.assertEqual(sum(pricing.total(pricing.price(trip)[1]) for trip in (there, back)), payment.paid)
        self.assertNotIn("extra_wait", [line.kind for line in pricing.price(back)[1]])
        made, _ = self.invoiced()
        self.assertEqual(sum(invoice.amount for invoice in made), payment.paid)

    def test_a_wait_that_was_not_measured_counts_as_the_wait_that_was_paid_for(self):
        self.fees_once()
        request, quote, payment = self.paid_booking("wait", wait_minutes=40)
        self.ride(request)  # the driver app recorded neither the drop-off nor the pickup
        made, skipped = self.invoiced()
        self.assertEqual(skipped, [])  # not refused, as an unpaid wait-and-return ride would be
        self.assertEqual(sum(invoice.amount for invoice in made), payment.paid)

        # The same ride, never paid online, can't be billed until the wait is measured.
        other = self.booking("wait", requested_datetime=at(11))
        self.sent(other, miles="10", wait_minutes=40)
        unpaid = Trip.objects.create(trip_request=other, scheduled_pickup_datetime=at(11), leg=TripLeg.OUTBOUND)
        back = build_return_leg(unpaid)
        back.save()
        Trip.objects.filter(pk__in=[unpaid.pk, back.pk]).update(status=TripStatus.COMPLETED)
        with self.assertRaisesMessage(pricing.PricingError, "The wait can't be measured"):
            pricing.price(Trip.objects.get(pk=back.pk))

    def test_a_ride_paid_with_no_waiting_in_it_is_billed_the_waiting_the_driver_app_recorded(self):
        # Paid for the free minutes only (no expected wait was typed): 31 minutes is 16 beyond the 15 free.
        request, quote, payment = self.paid_booking("wait", miles="5")
        self.assertEqual(quote.amount, Decimal("100.00"))
        there, back = self.ride(request, waited=31)
        self.assertEqual([(line.label, line.amount) for line in pricing.price(back)[1]],
                         [("Ride back: wheelchair trip", Decimal("50.00")),
                          ("Extra waiting time (16 min beyond the 15 paid for)", Decimal("16.00"))])

    def test_a_rate_card_changed_after_payment_changes_nothing(self):
        request, quote, payment = self.paid_booking("scheduled", miles="12.4")
        self.assertEqual(payment.paid, Decimal("129.60"))
        # The card itself is edited, a dearer card starts before the ride, and the miles on the trips change.
        RateCard.objects.filter(pk=self.card.pk).update(base_wheelchair=Decimal("95.00"), per_mile=Decimal("9.00"),
                                                        included_miles=Decimal("0"), after_hours_fee=Decimal("40.00"))
        RateCard.objects.create(name="Private pay from Jan 10", effective_from=date(2027, 1, 10),
                                base_wheelchair=Decimal("120.00"), per_mile=Decimal("7.00"))
        trips = self.ride(request)
        Trip.objects.update(billable_miles=Decimal("30"))
        trips = list(Trip.objects.order_by("scheduled_pickup_datetime"))
        self.assertEqual([pricing.total(pricing.price(trip)[1]) for trip in trips], [Decimal("64.80")] * 2)
        made, skipped = self.invoiced()
        self.assertEqual(skipped, [])
        self.assertEqual(sum(invoice.amount for invoice in made), payment.paid)
        self.assertEqual({invoice.rate_card_id for invoice in made}, {self.card.pk})

    def test_a_paid_price_with_a_discount_is_invoiced_what_was_paid(self):
        self.fees_once()
        for discount, paid in ((dollars("30"), "195.00"), (percent("10"), "202.50"), (dollars("200"), "25.00")):
            self.clear_rides()
            request, quote, payment = self.paid_booking("scheduled", discount=discount)
            self.assertEqual(payment.paid, Decimal(paid))
            there, back = self.ride(request)
            first, rest = pricing.discount_split(quote.lines)
            # The same shares as for a price that was never paid: one rule, from the saved lines.
            self.assertEqual(pricing.total(pricing.price(there)[1]), Decimal("175.00") - first)
            self.assertEqual(pricing.total(pricing.price(back)[1]), Decimal("50.00") - rest)
            made, _ = self.invoiced()
            self.assertEqual(sum(invoice.amount for invoice in made), payment.paid, discount)
            for invoice in made:
                self.assertGreater(invoice.amount, 0)
                self.assertNotIn("reason", repr(invoice.lines))

    def test_extra_waiting_is_charged_in_full_on_top_of_a_discounted_paid_price(self):
        self.fees_once()
        request, quote, payment = self.paid_booking("wait", wait_minutes=40, discount=dollars("230"))
        self.assertEqual(payment.paid, Decimal("31.00"))
        there, back = self.ride(request, waited=61)
        self.assertEqual(pricing.total(pricing.price(there)[1]), Decimal("0.00"))
        self.assertEqual(pricing.total(pricing.price(back)[1]), Decimal("31.00") + Decimal("36.00"))

    def test_a_no_show_on_a_paid_ride_is_invoiced_the_no_show_fee_and_notes_the_payment(self):
        RateCard.objects.update(no_show_fee=Decimal("25.00"))
        request, quote, payment = self.paid_booking()
        (trip,) = self.ride(request, status=TripStatus.NO_SHOW)
        self.assertEqual([(line.label, line.amount) for line in pricing.price(trip)[1]],
                         [("No-show fee", Decimal("25.00"))])
        (invoice,), _ = self.invoiced()
        self.assertEqual((invoice.amount, invoice.status), (Decimal("25.00"), InvoiceStatus.DRAFT))
        self.assertEqual(invoice.notes,
                         self.note(request, payment) + " This ride was a no-show: settle the payment by hand.")

    def test_the_payment_is_noted_on_the_first_invoice_only_whichever_is_made_first(self):
        request, quote, payment = self.paid_booking("scheduled")
        there, back = self.ride(request)
        note = self.note(request, payment)
        self.assertEqual((pricing.prepayment_note(there), pricing.prepayment_note(back)), (note, note))
        # The ride back is invoiced first (the office works a day at a time): it carries the note.
        first = Invoice.objects.create(trip=back, amount=Decimal("1.00"), notes=pricing.prepayment_note(back))
        self.assertEqual(pricing.prepayment_note(there), "")
        # A voided invoice no longer counts.
        first.status = InvoiceStatus.VOID
        first.save()
        self.assertEqual(pricing.prepayment_note(there), note)
        # Money refunded since is said too.
        self.assertTrue(checkout.record_refund(payment.payment_intent_id, 1000))
        self.assertEqual(pricing.prepayment_note(there), note + " $10.00 of it has been refunded.")

    def test_a_ride_that_was_not_paid_online_has_no_note_and_is_priced_as_before(self):
        request = self.booking("scheduled")
        quote = self.sent(request, miles="10")
        self.opened(quote)  # the customer opened the payment page and did not pay
        trips = self.ride(request)
        self.assertEqual([pricing.prepayment(trip) for trip in trips], [None, None])
        made, _ = self.invoiced()
        self.assertEqual([invoice.notes for invoice in made], ["", ""])
        self.assertEqual([line["label"] for line in made[0].lines], ["Wheelchair trip", "Miles beyond 5.00 included"])

    def test_a_payment_refunded_in_full_or_needing_attention_is_not_a_paid_ride(self):
        request, quote, payment = self.paid_booking()
        (trip,) = self.ride(request)
        self.assertEqual(pricing.prepayment(trip).pk, payment.pk)
        self.assertTrue(checkout.record_refund(payment.payment_intent_id, payment.paid_cents))
        self.assertIsNone(pricing.prepayment(trip))
        self.assertEqual(pricing.prepayment_note(trip), "")
        # Money that arrived for a cancelled request needs attention: it is not a paid price either.
        type(payment).objects.filter(pk=payment.pk).update(status=PaymentStatus.NEEDS_ATTENTION, refunded_cents=0)
        self.assertIsNone(pricing.prepayment(trip))

    def test_a_ride_back_added_by_hand_to_a_paid_one_way_price_is_charged_as_before(self):
        request, quote, payment = self.paid_booking(miles="5")
        (there,) = self.ride(request)
        back = Trip.objects.create(
            passenger=there.passenger, service_type="wheelchair", pickup_address="2 Clinic Rd",
            dropoff_address="1 Main St", scheduled_pickup_datetime=at(13), leg=TripLeg.RETURN, outbound_trip=there,
            billable_miles=Decimal("5"), status=TripStatus.COMPLETED,
        )
        # It was never in the paid price, so it is not free: it pays the card's booking fee.
        self.assertEqual(pricing.total(pricing.price(there)[1]), payment.paid)
        self.assertEqual([(line.label, line.amount) for line in pricing.price(back)[1]],
                         [("Wheelchair trip", Decimal("50.00"))])

    def test_a_paid_price_saved_without_kinds_falls_back_to_todays_calculation(self):
        request, quote, payment = self.paid_booking("scheduled", miles="12.4")
        Quote.objects.filter(pk=quote.pk).update(
            lines=[{"label": line["label"], "quantity": line["quantity"], "unit": line["unit"],
                    "amount": line["amount"]} for line in quote.lines])
        trips = self.ride(request)
        self.assertEqual([[line.label for line in pricing.price(trip)[1]] for trip in trips],
                         [["Wheelchair trip", "Miles beyond 5.00 included"]] * 2)
        made, _ = self.invoiced()
        self.assertEqual(sum(invoice.amount for invoice in made), payment.paid)
        self.assertEqual(made[0].notes, self.note(request, payment))

    def test_a_trip_billed_to_a_medicaid_or_broker_payer_is_never_priced_from_a_paid_price(self):
        request, quote, payment = self.paid_booking()
        (trip,) = self.ride(request)
        broker = Payer.objects.create(name="MTM", payer_type="broker")
        broker_card = RateCard.objects.create(name="MTM 2027", payer=broker, effective_from=date(2027, 1, 1),
                                              base_wheelchair=Decimal("40.00"))
        Trip.objects.update(payer=broker)
        trip = Trip.objects.get(pk=trip.pk)
        card, lines = pricing.price(trip)
        self.assertEqual((card, [line.label for line in lines]), (broker_card, ["Wheelchair trip"]))
        # The office still sees that the customer paid: the invoice says so.
        self.assertEqual(pricing.prepayment_note(trip), self.note(request, payment))

    def test_invoices_are_made_as_before_while_the_payment_tables_are_missing(self):
        self.fees_once()
        request = self.booking("scheduled")
        quote = self.sent(request, miles="10", discount=dollars("30"))
        self.ride(request)
        with connection.cursor() as cursor:  # undone with the test's transaction
            cursor.execute("ALTER TABLE billing_payment RENAME TO billing_payment_gone")
        with self.assertLogs("billing.payments", "WARNING"):
            made, skipped = self.invoiced()
        self.assertEqual(skipped, [])
        self.assertEqual(sum(invoice.amount for invoice in made), quote.amount)
        self.assertEqual([invoice.notes for invoice in made], ["", ""])
