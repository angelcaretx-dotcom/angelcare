from django import forms
from django_otp import devices_for_user
from django_otp.admin import OTPAdminAuthenticationForm
from django_otp.plugins.otp_totp.models import TOTPDevice
from unfold.widgets import BASE_INPUT_CLASSES


class UnfoldOTPAdminAuthenticationForm(OTPAdminAuthenticationForm):
    """
    OTPAdminAuthenticationForm (django-otp) styled to match Unfold's
    Tailwind admin theme (ADR 0016). Unfold has no django-otp
    awareness and vice versa -- neither package styles the other's
    fields -- so this applies the same class-injection trick Unfold's
    own unfold.forms.AuthenticationForm uses on username/password,
    extended to the otp_token field OTPAdminAuthenticationForm adds.

    otp_device and otp_challenge (also added by the parent form) are
    deliberately left unstyled/unrendered by our login template: they
    only matter for challenge-response device types (e.g. SMS OTP).
    Neither TOTP codes nor backup codes need a device picker or a
    challenge round-trip, so the login page shows a single code box and
    `_verify_token` below picks the device from what was typed.

    That override is required, not cosmetic: django-otp 1.7.4 removed
    the old fallback of trying every device when `otp_device` is blank
    (it now raises "Please select a device."), which silently broke
    every real staff login after the 2026-10-15 upgrade. See
    accounts.tests.RealLoginFormTests.
    """

    def _verify_token(self, user, token, device=None):
        if device is not None:
            return super()._verify_token(user, token, device)

        # Route by token shape rather than trying every device, keeping
        # django-otp's reason for removing the fallback: a mistyped
        # authenticator code must not count as a failed attempt against
        # (and throttle) the user's backup codes, or vice versa.
        token = token.strip()
        confirmed = list(devices_for_user(user, confirmed=True))
        if token.isdigit() and len(token) == 6:
            candidates = [d for d in confirmed if isinstance(d, TOTPDevice)]
        else:
            candidates = [d for d in confirmed if not isinstance(d, TOTPDevice)]

        error = forms.ValidationError(
            self.otp_error_messages["invalid_token"], code="invalid_token"
        )
        for candidate in candidates:
            try:
                return super()._verify_token(user, token, candidate)
            except forms.ValidationError as exc:
                error = exc
        raise error

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        css_class = " ".join(BASE_INPUT_CLASSES)
        self.fields["username"].widget.attrs["class"] = css_class
        self.fields["username"].widget.attrs["autofocus"] = ""
        self.fields["password"].widget.attrs["class"] = css_class
        self.fields["otp_token"].widget.attrs["class"] = css_class
        self.fields["otp_token"].widget.attrs["autocomplete"] = "one-time-code"
        self.fields["otp_token"].widget.attrs["inputmode"] = "numeric"
        self.fields["otp_token"].label = "Authentication code"
