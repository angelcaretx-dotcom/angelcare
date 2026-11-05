"""
The trip proof sheet (ADR 0034): everything an auditor asks for about
one trip, as a PDF built when it's downloaded and never stored. Times
are Texas time; locations are the driver's phone GPS at each step.
"""

import io
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from fpdf import FPDF

from organization.models import Organization
from vehicles.models import VehicleCheck

ZONE = ZoneInfo(settings.OPERATIONS_TIME_ZONE)
# The PDF core fonts are Latin-1 only.
REPLACEMENTS = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...", "±": "+/-", "→": "->"}


def _text(value) -> str:
    text = "" if value is None else str(value)
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def _when(value) -> str:
    if not value:
        return "-"
    local = timezone.localtime(value, ZONE)
    return f"{local:%b %d, %Y} {local:%I:%M %p}".replace(" 0", " ")


class ProofSheet(FPDF):
    def __init__(self, org_name: str, generated: str):
        super().__init__(format="Letter")
        self.org_name = org_name
        self.generated = generated
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(16, 16, 16)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", size=8)
        self.set_text_color(110)
        self.cell(
            0, 5,
            _text(f"Generated {self.generated} (Texas time) - not stored. Page {self.page_no()}/{{nb}}"),
            align="C",
        )

    def heading(self, text: str):
        self.ln(3)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(35, 90, 117)
        self.cell(0, 7, _text(text), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1.5)
        self.set_text_color(20)

    def pairs(self, rows):
        for label, value in rows:
            self.set_font("Helvetica", "B", 9)
            self.cell(48, 5.5, _text(label))
            self.set_font("Helvetica", size=9)
            self.multi_cell(0, 5.5, _text(value) or "-", new_x="LMARGIN", new_y="NEXT")


def build_proof_pdf(trip) -> bytes:
    org = Organization.objects.first()
    pdf = ProofSheet(org.legal_name if org else "AngelCare Transit", _when(timezone.now()))
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, _text(pdf.org_name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    if org:
        pdf.cell(0, 5, _text(f"{org.phone} · {org.service_area}".replace("·", "-")), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Trip proof of service", new_x="LMARGIN", new_y="NEXT")

    passenger = trip.passenger
    pdf.heading("Trip")
    pdf.pairs(
        [
            ("Trip ID", trip.pk),
            ("Status", trip.get_status_display()),
            ("Source / payer", f"{trip.get_source_display()} / {trip.payer or '-'}"),
            ("Broker trip number", trip.broker_trip_number),
            ("Prior authorization", trip.prior_authorization_number),
            ("Service", f"{trip.get_service_type_display()} - {trip.get_leg_display()}"),
            ("Scheduled pickup", _when(trip.scheduled_pickup_datetime)),
            ("Appointment", _when(trip.appointment_datetime)),
            ("Pickup address", trip.pickup_address),
            ("Drop-off address", trip.dropoff_address),
        ]
    )
    pdf.heading("Rider, driver and vehicle")
    pdf.pairs(
        [
            ("Rider", passenger.legal_name if passenger else "-"),
            ("Driver", f"{trip.driver.legal_name} (license {trip.driver.license_number})" if trip.driver else "-"),
            ("Vehicle", f"{trip.vehicle} - VIN {trip.vehicle.vin}" if trip.vehicle else "-"),
        ]
    )

    events = list(trip.events.select_related("recorded_by").order_by("occurred_at"))
    pdf.heading("Timeline (server time; GPS from the driver's phone)")
    if not events:
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 6, "No steps recorded in the driver app.", new_x="LMARGIN", new_y="NEXT")
    else:
        widths = (44, 50, 64, 26)
        pdf.set_font("Helvetica", "B", 9)
        for width, title in zip(widths, ("Time", "Step", "Location (lat, long)", "Accuracy")):
            pdf.cell(width, 6, title, border="B")
        pdf.ln()
        pdf.set_font("Helvetica", size=9)
        for event in events:
            step = event.get_event_type_display()
            if event.reason:
                step += f" ({event.get_reason_display()})"
            location = f"{event.latitude}, {event.longitude}" if event.has_location else "not shared"
            accuracy = f"+/-{event.accuracy_m:.0f} m" if event.accuracy_m is not None else "-"
            row_top = pdf.get_y()
            pdf.cell(widths[0], 6, _text(_when(event.occurred_at)))
            pdf.multi_cell(widths[1], 6, _text(step), new_x="RIGHT", new_y="TOP")
            pdf.cell(widths[2], 6, _text(location))
            pdf.cell(widths[3], 6, _text(accuracy))
            pdf.set_y(max(pdf.get_y() + 6, row_top + 6))
            if event.note:
                pdf.set_font("Helvetica", "I", 8)
                pdf.multi_cell(0, 4.5, _text(f"   Note: {event.note}"), new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=9)

    pickup = next((e for e in events if e.event_type == "picked_up"), None)
    pdf.heading("Rider's signature at pickup")
    pdf.set_font("Helvetica", size=9)
    if pickup is None:
        pdf.cell(0, 6, "The rider wasn't picked up in the driver app.", new_x="LMARGIN", new_y="NEXT")
    elif pickup.signature:
        signer = pickup.signed_by_name or (passenger.legal_name if passenger else "the rider")
        pdf.cell(0, 6, _text(f"Signed by {signer}, {_when(pickup.occurred_at)}"), new_x="LMARGIN", new_y="NEXT")
        pdf.image(io.BytesIO(bytes(pickup.signature)), w=80)
    else:
        pdf.multi_cell(
            0, 6,
            _text(f"Not signed: {pickup.get_signature_missing_reason_display() or 'no reason recorded'}"),
            new_x="LMARGIN", new_y="NEXT",
        )

    pdf.heading("Vehicle that day")
    check = None
    if trip.vehicle_id and trip.scheduled_pickup_datetime:
        day = timezone.localtime(trip.scheduled_pickup_datetime, ZONE).date()
        check = VehicleCheck.objects.filter(vehicle_id=trip.vehicle_id, service_date=day).select_related("driver").first()
    if check is None:
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 6, "No daily vehicle check recorded for that day.", new_x="LMARGIN", new_y="NEXT")
    else:
        result = "Passed" if check.passed else "Failed: " + "; ".join(check.failed_labels())
        if not check.passed and check.cleared_at:
            result += f" (fixed {_when(check.cleared_at)}: {check.cleared_note})"
        pdf.pairs(
            [
                ("Pre-trip inspection", f"{result} - {check.driver.legal_name}, {_when(check.checked_at)}"),
                ("Odometer, start of day", f"{check.start_odometer:,}"),
                ("Odometer, end of day", f"{check.end_odometer:,}" if check.end_odometer is not None else "not recorded"),
                ("Miles that day (vehicle)", f"{check.miles:,}" if check.miles is not None else "-"),
            ]
        )
    return bytes(pdf.output())
