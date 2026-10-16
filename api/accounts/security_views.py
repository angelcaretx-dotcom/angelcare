"""
"My security" pages in the admin (ADR 0024). Wired into AngelCareAdminSite
via get_urls(), so every view here sits behind the same login + MFA check
as the rest of /admin/ (admin_view). Logic lives in accounts/mfa.py.

Changes that weaken or reset a second factor (new backup codes, removing
an authenticator, issuing recovery codes) also require the current
password, so an unlocked, unattended browser isn't enough.
"""

import base64

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from unfold.widgets import BASE_INPUT_CLASSES

from . import mfa

INPUT_CLASS = " ".join(BASE_INPUT_CLASSES)


class PasswordConfirmForm(forms.Form):
    password = forms.CharField(
        label="Your password",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "current-password"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("That password isn't right.")
        return password


class AddAuthenticatorForm(forms.Form):
    name = forms.CharField(
        label="Name for this device",
        max_length=64,
        initial="My phone",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    code = forms.CharField(
        label="6-digit code shown in the app",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "one-time-code", "inputmode": "numeric"}
        ),
    )


def _render(site, request, template, title, **context):
    response = TemplateResponse(
        request,
        f"admin/security/{template}",
        {**site.each_context(request), "title": title, **context},
    )
    response["Cache-Control"] = "no-store"  # codes and secrets must never be cached
    return response


def overview(site, request):
    user = request.user
    staff = []
    if user.is_superuser:
        for other in get_user_model().objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk).order_by("username"):
            staff.append(
                {
                    "user": other,
                    "authenticators": mfa.authenticators(other).count(),
                    "backup_codes": mfa.backup_codes_remaining(other),
                }
            )
    return _render(
        site,
        request,
        "overview.html",
        "My security",
        authenticators=mfa.authenticators(user),
        backup_codes=mfa.backup_codes_remaining(user),
        backup_code_count=mfa.BACKUP_CODE_COUNT,
        staff=staff,
    )


def backup_codes(site, request):
    form = PasswordConfirmForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        codes = mfa.generate_backup_codes(request.user, actor=request.user)
        return _render(
            site, request, "codes.html", "Your new backup codes", codes=codes, for_user=request.user
        )
    return _render(
        site,
        request,
        "confirm.html",
        "New backup codes",
        form=form,
        intro=(
            f"This creates {mfa.BACKUP_CODE_COUNT} new single-use backup codes. "
            "Any backup codes you already have will stop working."
        ),
        button="Create backup codes",
    )


def add_authenticator(site, request):
    if request.method == "POST":
        device = mfa.pending_authenticator(request.user)
        form = AddAuthenticatorForm(request.POST)
        if device is not None and form.is_valid():
            try:
                mfa.confirm_authenticator(
                    request.user, code=form.cleaned_data["code"], name=form.cleaned_data["name"]
                )
            except mfa.MfaError as exc:
                form.add_error("code", str(exc))
            else:
                messages.success(request, "Authenticator added. You can use it from your next login.")
                return redirect("admin:security")
        if device is None:
            device = mfa.start_authenticator_enrollment(request.user)
    else:
        device = mfa.start_authenticator_enrollment(request.user)
        form = AddAuthenticatorForm()

    secret = base64.b32encode(device.bin_key).decode("ascii")
    return _render(
        site,
        request,
        "add_authenticator.html",
        "Add an authenticator app",
        form=form,
        qr=mark_safe(mfa.qr_svg(device.config_url)),
        secret=" ".join(secret[i : i + 4] for i in range(0, len(secret), 4)),
    )


def remove_authenticator(site, request, device_id):
    device = get_object_or_404(mfa.authenticators(request.user), pk=device_id)
    form = PasswordConfirmForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            mfa.remove_authenticator(request.user, device.pk)
        except mfa.MfaError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Removed “{device.name}”.")
        return redirect("admin:security")
    return _render(
        site,
        request,
        "confirm.html",
        f"Remove “{device.name}”",
        form=form,
        intro="Codes from this authenticator will stop working immediately.",
        button="Remove authenticator",
        danger=True,
    )


def issue_recovery_codes(site, request, user_id):
    if not request.user.is_superuser:
        raise PermissionDenied
    target = get_object_or_404(get_user_model(), pk=user_id, is_staff=True, is_active=True)
    if target.pk == request.user.pk:
        return redirect("admin:security_backup_codes")
    form = PasswordConfirmForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        codes = mfa.generate_backup_codes(target, actor=request.user)
        return _render(
            site, request, "codes.html", f"Recovery codes for {target.get_username()}", codes=codes, for_user=target
        )
    return _render(
        site,
        request,
        "confirm.html",
        f"Issue recovery codes for {target.get_username()}",
        form=form,
        intro=(
            f"Use this when {target.get_username()} has lost their authenticator and has no "
            f"backup codes. It creates {mfa.BACKUP_CODE_COUNT} new single-use codes for them "
            "and cancels any they had. Give the codes to them in person or by phone, never by "
            "email or chat. They should log in with one, then add a new authenticator on "
            "their My security page. This is recorded in the audit log."
        ),
        button="Issue recovery codes",
    )


def urls(site):
    """URL patterns for AngelCareAdminSite.get_urls()."""
    from django.urls import path

    def view(func):
        return site.admin_view(never_cache(lambda request, *args, **kwargs: func(site, request, *args, **kwargs)))

    return [
        path("security/", view(overview), name="security"),
        path("security/backup-codes/", view(backup_codes), name="security_backup_codes"),
        path("security/authenticators/add/", view(add_authenticator), name="security_add_authenticator"),
        path(
            "security/authenticators/<int:device_id>/remove/",
            view(remove_authenticator),
            name="security_remove_authenticator",
        ),
        path(
            "security/recovery/<int:user_id>/",
            view(issue_recovery_codes),
            name="security_issue_recovery_codes",
        ),
    ]
