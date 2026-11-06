"""
X12 files for TMHP (ADR 0040): building the 837P professional claim
(005010X222A1) and reading what comes back: 999 (file accepted?),
277CA (each claim accepted or rejected), 835 (paid or denied, and why).

The layout is the national 5010 standard. Everything TMHP-specific
(submitter and receiver IDs, payer ID, TPI, place of service...) comes
from ClaimSettings, entered from TMHP's own companion guide and
enrollment papers: none of it is written into the code.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

SEGMENT, ELEMENT, COMPONENT, REPETITION = "~", "*", ":", "^"
ALLOWED = re.compile(r"[^A-Z0-9 !\"&'()+,\-./;?=%@\[\]_{}\\|<>#$]")


class X12Error(Exception):
    """A claim (or the settings) can't go in a file; .problems says why."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def clean(value, size=None) -> str:
    """X12 basic character set, upper case, no delimiters."""
    text = ALLOWED.sub("", str(value or "").upper()).strip()
    return text[:size] if size else text


def amount(value: Decimal) -> str:
    text = f"{Decimal(value):.2f}"
    return text[:-3] if text.endswith(".00") else text.rstrip("0")


def split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


@dataclass
class Segments:
    items: list[str] = field(default_factory=list)

    def add(self, *elements) -> None:
        while elements and elements[-1] in ("", None):
            elements = elements[:-1]
        self.items.append(ELEMENT.join("" if e is None else str(e) for e in elements))


def claim_problems(claim, settings) -> list[str]:
    """Everything stopping this claim from going in an 837P."""
    trip, passenger = claim.invoice.trip, claim.invoice.trip.passenger
    problems = []
    if passenger is None:
        return ["The trip has no passenger."]
    for value, label in ((passenger.medicaid_id, "Medicaid ID"), (passenger.date_of_birth, "date of birth"),
                         (passenger.sex, "sex"), (passenger.home_street, "home street"),
                         (passenger.home_city, "home city"), (passenger.home_state, "home state"),
                         (passenger.home_zip, "home ZIP")):
        if not value:
            problems.append(f"Rider's {label} is missing (passenger page).")
    if not trip.prior_authorization_number:
        problems.append("The trip has no prior authorization number.")
    if not (claim.diagnosis_code or settings.default_diagnosis_code):
        problems.append("No diagnosis code on the claim or in the settings.")
    if not claim.service_date:
        problems.append("No date of service.")
    if claim.amount_claimed is None or claim.amount_claimed <= 0:
        problems.append("Nothing to claim.")
    if claim.replaces_id and not claim.replaces.claim_number:
        problems.append("The claim this replaces has no TMHP claim number (ICN) yet.")
    return problems


def build_837p(claims, settings, *, control_number: int, now: datetime) -> str:
    missing = settings.missing()
    if missing:
        raise X12Error(["Claims settings are incomplete: " + ", ".join(missing) + "."])
    problems = [f"{c.patient_control_number}: {p}" for c in claims for p in claim_problems(c, settings)]
    if problems:
        raise X12Error(problems)

    sub, rec = clean(settings.submitter_id, 15), clean(settings.receiver_id, 15)
    icn = f"{control_number:09d}"
    s = Segments()
    qualifier = clean(settings.interchange_qualifier, 2)
    s.add("ISA", "00", " " * 10, "00", " " * 10, qualifier, sub.ljust(15), qualifier, rec.ljust(15),
          now.strftime("%y%m%d"), now.strftime("%H%M"), REPETITION, "00501", icn, "1",
          "P" if settings.production else "T", COMPONENT)
    s.add("GS", "HC", sub, rec, now.strftime("%Y%m%d"), now.strftime("%H%M"), control_number, "X", "005010X222A1")
    start = len(s.items)
    s.add("ST", "837", "0001", "005010X222A1")
    s.add("BHT", "0019", "00", icn, now.strftime("%Y%m%d"), now.strftime("%H%M"), "CH")
    org_name = clean(_org_name(), 60)
    s.add("NM1", "41", "2", org_name, "", "", "", "", "46", sub)
    s.add("PER", "IC", clean(settings.submitter_contact_name, 60), "TE", re.sub(r"\D", "", settings.submitter_contact_phone))
    s.add("NM1", "40", "2", clean(settings.receiver_name, 60), "", "", "", "", "46", rec)
    s.add("HL", "1", "", "20", "1")
    s.add("PRV", "BI", "PXC", clean(settings.taxonomy_code))
    s.add("NM1", "85", "2", org_name, "", "", "", "", "XX", clean(settings.billing_npi))
    s.add("N3", clean(settings.billing_street, 55))
    s.add("N4", clean(settings.billing_city, 30), clean(settings.billing_state), clean(settings.billing_zip))
    s.add("REF", "EI", re.sub(r"\D", "", _ein()))

    for n, claim in enumerate(claims, start=2):
        trip, passenger = claim.invoice.trip, claim.invoice.trip.passenger
        last, first = split_name(passenger.legal_name)
        s.add("HL", n, "1", "22", "0")
        s.add("SBR", "P", "18", "", "", "", "", "", "", "MC")
        s.add("NM1", "IL", "1", clean(last, 60), clean(first, 35), "", "", "", "MI", clean(passenger.medicaid_id))
        s.add("N3", clean(passenger.home_street, 55))
        s.add("N4", clean(passenger.home_city, 30), clean(passenger.home_state), re.sub(r"\D", "", passenger.home_zip))
        s.add("DMG", "D8", passenger.date_of_birth.strftime("%Y%m%d"), passenger.sex)
        s.add("NM1", "PR", "2", clean(settings.receiver_name, 60), "", "", "", "", "PI", clean(settings.payer_id))
        if settings.tpi:  # optional since TMHP retired the TPI
            s.add("REF", "G2", clean(settings.tpi))
        frequency = "7" if claim.replaces_id else "1"
        s.add("CLM", clean(claim.patient_control_number, 20), amount(claim.amount_claimed), "", "",
              COMPONENT.join([clean(settings.place_of_service), "B", frequency]), "Y", "A", "Y", "Y")
        if claim.replaces_id:
            s.add("REF", "F8", clean(claim.replaces.claim_number))
        s.add("REF", "G1", clean(trip.prior_authorization_number, 50))
        s.add("HI", f"ABK{COMPONENT}{clean(claim.diagnosis_code or settings.default_diagnosis_code)}")
        for line_number, (code, charge, units) in enumerate(_service_lines(claim, settings), start=1):
            modifiers = [m for m in (claim.modifier_1, claim.modifier_2) if m] if code == settings.procedure_code else []
            s.add("LX", line_number)
            s.add("SV1", COMPONENT.join(["HC", clean(code)] + [clean(m) for m in modifiers]), amount(charge), "UN",
                  units, "", "", "1")
            s.add("DTP", "472", "D8", claim.service_date.strftime("%Y%m%d"))

    s.add("SE", len(s.items) - start + 1, "0001")
    s.add("GE", "1", control_number)
    s.add("IEA", "1", icn)
    return SEGMENT.join(s.items) + SEGMENT


def _service_lines(claim, settings):
    """The trip (and, if a mileage code is set, the miles as their own line) from the invoice's calculation."""
    miles = [l for l in claim.invoice.lines if l["label"].startswith("Miles")]
    if settings.mileage_code and miles:
        miles_charge = sum(Decimal(l["amount"]) for l in miles)
        units = sum(Decimal(l["quantity"]) for l in miles)
        trip_charge = Decimal(claim.amount_claimed) - miles_charge
        return [(settings.procedure_code, trip_charge, "1"), (settings.mileage_code, miles_charge, amount(units))]
    return [(settings.procedure_code, Decimal(claim.amount_claimed), "1")]


def _org_name() -> str:
    from organization.models import Organization

    org = Organization.objects.first()
    return org.legal_name if org else "AngelCare Transit"


def _ein() -> str:
    from organization.models import Organization

    org = Organization.objects.first()
    return getattr(org, "ein", "") or ""


# --- Reading TMHP's files ---------------------------------------------------

def segments(text: str) -> list[list[str]]:
    """Split any X12 file using the delimiters its own ISA declares."""
    text = text.strip()
    if not text.startswith("ISA") or len(text) < 106:
        raise X12Error(["That isn't an X12 file (it should start with ISA)."])
    element, terminator = text[3], text[105]
    parts = [p.strip() for p in text.split(terminator) if p.strip()]
    return [p.split(element) for p in parts]


def kind_of(text: str) -> str:
    for seg in segments(text):
        if seg[0] == "ST":
            return seg[1]
    raise X12Error(["No ST segment: can't tell what kind of file this is."])


def read_999(text: str) -> list[dict]:
    """[{group control number, accepted}] -- whether TMHP took each file."""
    results, group = [], None
    for seg in segments(text):
        if seg[0] == "AK1":
            group = seg[2] if len(seg) > 2 else None
        elif seg[0] == "AK9" and group is not None:
            results.append({"control_number": int(group), "accepted": seg[1] in ("A", "E")})
            group = None
    return results


ACCEPTED_277 = {"A1", "A2", "A5"}


def read_277ca(text: str) -> list[dict]:
    """[{pcn, accepted, icn, reasons}] per claim: the 2200D TRN*2 is our CLM01."""
    results, current = [], None
    for seg in segments(text):
        if seg[0] == "TRN" and len(seg) > 2 and seg[1] == "2":
            current = {"pcn": seg[2], "accepted": None, "icn": "", "reasons": []}
            results.append(current)
        elif seg[0] == "STC" and current is not None:
            category = seg[1].split(":")[0] if len(seg) > 1 else ""
            current["accepted"] = category in ACCEPTED_277 if current["accepted"] in (None, True) else False
            if category not in ACCEPTED_277:
                current["reasons"].append(f"277CA {seg[1]}" + (f" {seg[-1]}" if len(seg) > 12 else ""))
        elif seg[0] == "REF" and current is not None and seg[1] == "1K":
            current["icn"] = seg[2]
    return [r for r in results if r["accepted"] is not None]


def read_835(text: str) -> list[dict]:
    """[{pcn, status_code, charged, paid, icn, adjustments}] per claim (CLP and its CAS)."""
    results, current = [], None
    for seg in segments(text):
        if seg[0] == "CLP":
            current = {
                "pcn": seg[1], "status_code": seg[2], "charged": Decimal(seg[3] or 0),
                "paid": Decimal(seg[4] or 0), "icn": seg[7] if len(seg) > 7 else "", "adjustments": [],
            }
            results.append(current)
        elif seg[0] == "CAS" and current is not None:
            group = seg[1]
            for i in range(2, len(seg) - 1, 3):
                if seg[i]:
                    current["adjustments"].append(f"{group}-{seg[i]} ${seg[i + 1]}")
        elif seg[0] == "SE":
            current = None
    return results
