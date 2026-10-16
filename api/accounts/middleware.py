"""
First-login gate for staff (ADR 0025). Once a staff member is fully
logged in (password + second factor), they can't use the admin until:

1. they've replaced a temporary password they were handed
   (accounts.onboarding), and then
2. they have an authenticator app of their own -- so nobody keeps
   working indefinitely on single-use recovery or backup codes
   (ADR 0024), whether newly added or recovering from a lost phone.

Must come after AuthenticationMiddleware, OTPMiddleware (is_verified)
and MessageMiddleware.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .mfa import authenticators
from .onboarding import must_change_password


class StaffSetupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect_to = self._required_step(request)
        if redirect_to:
            return redirect(redirect_to)
        return self.get_response(request)

    def _required_step(self, request):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated and user.is_staff):
            return None
        if not (hasattr(user, "is_verified") and user.is_verified()):
            return None  # not past MFA yet: the admin's own login handles it
        path = request.path
        if not path.startswith(reverse("admin:index")):
            return None

        always_allowed = {reverse("admin:logout"), reverse("admin:jsi18n")}
        if path in always_allowed:
            return None

        if must_change_password(user):
            step = reverse("admin:password_change")
            if path in {step, reverse("admin:password_change_done")}:
                return None
            if request.method == "GET":
                messages.info(request, "Welcome! First, replace the temporary password you were given.")
            return step

        if not authenticators(user).exists():
            step = reverse("admin:security_add_authenticator")
            if path == step:
                return None
            if request.method == "GET":
                messages.info(
                    request,
                    "Set up your authenticator app now. Backup and recovery codes "
                    "only work once each.",
                )
            return step
        return None
