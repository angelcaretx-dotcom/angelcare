"""
One-way miles estimated inside the system from the two ZIP codes (ADR
0049). No address is ever sent to a map company: the table below is a
public Census file kept in this repository, and nothing here makes a
network call.

The figure is the straight-line distance between the two ZIP areas'
centre points, made a little longer because roads are not straight. It
is an estimate for staff to confirm on a map before a price is sent,
never the driving distance. `map_url` is the link they click to do that,
in their own browser.
"""

import csv
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlencode

# Texas ZIP Code Tabulation Areas and their centre points (see data/README.md).
TABLE_PATH = Path(__file__).parent / "data" / "tx_zip_centroids.csv"
# Roads are longer than straight lines. This is an estimate that staff confirm.
ROAD_FACTOR = Decimal("1.3")
EARTH_RADIUS_MILES = 3958.8
TENTH = Decimal("0.1")
SIDES = {"pickup": "pickup", "dropoff": "drop-off"}
# A 5-digit ZIP, with or without the +4, that is not part of a longer number.
ZIP_IN_TEXT = re.compile(r"(?<![\d-])(\d{5})(?:-\d{4})?(?![\d-])")


@dataclass(frozen=True)
class Estimate:
    """`miles` is one way, to one decimal place; None with a `reason` when there is no estimate."""

    miles: Decimal | None
    pickup_zip: str
    dropoff_zip: str
    reason: str = ""

    @property
    def round_trip_miles(self) -> Decimal | None:
        return None if self.miles is None else self.miles * 2


@lru_cache(maxsize=1)
def _table() -> dict[str, tuple[float, float]]:
    """
    ZIP -> (latitude, longitude), read once per process. A missing,
    unreadable or damaged file is an empty table: no estimate, never an
    exception and never a figure worked out from a bad row.
    """
    try:
        with TABLE_PATH.open(newline="", encoding="ascii") as handle:
            table = {row["zip"]: (float(row["lat"]), float(row["lon"])) for row in csv.DictReader(handle)}
    except (OSError, ValueError, KeyError, TypeError, csv.Error):
        return {}
    # "nan", "inf" and 3278 all parse as numbers, but none is a latitude or a longitude.
    # ("nan" fails every comparison, so it is refused here too.)
    if not all(-90 <= lat <= 90 and -180 <= lon <= 180 for lat, lon in table.values()):
        return {}
    return table


def zip_for(trip_request, side: str) -> str:
    """
    The 5-digit ZIP of the pickup or the drop-off, or "". The stored part
    when the request has one; otherwise the last ZIP in the one-line
    address (older requests have no parts).
    """
    if side not in SIDES:
        raise ValueError(f"side must be 'pickup' or 'dropoff', not {side!r}")
    stored = re.match(r"\d{5}", (getattr(trip_request, f"{side}_zip", "") or "").strip())
    if stored:
        return stored.group()
    address = (getattr(trip_request, f"{side}_address", "") or "").strip()
    found = list(ZIP_IN_TEXT.finditer(address))
    if not found:
        return ""
    last = found[-1]
    if last.start() == 0 and address[last.end():].strip(" ,"):
        # "12345 Main St, Fort Worth, TX": a house number, not a ZIP.
        return ""
    return last.group(1)


def _great_circle_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = (math.radians(value) for value in (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(h)))


def estimate(trip_request) -> Estimate:
    """About how many miles one way, from the two ZIP codes; or why that can't be said."""
    pickup, dropoff = zip_for(trip_request, "pickup"), zip_for(trip_request, "dropoff")

    def none(reason: str) -> Estimate:
        return Estimate(None, pickup, dropoff, reason)

    for side, found in (("pickup", pickup), ("dropoff", dropoff)):
        if not found:
            return none(f"The {SIDES[side]} address has no ZIP code.")
    table = _table()
    if not table:
        return none("The ZIP code table could not be read.")
    for found in (pickup, dropoff):
        if found not in table:
            return none(f"ZIP code {found} is not in the Texas ZIP code table.")
    if pickup == dropoff:
        return none(f"Both addresses are in ZIP code {pickup}, so ZIP codes cannot tell how far apart they are.")
    straight = Decimal(repr(_great_circle_miles(table[pickup], table[dropoff])))
    miles = (straight * ROAD_FACTOR).quantize(TENTH, rounding=ROUND_HALF_UP)
    if not miles:
        return none(f"ZIP codes {pickup} and {dropoff} are too close together to estimate from.")
    return Estimate(miles, pickup, dropoff)


def map_url(trip_request) -> str:
    """
    Google Maps driving directions between the two addresses. Only ever a
    link staff click in their own browser, like the driver app's
    directions link: the system itself sends the addresses nowhere.
    """
    query = {
        "api": "1",
        "origin": " ".join((trip_request.pickup_address or "").split()),
        "destination": " ".join((trip_request.dropoff_address or "").split()),
        "travelmode": "driving",
    }
    return "https://www.google.com/maps/dir/?" + urlencode(query, quote_via=quote)
