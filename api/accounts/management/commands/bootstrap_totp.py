"""
Out-of-band first-device enrollment for staff MFA (ADR 0014).

`admin.site` is an OTPAdminSite (see accounts/apps.py): every /admin/ page,
including any page that could otherwise be used to enroll a TOTP device,
requires an already-verified device to view it. That's a chicken-and-egg
problem for the very first device on a given deployment, so that one device
has to be created outside the web UI -- here, via `manage.py`, which runs
with direct database access and isn't subject to admin auth at all.

Usage:
    python manage.py bootstrap_totp <username>

Creates one confirmed TOTPDevice for the user and prints:
- the otpauth:// URL (config_url), which any TOTP-capable authenticator
  app can import directly if given the raw text, and
- an ASCII QR code encoding the same URL, for scanning off the terminal
  when a direct-import option isn't available.

Safe to re-run: if the user already has a confirmed device, this refuses
to create a second one silently (use --replace to remove and replace it,
e.g. after losing the authenticator device).
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice
import qrcode


class Command(BaseCommand):
    help = "Create the first confirmed TOTP (MFA) device for a staff user."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Username of the staff account to enroll.")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete any existing confirmed TOTP device(s) for this user first "
            "(e.g. the authenticator device was lost).",
        )

    def handle(self, username, replace, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with username {username!r}.") from exc

        if not user.is_staff:
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: {username!r} is not marked is_staff, so this device "
                    "won't be usable to log into /admin/ until that's set."
                )
            )

        existing = TOTPDevice.objects.filter(user=user, confirmed=True)
        if existing.exists():
            if not replace:
                raise CommandError(
                    f"{username!r} already has a confirmed TOTP device. "
                    "Pass --replace to remove it and enroll a new one."
                )
            existing.delete()

        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created a confirmed TOTP device for {username!r}."))
        self.stdout.write("")
        self.stdout.write("Scan this QR code with an authenticator app (Google Authenticator,")
        self.stdout.write("Authy, 1Password, etc.), or import the URL below directly:")
        self.stdout.write("")

        self._print_qr(device.config_url)

        self.stdout.write("")
        self.stdout.write(device.config_url)
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "This URL contains the device's secret key -- treat it like a "
                "password. Don't paste it anywhere it could be logged or persisted."
            )
        )

    def _print_qr(self, data):
        """
        Render `data` as an ASCII QR code, falling back to a plain-text
        note if the current terminal's encoding can't represent the
        block characters qrcode uses (e.g. a Windows console still on
        the legacy cp1252/cp437 codepage rather than UTF-8). Either way
        the otpauth:// URL is printed separately right after this, so
        the command stays fully usable even without a renderable QR.
        """
        import io

        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)

        buffer = io.StringIO()
        qr.print_ascii(out=buffer, tty=False)

        try:
            self.stdout.write(buffer.getvalue())
        except UnicodeEncodeError:
            self.stdout.write(
                self.style.WARNING(
                    "(QR code omitted: this terminal's encoding can't display it. "
                    "Import the URL below directly into your authenticator app instead.)"
                )
            )
