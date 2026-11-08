import uuid

from django.conf import settings
from django.db import models


class DeletedFrom(models.TextChoices):
    ADMIN = "admin", "Admin"
    RIDER = "rider", "Rider account"
    FACILITY_PORTAL = "facility_portal", "Facility portal"
    DRIVER_APP = "driver_app", "Driver app"


class TrashEntry(models.Model):
    """
    One delete, kept whole until an admin restores it or deletes it for
    good (ADR 0046). `records` is every row the delete removed, in
    Django's JSON serialization: the record itself first, then its parts
    (a facility's contacts) and the unbilled trips that went with it.
    `relinks` are records that pointed at them and were left with a
    blank link; a restore points them back.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_label = models.CharField("record type", max_length=100, db_index=True)
    object_id = models.CharField(max_length=64)
    label = models.CharField("record", max_length=300)
    records = models.JSONField()
    relinks = models.JSONField(default=list, blank=True)
    contents = models.JSONField(default=dict, blank=True, help_text="How many records of each type it holds.")
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    deleted_by_name = models.CharField(max_length=150, blank=True, help_text="Kept if that login is removed later.")
    deleted_from = models.CharField(max_length=20, choices=DeletedFrom.choices, default=DeletedFrom.ADMIN)
    deleted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-deleted_at"]
        verbose_name = "trash item"
        verbose_name_plural = "trash"

    def __str__(self) -> str:
        return f"{self.type_name}: {self.label}"

    @property
    def type_name(self) -> str:
        from django.apps import apps

        try:
            return str(apps.get_model(self.model_label)._meta.verbose_name).capitalize()
        except LookupError:
            return self.model_label
