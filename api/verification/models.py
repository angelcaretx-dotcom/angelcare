import uuid

from django.db import models


class Purpose(models.TextChoices):
    BOOKING = "booking", "Ride booking"
    CONTACT = "contact", "Contact form"
    FACILITY_SIGN_IN = "facility_sign_in", "Facility portal sign-in"


class EmailVerification(models.Model):
    """
    A one-time code emailed to someone using a website form (ADR 0033).

    Entering the code proves they can read that inbox, which a spam bot
    can't. The reward is a token that lets that email address submit the
    booking and contact forms for a while. Only hashes are stored: a
    database leak gives away neither the codes nor the tokens.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True, help_text="Always stored lowercase.")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(help_text="When the code stops working.")
    verified_at = models.DateTimeField(null=True, blank=True)
    token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email} ({self.get_purpose_display()})"
