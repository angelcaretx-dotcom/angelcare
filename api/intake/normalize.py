"""
From one row's mapped values to trip details, or to the reasons it
needs a person (ADR 0038). Nothing is guessed: a missing time, a bad
ZIP, a past date or an unknown service sends the row to review.

Problems are "hard" (the data is wrong or unusable: fix it in review or
reject the row) or "soft" (it looks fine but a person should confirm,
e.g. a rider we don't know yet).

All dates and times in a file are Texas (Central) time.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from passengers.models import Passenger
from transportation.addresses import compose, validate_parts
from transportation.models import ReturnRide
from trips.models import Trip

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%b %d %Y", "%B %d %Y",
                "%d-%b-%Y", "%d-%b-%y", "%Y/%m/%d"]
TIME_FORMATS = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p", "%I %p", "%I%p", "%H%M"]
SERVICE_CODES = {
    "ambulatory": "ambulatory", "amb": "ambulatory", "a": "ambulatory", "sedan": "ambulatory",
    "car": "ambulatory", "ambulant": "ambulatory",
    "wheelchair": "wheelchair", "wheel chair": "wheelchair", "wc": "wheelchair", "w/c": "wheelchair",
    "w": "wheelchair", "wch": "wheelchair", "wcv": "wheelchair", "wheelchair van": "wheelchair",
}
STRETCHER_CODES = {"stretcher", "str", "s", "gurney", "stretcher van"}
YES = {"y", "yes", "true", "1", "x", "will call", "willcall"}
ONE_LINE_ADDRESS = re.compile(
    r"^(?P<street>[^,]+?),\s*(?:(?P<unit>(?:apt|apartment|unit|suite|ste|#|bldg|room|rm)\.?\s*[^,]*),\s*)?"
    r"(?P<city>[^,]+?),?\s+(?P<state>[A-Za-z]{2})\.?\s+(?P<zip>[0-9-]{3,10})$",
    re.IGNORECASE,
)


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def problem(message: str, *, hard: bool = True) -> dict:
    return {"message": message, "hard": hard}


def parse_date(text: str):
    text = re.sub(r"\s+", " ", text.replace(",", " ")).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(text: str):
    text = re.sub(r"\s+", " ", text.strip().upper().replace(".", ""))
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _date_and_time(date_text: str, time_text: str):
    """A date column that may also carry the time ('2027-01-15 10:00', '1/15/2027 10:00 AM')."""
    day, at = parse_date(date_text), parse_time(time_text) if time_text else None
    if day is None and date_text:
        for cut in range(len(date_text.split(" ")) - 1, 0, -1):
            parts = date_text.split(" ")
            day = parse_date(" ".join(parts[:cut]))
            if day is not None:
                at = at or parse_time(" ".join(parts[cut:]))
                break
    return day, at


def split_address(text: str) -> dict:
    match = ONE_LINE_ADDRESS.match(re.sub(r"\s+", " ", text.strip()))
    if not match:
        return {"street": text.strip(), "unit": "", "city": "", "state": "", "zip": ""}
    return {k: (match.group(k) or "").strip() for k in ("street", "unit", "city", "state", "zip")}


def _address(values: dict, side: str, label: str, problems: list) -> str:
    parts = {part: values.get(f"{side}_{part}", "") for part in ("street", "unit", "city", "state", "zip")}
    if not any(parts.values()) and values.get(f"{side}_address"):
        parts = split_address(values[f"{side}_address"])
    if not any(parts.values()):
        problems.append(problem(f"No {label} address."))
        return ""
    cleaned, errors = validate_parts(parts)
    for messages in errors.values():
        message = messages[0].rstrip(".")
        problems.append(problem(f"{label.capitalize()} address: {message[0].lower()}{message[1:]}."))
    return compose(**{k if k != "zip" else "zip_code": v for k, v in cleaned.items()})


def _name(values: dict) -> str:
    name = values.get("rider_name", "").strip()
    if not name:
        name = " ".join(p for p in (values.get("rider_first", ""), values.get("rider_last", "")) if p).strip()
    if name.count(",") == 1:  # "LEE, MORGAN" -> "MORGAN LEE"
        last, first = (p.strip() for p in name.split(","))
        name = f"{first} {last}".strip()
    if name.isupper():
        name = name.title()
    return re.sub(r"\s+", " ", name)


def phone_digits(text: str) -> str:
    digits = re.sub(r"\D", "", text or "")
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def normalize(values: dict) -> tuple[dict, list]:
    """Our fields as text -> (trip details, problems). Details are JSON-safe."""
    problems = []
    data = {"trip_id": values.get("trip_id", ""), "prior_auth": values.get("prior_auth", "")}

    data["rider_name"] = _name(values)
    if not data["rider_name"]:
        problems.append(problem("No rider name."))
    digits = phone_digits(values.get("rider_phone", ""))
    data["rider_phone"] = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else ""
    if not digits:
        problems.append(problem("No rider phone number."))
    elif len(digits) != 10:
        problems.append(problem(f"Rider phone '{values.get('rider_phone')}' isn't a 10-digit number."))

    day, at = _date_and_time(values.get("pickup_date", ""), values.get("pickup_time", ""))
    pickup_at = None
    if day is None:
        problems.append(problem("No pickup date, or one we can't read."))
    elif at is None:
        problems.append(problem("No pickup time, or one we can't read."))
    else:
        pickup_at = datetime.combine(day, at, tzinfo=_zone())
        if pickup_at < timezone.now():
            problems.append(problem(f"Pickup {pickup_at:%b %d, %Y %I:%M %p} is in the past."))
    data["pickup_at"] = pickup_at.isoformat() if pickup_at else ""

    data["appointment_at"] = ""
    if values.get("appointment_time") and day:
        appointment = parse_time(values["appointment_time"])
        if appointment is None:
            problems.append(problem(f"Appointment time '{values['appointment_time']}' can't be read."))
        else:
            data["appointment_at"] = datetime.combine(day, appointment, tzinfo=_zone()).isoformat()

    data["pickup_address"] = _address(values, "pickup", "pickup", problems)
    data["dropoff_address"] = _address(values, "dropoff", "drop-off", problems)
    if data["pickup_address"] and data["pickup_address"].casefold() == data["dropoff_address"].casefold():
        problems.append(problem("Pickup and drop-off are the same address."))

    code = re.sub(r"\s+", " ", values.get("service_type", "").strip().lower())
    data["service_type"] = SERVICE_CODES.get(code, "")
    if not code:
        problems.append(problem("No service type (ambulatory or wheelchair)."))
    elif code in STRETCHER_CODES:
        problems.append(problem("Stretcher rows aren't created by import: a person decides."))
    elif not data["service_type"]:
        problems.append(problem(f"Service '{values.get('service_type')}' isn't ambulatory or wheelchair."))

    data["return_ride"], data["return_at"] = ReturnRide.NONE, ""
    return_text = values.get("return_time", "").strip()
    if values.get("will_call", "").strip().lower() in YES or "will" in return_text.lower():
        data["return_ride"] = ReturnRide.WILL_CALL
    elif return_text:
        back = parse_time(return_text)
        if back is None or day is None:
            problems.append(problem(f"Return time '{return_text}' can't be read."))
        else:
            return_at = datetime.combine(day, back, tzinfo=_zone())
            if pickup_at and return_at <= pickup_at:
                problems.append(problem("The return pickup isn't after the pickup."))
            data["return_ride"], data["return_at"] = ReturnRide.SCHEDULED, return_at.isoformat()

    notes = []
    for field, label in (("mobility", "Mobility"), ("oxygen", "Oxygen"), ("escort", "Escort"),
                         ("instructions", "Instructions")):
        if values.get(field, "").strip():
            notes.append(f"{label}: {values[field].strip()}")
    data["notes"] = "\n".join(notes)
    return data, problems


def find_duplicate(batch, data: dict, earlier_ids: set) -> str:
    """Why this row is already on the schedule (or earlier in the file), or ''."""
    if data["trip_id"]:
        if data["trip_id"] in earlier_ids:
            return "Same trip ID as an earlier row in this file."
        if Trip.objects.filter(source=batch.source, broker_trip_number=data["trip_id"]).exists():
            return f"Trip {data['trip_id']} is already on the schedule."
        return ""
    if data["pickup_at"] and data["rider_phone"]:
        same = Trip.objects.filter(scheduled_pickup_datetime=datetime.fromisoformat(data["pickup_at"]))
        for trip in same.select_related("passenger"):
            if trip.passenger and phone_digits(trip.passenger.phone) == phone_digits(data["rider_phone"]) \
                    and trip.passenger.legal_name.casefold() == data["rider_name"].casefold():
                return "This rider already has a trip at that time."
    return ""


def match_passenger(data: dict) -> tuple[Passenger | None, dict | None]:
    """(the passenger to use, or None to create one on approval; a soft problem, if any)."""
    digits = phone_digits(data["rider_phone"])
    if len(digits) != 10:
        return None, None
    same_phone = [p for p in Passenger.objects.filter(phone__contains=digits[-4:]) if phone_digits(p.phone) == digits]
    for passenger in same_phone:
        if passenger.legal_name.casefold() == data["rider_name"].casefold():
            return passenger, None
    if same_phone:
        names = ", ".join(p.legal_name for p in same_phone)
        return None, problem(
            f"That phone belongs to {names}, but the name is {data['rider_name']}: approving creates a new passenger.",
            hard=False,
        )
    return None, problem(f"New rider: approving creates a passenger record for {data['rider_name']}.", hard=False)


def assess(row, earlier_ids: set):
    """Re-check a row from its values: details, problems, duplicate, passenger, status."""
    from .models import RowStatus

    data, problems = normalize(row.values)
    duplicate = find_duplicate(row.batch, data, earlier_ids)
    if data["trip_id"]:
        earlier_ids.add(data["trip_id"])
    row.passenger, soft = (None, None) if duplicate else match_passenger(data)
    if soft:
        problems.append(soft)
    row.problems = problems
    row.data = data
    if duplicate:
        row.problems = [problem(duplicate)]
        row.status = RowStatus.DUPLICATE
    else:
        row.status = RowStatus.REVIEW if problems else RowStatus.READY
    return row
