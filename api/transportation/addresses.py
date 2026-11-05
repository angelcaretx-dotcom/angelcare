"""
US street addresses entered as separate parts (ADR 0032): street, unit,
city, state, ZIP. Claims and broker files need the ZIP and city on their
own, and a single free-text box let riders leave them out.

The parts are stored alongside one composed line (``compose``), which is
what dispatch, the driver app and maps links keep using.
"""

import re

from rest_framework import serializers

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_CHOICES = sorted(US_STATES.items(), key=lambda item: item[1])
DEFAULT_STATE = "TX"
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
PARTS = ("street", "unit", "city", "state", "zip")


def compose(street: str, unit: str, city: str, state: str, zip_code: str) -> str:
    """'123 Main St, Apt 4, Fort Worth, TX 76104'."""
    first = ", ".join(p for p in (street.strip(), unit.strip()) if p)
    region = " ".join(p for p in (state.strip(), zip_code.strip()) if p)
    return ", ".join(p for p in (first, city.strip(), region) if p)


def validate_parts(parts: dict, prefix: str = "") -> tuple[dict, dict]:
    """
    Clean street/unit/city/state/zip. Returns (cleaned, errors); errors
    are keyed ``{prefix}{part}`` so they land on the right form field.
    """
    cleaned = {part: str(parts.get(part) or "").strip() for part in PARTS}
    cleaned["state"] = cleaned["state"].upper()
    errors = {}
    if not cleaned["street"]:
        errors[f"{prefix}street"] = ["Enter the street address."]
    if not cleaned["city"]:
        errors[f"{prefix}city"] = ["Enter the city."]
    if cleaned["state"] not in US_STATES:
        errors[f"{prefix}state"] = ["Choose a state."]
    if not ZIP_RE.match(cleaned["zip"]):
        errors[f"{prefix}zip"] = ["Enter a 5-digit ZIP code."]
    return cleaned, errors


class AddressPartsSerializer(serializers.Serializer):
    """Street/unit/city/state/zip as one nested object (saved addresses)."""

    street = serializers.CharField(max_length=200, required=False, allow_blank=True)
    unit = serializers.CharField(max_length=50, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    state = serializers.CharField(max_length=2, required=False, allow_blank=True)
    zip = serializers.CharField(max_length=10, required=False, allow_blank=True)

    def validate(self, attrs):
        cleaned, errors = validate_parts(attrs)
        if errors:
            raise serializers.ValidationError(errors)
        return cleaned
