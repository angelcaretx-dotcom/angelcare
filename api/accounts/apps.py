from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from . import roles

        post_migrate.connect(roles.seed_roles, sender=self)


class AngelCareAdminConfig(AdminConfig):
    """
    Registered in INSTALLED_APPS in place of the bare "django.contrib.admin"
    string. `default_site` is Django's documented mechanism for
    swapping in a custom AdminSite class (see
    django.contrib.admin.sites.DefaultAdminSite) -- `admin.site` ends
    up being an instance of AngelCareAdminSite (accounts/admin_site.py),
    which combines Unfold's admin UI with django-otp's MFA enforcement.
    `name` is inherited as "django.contrib.admin" from AdminConfig, so
    Django still recognizes this as configuring that app even though
    the class itself lives here -- this is the same pattern Django's
    own docs demonstrate for AdminConfig.default_site.
    """

    default_site = "accounts.admin_site.AngelCareAdminSite"
