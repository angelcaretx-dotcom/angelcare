"""The mileage estimate from two ZIP codes, worked out inside the system (ADR 0049)."""

import re
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from . import distance
from .models import TripRequest


def make_request(**extra) -> TripRequest:
    """An unsaved request: the estimate reads its fields and nothing else."""
    defaults = {
        "full_name": "Jamie Chen", "phone": "817-555-0100", "email": "jamie@example.com",
        "pickup_address": "1 Main St, Fort Worth, TX", "dropoff_address": "2 Clinic Rd, Dallas, TX",
        "requested_datetime": datetime(2027, 1, 13, 9, 0, tzinfo=ZoneInfo("America/Chicago")),
        "service_type": "wheelchair",
    }
    defaults.update(extra)
    return TripRequest(**defaults)


class ZipTableTests(SimpleTestCase):
    def test_the_table_is_texas_zips_with_coordinates_inside_texas(self):
        table = distance._table()
        self.assertGreater(len(table), 1900)
        for zip_code, (lat, lon) in table.items():
            self.assertRegex(zip_code, r"^(7[5-9]\d{3}|73301|73344|885\d{2})$")
            self.assertTrue(25.5 < lat < 36.7 and -107 < lon < -93.3, zip_code)

    def test_the_file_is_sorted_with_the_documented_header(self):
        lines = distance.TABLE_PATH.read_text(encoding="ascii").splitlines()
        self.assertEqual(lines[0], "zip,lat,lon")
        zips = [line.split(",")[0] for line in lines[1:]]
        self.assertEqual(zips, sorted(set(zips)))

    def test_the_table_is_read_once(self):
        distance._table.cache_clear()
        request = make_request(pickup_zip="76102", dropoff_zip="75201")
        distance.estimate(request)
        distance.estimate(request)
        self.assertEqual(distance._table.cache_info().misses, 1)

    def test_a_missing_or_unreadable_file_means_no_estimate_not_a_crash(self):
        request = make_request(pickup_zip="76102", dropoff_zip="75201")
        self.addCleanup(distance._table.cache_clear)
        for broken in (Path(__file__).parent / "data" / "not-there.csv", Path(__file__)):  # absent; not a table
            distance._table.cache_clear()
            with patch.object(distance, "TABLE_PATH", broken):
                result = distance.estimate(request)
            self.assertIsNone(result.miles)
            self.assertIn("table could not be read", result.reason)

    def test_a_damaged_table_means_no_estimate_not_a_crash_or_a_wrong_figure(self):
        request = make_request(pickup_zip="76102", dropoff_zip="75201")
        self.addCleanup(distance._table.cache_clear)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "table.csv"

        def estimate_from(rows):
            path.write_text("zip,lat,lon\n" + "\n".join(rows) + "\n", encoding="ascii")
            distance._table.cache_clear()
            with patch.object(distance, "TABLE_PATH", path):
                return distance.estimate(request)

        # Two rough points about 30 miles apart: a table like this one is read.
        fort_worth, dallas = "76102,32.75,-97.33", "75201,32.79,-96.80"
        self.assertTrue(Decimal("30") < estimate_from([fort_worth, dallas]).miles < Decimal("50"))
        damaged = {
            "nan, which would give a figure of thousands of miles": ["76102,nan,nan", dallas],
            "inf, which the distance sum cannot take": [fort_worth, "75201,inf,-inf"],
            "a number too large to be one": [fort_worth, "75201,1e999,-96.80"],
            "numbers that are not a latitude and a longitude": [fort_worth, "75201,3279,-9680"],
            "a field longer than the csv reader accepts": [fort_worth, dallas, "79999," + "1" * 140_000 + ",-96.80"],
        }
        for what, rows in damaged.items():
            result = estimate_from(rows)
            self.assertIsNone(result.miles, what)
            self.assertIn("table could not be read", result.reason, what)


class ZipForTests(SimpleTestCase):
    def test_the_stored_part_is_used_and_a_zip_plus_four_is_cut_to_five_digits(self):
        request = make_request(pickup_zip="76104", dropoff_zip="75201-1234",
                               pickup_address="1 Main St, Fort Worth, TX 76102")
        self.assertEqual(distance.zip_for(request, "pickup"), "76104")  # the part wins over the line
        self.assertEqual(distance.zip_for(request, "dropoff"), "75201")

    def test_an_older_request_falls_back_to_the_last_zip_in_the_one_line_address(self):
        request = make_request(pickup_address="76010 Main St, Fort Worth, TX 76104",
                               dropoff_address="2 Clinic Rd, Dallas, TX 75201-1234, USA")
        self.assertEqual(distance.zip_for(request, "pickup"), "76104")
        self.assertEqual(distance.zip_for(request, "dropoff"), "75201")

    def test_an_address_with_no_zip_has_none_and_a_house_number_is_not_one(self):
        request = make_request(pickup_address="1 Main St, Fort Worth, TX",
                               dropoff_address="12345 Clinic Rd, Dallas, TX")
        self.assertEqual(distance.zip_for(request, "pickup"), "")
        self.assertEqual(distance.zip_for(request, "dropoff"), "")
        # Longer numbers (a phone number typed into the address) are not ZIPs either.
        self.assertEqual(distance.zip_for(make_request(pickup_address="1 Main St, call 8175550100"), "pickup"), "")

    def test_only_pickup_and_dropoff_are_sides(self):
        with self.assertRaises(ValueError):
            distance.zip_for(make_request(), "return")


class EstimateTests(SimpleTestCase):
    def test_a_known_pair_gives_a_plausible_figure_to_one_decimal_place(self):
        # Downtown Fort Worth to downtown Dallas: about 31 miles in a straight line, about 33 by road.
        result = distance.estimate(make_request(pickup_zip="76102", dropoff_zip="75201"))
        self.assertEqual((result.pickup_zip, result.dropoff_zip, result.reason), ("76102", "75201", ""))
        self.assertIsInstance(result.miles, Decimal)
        self.assertEqual(result.miles.as_tuple().exponent, -1)
        self.assertTrue(Decimal("36") < result.miles < Decimal("44"), result.miles)
        self.assertEqual(result.round_trip_miles, result.miles * 2)
        straight = result.miles / distance.ROAD_FACTOR
        self.assertTrue(Decimal("29") < straight < Decimal("33"), straight)
        # The same route reversed is the same distance.
        back = distance.estimate(make_request(pickup_zip="75201", dropoff_zip="76102"))
        self.assertEqual(back.miles, result.miles)

    def test_the_road_factor_is_the_documented_constant(self):
        self.assertEqual(distance.ROAD_FACTOR, Decimal("1.3"))

    def test_the_estimate_works_from_one_line_addresses_alone(self):
        result = distance.estimate(make_request(pickup_address="1 Main St, Fort Worth, TX 76102-4321",
                                                dropoff_address="2 Clinic Rd, Dallas, TX 75201"))
        self.assertEqual(result.miles, distance.estimate(make_request(pickup_zip="76102", dropoff_zip="75201")).miles)

    def test_the_same_zip_at_both_ends_has_no_estimate(self):
        result = distance.estimate(make_request(pickup_zip="76104", dropoff_zip="76104-0001"))
        self.assertIsNone(result.miles)
        self.assertIsNone(result.round_trip_miles)
        self.assertIn("Both addresses are in ZIP code 76104", result.reason)

    def test_a_zip_that_is_not_in_the_table_has_no_estimate(self):
        result = distance.estimate(make_request(pickup_zip="76104", dropoff_zip="10001"))
        self.assertIsNone(result.miles)
        self.assertIn("10001 is not in the Texas ZIP code table", result.reason)

    def test_a_missing_zip_has_no_estimate_and_says_which_end(self):
        result = distance.estimate(make_request(pickup_zip="76104"))
        self.assertIsNone(result.miles)
        self.assertEqual(result.reason, "The drop-off address has no ZIP code.")
        result = distance.estimate(make_request(dropoff_zip="76104"))
        self.assertEqual(result.reason, "The pickup address has no ZIP code.")


class MapLinkTests(SimpleTestCase):
    def test_the_link_is_google_maps_driving_directions_with_both_addresses_encoded(self):
        request = make_request(pickup_address="1 Main St, Apt #4, Fort Worth, TX 76104",
                               dropoff_address="2 Clinic Rd & Elm,\nDallas, TX 75201")
        url = distance.map_url(request)
        self.assertTrue(url.startswith("https://www.google.com/maps/dir/?api=1&origin="))
        self.assertNotRegex(url, r"[\s#]")
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "www.google.com", "/maps/dir/"))
        self.assertEqual(parse_qs(parts.query), {
            "api": ["1"], "travelmode": ["driving"],
            "origin": ["1 Main St, Apt #4, Fort Worth, TX 76104"],
            "destination": ["2 Clinic Rd & Elm, Dallas, TX 75201"],
        })


class NoNetworkTests(SimpleTestCase):
    def test_the_module_imports_nothing_that_can_call_out(self):
        """Decision of 2026-11-26: no address is sent to a map company by the system."""
        source = Path(distance.__file__).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", source, flags=re.MULTILINE)
        self.assertEqual(sorted(set(imports)),
                         ["csv", "dataclasses", "decimal", "functools", "math", "pathlib", "re", "urllib.parse"])
        # The exact list above already rules out `requests`; these may not appear anywhere, even in a function.
        for banned in ("import requests", "urllib.request", "http.client", "urllib3", "httpx", "socket", "urlopen",
                       "__import__", "importlib"):
            self.assertNotIn(banned, source)
