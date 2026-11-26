"""Rate cards, calculated invoices and driver pay (ADR 0039)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.test import TestCase

from accounts.otp_test_utils import login_with_otp
from audit.models import AuditLog
from payers.models import Payer
from trips.models import Trip, TripEvent, TripLeg, TripStatus
from trips.tests import make_driver, make_passenger
from vehicles.models import VehicleCheck

from . import pricing
from .models import DriverPayPlan, Invoice, InvoiceStatus, RateCard

CENTRAL = ZoneInfo("America/Chicago")
DAY = date(2027, 1, 13)  # a Wednesday


def at(hour, minute=0, day=DAY):
    return datetime.combine(day, time(hour, minute), tzinfo=CENTRAL)


class RatesTestCase(TestCase):
    def setUp(self):
        self.mtm = Payer.objects.create(name="MTM", payer_type="broker")
        self.passenger = make_passenger()
        self.card = RateCard.objects.create(
            name="MTM 2027", payer=self.mtm, effective_from=date(2027, 1, 1),
            base_ambulatory=Decimal("25.00"), base_wheelchair=Decimal("45.00"),
            per_mile=Decimal("2.50"), included_miles=Decimal("5"), no_show_fee=Decimal("15.00"),
            after_hours_fee=Decimal("10.00"), after_hours_start=time(18, 0), after_hours_end=time(6, 0),
        )

    def trip(self, *, pickup=None, status=TripStatus.COMPLETED, payer="mtm", **extra):
        defaults = {
            "passenger": self.passenger, "source": "mtm", "payer": self.mtm if payer == "mtm" else payer,
            "service_type": "wheelchair", "pickup_address": "1 Main St", "dropoff_address": "2 Clinic Rd",
            "scheduled_pickup_datetime": pickup or at(9), "status": status,
        }
        defaults.update(extra)
        return Trip.objects.create(**defaults)


class PriceTests(RatesTestCase):
    def test_base_plus_miles_beyond_the_included_ones(self):
        card, lines = pricing.price(self.trip(billable_miles=Decimal("12.4")))
        self.assertEqual(card, self.card)
        self.assertEqual([(l.label, l.amount) for l in lines],
                         [("Wheelchair trip", Decimal("45.00")), ("Miles beyond 5.00 included", Decimal("18.50"))])
        self.assertEqual(pricing.total(lines), Decimal("63.50"))

    def test_per_mile_card_needs_the_trips_miles(self):
        with self.assertRaisesMessage(pricing.PricingError, "billable miles"):
            pricing.price(self.trip())

    def test_after_hours_in_texas_time_including_overnight_windows(self):
        for hour, expected in ((5, True), (6, False), (17, False), (18, True), (23, True)):
            trip = self.trip(pickup=at(hour), billable_miles=Decimal("1"))
            labels = [l.label for l in pricing.price(trip)[1]]
            self.assertEqual("After-hours" in labels, expected, hour)

    def test_weekends_can_count_as_after_hours(self):
        self.card.weekends_after_hours = True
        self.card.save()
        saturday = self.trip(pickup=at(11, day=date(2027, 1, 16)), billable_miles=Decimal("1"))
        self.assertIn("After-hours", [l.label for l in pricing.price(saturday)[1]])

    def test_no_show_fee_only(self):
        _, lines = pricing.price(self.trip(status=TripStatus.NO_SHOW))
        self.assertEqual(pricing.total(lines), Decimal("15.00"))

    def test_newest_card_for_the_trip_date_and_private_pay(self):
        newer = RateCard.objects.create(name="MTM mid-2027", payer=self.mtm, effective_from=date(2027, 7, 1),
                                        base_wheelchair=Decimal("50.00"))
        self.assertEqual(pricing.card_for(self.trip()), self.card)
        self.assertEqual(pricing.card_for(self.trip(pickup=at(9, day=date(2027, 7, 2)))), newer)
        with self.assertRaisesMessage(pricing.PricingError, "No rate card for private pay"):
            pricing.card_for(self.trip(payer=None))
        private = RateCard.objects.create(name="Private pay", effective_from=date(2026, 1, 1),
                                          base_wheelchair=Decimal("60.00"))
        self.assertEqual(pricing.card_for(self.trip(payer=None)), private)

    def test_wait_time_fifteen_minutes_free_then_a_dollar_a_minute(self):
        private = RateCard.objects.create(name="Private pay", effective_from=date(2026, 1, 1),
                                          base_wheelchair=Decimal("60.00"), wait_per_minute=Decimal("1.00"))
        outbound = self.trip(payer=None, leg=TripLeg.OUTBOUND)
        back = self.trip(payer=None, leg=TripLeg.RETURN, outbound_trip=outbound, driver_waits=True, pickup=at(10))
        TripEvent.objects.create(trip=outbound, event_type="dropped_off", occurred_at=at(9, 40))
        with self.assertRaisesMessage(pricing.PricingError, "can't be measured"):
            pricing.price(back)
        TripEvent.objects.create(trip=back, event_type="picked_up", occurred_at=at(10, 20))
        card, lines = pricing.price(back)
        self.assertEqual(card, private)
        self.assertEqual(lines[-1].label, "Wait time (40 min, first 15 free)")
        self.assertEqual(lines[-1].amount, Decimal("25.00"))

    def test_a_return_leg_entered_on_its_own_pays_the_booking_and_loading_fees(self):
        # "Fees once per round trip" is about a ride back linked to its ride there (ADR 0049).
        RateCard.objects.filter(pk=self.card.pk).update(fees_on_return=False, loading_wheelchair=Decimal("20.00"))
        miles = "Miles beyond 5.00 included"
        outbound = self.trip(leg=TripLeg.OUTBOUND, billable_miles=Decimal("12.4"))
        linked = self.trip(leg=TripLeg.RETURN, outbound_trip=outbound, pickup=at(11), billable_miles=Decimal("12.4"))
        alone = self.trip(leg=TripLeg.RETURN, pickup=at(12), billable_miles=Decimal("12.4"))
        self.assertEqual([l.label for l in pricing.price(outbound)[1]], ["Wheelchair trip", "Loading", miles])
        self.assertEqual([l.label for l in pricing.price(linked)[1]], [miles])
        self.assertEqual([(l.label, l.kind, l.leg) for l in pricing.price(alone)[1]],
                         [("Wheelchair trip", "base", "back"), ("Loading", "loading", "back"), (miles, "miles", "back")])
        # A card that charges the fees on each ride is unchanged: the linked ride back pays them too.
        RateCard.objects.filter(pk=self.card.pk).update(fees_on_return=True)
        self.assertEqual([l.label for l in pricing.price(Trip.objects.get(pk=linked.pk))[1]],
                         ["Wheelchair trip", "Loading", miles])

    def test_a_ride_back_that_was_never_quoted_still_pays_after_hours(self):
        # ADR 0049 drops the fee only where an emailed price promised a ride back without it.
        # A broker's or a contract payer's will-call and wait-and-return rides back keep it.
        contract = Payer.objects.create(name="Example Rehab contract", payer_type="facility_contract")
        RateCard.objects.create(
            name="Example Rehab 2027", payer=contract, effective_from=date(2027, 1, 1),
            base_wheelchair=Decimal("40.00"), after_hours_fee=Decimal("12.00"),
            after_hours_start=time(18, 0), after_hours_end=time(6, 0),
        )
        for payer in (self.mtm, contract):
            for choice in ("will_call", "driver_waits"):
                outbound = self.trip(payer=payer, leg=TripLeg.OUTBOUND, pickup=at(17), billable_miles=Decimal("1"))
                back = self.trip(payer=payer, leg=TripLeg.RETURN, outbound_trip=outbound, pickup=at(19),
                                 billable_miles=Decimal("1"), **{choice: True})
                self.assertIsNone(pricing.quoted_card(back))
                self.assertEqual([l.label for l in pricing.price(back)[1]], ["Wheelchair trip", "After-hours"],
                                 (payer.name, choice))

    def test_a_standing_orders_will_call_ride_back_at_night_still_pays_after_hours(self):
        from transportation.models import ReturnRide
        from trips import standing
        from trips.test_standing import TOMORROW, make_order

        # Private pay, like the riders who are emailed a price, but this ride never had one.
        RateCard.objects.create(
            name="Private pay", effective_from=date(2020, 1, 1), base_wheelchair=Decimal("60.00"),
            after_hours_fee=Decimal("10.00"), after_hours_start=time(18, 0), after_hours_end=time(6, 0),
        )
        order = make_order(return_ride=ReturnRide.WILL_CALL, weekdays=[TOMORROW.weekday()], pickup_time=time(17, 0))
        back = next(t for t in standing.generate(order) if t.leg == TripLeg.RETURN)
        back.status = TripStatus.COMPLETED
        self.assertEqual((back.will_call, back.payer_id, pricing.local(back.scheduled_pickup_datetime).hour),
                         (True, None, 19))
        self.assertIsNone(pricing.quoted_card(back))
        self.assertEqual([l.label for l in pricing.price(back)[1]], ["Wheelchair trip", "After-hours"])

    def test_service_without_a_rate_is_not_billed(self):
        with self.assertRaisesMessage(pricing.PricingError, "has no rate for medical supply"):
            pricing.price(self.trip(service_type="equipment_delivery"))


class InvoiceBatchTests(RatesTestCase):
    def test_drafts_with_their_calculation_and_a_reason_for_each_skip(self):
        good = self.trip(billable_miles=Decimal("3"))
        no_miles = self.trip(pickup=at(11))
        self.trip(pickup=at(12), status=TripStatus.CANCELLED)
        made, skipped = pricing.create_invoices(self.mtm, DAY, DAY, user=None)
        self.assertEqual([i.trip for i in made], [good])
        invoice = made[0]
        self.assertEqual((invoice.amount, invoice.status, invoice.rate_card), (Decimal("45.00"), InvoiceStatus.DRAFT, self.card))
        self.assertEqual(invoice.lines[0]["label"], "Wheelchair trip")
        self.assertEqual([(t, "billable miles" in why) for t, why in skipped], [(no_miles, True)])

    def test_running_again_only_bills_what_is_left(self):
        self.trip(billable_miles=Decimal("3"))
        pricing.create_invoices(self.mtm, DAY, DAY, user=None)
        self.assertEqual(pricing.create_invoices(self.mtm, DAY, DAY, user=None), ([], []))
        Invoice.objects.update(status=InvoiceStatus.VOID)
        self.assertEqual(len(pricing.create_invoices(self.mtm, DAY, DAY, user=None)[0]), 1)

    def test_a_rate_change_does_not_rewrite_past_invoices(self):
        self.trip(billable_miles=Decimal("3"))
        invoice = pricing.create_invoices(self.mtm, DAY, DAY, user=None)[0][0]
        self.card.base_wheelchair = Decimal("99.00")
        self.card.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount, Decimal("45.00"))


class DriverPayTests(RatesTestCase):
    def setUp(self):
        super().setUp()
        self.driver = make_driver()

    def test_every_kind_of_pay_adds_up(self):
        DriverPayPlan.objects.create(
            driver=self.driver, effective_from=date(2027, 1, 1), per_trip=Decimal("10"),
            per_trip_wheelchair=Decimal("14"), per_mile=Decimal("0.50"), percent_of_charge=Decimal("20"),
            hourly=Decimal("12"),
        )
        trip = self.trip(driver=self.driver, billable_miles=Decimal("10"))  # charge 45 + 5 mi × 2.50 = 57.50
        check = VehicleCheck.objects.create(vehicle=self._vehicle(), driver=self.driver, service_date=DAY,
                                            start_odometer=100, checked_at=at(7), end_odometer=180, ended_at=at(15, 30))
        result = pricing.statement(self.driver, DAY, DAY)
        trip_row = result["rows"][0]
        self.assertEqual(trip_row["trip"], trip)
        self.assertEqual([l.amount for l in trip_row["lines"]], [Decimal("14.00"), Decimal("5.00"), Decimal("11.50")])
        self.assertEqual(result["hours"][0]["line"].amount, Decimal("102.00"))  # 8.5 h × $12
        self.assertEqual(result["total"], Decimal("132.50"))
        self.assertEqual(result["hours"][0]["check"], check)

    def test_no_plan_or_missing_numbers_are_flagged_not_guessed(self):
        self.trip(driver=self.driver)
        self.assertIn("no pay plan", pricing.statement(self.driver, DAY, DAY)["rows"][0]["notes"][0])
        DriverPayPlan.objects.create(driver=self.driver, effective_from=date(2027, 1, 1), per_mile=Decimal("1"))
        self.assertIn("no billable miles", pricing.statement(self.driver, DAY, DAY)["rows"][0]["notes"][0])

    def _vehicle(self):
        from trips.tests import make_vehicle

        return make_vehicle()


class AdminTests(RatesTestCase):
    def login(self, group):
        user = User.objects.create_user(username=group.lower(), password="testpass123", is_staff=True)
        user.groups.add(Group.objects.get(name=group))
        login_with_otp(self.client, username=group.lower(), password="testpass123")

    def test_administrator_batch_invoices_from_the_page(self):
        self.login("Administrator")
        self.trip(billable_miles=Decimal("3"))
        response = self.client.post("/admin/billing/invoice/create-batch/",
                                    {"payer": str(self.mtm.pk), "start": DAY.isoformat(), "end": DAY.isoformat()})
        self.assertContains(response, "1 created")
        invoice = Invoice.objects.get()
        self.assertContains(self.client.get(f"/admin/billing/invoice/{invoice.pk}/change/"), "Wheelchair trip: 1 × $45.00")

    def test_rate_card_changes_are_audited_and_dispatchers_cannot_see_rates(self):
        self.login("Administrator")
        self.client.post(f"/admin/billing/ratecard/{self.card.pk}/change/", {
            "name": "MTM 2027", "payer": str(self.mtm.pk), "effective_from": "2027-01-01", "effective_to": "",
            "base_ambulatory": "26.00", "base_wheelchair": "45.00", "base_equipment_delivery": "",
            "per_mile": "2.50", "included_miles": "5", "wait_free_minutes": "15", "wait_per_minute": "0",
            "wait_block_minutes": "1", "fees_on_return": "on",
            "no_show_fee": "15.00", "after_hours_fee": "10.00", "after_hours_start": "18:00", "after_hours_end": "06:00",
            "notes": "",
        })
        self.card.refresh_from_db()
        self.assertEqual(self.card.base_ambulatory, Decimal("26.00"))
        self.assertTrue(AuditLog.objects.filter(resource_type="RateCard", action="changed").exists())
        self.client.logout()
        self.login("Dispatcher")
        self.assertEqual(self.client.get("/admin/billing/ratecard/").status_code, 403)
        self.assertEqual(self.client.get("/admin/billing/driverpayplan/statement/").status_code, 403)

    def test_pay_statement_page_and_csv(self):
        self.login("Administrator")
        driver = make_driver()
        DriverPayPlan.objects.create(driver=driver, effective_from=date(2027, 1, 1), per_trip=Decimal("12"))
        self.trip(driver=driver)
        query = {"driver": str(driver.pk), "start": DAY.isoformat(), "end": DAY.isoformat()}
        self.assertContains(self.client.get("/admin/billing/driverpayplan/statement/", query), "Total $12.00")
        csv = self.client.get("/admin/billing/driverpayplan/statement/", dict(query, format="csv"))
        self.assertEqual(csv["Content-Type"], "text/csv")
        self.assertIn(b"Total,,,12.00", csv.content)

    def test_a_blank_start_date_is_a_form_error_not_a_crash(self):
        self.login("Administrator")
        response = self.client.post("/admin/billing/ratecard/add/", {
            "name": "No start date", "payer": "", "effective_from": "", "effective_to": "2027-12-31",
            "per_mile": "0", "included_miles": "0", "wait_free_minutes": "15", "wait_block_minutes": "1",
            "wait_per_minute": "0", "no_show_fee": "0", "after_hours_fee": "0", "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertFalse(RateCard.objects.filter(name="No start date").exists())
        with self.assertRaises(Exception) as caught:
            DriverPayPlan(driver=make_driver(), effective_to=date(2027, 12, 31)).full_clean()
        self.assertNotIsInstance(caught.exception, TypeError)

    def test_one_card_per_payer_per_start_date(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            RateCard(name="dup", payer=self.mtm, effective_from=date(2027, 1, 1)).full_clean()
