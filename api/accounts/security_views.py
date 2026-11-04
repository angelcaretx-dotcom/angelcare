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
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from unfold.widgets import BASE_INPUT_CLASSES, UnfoldBooleanSwitchWidget

from . import access, mfa, onboarding

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


class AddStaffForm(forms.Form):
    email = forms.EmailField(
        label="Email address (this is their username)",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "off"}),
    )
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    role = forms.ChoiceField(
        choices=[(r, r) for r in onboarding.ROLES],
        help_text="Dispatcher: trip requests, trips, passengers, day board. Administrator: "
        "everything except owner-only tasks, including billing, claims and staff access. "
        "Extra access can be granted per person afterwards.",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
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
    can_manage = access.can_manage_access(user)
    if can_manage:
        # Switched-off accounts stay listed, so access can be restored.
        others = get_user_model().objects.filter(is_staff=True).exclude(pk=user.pk)
        for other in others.order_by("-is_active", "username"):
            staff.append(
                {
                    "user": other,
                    "role": "Owner" if other.is_superuser else (access.role_of(other) or "No role"),
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
        can_manage=can_manage,
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


class StaffAccessForm(PasswordConfirmForm):
    """ADR 0030: role + per-area extras + on/off, confirmed with the admin's password."""

    def __init__(self, actor, target, *args, **kwargs):
        super().__init__(actor, *args, **kwargs)
        current = access.describe(target)
        self.fields["active"] = forms.BooleanField(
            label="Can sign in",
            required=False,
            initial=current["active"],
            help_text="Switch off to block this person immediately (e.g. they've left).",
            widget=UnfoldBooleanSwitchWidget,
        )
        self.fields["role"] = forms.ChoiceField(
            label="Role",
            choices=[("", "No role (only the access below)")] + [(r, r) for r in onboarding.ROLES],
            required=False,
            initial=current["role"] or "",
            widget=forms.Select(attrs={"class": INPUT_CLASS}),
        )
        self.area_rows = []
        for area in access.AREAS:
            name = f"area_{area.key}"
            self.fields[name] = forms.TypedChoiceField(
                label=area.label,
                choices=[(v, l) for v, l in access.LEVELS if v <= area.max_level],
                coerce=int,
                initial=current["levels"][area.key],
                widget=forms.Select(attrs={"class": INPUT_CLASS}),
            )
            self.area_rows.append((self[name], dict(access.LEVELS)[current["role_levels"][area.key]]))
        # Password last, after the choices it confirms.
        self.fields["password"] = self.fields.pop("password")

    def access_kwargs(self):
        data = self.cleaned_data
        return {
            "role": data["role"] or None,
            "active": data["active"],
            "levels": {a.key: data[f"area_{a.key}"] for a in access.AREAS},
        }


def staff_access(site, request, user_id):
    target = get_object_or_404(get_user_model(), pk=user_id, is_staff=True)
    try:
        access.check_can_manage(request.user, target)
    except access.AccessError as exc:
        if not access.can_manage_access(request.user):
            raise PermissionDenied from exc
        messages.error(request, str(exc))
        return redirect("admin:security")
    form = StaffAccessForm(request.user, target, request.POST or None)
    if request.method == "POST" and form.is_valid():
        access.set_access(target, actor=request.user, **form.access_kwargs())
        messages.success(request, f"Access for {target.get_full_name() or target.get_username()} saved.")
        return redirect("admin:security")
    return _render(
        site,
        request,
        "access.html",
        f"Access for {target.get_full_name() or target.get_username()}",
        form=form,
        target=target,
    )


def add_staff_member(site, request):
    if not access.can_manage_access(request.user):
        raise PermissionDenied
    form = AddStaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user, password, codes = onboarding.create_staff_member(actor=request.user, **form.cleaned_data)
        except onboarding.OnboardingError as exc:
            form.add_error(None, str(exc))
        else:
            return _render(
                site,
                request,
                "handover.html",
                f"Handover sheet for {user.get_full_name() or user.get_username()}",
                new_user=user,
                role=form.cleaned_data["role"],
                password=password,
                codes=codes,
                login_url=settings.SITE_URL,
            )
    return _render(site, request, "add_staff.html", "Add a staff member", form=form)


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
        path("security/staff/add/", view(add_staff_member), name="security_add_staff"),
        path("security/staff/<int:user_id>/access/", view(staff_access), name="security_staff_access"),
        path(
            "security/recovery/<int:user_id>/",
            view(issue_recovery_codes),
            name="security_issue_recovery_codes",
        ),
    ]
