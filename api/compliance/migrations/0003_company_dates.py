"""
The company's own dates, from the owner (roadmap Phase 27, confirmed
2026-11-05; ADR 0036), so the daily reminders cover them. Days the owner
gave only as a month are noted on the record for staff to correct.
Skipped for any record that already exists by name.
"""

from datetime import date

from django.db import migrations

RECORDS = [
    {
        "name": "SAM.gov entity registration",
        "record_type": "registration",
        "issuing_authority": "U.S. General Services Administration (SAM.gov)",
        "expiration_date": date(2027, 10, 26),
        "notes": "Renew on SAM.gov before it lapses.",
    },
    {
        "name": "Texas Medicaid provider enrollment (PEMS) revalidation",
        "record_type": "registration",
        "issuing_authority": "TMHP / Texas HHSC (PEMS)",
        "expiration_date": date(2028, 12, 31),
        "notes": "Owner gave the month (December 2028); the day is a placeholder: correct it from PEMS.",
    },
    {
        "name": "Business insurance",
        "record_type": "insurance_policy",
        "issuing_authority": "",
        "expiration_date": date(2027, 10, 1),
        "notes": "Owner gave the month (October 2027); the day is a placeholder: correct it, "
        "and add the insurer and policy number, from the policy.",
    },
    {
        "name": "City of Fort Worth vehicle-for-hire license",
        "record_type": "license",
        "issuing_authority": "City of Fort Worth",
        "expiration_date": None,
        "notes": "Expiration date not known yet: add it from the license so reminders cover it.",
    },
]


def add_company_dates(apps, schema_editor):
    ComplianceRecord = apps.get_model("compliance", "ComplianceRecord")
    for record in RECORDS:
        if not ComplianceRecord.objects.filter(name=record["name"]).exists():
            ComplianceRecord.objects.create(**record)


class Migration(migrations.Migration):
    dependencies = [("compliance", "0002_expiryreminder")]

    operations = [migrations.RunPython(add_company_dates, migrations.RunPython.noop)]
