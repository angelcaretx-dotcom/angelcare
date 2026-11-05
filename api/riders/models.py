import uuid

from django.conf import settings
from django.db import models

from transportation.addresses import compose


class RiderAccount(models.Model):
    """
    An online account for someone who books rides on the website
    (ADR 0031). Optional: booking never requires it. Created on the
    first booking from a new email, usable only after the owner of that
    inbox follows the set-password link.

    It shows the requests made with this email -- nothing else. It is
    deliberately NOT linked to a passengers.Passenger (ADR 0006): the
    person who owns the email may be a caregiver, not the rider.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rider_account"
    )
    email = models.EmailField(unique=True, help_text="Always stored lowercase.")
    full_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    mobility_notes = models.TextField(blank=True)

    invited_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the owner of the email first set a password. Empty = never used.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.email


class SavedAddress(models.Model):
    MAX_PER_ACCOUNT = 10

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(RiderAccount, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=60, blank=True, help_text="e.g. Home, Dialysis center")
    # One composed line; saved since ADR 0032 with its parts too, so the
    # booking form can fill street, city, state and ZIP separately.
    address = models.TextField()
    street = models.CharField(max_length=200, blank=True)
    unit = models.CharField("apt / suite", max_length=50, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    zip = models.CharField("ZIP", max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "saved addresses"

    def __str__(self) -> str:
        return self.label or self.address[:40]

    def save(self, *args, **kwargs):
        if self.street:
            self.address = compose(self.street, self.unit, self.city, self.state, self.zip)
        super().save(*args, **kwargs)
