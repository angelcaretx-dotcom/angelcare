"""
Unfold-styled versions of the admins that ship with Django and
django-otp (users, groups, authenticators). Registered in
AngelCareAdminConfig.ready(), after autodiscovery has registered the
originals -- this module is imported before django.contrib.auth's
admin, so it can't swap them itself.
"""

from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django_otp.plugins.otp_static.admin import StaticDeviceAdmin as BaseStaticDeviceAdmin
from django_otp.plugins.otp_totp.admin import TOTPDeviceAdmin as BaseTOTPDeviceAdmin
from unfold.admin import TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .admin_base import AngelCareModelAdmin


class UserAdmin(BaseUserAdmin, AngelCareModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


class GroupAdmin(BaseGroupAdmin, AngelCareModelAdmin):
    pass


class TOTPDeviceAdmin(BaseTOTPDeviceAdmin, AngelCareModelAdmin):
    pass


def _static_token_inline():
    from django_otp.plugins.otp_static.models import StaticToken

    class StaticTokenInline(TabularInline):
        model = StaticToken
        extra = 0

    return StaticTokenInline


class StaticDeviceAdmin(BaseStaticDeviceAdmin, AngelCareModelAdmin):
    inlines = [_static_token_inline()]


def restyle_builtin_admins(site):
    from django.contrib.auth.models import Group, User
    from django_otp.plugins.otp_static.models import StaticDevice
    from django_otp.plugins.otp_totp.models import TOTPDevice

    for model, admin_class in (
        (User, UserAdmin),
        (Group, GroupAdmin),
        (TOTPDevice, TOTPDeviceAdmin),
        (StaticDevice, StaticDeviceAdmin),
    ):
        if site.is_registered(model):
            site.unregister(model)
        site.register(model, admin_class)
