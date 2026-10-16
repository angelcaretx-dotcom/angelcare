from django.conf import settings
from django.db import models


class StaffOnboarding(models.Model):
    """
    A staff account created through "Add staff member" (ADR 0025), until
    its owner has replaced the temporary password they were handed.

    Deliberately small: the account itself is a normal auth.User with a
    role Group; this row only exists to force that first password
    change (accounts.middleware.StaffSetupMiddleware). Authenticator
    setup needs no flag -- "has no confirmed authenticator" is checked
    directly.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="onboarding"
    )
    must_change_password = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        state = "temporary password" if self.must_change_password else "set up"
        return f"{self.user} ({state})"
