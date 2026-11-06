"""
Per-person staff access (ADR 0030). A staff member's role (Dispatcher /
Administrator, ADR 0005) is their baseline; an admin can grant extra
access on top, area by area, or switch someone's access off entirely --
from the browser, without code or Django's raw permission lists.

Extras are stored as the user's own Django permissions, so the rest of
the system (admin pages, sidebar, dashboard) honours them with no
special cases. The catalog below is the only thing that can be granted:
nobody can be made a superuser here, and "managing staff" itself is not
an area.
"""

from dataclasses import dataclass

from django.contrib.auth.models import Group, Permission
from django.db import transaction

from audit.services import record_change

from .onboarding import ROLES

NONE, VIEW, EDIT, FULL = 0, 1, 2, 3
LEVELS = [(NONE, "No access"), (VIEW, "View"), (EDIT, "Edit"), (FULL, "Full (incl. delete)")]
LEVEL_ACTIONS = {VIEW: ["view"], EDIT: ["view", "add", "change"], FULL: ["view", "add", "change", "delete"]}


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    models: tuple  # (app_label, model_name) pairs
    # Highest level that makes sense, e.g. logs are never editable.
    max_level: int = FULL


AREAS = [
    Area("trip_requests", "Trip requests (website)", (("transportation", "triprequest"),)),
    Area(
        "trips",
        "Trips, day board, standing orders & prior authorizations",
        (("trips", "trip"), ("trips", "standingorder"), ("trips", "standingorderskip"),
         ("trips", "priorauthorization")),
    ),
    Area("turn_downs", "Turn-downs", (("trips", "turndown"),)),
    Area("trip_imports", "Trip imports", (("intake", "importbatch"), ("intake", "mappingtemplate"))),
    Area("passengers", "Passengers", (("passengers", "passenger"),)),
    Area("rider_accounts", "Rider website accounts", (("riders", "rideraccount"),)),
    Area(
        "facilities",
        "Facilities (accounts, contacts, activity)",
        (("facilities", "facility"), ("facilities", "facilitycontact"), ("facilities", "facilityactivity")),
    ),
    Area("facility_portal", "Facility portal logins", (("facilities", "facilityuser"),)),
    Area("contact_messages", "Website messages", (("contact", "contactmessage"),)),
    Area(
        "drivers_vehicles",
        "Drivers & vehicles",
        (
            ("drivers", "driver"),
            ("drivers", "drivercredential"),
            ("vehicles", "vehicle"),
            ("vehicles", "vehiclecheck"),
        ),
    ),
    Area("payers", "Payers", (("payers", "payer"),)),
    Area(
        "billing",
        "Billing (invoices, rate cards, driver pay)",
        (("billing", "invoice"), ("billing", "ratecard"), ("billing", "driverpayplan")),
    ),
    Area(
        "claims",
        "Claims (TMHP files, responses, settings)",
        (("claims", "claim"), ("claims", "claimsettings"), ("claims", "claimfile"), ("claims", "responsefile")),
    ),
    Area(
        "compliance",
        "Compliance records & documents",
        (("compliance", "compliancerecord"), ("documents", "document")),
    ),
    Area("notification_log", "Email log", (("notifications", "notificationlog"),), max_level=VIEW),
    Area("audit_log", "Audit log", (("audit", "auditlog"),), max_level=VIEW),
]
AREAS_BY_KEY = {a.key: a for a in AREAS}


class AccessError(Exception):
    pass


def can_manage_access(user) -> bool:
    return user.is_active and user.is_staff and (
        user.is_superuser or user.groups.filter(name="Administrator").exists()
    )


def check_can_manage(actor, target) -> None:
    if not can_manage_access(actor):
        raise AccessError("Only an administrator can change staff access.")
    if target.pk == actor.pk:
        raise AccessError("You can't change your own access — ask another administrator.")
    if target.is_superuser:
        raise AccessError("Owner (superuser) accounts can't be changed here.")
    if not target.is_staff:
        raise AccessError("That isn't a staff account.")


def _codenames(perms) -> set:
    return {(p.content_type.app_label, p.codename) for p in perms}


def _level(granted: set, area: Area) -> int:
    level = NONE
    for candidate in (VIEW, EDIT, FULL):
        if candidate > area.max_level:
            break
        needed = {
            (app, f"{action}_{model}")
            for app, model in area.models
            for action in LEVEL_ACTIONS[candidate]
        }
        if needed <= granted:
            level = candidate
    return level


def role_of(user) -> str | None:
    names = set(user.groups.values_list("name", flat=True))
    for role in reversed(ROLES):  # Administrator outranks Dispatcher
        if role in names:
            return role
    return None


def role_levels(role: str | None) -> dict:
    if role is None:
        granted = set()
    else:
        granted = _codenames(
            Group.objects.get(name=role).permissions.select_related("content_type")
        )
    return {a.key: _level(granted, a) for a in AREAS}


def extra_levels(user) -> dict:
    granted = _codenames(user.user_permissions.select_related("content_type"))
    return {a.key: _level(granted, a) for a in AREAS}


def describe(user) -> dict:
    role = role_of(user)
    base, extra = role_levels(role), extra_levels(user)
    return {
        "role": role,
        "active": user.is_active,
        "levels": {k: max(base[k], extra[k]) for k in base},
        "role_levels": base,
    }


def _permissions_for(area: Area, level: int):
    perms = []
    for app, model in area.models:
        perms += list(
            Permission.objects.filter(
                content_type__app_label=app,
                codename__in=[f"{a}_{model}" for a in LEVEL_ACTIONS[level]],
            )
        )
    return perms


@transaction.atomic
def set_access(target, *, actor, role: str | None, levels: dict, active: bool) -> None:
    """
    Make `target`'s access exactly this: the role's baseline, plus
    whatever in `levels` goes beyond it. Levels at or below the role's
    are simply covered by the role.
    """
    check_can_manage(actor, target)
    if role is not None and role not in ROLES:
        raise AccessError(f"Unknown role {role!r}.")
    before = describe(target)

    target.groups.remove(*Group.objects.filter(name__in=ROLES))
    if role:
        target.groups.add(Group.objects.get(name=role))
    base = role_levels(role)
    extras = []
    for area in AREAS:
        wanted = min(int(levels.get(area.key, NONE)), area.max_level)
        if wanted > base[area.key]:
            extras += _permissions_for(area, wanted)
    target.user_permissions.set(extras)
    target.is_active = active
    target.save(update_fields=["is_active"])

    after = describe(target)
    if after != before:
        record_change(
            actor=actor,
            action="staff_access_changed",
            resource_type="auth.user",
            resource_id=target.pk,
            before=_summary(before),
            after=_summary(after),
            source="admin",
        )


def _summary(state: dict) -> dict:
    names = dict(LEVELS)
    return {
        "role": state["role"],
        "active": state["active"],
        "access": {k: names[v] for k, v in state["levels"].items() if v},
    }
