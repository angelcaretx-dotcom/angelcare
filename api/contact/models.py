import uuid

from django.conf import settings
from django.db import models


class MessageStatus(models.TextChoices):
    NEW = "new", "New"
    HANDLED = "handled", "Handled"


class ContactMessage(models.Model):
    """
    A message from the website's contact form (ADR 0033). It replaces the
    published email address: only people who confirmed their own email
    with a code can send one. Staff reply from their own mailbox.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField(help_text="Confirmed with an emailed code.")
    phone = models.CharField(max_length=20, blank=True)
    message = models.TextField(max_length=5000)
    status = models.CharField(max_length=20, choices=MessageStatus.choices, default=MessageStatus.NEW)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "website message"

    def __str__(self) -> str:
        return f"{self.name} ({self.created_at:%b %d})"

    @property
    def reference(self) -> str:
        return f"#{str(self.id)[:8].upper()}"
