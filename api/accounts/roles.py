"""
Seeds the initial staff roles as Django Groups with explicit
permissions (Section 14: permission-based access, not one generic
"admin" role). Runs on every `migrate` via the post_migrate signal
(see apps.py) rather than a data migration -- a data migration would
run mid-migration, before every app's database schema (in particular
contenttypes) has settled into its final shape, which is fragile. By
post_migrate time, all schema migrations across all apps are already
fully applied, so this is safe and idempotent to re-run.

Only roles grounded in what actually exists today -- see
docs/decisions/0005-rbac-and-audit-foundation.md for why these two and
not more.
"""

from django.apps import apps as app_registry
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission


def _ensure_permissions_exist(app_labels):
    for app_label in app_labels:
        create_permissions(app_registry.get_app_config(app_label), verbosity=0)


def _permissions(app_label, model, actions):
    return Permission.objects.filter(
        content_type__app_label=app_label,
        content_type__model=model,
        codename__in=[f"{action}_{model}" for action in actions],
    )


def seed_roles(sender, **kwargs):
    _ensure_permissions_exist(
        [
            "transportation",
            "notifications",
            "audit",
            "passengers",
            "drivers",
            "vehicles",
            "trips",
            "documents",
            "payers",
            "billing",
            "claims",
            "compliance",
            "riders",
            "contact",
            "intake",
        ]
    )

    dispatcher, _ = Group.objects.get_or_create(name="Dispatcher")
    dispatcher.permissions.set(
        list(_permissions("transportation", "triprequest", ["view", "change"]))
        + list(_permissions("passengers", "passenger", ["view", "change", "add"]))
        # Dispatchers need to see who/what is available, not edit HR/fleet
        # records -- that stays with Administrator (least privilege).
        + list(_permissions("drivers", "driver", ["view"]))
        + list(_permissions("drivers", "drivercredential", ["view"]))
        + list(_permissions("vehicles", "vehicle", ["view"]))
        # Daily checks are the driver app's proof: dispatch reads them to
        # reassign trips; only an Administrator marks a failed one fixed.
        + list(_permissions("vehicles", "vehiclecheck", ["view"]))
        + list(_permissions("documents", "document", ["view"]))
        # Same least-privilege reasoning as drivers/vehicles: payer
        # records are administrative, not day-to-day dispatch work.
        + list(_permissions("payers", "payer", ["view"]))
        + list(_permissions("compliance", "compliancerecord", ["view"]))
        # Scheduling trips IS dispatch work -- full access here.
        + list(_permissions("trips", "trip", ["view", "change", "add"]))
        # Repeating rides are scheduling too (ADR 0035).
        + list(_permissions("trips", "standingorder", ["view", "change", "add"]))
        + list(_permissions("trips", "standingorderskip", ["view", "change", "add", "delete"]))
        # Importing order files is scheduling too (ADR 0038).
        + list(_permissions("intake", "importbatch", ["view", "change", "add"]))
        + list(_permissions("intake", "mappingtemplate", ["view", "change"]))
        # Logging a declined broker offer is part of answering the offer.
        + list(_permissions("trips", "turndown", ["view", "change", "add"]))
        # The driver app's timeline: proof of service, so read-only for
        # everyone -- only the driver app writes it (ADR 0029).
        + list(_permissions("trips", "tripevent", ["view"]))
        # Dispatchers answer riders' calls about their online accounts.
        + list(_permissions("riders", "rideraccount", ["view"]))
        # Answering the website's messages is front-desk work.
        + list(_permissions("contact", "contactmessage", ["view", "change"]))
    )

    administrator, _ = Group.objects.get_or_create(name="Administrator")
    administrator.permissions.set(
        list(_permissions("transportation", "triprequest", ["view", "change", "add", "delete"]))
        + list(_permissions("passengers", "passenger", ["view", "change", "add", "delete"]))
        + list(_permissions("drivers", "driver", ["view", "change", "add", "delete"]))
        + list(_permissions("drivers", "drivercredential", ["view", "change", "add", "delete"]))
        + list(_permissions("vehicles", "vehicle", ["view", "change", "add", "delete"]))
        + list(_permissions("vehicles", "vehiclecheck", ["view", "change"]))
        + list(_permissions("trips", "trip", ["view", "change", "add", "delete"]))
        + list(_permissions("trips", "standingorder", ["view", "change", "add", "delete"]))
        + list(_permissions("trips", "standingorderskip", ["view", "change", "add", "delete"]))
        + list(_permissions("intake", "importbatch", ["view", "change", "add"]))
        + list(_permissions("intake", "mappingtemplate", ["view", "change", "delete"]))
        + list(_permissions("trips", "turndown", ["view", "change", "add", "delete"]))
        + list(_permissions("trips", "tripevent", ["view"]))
        + list(_permissions("riders", "rideraccount", ["view", "change", "add", "delete"]))
        + list(_permissions("contact", "contactmessage", ["view", "change", "delete"]))
        + list(_permissions("documents", "document", ["view", "change", "add", "delete"]))
        + list(_permissions("payers", "payer", ["view", "change", "add", "delete"]))
        # Billing is administrative/financial -- Administrator only, no
        # confirmed day-to-day dispatch need (least privilege, same
        # reasoning ADR 0007 applied to Driver/Vehicle edit access).
        + list(_permissions("billing", "invoice", ["view", "change", "add", "delete"]))
        # Rates and pay are money: Administrator only (ADR 0039).
        + list(_permissions("billing", "ratecard", ["view", "change", "add", "delete"]))
        + list(_permissions("billing", "driverpayplan", ["view", "change", "add", "delete"]))
        + list(_permissions("claims", "claim", ["view", "change", "add", "delete"]))
        # TMHP electronic claims (ADR 0040): files and responses are records, never edited.
        + list(_permissions("claims", "claimsettings", ["view", "change"]))
        + list(_permissions("claims", "claimfile", ["view"]))
        + list(_permissions("claims", "responsefile", ["view", "add"]))
        + list(
            _permissions(
                "compliance", "compliancerecord", ["view", "change", "add", "delete"]
            )
        )
        + list(_permissions("notifications", "notificationlog", ["view"]))
        + list(_permissions("audit", "auditlog", ["view"]))
    )
