"""
Import drivers (with their credential dates) and vehicles from a filled-in
spreadsheet template (ADR 0045). Rows are checked first and shown for
review. Nothing is saved until the person confirms, and then only the rows
with no problems. A driver already on file (same license number) or a
vehicle already on file (same VIN) is skipped, never changed.
"""

import csv
import io
import re
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.services import record_change
from intake.extract import ExtractError, read_file
from intake.normalize import parse_date
from vehicles.models import Vehicle

from .models import CredentialType, Driver, DriverCredential, EmploymentType

DRIVERS, VEHICLES = "drivers", "vehicles"

DRIVER_COLUMNS = [
    ("legal_name", "Legal name"), ("phone", "Phone"), ("email", "Email"),
    ("employment_type", "Employment type"), ("license_number", "License number"),
    ("license_expiration_date", "License expires"),
] + [
    column for kind, label in CredentialType.choices
    for column in ((f"{kind}_done", f"{label} done"), (f"{kind}_expires", f"{label} expires"))
] + [("notes", "Notes")]

VEHICLE_COLUMNS = [
    ("vin", "VIN"), ("year", "Year"), ("make", "Make"), ("model", "Model"), ("license_plate", "License plate"),
    ("wheelchair_capable", "Wheelchair accessible"), ("passenger_capacity", "Passenger seats"),
    ("registration_expiration_date", "Registration expires"), ("inspection_expiration_date", "Inspection expires"),
    ("insurance_expiration_date", "Insurance expires"), ("notes", "Notes"),
]
COLUMNS = {DRIVERS: DRIVER_COLUMNS, VEHICLES: VEHICLE_COLUMNS}
YES, NO = {"yes", "y", "true", "1", "x"}, {"no", "n", "false", "0", ""}


@dataclass
class Row:
    number: int
    label: str
    record: object = None
    credentials: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    skipped: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems and not self.skipped


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def template_csv(kind: str) -> str:
    out = io.StringIO()
    csv.writer(out).writerow([label for _, label in COLUMNS[kind]])
    return out.getvalue()


def _values(kind: str, headers: list[str], raw: dict) -> dict:
    by_key = {_key(h): h for h in headers}
    values = {}
    for name, label in COLUMNS[kind]:
        header = by_key.get(_key(label)) or by_key.get(_key(name))
        values[name] = str(raw.get(header, "") if header else "").strip()
    return values


def _date(values: dict, name: str, label: str, problems: list, *, required: bool):
    text = values.get(name, "")
    if not text:
        if required:
            problems.append(f"{label}: missing.")
        return None
    day = parse_date(text)
    if day is None:
        problems.append(f"{label}: '{text}' isn't a date we can read (use 2027-03-31 or 3/31/2027).")
    return day


def _messages(exc: ValidationError) -> list[str]:
    if hasattr(exc, "message_dict"):
        return [f"{name.replace('_', ' ')}: {' '.join(msgs)}" for name, msgs in exc.message_dict.items()]
    return exc.messages


def _driver_row(number: int, values: dict, seen: set) -> Row:
    row = Row(number, values["legal_name"] or f"Row {number}")
    problems = row.problems
    employment = values["employment_type"].lower()
    if employment in ("1099", "contract", "independent contractor"):
        employment = EmploymentType.CONTRACTOR
    if employment not in EmploymentType.values:
        problems.append("Employment type: write employee or contractor.")
    driver = Driver(
        legal_name=values["legal_name"], phone=values["phone"], email=values["email"], employment_type=employment,
        license_number=values["license_number"], notes=values["notes"],
        license_expiration_date=_date(values, "license_expiration_date", "License expires", problems, required=True),
    )
    try:
        driver.full_clean(exclude=["license_expiration_date", "employment_type"])
    except ValidationError as exc:
        problems.extend(_messages(exc))
    number_key = values["license_number"].lower()
    if number_key and (number_key in seen or Driver.objects.filter(license_number__iexact=number_key).exists()):
        row.skipped = f"License {values['license_number']} is already on file: skipped, not changed."
    seen.add(number_key)
    for kind, label in CredentialType.choices:
        done = _date(values, f"{kind}_done", f"{label} done", problems, required=False)
        expires = _date(values, f"{kind}_expires", f"{label} expires", problems, required=False)
        if expires and not done:
            problems.append(f"{label}: an expiry date needs the date it was done.")
        elif done:
            if expires and expires < done:
                problems.append(f"{label}: expires before it was done.")
            row.credentials.append(DriverCredential(credential_type=kind, completed_on=done, expires_on=expires))
    row.record = driver
    return row


def _vehicle_row(number: int, values: dict, seen: set) -> Row:
    row = Row(number, " ".join(p for p in (values["year"], values["make"], values["model"]) if p) or f"Row {number}")
    problems = row.problems
    wheelchair = values["wheelchair_capable"].lower()
    if wheelchair not in YES | NO:
        problems.append("Wheelchair accessible: write yes or no.")
    vehicle = Vehicle(
        vin=values["vin"].upper(), make=values["make"], model=values["model"],
        license_plate=values["license_plate"].upper(), wheelchair_capable=wheelchair in YES, notes=values["notes"],
        year=int(values["year"]) if values["year"].isdigit() else None,
        passenger_capacity=int(values["passenger_capacity"]) if values["passenger_capacity"].isdigit() else None,
        registration_expiration_date=_date(values, "registration_expiration_date", "Registration expires", problems,
                                           required=True),
        inspection_expiration_date=_date(values, "inspection_expiration_date", "Inspection expires", problems,
                                         required=True),
        insurance_expiration_date=_date(values, "insurance_expiration_date", "Insurance expires", problems,
                                        required=False),
    )
    try:
        vehicle.full_clean(exclude=["registration_expiration_date", "inspection_expiration_date", "vin"])
    except ValidationError as exc:
        problems.extend(_messages(exc))
    if not vehicle.vin:
        problems.append("VIN: missing.")
    elif len(vehicle.vin) != 17:
        problems.append(f"VIN: '{vehicle.vin}' isn't 17 characters.")
    elif vehicle.vin in seen or Vehicle.objects.filter(vin__iexact=vehicle.vin).exists():
        row.skipped = f"VIN {vehicle.vin} is already on file: skipped, not changed."
    seen.add(vehicle.vin)
    row.record = vehicle
    return row


def check(kind: str, content: bytes, name: str) -> list[Row]:
    """Every row of the file, checked. Raises ExtractError for an unreadable file or the wrong template."""
    headers, raws = read_file(content, name)
    first = COLUMNS[kind][0]
    if not any(_key(h) in (_key(first[0]), _key(first[1])) for h in headers):
        raise ExtractError(f"No “{first[1]}” column: use the {kind} template.")
    build, seen = (_driver_row if kind == DRIVERS else _vehicle_row), set()
    return [build(number, _values(kind, headers, raw), seen) for number, raw in enumerate(raws, start=2)]


@transaction.atomic
def create(kind: str, rows: list[Row], *, user) -> list:
    """Save the rows with no problems; returns the records made."""
    made = []
    for row in rows:
        if not row.ok:
            continue
        record = row.record
        record.save()
        for credential in row.credentials:
            credential.driver = record
            credential.save()
        record_change(actor=user, action="created", resource_type=type(record).__name__, resource_id=record.pk,
                      after={"via": "spreadsheet_import", "row": row.number,
                             "credentials": [c.credential_type for c in row.credentials]}, source="admin")
        made.append(record)
    return made
