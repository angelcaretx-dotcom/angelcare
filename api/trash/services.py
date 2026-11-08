"""
The trash bin (ADR 0046). A delete made by a person, in the admin or on
the rider pages, is kept whole here instead of being lost:

- `plan(objs)`: what the delete takes with it. That's the record's own
  parts (what the database already removes with it), plus the trips and
  other records that depend on it and would otherwise block it (a
  passenger's trips, those trips' steps). A delete that would take a
  billed record (an invoice or claim) is refused: set the record Inactive.
- `send(obj, ...)`: snapshot all of it into a TrashEntry, then delete.
- `restore(entry, ...)`: put every row back with its id, and point
  records that lost their link back at it.
- `purge(entry, ...)`: forget it for good.

Each step is in the audit log and Django's admin history.
"""

import json
from dataclasses import dataclass, field

from django.apps import apps
from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.contrib.admin.utils import NestedObjects
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.db import DEFAULT_DB_ALIAS, IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError, RestrictedError

from audit.services import record_change

from .models import DeletedFrom, TrashEntry

# Records kept by the trash. Logins, MFA devices and the logs themselves
# are not: switch logins off instead.
TRASHABLE_APPS = {
    "billing", "claims", "compliance", "contact", "documents", "drivers", "facilities", "intake",
    "passengers", "payers", "riders", "transportation", "trips", "vehicles",
}
# A delete that would reach any of these is refused: money has moved, or
# a claim was filed, so the record must stay (set it Inactive instead).
BLOCKING = {"billing.invoice", "claims.claim", "claims.claimfile", "claims.responsefile", "claims.claimsettings"}
# Records nobody deletes on their own (a driver's proof, a log line) but
# that go to the trash with their parent: no delete permission needed.
TAKEN_ALONG = {
    "trips.tripevent", "vehicles.vehiclecheck", "trips.standingorderskip", "intake.importrow",
    "facilities.facilityactivity", "riders.savedaddress",
}


class TrashError(Exception):
    pass


def trashable(model) -> bool:
    return model._meta.app_label in TRASHABLE_APPS


def can_use_trash(user) -> bool:
    """Superusers and Administrators only, no exceptions (owner, 2026-11-08)."""
    if not (user and user.is_active and user.is_staff):
        return False
    return user.is_superuser or user.groups.filter(name="Administrator").exists()


def _key(obj):
    return obj._meta.label_lower, str(obj.pk)


@dataclass
class Plan:
    objects: list = field(default_factory=list)  # every row the delete removes, roots first
    blocked: list = field(default_factory=list)  # billed records that stop it

    def counts(self) -> dict:
        result = {}
        for obj in self.objects:
            name = str(obj._meta.verbose_name_plural)
            result[name] = result.get(name, 0) + 1
        return result


def plan(roots) -> Plan:
    result, seen, frontier = Plan(), {}, list(roots)
    while frontier:
        collector = NestedObjects(using=DEFAULT_DB_ALIAS)
        collector.collect(frontier)
        for model_objs in collector.data.values():
            for obj in model_objs:
                seen.setdefault(_key(obj), obj)
        frontier = []
        for obj in collector.protected:
            if _key(obj) in seen:
                continue
            if obj._meta.label_lower in BLOCKING:
                if obj not in result.blocked:
                    result.blocked.append(obj)
            else:
                frontier.append(obj)
    result.blocked += [o for o in seen.values() if o._meta.label_lower in BLOCKING and o not in result.blocked]
    roots_keys = [_key(r) for r in roots]
    result.objects = [seen[k] for k in roots_keys if k in seen] + [o for k, o in seen.items() if k not in roots_keys]
    return result


def _relinks(objects) -> list:
    """Records outside the delete that point at it with a link the delete blanks (SET_NULL)."""
    inside = {_key(o) for o in objects}
    found = []
    for obj in objects:
        for rel in obj._meta.related_objects:
            if rel.many_to_many or rel.on_delete is not models.SET_NULL:
                continue
            related = rel.related_model
            pks = [str(pk) for pk in related._base_manager.filter(**{rel.field.name: obj.pk}).values_list("pk", flat=True)
                   if (related._meta.label_lower, str(pk)) not in inside]
            if pks:
                found.append({"model": related._meta.label_lower, "field": rel.field.attname, "pks": pks,
                              "target": str(obj.pk)})
    return found


def _actor_name(user) -> str:
    return getattr(user, "username", "") or ""


@transaction.atomic
def send(obj, *, user, deleted_from: str = DeletedFrom.ADMIN) -> TrashEntry:
    """Snapshot `obj` and everything its delete takes, then delete it. Raises TrashError if a billed record blocks it."""
    the_plan = plan([obj])
    if the_plan.blocked:
        raise TrashError(f"{obj} can't be deleted: billed records depend on it "
                         f"({', '.join(str(b) for b in the_plan.blocked[:5])}). Set it Inactive instead.")
    objects = the_plan.objects
    entry = TrashEntry.objects.create(
        model_label=obj._meta.label_lower, object_id=str(obj.pk), label=str(obj)[:300],
        records=json.loads(serializers.serialize("json", objects)), relinks=_relinks(objects),
        contents=the_plan.counts(), deleted_by=user if getattr(user, "pk", None) else None,
        deleted_by_name=_actor_name(user), deleted_from=deleted_from,
    )
    _delete_all(objects)
    record_change(actor=user if getattr(user, "pk", None) else None, action="deleted",
                  resource_type=obj._meta.object_name, resource_id=obj.pk,
                  after={"trash": str(entry.pk), "with": entry.contents}, source=deleted_from)
    return entry


def _delete_all(objects) -> None:
    """Delete every row; ones others still protect wait for a later round (their dependents go first)."""
    remaining = list(reversed(objects))
    while remaining:
        left = []
        for obj in remaining:
            try:
                with transaction.atomic():
                    type(obj)._base_manager.filter(pk=obj.pk).delete()
            except (ProtectedError, RestrictedError):
                left.append(obj)
        if len(left) == len(remaining):
            raise TrashError("These records still depend on each other and can't be deleted: "
                             + ", ".join(str(o) for o in left[:5]))
        remaining = left


def restore(entry: TrashEntry, *, user) -> None:
    """Put everything back, ids and links included. Raises TrashError when it can't."""
    try:
        with transaction.atomic():
            for item in serializers.deserialize("json", json.dumps(entry.records), ignorenonexistent=True):
                model = type(item.object)
                if model._base_manager.filter(pk=item.object.pk).exists():
                    raise TrashError(f"{model._meta.verbose_name.capitalize()} “{item.object}” is already back.")
                item.save()
            for link in entry.relinks:
                model = apps.get_model(link["model"])
                model._base_manager.filter(pk__in=link["pks"], **{f"{link['field']}__isnull": True}).update(
                    **{link["field"]: link["target"]})
            _log(user, entry, ADDITION, "Restored from the trash.")
            record_change(actor=user, action="restored", resource_type=entry.model_label.split(".")[1],
                          resource_id=entry.object_id, after={"trash": str(entry.pk), "with": entry.contents},
                          source="admin")
            entry.delete()
    except IntegrityError:
        raise TrashError("It can't go back as it was: a record it links to is gone, or a record with the same "
                         "number now exists. Nothing was changed.") from None
    except (serializers.base.DeserializationError, LookupError):
        raise TrashError("It was saved by an older version of the system and can't be read back. "
                         "Nothing was changed.") from None


@transaction.atomic
def purge(entry: TrashEntry, *, user) -> None:
    """Gone for good. The admin history line and the audit log keep who did it and when."""
    _log(user, entry, DELETION, "Permanently deleted from the trash.")
    record_change(actor=user, action="purged", resource_type=entry.model_label.split(".")[1],
                  resource_id=entry.object_id, after={"label": entry.label, "with": entry.contents}, source="admin")
    entry.delete()


def _log(user, entry, flag, message) -> None:
    try:
        content_type = ContentType.objects.get_for_model(apps.get_model(entry.model_label))
    except LookupError:
        content_type = ContentType.objects.get_for_model(TrashEntry)
    LogEntry.objects.create(user=user, content_type=content_type, object_id=entry.object_id,
                            object_repr=entry.label[:200], action_flag=flag, change_message=message)


def still_pointing(entry: TrashEntry) -> list[str]:
    """What still refers to it (blank links a restore would refill): shown before a permanent delete."""
    lines = []
    for link in entry.relinks:
        try:
            model = apps.get_model(link["model"])
        except LookupError:
            continue
        count = model._base_manager.filter(pk__in=link["pks"], **{f"{link['field']}__isnull": True}).count()
        if count:
            lines.append(f"{count} {model._meta.verbose_name_plural} (their link would stay blank for good)")
    return lines
