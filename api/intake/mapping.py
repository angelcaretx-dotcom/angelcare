"""
Which of the sender's columns is which of our fields (ADR 0038). A
first file from a new sender gets a best guess from common column
names, which staff check and save as a MappingTemplate; that sender's
next files then map without anyone touching it.
"""

import re

from .models import MappingTemplate

# Our fields, in the order the mapping screen shows them.
FIELDS = [
    ("trip_id", "Sender's trip ID"),
    ("prior_auth", "Prior authorization number"),
    ("rider_name", "Rider name"),
    ("rider_first", "Rider first name"),
    ("rider_last", "Rider last name"),
    ("rider_phone", "Rider phone"),
    ("pickup_date", "Pickup date (or date and time)"),
    ("pickup_time", "Pickup time"),
    ("appointment_time", "Appointment time"),
    ("pickup_address", "Pickup address (one line)"),
    ("pickup_street", "Pickup street"),
    ("pickup_unit", "Pickup apt / suite"),
    ("pickup_city", "Pickup city"),
    ("pickup_state", "Pickup state"),
    ("pickup_zip", "Pickup ZIP"),
    ("dropoff_address", "Drop-off address (one line)"),
    ("dropoff_street", "Drop-off street"),
    ("dropoff_unit", "Drop-off apt / suite"),
    ("dropoff_city", "Drop-off city"),
    ("dropoff_state", "Drop-off state"),
    ("dropoff_zip", "Drop-off ZIP"),
    ("service_type", "Service / level of service"),
    ("return_time", "Return pickup time (or 'will call')"),
    ("will_call", "Will-call return (yes/no)"),
    ("mobility", "Mobility aids"),
    ("oxygen", "Oxygen"),
    ("escort", "Escort / attendant"),
    ("instructions", "Instructions / notes"),
]
FIELD_NAMES = [name for name, _ in FIELDS]

# Column names senders commonly use, normalized (lower case, letters and digits only).
SYNONYMS = {
    "trip_id": ["tripid", "tripnumber", "tripno", "trip", "ridenumber", "rideid", "orderid", "ordernumber",
                "orderno", "confirmationnumber", "confirmation", "referencenumber", "reference", "ref",
                "bookingid", "legid", "tripnum"],
    "prior_auth": ["priorauth", "priorauthorization", "priorauthorizationnumber", "pa", "panumber", "authnumber",
                   "authorization", "authorizationnumber", "auth"],
    "rider_name": ["membername", "ridername", "patientname", "passengername", "clientname", "name", "member",
                   "patient", "passenger", "rider", "client", "fullname"],
    "rider_first": ["firstname", "memberfirstname", "patientfirstname", "patientfirst", "memberfirst",
                    "first", "fname"],
    "rider_last": ["lastname", "memberlastname", "patientlastname", "patientlast", "memberlast", "last",
                   "lname", "surname"],
    "rider_phone": ["phone", "memberphone", "phonenumber", "patientphone", "riderphone", "contactphone", "telephone",
                    "cell", "mobile", "homephone"],
    "pickup_date": ["date", "tripdate", "servicedate", "pickupdate", "appointmentdate", "dateofservice", "dos",
                    "ridedate", "pudate"],
    "pickup_time": ["pickuptime", "putime", "time", "scheduledpickup", "scheduledtime", "pickup",
                    "requestedpickuptime", "readytime"],
    "appointment_time": ["appointmenttime", "appttime", "appointment", "appt", "arrivaltime", "arrivetime"],
    "pickup_address": ["pickupaddress", "puaddress", "fromaddress", "origin", "originaddress", "from",
                       "pickuplocation"],
    "pickup_street": ["pickupstreet", "pustreet", "fromstreet", "pickupaddress1", "pickupaddressline1"],
    "pickup_unit": ["pickupunit", "pickupapt", "pickupsuite", "pickupaddress2", "pickupaddressline2"],
    "pickup_city": ["pickupcity", "pucity", "fromcity", "origincity"],
    "pickup_state": ["pickupstate", "pustate", "fromstate", "originstate"],
    "pickup_zip": ["pickupzip", "puzip", "fromzip", "originzip", "pickupzipcode", "pickuppostalcode"],
    "dropoff_address": ["dropoffaddress", "doaddress", "toaddress", "destination", "destinationaddress", "to",
                        "dropofflocation", "deliveryaddress"],
    "dropoff_street": ["dropoffstreet", "dostreet", "tostreet", "dropoffaddress1", "dropoffaddressline1"],
    "dropoff_unit": ["dropoffunit", "dropoffapt", "dropoffsuite", "dropoffaddress2", "dropoffaddressline2"],
    "dropoff_city": ["dropoffcity", "docity", "tocity", "destinationcity"],
    "dropoff_state": ["dropoffstate", "dostate", "tostate", "destinationstate"],
    "dropoff_zip": ["dropoffzip", "dozip", "tozip", "destinationzip", "dropoffzipcode", "dropoffpostalcode"],
    "service_type": ["servicetype", "levelofservice", "los", "service", "mode", "vehicletype", "transporttype",
                     "triptype", "mobilitytype", "servicelevel"],
    "return_time": ["returntime", "returnpickuptime", "returnpickup", "return", "pickupreturntime"],
    "will_call": ["willcall", "willcallreturn"],
    "mobility": ["mobilityaids", "mobilityaid", "mobility", "assistivedevice", "aids", "equipment"],
    "oxygen": ["oxygen", "o2"],
    "escort": ["escort", "attendant", "escorts", "companion", "additionalpassengers"],
    "instructions": ["instructions", "specialinstructions", "notes", "comments", "drivernotes", "remarks"],
}


def normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def guess(headers: list[str]) -> dict:
    """Our field → the sender's column, for the columns we recognize."""
    by_key = {}
    for header in headers:
        by_key.setdefault(normalize_header(header), header)
    found, used = {}, set()
    for field in FIELD_NAMES:
        for alias in SYNONYMS.get(field, []):
            header = by_key.get(alias)
            if header and header not in used:
                found[field] = header
                used.add(header)
                break
    return found


def find_template(source: str, headers: list[str]) -> MappingTemplate | None:
    """A saved mapping from this sender whose columns are all in this file."""
    present = set(headers)
    for template in MappingTemplate.objects.filter(source=source).order_by("-updated_at"):
        if template.field_map and set(template.field_map.values()) <= present:
            return template
    return None


def apply(field_map: dict, raw: dict) -> dict:
    """The row's values for our fields, as text."""
    return {field: str(raw.get(column, "")).strip() for field, column in field_map.items() if column}
