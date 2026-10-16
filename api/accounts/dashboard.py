"""
Admin home page dashboard (ADR 0023): what needs a staff member's
attention today, instead of Django's flat list of every model.

Wired in through Unfold's DASHBOARD_CALLBACK (config/settings.py) and
rendered by templates/admin/index.html. Everything here is a count or
list of real records -- nothing estimated or invented -- and every
section is shown only to users with view permission on that model, so
a Dispatcher never sees Billing/Claims figures (ADR 0005/0018/0019).

"Today" is the operations day in OPERATIONS_TIME_ZONE (Texas), not in
UTC: the database stores UTC (USE_TZ), but a dispatcher's "today"
ends at midnight Central, not 7 PM.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceStatus
from claims.models import Claim, ClaimStatus
from compliance.models import ComplianceRecord, ComplianceRecordStatus
from documents.models import Document, DocumentStatus
from drivers.models import Driver, DriverStatus
from notifications.models import NotificationLog, NotificationStatus
from transportation.models import TripRequest, TripRequestStatus
from trips.models import Trip, TripStatus
from vehicles.models import Vehicle, VehicleStatus

from .mfa import backup_codes_remaining

# Look-ahead windows. Operational choices, not business facts: change
# freely if staff want a longer or shorter horizon.
UPCOMING_TRIP_DAYS = 7
EXPIRY_WARNING_DAYS = 30
FAILED_NOTIFICATION_DAYS = 7
LIST_LIMIT = 8


@dataclass
class Stat:
    label: str
    value: str
    url: str
    alert: bool = False  # highlight: something needs action


@dataclass
class Row:
    cells: list[str]
    url: str
    alert: bool = False


@dataclass
class Panel:
    title: str
    headers: list[str]
    rows: list[Row] = field(default_factory=list)
    empty_text: str = "Nothing here."
    more_url: str = ""
    more_count: int = 0


@dataclass
class Dashboard:
    today: date
    # Shown to the viewer about their own account (ADR 0024).
    no_backup_codes: bool = False
    stats: list[Stat] = field(default_factory=list)
    finance_stats: list[Stat] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)


def operations_tz() -> ZoneInfo:
    return ZoneInfo(settings.OPERATIONS_TIME_ZONE)


def _local(dt: datetime) -> str:
    return timezone.localtime(dt, operations_tz()).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _changelist(model, **filters) -> str:
    opts = model._meta
    url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    if filters:
        url += "?" + "&".join(f"{k}={v}" for k, v in filters.items())
    return url


def _change(obj) -> str:
    opts = obj._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])


def _can_view(user, model) -> bool:
    opts = model._meta
    return user.has_perm(f"{opts.app_label}.view_{opts.model_name}")


def _expiry_text(expires: date, today: date) -> str:
    days = (expires - today).days
    if days < 0:
        return f"Expired {-days} day{'s' if days != -1 else ''} ago"
    if days == 0:
        return "Expires today"
    return f"In {days} day{'s' if days != 1 else ''}"


def build_dashboard(user, now: datetime | None = None) -> Dashboard:
    now = now or timezone.now()
    tz = operations_tz()
    today = timezone.localtime(now, tz).date()
    day_start = datetime.combine(today, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    dash = Dashboard(today=today, no_backup_codes=backup_codes_remaining(user) == 0)

    # --- Headline numbers -------------------------------------------------
    if _can_view(user, TripRequest):
        new_requests = TripRequest.objects.filter(status=TripRequestStatus.NEW)
        count = new_requests.count()
        dash.stats.append(
            Stat(
                "New trip requests to review",
                str(count),
                _changelist(TripRequest, status__exact=TripRequestStatus.NEW),
                alert=count > 0,
            )
        )

    if _can_view(user, Trip):
        today_trips = Trip.objects.filter(
            scheduled_pickup_datetime__gte=day_start,
            scheduled_pickup_datetime__lt=day_end,
            status__in=[TripStatus.SCHEDULED, TripStatus.IN_PROGRESS],
        ).count()
        dash.stats.append(Stat("Trips today", str(today_trips), _changelist(Trip)))

    if _can_view(user, Document):
        pending = Document.objects.filter(status=DocumentStatus.PENDING).count()
        dash.stats.append(
            Stat(
                "Documents awaiting review",
                str(pending),
                _changelist(Document, status__exact=DocumentStatus.PENDING),
                alert=pending > 0,
            )
        )

    if _can_view(user, NotificationLog):
        failed = NotificationLog.objects.filter(
            status=NotificationStatus.FAILED,
            created_at__gte=now - timedelta(days=FAILED_NOTIFICATION_DAYS),
        ).count()
        dash.stats.append(
            Stat(
                f"Failed emails (last {FAILED_NOTIFICATION_DAYS} days)",
                str(failed),
                _changelist(NotificationLog, status__exact=NotificationStatus.FAILED),
                alert=failed > 0,
            )
        )

    # --- Finance (Administrator only, via model permissions) --------------
    if _can_view(user, Invoice):
        unpaid = Invoice.objects.filter(status=InvoiceStatus.SENT)
        total = unpaid.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        dash.finance_stats.append(
            Stat(
                f"Unpaid invoices ({unpaid.count()})",
                f"${total:,.2f}",
                _changelist(Invoice, status__exact=InvoiceStatus.SENT),
            )
        )
    if _can_view(user, Claim):
        open_claims = Claim.objects.filter(
            status__in=[ClaimStatus.SUBMITTED, ClaimStatus.ACCEPTED]
        ).count()
        denied = Claim.objects.filter(status=ClaimStatus.DENIED).count()
        dash.finance_stats.append(
            Stat("Claims awaiting payment", str(open_claims), _changelist(Claim))
        )
        dash.finance_stats.append(
            Stat(
                "Denied claims",
                str(denied),
                _changelist(Claim, status__exact=ClaimStatus.DENIED),
                alert=denied > 0,
            )
        )

    # --- Lists --------------------------------------------------------------
    if _can_view(user, TripRequest):
        queue = TripRequest.objects.filter(status=TripRequestStatus.NEW).order_by("created_at")
        total = queue.count()
        dash.panels.append(
            Panel(
                "Trip requests waiting for review (oldest first)",
                ["Name", "Service", "Requested pickup", "Submitted"],
                [
                    Row(
                        [
                            r.full_name,
                            r.get_service_type_display(),
                            _local(r.requested_datetime),
                            _local(r.created_at),
                        ],
                        _change(r),
                    )
                    for r in queue[:LIST_LIMIT]
                ],
                empty_text="No new requests. All caught up.",
                more_url=_changelist(TripRequest, status__exact=TripRequestStatus.NEW),
                more_count=max(total - LIST_LIMIT, 0),
            )
        )

    if _can_view(user, Trip):
        upcoming = (
            Trip.objects.filter(
                status=TripStatus.SCHEDULED,
                scheduled_pickup_datetime__gte=now,
                scheduled_pickup_datetime__lt=day_start + timedelta(days=UPCOMING_TRIP_DAYS + 1),
            )
            .select_related("trip_request", "driver", "vehicle")
            .order_by("scheduled_pickup_datetime")
        )
        total = upcoming.count()
        dash.panels.append(
            Panel(
                f"Upcoming trips (next {UPCOMING_TRIP_DAYS} days)",
                ["Pickup", "Passenger", "Driver", "Vehicle"],
                [
                    Row(
                        [
                            _local(t.scheduled_pickup_datetime),
                            t.trip_request.full_name,
                            t.driver.legal_name,
                            f"{t.vehicle.make} {t.vehicle.model} ({t.vehicle.license_plate})",
                        ],
                        _change(t),
                    )
                    for t in upcoming[:LIST_LIMIT]
                ],
                empty_text="No trips scheduled.",
                more_url=_changelist(Trip, status__exact=TripStatus.SCHEDULED),
                more_count=max(total - LIST_LIMIT, 0),
            )
        )

    expiring = _expiring_credentials(user, today)
    if expiring is not None:
        dash.panels.append(expiring)

    return dash


def _expiring_credentials(user, today: date) -> Panel | None:
    """
    Every active credential expiring within EXPIRY_WARNING_DAYS, plus
    any already expired. Expired Driver/Vehicle credentials already
    block trip assignment (ADR 0008); this is the advance warning.
    """
    cutoff = today + timedelta(days=EXPIRY_WARNING_DAYS)
    items: list[tuple[date, Row]] = []
    shown = False

    def add(obj, who: str, what: str, expires: date):
        items.append(
            (
                expires,
                Row(
                    [who, what, expires.strftime("%b %d, %Y"), _expiry_text(expires, today)],
                    _change(obj),
                    alert=expires < today,
                ),
            )
        )

    if _can_view(user, Driver):
        shown = True
        for d in Driver.objects.filter(
            status=DriverStatus.ACTIVE, license_expiration_date__lte=cutoff
        ):
            add(d, d.legal_name, "Driver's license", d.license_expiration_date)

    if _can_view(user, Vehicle):
        shown = True
        in_use = [VehicleStatus.ACTIVE, VehicleStatus.MAINTENANCE]
        for v in Vehicle.objects.filter(status__in=in_use):
            name = f"{v.make} {v.model} ({v.license_plate})"
            if v.registration_expiration_date <= cutoff:
                add(v, name, "Registration", v.registration_expiration_date)
            if v.inspection_expiration_date <= cutoff:
                add(v, name, "Inspection", v.inspection_expiration_date)

    if _can_view(user, ComplianceRecord):
        shown = True
        for c in ComplianceRecord.objects.filter(
            status=ComplianceRecordStatus.ACTIVE,
            expiration_date__isnull=False,
            expiration_date__lte=cutoff,
        ):
            add(c, c.name, c.get_record_type_display(), c.expiration_date)

    if not shown:
        return None
    items.sort(key=lambda item: item[0])
    return Panel(
        f"Credentials expiring within {EXPIRY_WARNING_DAYS} days",
        ["Who / what", "Credential", "Expires", "Status"],
        [row for _, row in items],
        empty_text=f"Nothing expires in the next {EXPIRY_WARNING_DAYS} days.",
    )


def dashboard_callback(request, context):
    """Unfold DASHBOARD_CALLBACK: adds the dashboard to the index context."""
    context["dashboard"] = build_dashboard(request.user)
    return context
