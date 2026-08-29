from django_otp.admin import OTPAdminAuthenticationForm
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
    This project only issues TOTPDevices, which don't need a device
    picker or challenge round-trip -- entering the code from an
    authenticator app alongside username/password in one submission is
    enough (see django_otp.forms.OTPAuthenticationFormMixin.clean_otp,
    which falls back to trying all of the user's devices when
    otp_device is blank).
    """

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
