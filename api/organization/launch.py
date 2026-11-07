"""
The launch checklist (ADR 0045): what's still missing before the first
real trip, checked live against the records, so it updates itself as
data is entered. Items the app can't see (signed BAAs, email hosting)
are listed as "check yourself".
"""

from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse

DONE, TODO, MANUAL = "done", "todo", "manual"
BACKUP_RUNS_URL = "https://github.com/angelcaretx-dotcom/angelcare/actions/workflows/backup.yml"


@dataclass
class Item:
    area: str
    title: str
    state: str
    detail: str
    url: str = ""


def _names(objs, limit: int = 5) -> str:
    names = [str(o) for o in objs]
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _in_effect(queryset, today: date):
    return queryset.filter(effective_from__lte=today).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))


def _company(today):
    from compliance.models import ComplianceRecord, ComplianceRecordStatus
    from organization.models import Organization

    org = Organization.objects.first()
    yield Item(
        "Company", "Legal name includes “LLC”",
        DONE if org and "LLC" in org.legal_name else TODO,
        org.legal_name if org else "No organization record yet.",
        reverse("admin:organization_organization_changelist"),
    )
    records = ComplianceRecord.objects.filter(status=ComplianceRecordStatus.ACTIVE)
    expired = [r for r in records if r.expiration_date and r.expiration_date < today]
    if expired:
        state, detail = TODO, f"Expired: {_names(expired)}."
    elif records.exists():
        state, detail = DONE, f"{records.count()} active record(s), none expired."
    else:
        state, detail = TODO, "No company compliance records (insurance, registrations) entered."
    yield Item("Company", "Company insurance and registrations current", state, detail,
               reverse("admin:compliance_compliancerecord_changelist"))


def _staff(user):
    from accounts.mfa import backup_codes_remaining

    codes = backup_codes_remaining(user)
    yield Item(
        "Staff and security", "You have MFA backup codes",
        DONE if codes else TODO,
        f"{codes} unused backup code(s)." if codes else "None: losing your phone would lock you out.",
        reverse("admin:security"),
    )
    staff = get_user_model().objects.filter(is_active=True, is_staff=True, is_superuser=False)
    no_role = [u for u in staff if not u.groups.exists()]
    if no_role:
        state, detail = TODO, f"Staff logins with no role (they can't do anything): {_names(no_role)}."
    elif staff.exists():
        state, detail = DONE, f"{staff.count()} staff login(s), each in a role."
    else:
        state, detail = DONE, "Only superuser accounts so far. Add each dispatcher in the Dispatcher role before they start."
    yield Item("Staff and security", "Every staff login is in a role", state, detail,
               reverse("admin:auth_user_changelist"))


def _payers(today):
    from billing.models import RateCard
    from payers.models import Payer, PayerStatus

    payers = list(Payer.objects.filter(status=PayerStatus.ACTIVE).order_by("name"))
    yield Item("Payers and rates", "At least one payer", DONE if payers else TODO,
               _names(payers) + "." if payers else "No payers entered.",
               reverse("admin:payers_payer_changelist"))
    without = [p for p in payers if not _in_effect(RateCard.objects.filter(payer=p), today).exists()]
    if not payers:
        state, detail = TODO, "Add payers first."
    elif without:
        state, detail = TODO, f"No rate card in effect today: {_names(without)}."
    else:
        state, detail = DONE, "Every payer has a rate card in effect."
    yield Item("Payers and rates", "Every payer has a current rate card", state, detail,
               reverse("admin:billing_ratecard_changelist"))


def _drivers(today):
    from billing.models import DriverPayPlan
    from drivers.models import Driver, DriverStatus, missing_credentials

    drivers = list(Driver.objects.filter(status=DriverStatus.ACTIVE).order_by("legal_name"))
    url = reverse("admin:drivers_driver_changelist")
    yield Item("Drivers", "At least one active driver", DONE if drivers else TODO,
               f"{len(drivers)} active driver(s)." if drivers else "No drivers entered.", url)
    short = [d for d in drivers if d.license_expiration_date < today or missing_credentials(d, day=today)]
    yield Item(
        "Drivers", "Every driver's license and required credentials are current",
        TODO if short or not drivers else DONE,
        f"Missing or expired: {_names(short)}. Until they're entered, these drivers can't be assigned."
        if short else ("Add drivers first." if not drivers else "All current."),
        url,
    )
    unpaid = [d for d in drivers if not _in_effect(DriverPayPlan.objects.filter(driver=d), today).exists()]
    yield Item(
        "Drivers", "Every driver has a pay plan",
        TODO if unpaid or not drivers else DONE,
        f"No pay plan in effect: {_names(unpaid)}." if unpaid else ("Add drivers first." if not drivers else "All set."),
        reverse("admin:billing_driverpayplan_changelist"),
    )


def _vehicles(today):
    from vehicles.models import Vehicle, VehicleStatus

    vehicles = list(Vehicle.objects.filter(status=VehicleStatus.ACTIVE))
    url = reverse("admin:vehicles_vehicle_changelist")
    yield Item("Vehicles", "At least one active vehicle", DONE if vehicles else TODO,
               f"{len(vehicles)} active vehicle(s)." if vehicles else "No vehicles entered.", url)
    short = [
        v for v in vehicles
        if not v.insurance_expiration_date or v.insurance_expiration_date < today
        or v.registration_expiration_date < today or v.inspection_expiration_date < today
    ]
    yield Item(
        "Vehicles", "Every vehicle's insurance, registration and inspection are current",
        TODO if short or not vehicles else DONE,
        f"Missing or expired: {_names(short)}. Until insurance is entered, a vehicle can't be assigned."
        if short else ("Add vehicles first." if not vehicles else "All current."),
        url,
    )


def _claims():
    from claims.models import ClaimSettings
    from passengers.models import Passenger, PassengerStatus
    from payers.models import PayerType

    settings = ClaimSettings.load()
    missing = settings.missing()
    url = reverse("admin:claims_claimsettings_change", args=[settings.pk])
    yield Item("Medicaid claims", "Claims settings complete", TODO if missing else DONE,
               f"Still needed: {', '.join(missing)}." if missing else "Complete: 837P files can be made.", url)
    yield Item(
        "Medicaid claims", "Test mode until TMHP approves", MANUAL,
        "Production is ON: files are marked for real processing." if settings.production
        else "Test mode (Production off). Switch it on only after TMHP signs off your test files.",
        url,
    )
    medicaid = Q(payer__payer_type=PayerType.MEDICAID_MCO)
    if settings.tmhp_payer_id:
        medicaid |= Q(payer_id=settings.tmhp_payer_id)
    riders = Passenger.objects.filter(medicaid, status=PassengerStatus.ACTIVE)
    blank = Q(medicaid_id="") | Q(date_of_birth__isnull=True) | Q(sex="") | Q(home_street="") | Q(home_city="") \
        | Q(home_state="") | Q(home_zip="")
    incomplete = riders.filter(blank).count()
    yield Item(
        "Medicaid claims", "Medicaid riders have ID, birth date, sex and home address",
        TODO if incomplete else DONE,
        f"{incomplete} of {riders.count()} Medicaid rider(s) are missing details." if incomplete
        else (f"All {riders.count()} complete." if riders.exists() else "No Medicaid riders yet."),
        reverse("admin:passengers_passenger_changelist"),
    )


def _portal():
    from facilities.models import FacilityUser

    signed_in = FacilityUser.objects.filter(verified_at__isnull=False).exists()
    yield Item(
        "Facility portal", "Portal tested end to end", DONE if signed_in else TODO,
        "A portal user has set a password and signed in." if signed_in
        else "No portal user has signed in yet: set the password from the set-up email, sign in, book a test ride.",
        reverse("admin:facilities_facilityuser_changelist"),
    )


def _outside():
    for title, detail in (
        ("BAAs signed with Supabase, Vercel and Resend",
         "Before the first Medicaid trip: no rider information should pass through a service without one."),
        ("Rider email on Google Workspace, not consumer Gmail",
         "Consumer Gmail can't sign a BAA; use Workspace on angelcaretransit.com for any email about riders."),
        ("Resend shows angelcaretransit.com as Verified", "Resend → Domains."),
    ):
        yield Item("Check yourself", title, MANUAL, detail)
    yield Item("Check yourself", "Nightly backup succeeding", MANUAL,
               "GitHub → Actions → Nightly backup: the latest run is green. Keep a copy somewhere other than Supabase.",
               BACKUP_RUNS_URL)


def checklist(user, today: date) -> list[Item]:
    items = []
    for part in (_company(today), _staff(user), _payers(today), _drivers(today), _vehicles(today), _claims(),
                 _portal(), _outside()):
        items.extend(part)
    return items
