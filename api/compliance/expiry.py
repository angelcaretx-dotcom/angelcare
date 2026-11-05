"""
Everything with an expiry date that the company must keep current
(ADR 0036): drivers' licenses and credentials, vehicles' registration,
inspection and insurance, and company compliance records. The admin
dashboard and the daily reminder email both read this one list.
"""

from dataclasses import dataclass
from datetime import date

from django.db import transaction

from drivers.models import Driver, DriverStatus, current_credentials
from notifications.services import NotificationService
from vehicles.models import Vehicle, VehicleStatus

from .models import ComplianceRecord, ComplianceRecordStatus, ExpiryReminder

# Days before expiry when the office is reminded; 0 = on/after expiry.
REMINDER_DAYS = (90, 60, 30, 7, 0)


@dataclass
class ExpiringItem:
    key: str  # stable per item and expiry date: a renewal starts reminders afresh
    who: str
    what: str
    expires: date
    obj: object
    model: type

    def days_left(self, today: date) -> int:
        return (self.expires - today).days


def expiring_items(*, drivers=True, vehicles=True, company=True) -> list[ExpiringItem]:
    items = []
    if drivers:
        for driver in Driver.objects.filter(status=DriverStatus.ACTIVE).prefetch_related("credentials"):
            items.append(ExpiringItem(
                f"driver:{driver.pk}:license:{driver.license_expiration_date}",
                driver.legal_name, "Driver's license", driver.license_expiration_date, driver, Driver,
            ))
            for credential in current_credentials(driver).values():
                if credential.expires_on:
                    items.append(ExpiringItem(
                        f"credential:{credential.pk}:{credential.expires_on}",
                        driver.legal_name, credential.get_credential_type_display(),
                        credential.expires_on, driver, Driver,
                    ))
    if vehicles:
        for vehicle in Vehicle.objects.filter(status__in=[VehicleStatus.ACTIVE, VehicleStatus.MAINTENANCE]):
            name = f"{vehicle.make} {vehicle.model} ({vehicle.license_plate})"
            for field, label in (
                ("registration_expiration_date", "Registration"),
                ("inspection_expiration_date", "Inspection"),
                ("insurance_expiration_date", "Insurance"),
            ):
                expires = getattr(vehicle, field)
                if expires:
                    items.append(ExpiringItem(f"vehicle:{vehicle.pk}:{field}:{expires}", name, label, expires, vehicle, Vehicle))
    if company:
        for record in ComplianceRecord.objects.filter(
            status=ComplianceRecordStatus.ACTIVE, expiration_date__isnull=False
        ):
            items.append(ExpiringItem(
                f"compliance:{record.pk}:{record.expiration_date}",
                record.name, record.get_record_type_display(), record.expiration_date, record, ComplianceRecord,
            ))
    return items


def _stage(days_left: int) -> int | None:
    """The reminder point this item has reached (90, 60, 30, 7 or 0), or None if it's further off."""
    reached = [d for d in REMINDER_DAYS if days_left <= d]
    return min(reached) if reached else None


@transaction.atomic
def send_reminders(today: date) -> int:
    """
    One digest email to the office listing every item that reached a
    new reminder point since the last run, so a missed day doesn't skip
    a reminder and nothing is sent twice. Returns how many items it named.
    """
    due = []
    sent = dict(ExpiryReminder.objects.values_list("key", "stage"))
    for item in expiring_items():
        stage = _stage(item.days_left(today))
        if stage is None or (item.key in sent and sent[item.key] <= stage):
            continue
        due.append(item)
        ExpiryReminder.objects.update_or_create(key=item.key, defaults={"stage": stage})
    if due:
        due.sort(key=lambda item: item.expires)
        rows = [(item.who, item.what, item.expires, item.days_left(today)) for item in due]
        transaction.on_commit(lambda: NotificationService().notify_expiring_credentials(rows))
    return len(due)
