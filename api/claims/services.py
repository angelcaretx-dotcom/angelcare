"""
The claims workflow with TMHP (ADR 0040):

  invoices -> draft claims -> an 837P file (uploaded to TexMedConnect)
  -> 999 (file accepted?) -> 277CA (each claim accepted or rejected)
  -> 835 (paid or denied, with reasons) -> fix and resubmit if needed.

Every claim carries TMHP's 95-day filing deadline from the date of
service; the attention queue lists what's close or past.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.services import record_change
from billing.models import Invoice, InvoiceStatus

from . import x12
from .models import Claim, ClaimFile, ClaimSettings, ClaimStatus, ResponseFile

OPEN = (ClaimStatus.DRAFT, ClaimStatus.SUBMITTED, ClaimStatus.ACCEPTED, ClaimStatus.REJECTED, ClaimStatus.DENIED)
WARN_DAYS = 15


def _zone():
    return ZoneInfo(django_settings.OPERATIONS_TIME_ZONE)


def today():
    return timezone.now().astimezone(_zone()).date()


def _pcn(claim: Claim) -> str:
    return "AC" + str(claim.pk).replace("-", "")[:12].upper()


@transaction.atomic
def claims_from_invoices(*, user) -> list[Claim]:
    """A draft claim for every invoice of the TMHP payer that doesn't have an open or paid claim yet."""
    config = ClaimSettings.load()
    if not config.tmhp_payer_id:
        raise x12.X12Error(["Choose the TMHP payer in Claims settings first."])
    claimed = Claim.objects.exclude(status=ClaimStatus.REJECTED).values("invoice_id")
    invoices = (
        Invoice.objects.filter(payer_id=config.tmhp_payer_id)
        .exclude(status=InvoiceStatus.VOID)
        .exclude(pk__in=claimed)
        .select_related("trip")
    )
    made = []
    for invoice in invoices:
        made.append(_new_claim(invoice, config, user=user))
    return made


def _new_claim(invoice, config, *, user, replaces=None) -> Claim:
    claim = Claim(
        invoice=invoice, payer=invoice.payer, amount_claimed=invoice.amount, status=ClaimStatus.DRAFT,
        service_date=timezone.localtime(invoice.trip.scheduled_pickup_datetime, _zone()).date(),
        diagnosis_code=(replaces.diagnosis_code if replaces else "") or config.default_diagnosis_code,
        modifier_1=(replaces.modifier_1 if replaces else "") or config.default_modifier_1,
        modifier_2=(replaces.modifier_2 if replaces else "") or config.default_modifier_2,
        replaces=replaces if replaces and replaces.claim_number else None,
    )
    claim.patient_control_number = _pcn(claim)
    claim.save()
    record_change(actor=user, action="created", resource_type="Claim", resource_id=claim.pk,
                  after={"invoice": str(invoice.pk), "amount": str(claim.amount_claimed),
                         "replaces": str(replaces.pk) if replaces else None}, source="admin")
    return claim


@transaction.atomic
def make_file(claims, *, user) -> ClaimFile:
    """One 837P with these draft claims. Raises X12Error listing every problem, before anything changes."""
    config = ClaimSettings.load()
    claims = list(claims)
    if not claims:
        raise x12.X12Error(["Choose at least one draft claim."])
    not_draft = [c.patient_control_number for c in claims if c.status != ClaimStatus.DRAFT]
    if not_draft:
        raise x12.X12Error([f"Only draft claims go in a new file ({', '.join(not_draft)} aren't)."])
    late = [c.patient_control_number for c in claims if c.filing_deadline and c.filing_deadline < today()]
    if late:
        raise x12.X12Error([f"Past TMHP's 95-day filing deadline: {', '.join(late)}."])
    number = (ClaimFile.objects.aggregate(top=Max("control_number"))["top"] or 0) + 1
    content = x12.build_837p(claims, config, control_number=number, now=timezone.now().astimezone(_zone()))
    claim_file = ClaimFile.objects.create(control_number=number, content=content, production=config.production,
                                          created_by=user)
    for claim in claims:
        claim.claim_file = claim_file
        claim.status = ClaimStatus.SUBMITTED
        claim.submitted_date = today()
        claim.save(update_fields=["claim_file", "status", "submitted_date", "updated_at"])
    record_change(actor=user, action="created", resource_type="ClaimFile", resource_id=claim_file.pk,
                  after={"control_number": number, "claims": len(claims), "production": config.production},
                  source="admin")
    return claim_file


def _set(claim, status, *, user, details=None, **fields):
    before = claim.status
    claim.status = status
    for name, value in fields.items():
        setattr(claim, name, value)
    if details is not None:
        claim.response_details = details
    claim.response_date = today()
    claim.save()
    record_change(actor=user, action="status_changed", resource_type="Claim", resource_id=claim.pk,
                  before={"status": before}, after={"status": status, "via": "tmhp_response"}, source="admin")


@transaction.atomic
def apply_response(text: str, name: str, *, user) -> ResponseFile:
    """Read a 999, 277CA or 835 and update the claims it's about. Unknown claim IDs are listed, not guessed."""
    kind = x12.kind_of(text)
    summary = []
    if kind == "999":
        for result in x12.read_999(text):
            updated = ClaimFile.objects.filter(control_number=result["control_number"]).update(
                acknowledged="accepted" if result["accepted"] else "rejected")
            summary.append(f"File {result['control_number']:09d}: {'accepted' if result['accepted'] else 'REJECTED'}"
                           + ("" if updated else " (no such file here)"))
            if not result["accepted"]:
                for claim in Claim.objects.filter(claim_file__control_number=result["control_number"]):
                    _set(claim, ClaimStatus.REJECTED, user=user, details=["The whole file was rejected (999)."])
    elif kind == "277":
        for result in x12.read_277ca(text):
            claim = Claim.objects.filter(patient_control_number=result["pcn"]).first()
            if claim is None:
                summary.append(f"{result['pcn']}: not one of our claims")
                continue
            if result["accepted"]:
                _set(claim, ClaimStatus.ACCEPTED, user=user, details=[], claim_number=result["icn"] or claim.claim_number)
                summary.append(f"{result['pcn']}: accepted")
            else:
                _set(claim, ClaimStatus.REJECTED, user=user, details=result["reasons"])
                summary.append(f"{result['pcn']}: REJECTED ({'; '.join(result['reasons'])})")
    elif kind == "835":
        for result in x12.read_835(text):
            claim = Claim.objects.filter(patient_control_number=result["pcn"]).first()
            if claim is None:
                summary.append(f"{result['pcn']}: not one of our claims")
                continue
            if result["status_code"] == "22":
                summary.append(f"{result['pcn']}: payment reversed — check the remittance")
                _set(claim, ClaimStatus.DENIED, user=user, details=["Payment reversed (835 status 22)."]
                     + result["adjustments"], amount_paid=None, claim_number=result["icn"] or claim.claim_number)
            elif result["status_code"] == "4" or result["paid"] <= 0:
                _set(claim, ClaimStatus.DENIED, user=user, details=result["adjustments"], amount_paid=result["paid"],
                     claim_number=result["icn"] or claim.claim_number)
                summary.append(f"{result['pcn']}: DENIED ({'; '.join(result['adjustments']) or 'no reason given'})")
            else:
                _set(claim, ClaimStatus.PAID, user=user, details=result["adjustments"], amount_paid=result["paid"],
                     claim_number=result["icn"] or claim.claim_number)
                Invoice.objects.filter(pk=claim.invoice_id).update(status=InvoiceStatus.PAID, paid_date=today())
                summary.append(f"{result['pcn']}: paid ${result['paid']}")
    else:
        raise x12.X12Error([f"An X12 {kind} file isn't one TMHP sends back here (999, 277CA or 835)."])
    response = ResponseFile.objects.create(kind=kind, original_name=name[:255], content=text, summary=summary,
                                           uploaded_by=user)
    record_change(actor=user, action="imported", resource_type="ResponseFile", resource_id=response.pk,
                  after={"kind": kind, "lines": len(summary)}, source="admin")
    return response


@transaction.atomic
def resubmit(claim: Claim, *, user) -> Claim:
    """A new draft for a rejected or denied claim. A denied one (TMHP has its ICN) goes as a replacement."""
    if claim.status not in (ClaimStatus.REJECTED, ClaimStatus.DENIED):
        raise x12.X12Error(["Only rejected or denied claims are resubmitted."])
    if claim.replacements.filter(status__in=OPEN).exists():
        raise x12.X12Error(["This claim already has a resubmission in progress."])
    return _new_claim(claim.invoice, ClaimSettings.load(), user=user,
                      replaces=claim if claim.status == ClaimStatus.DENIED else None)


def needs_attention():
    """Rejected and denied claims, and open ones near or past the 95-day deadline, soonest first."""
    soon = today() + timedelta(days=WARN_DAYS)
    rows = []
    for claim in Claim.objects.filter(status__in=OPEN).select_related("invoice__trip__passenger"):
        deadline = claim.filing_deadline
        reason = ""
        if claim.status in (ClaimStatus.REJECTED, ClaimStatus.DENIED) and not claim.replacements.exists():
            reason = claim.get_status_display()
        elif claim.status == ClaimStatus.DRAFT and deadline and deadline <= soon:
            reason = "Not sent yet"
        if reason:
            rows.append({"claim": claim, "reason": reason, "deadline": deadline,
                         "days_left": (deadline - today()).days if deadline else None})
    return sorted(rows, key=lambda r: (r["deadline"] is None, r["deadline"]))
