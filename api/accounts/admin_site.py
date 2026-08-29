"""
Combines Unfold's modern admin UI (ADR 0016) with django-otp's MFA
enforcement (ADR 0014) into one AdminSite, registered as Django's real
default admin site via the documented AdminConfig.default_site
mechanism -- see django.contrib.admin.apps.AdminConfig and
django.contrib.admin.sites.DefaultAdminSite (a LazyObject that builds
`admin.site` from whatever class that setting points to).

ADR 0014 originally used a simpler after-the-fact monkey-patch
(`admin.site.__class__ = OTPAdminSite`) in accounts/apps.py. That
worked because OTPAdminSite.__init__() does nothing beyond calling
super().__init__() -- there was no real state to set up, so swapping
the class after Django had already built a vanilla AdminSite instance
was harmless. UnfoldAdminSite.__init__() is not like that: it reads
the UNFOLD settings to decide the login form, and that needs to run
for the actual instance django.contrib.admin.sites.site ends up being
-- not be retrofitted onto an already-constructed object. Using
AdminConfig.default_site instead makes Django build the *correct*
class from the start, for both packages at once.
"""

from django_otp.admin import OTPAdminSite
from unfold.sites import UnfoldAdminSite

from .forms import UnfoldOTPAdminAuthenticationForm


class AngelCareAdminSite(UnfoldAdminSite, OTPAdminSite):
    """
    MRO: AngelCareAdminSite -> UnfoldAdminSite -> OTPAdminSite ->
    AdminSite -> object. Neither UnfoldAdminSite nor AdminSite define
    has_permission()/login_form/login_template, so those resolve
    straight through to OTPAdminSite -- MFA enforcement (require
    request.user.is_verified()) is preserved exactly as ADR 0014
    established it. Everything else (each_context, index, search,
    sidebar navigation, branding) comes from UnfoldAdminSite, since
    OTPAdminSite doesn't touch any of that.

    Two explicit overrides on top of that MRO:

    - `login_form`: OTPAdminSite's own OTPAdminAuthenticationForm
      would otherwise be used unstyled (it doesn't know about
      Unfold's Tailwind input classes, since Unfold has no django-otp
      awareness and vice versa). UnfoldOTPAdminAuthenticationForm
      (accounts/forms.py) is the same form with Unfold's styling
      applied to all its fields, including the otp_token field
      OTPAdminSite adds.
    - `login_template`: OTPAdminSite points this at its own bare,
      unstyled template ('otp/admin111/login.html'), which would
      otherwise win over Unfold's branded one via MRO. Resetting it to
      None here falls back to Django's default resolution
      ("admin/login.html"), which resolves to our own
      templates/admin/login.html override -- Unfold's own login page
      layout plus the one added otp_token field.
    """

    login_form = UnfoldOTPAdminAuthenticationForm
    login_template = None
