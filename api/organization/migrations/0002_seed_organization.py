# Seeds the single Organization record from confirmed facts in
# docs/business-decisions-log.md -- not invented here, just recorded.

from django.db import migrations


ORGANIZATION_FIELDS = {
    "legal_name": "AngelCare Transit",
    "phone": "817-766-9228",
    "email": "angelcaretx@gmail.com",
    "service_area": "State of Texas",
}


def seed_organization(apps, schema_editor):
    Organization = apps.get_model("organization", "Organization")
    if not Organization.objects.exists():
        Organization.objects.create(**ORGANIZATION_FIELDS)


def unseed_organization(apps, schema_editor):
    Organization = apps.get_model("organization", "Organization")
    Organization.objects.filter(legal_name=ORGANIZATION_FIELDS["legal_name"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_organization, unseed_organization),
    ]
