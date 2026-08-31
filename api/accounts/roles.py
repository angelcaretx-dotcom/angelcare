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
        ]
    )

    dispatcher, _ = Group.objects.get_or_create(name="Dispatcher")
    dispatcher.permissions.set(
        list(_permissions("transportation", "triprequest", ["view", "change"]))
        + list(_permissions("passengers", "passenger", ["view", "change", "add"]))
        # Dispatchers need to see who/what is available, not edit HR/fleet
        # records -- that stays with Administrator (least privilege).
        + list(_permissions("drivers", "driver", ["view"]))
        + list(_permissions("vehicles", "vehicle", ["view"]))
        + list(_permissions("documents", "document", ["view"]))
        # Same least-privilege reasoning as drivers/vehicles: payer
        # records are administrative, not day-to-day dispatch work.
        + list(_permissions("payers", "payer", ["view"]))
        + list(_permissions("compliance", "compliancerecord", ["view"]))
        # Scheduling trips IS dispatch work -- full access here.
        + list(_permissions("trips", "trip", ["view", "change", "add"]))
    )

    administrator, _ = Group.objects.get_or_create(name="Administrator")
    administrator.permissions.set(
        list(_permissions("transportation", "triprequest", ["view", "change", "add", "delete"]))
        + list(_permissions("passengers", "passenger", ["view", "change", "add", "delete"]))
        + list(_permissions("drivers", "driver", ["view", "change", "add", "delete"]))
        + list(_permissions("vehicles", "vehicle", ["view", "change", "add", "delete"]))
        + list(_permissions("trips", "trip", ["view", "change", "add", "delete"]))
        + list(_permissions("documents", "document", ["view", "change", "add", "delete"]))
        + list(_permissions("payers", "payer", ["view", "change", "add", "delete"]))
        # Billing is administrative/financial -- Administrator only, no
        # confirmed day-to-day dispatch need (least privilege, same
        # reasoning ADR 0007 applied to Driver/Vehicle edit access).
        + list(_permissions("billing", "invoice", ["view", "change", "add", "delete"]))
        + list(_permissions("claims", "claim", ["view", "change", "add", "delete"]))
        + list(
            _permissions(
                "compliance", "compliancerecord", ["view", "change", "add", "delete"]
            )
        )
        + list(_permissions("notifications", "notificationlog", ["view"]))
        + list(_permissions("audit", "auditlog", ["view"]))
    )
