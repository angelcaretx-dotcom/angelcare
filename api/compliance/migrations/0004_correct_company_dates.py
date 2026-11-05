"""
Corrects the company dates entered by 0003 from the owner's verified
records (2026-11-05, ADR 0036):

- SAM.gov renews Nov 3, 2027: the date reset on Nov 4, 2026 when the
  NAICS 492110 update took effect (Oct 26 was the old date).
- PEMS revalidation is due Dec 3, 2028 (not the Dec 31 placeholder,
  which would have sent the 30- and 7-day reminders too late).
- The insurance is the commercial auto policy WCAN261110 with Incline
  Casualty, 10/13/2026 to 10/13/2027.
- There is no Fort Worth vehicle-for-hire license yet, so no record.

Only records still as 0003 left them are changed; anything staff have
edited since is left alone.
"""

from datetime import date

from django.db import migrations


def correct(apps, schema_editor):
    ComplianceRecord = apps.get_model("compliance", "ComplianceRecord")
    ComplianceRecord.objects.filter(
        name="SAM.gov entity registration", expiration_date=date(2027, 10, 26)
    ).update(expiration_date=date(2027, 11, 3), notes="Renew on SAM.gov before it lapses. "
             "Renewal date reset on Nov 4, 2026 when the NAICS 492110 update took effect.")
    ComplianceRecord.objects.filter(
        name="Texas Medicaid provider enrollment (PEMS) revalidation", expiration_date=date(2028, 12, 31)
    ).update(expiration_date=date(2028, 12, 3), notes="Revalidate in PEMS before Dec 3, 2028.")
    ComplianceRecord.objects.filter(name="Business insurance", expiration_date=date(2027, 10, 1)).update(
        name="Commercial auto insurance",
        issuing_authority="Incline Casualty",
        reference_number="WCAN261110",
        issued_date=date(2026, 10, 13),
        expiration_date=date(2027, 10, 13),
        notes="Policy period 10/13/2026 to 10/13/2027.",
    )
    ComplianceRecord.objects.filter(
        name="City of Fort Worth vehicle-for-hire license", expiration_date__isnull=True, reference_number=""
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("compliance", "0003_company_dates")]

    operations = [migrations.RunPython(correct, migrations.RunPython.noop)]
