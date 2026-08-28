"""
Test helper for staff MFA (ADR 0014).

`admin.site` is an OTPAdminSite (see accounts/apps.py): every /admin/
request requires `request.user.is_verified()`, which normally only
becomes true after a real TOTP challenge during login. Tests across the
project that exercise /admin/ views need an equivalent shortcut so they
keep testing what they were written to test (permissions, workflows)
rather than re-deriving a TOTP code every time.

This mirrors django-otp's own documented test pattern: stash a confirmed
device's persistent_id in the session, exactly what the real login flow
does after a successful OTP challenge. It does not weaken the real
enforcement in accounts/apps.py -- it satisfies it the same way a real
login would.
"""

from django.contrib.auth import get_user_model
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

User = get_user_model()


def login_with_otp(client, **credentials):
    """
    Drop-in replacement for `client.login(**credentials)` that also
    satisfies django-otp's `is_verified()` check, for tests that need to
    reach /admin/. Returns whatever `client.login()` returned.
    """
    logged_in = client.login(**credentials)
    if logged_in and "username" in credentials:
        user = User.objects.get(username=credentials["username"])
        device, created = TOTPDevice.objects.get_or_create(
            user=user, name="test-device", defaults={"confirmed": True}
        )
        if not created and not device.confirmed:
            device.confirmed = True
            device.save(update_fields=["confirmed"])

        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
    return logged_in
