# Sets the EIN on the existing (singleton) Organization record from a
# confirmed fact in docs/business-decisions-log.md -- not invented
# here, just recorded. A separate data migration from 0002's initial
# seed since that record already exists in production; this only
# updates it, it doesn't create a new one.

from django.db import migrations


EIN = "93-312-2590"


def seed_ein(apps, schema_editor):
    Organization = apps.get_model("organization", "Organization")
    Organization.objects.update(ein=EIN)


def unseed_ein(apps, schema_editor):
    Organization = apps.get_model("organization", "Organization")
    Organization.objects.filter(ein=EIN).update(ein="")


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0003_organization_ein"),
    ]

    operations = [
        migrations.RunPython(seed_ein, unseed_ein),
    ]
