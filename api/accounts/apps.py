from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from . import roles

        post_migrate.connect(roles.seed_roles, sender=self)

        self._enforce_admin_mfa()

    def _enforce_admin_mfa(self):
        """
        Require a verified OTP (TOTP) device to use /admin/ at all --
        Section 13's "MFA capability" requirement. Applied to every
        staff account, not just superusers, since Dispatcher accounts
        will eventually hold real staff logins too.

        Uses django-otp's documented in-place technique (swap the
        existing admin.site's class) rather than a second AdminSite
        instance, so none of the project's existing admin.py files
        (which all register against the default `admin.site`) need to
        change. See docs/decisions/0014-staff-mfa.md for the bootstrap
        process -- you can't enroll your first device through an admin
        page that itself requires a verified device, so the first
        device is created via `manage.py bootstrap_totp <username>`.
        """
        from django.contrib import admin
        from django_otp.admin import OTPAdminSite

        admin.site.__class__ = OTPAdminSite
