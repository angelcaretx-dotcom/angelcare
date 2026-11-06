from django.utils import timezone
from rest_framework import serializers

from verification.services import token_is_valid

from .addresses import PARTS, compose, validate_parts
from .models import ReturnRide, ServiceType, TripRequest

SIDES = ("pickup", "dropoff")
ADDRESS_PART_FIELDS = [f"{side}_{part}" for side in SIDES for part in PARTS]


def _one_line(attrs, side: str) -> str:
    if attrs.get(f"{side}_address"):
        return attrs[f"{side}_address"]
    return compose(*(attrs.get(f"{side}_{part}", "") for part in ("street", "unit", "city", "state")),
                   attrs.get(f"{side}_zip", ""))


class TripRequestCreateSerializer(serializers.ModelSerializer):
    # Proof the booker can read this email (ADR 0033): from an emailed
    # code, or from being signed in to their rider account.
    verification_token = serializers.CharField(write_only=True)

    class Meta:
        model = TripRequest
        fields = [
            "id",
            "full_name",
            "phone",
            "email",
            *ADDRESS_PART_FIELDS,
            "pickup_address",
            "dropoff_address",
            "requested_datetime",
            "return_ride",
            "return_datetime",
            "service_type",
            "mobility_notes",
            "additional_notes",
            "booked_for",
            "verification_token",
        ]
        read_only_fields = ["id"]
        # The one-line addresses are composed from the parts (ADR 0032);
        # they're still accepted on their own from older clients.
        extra_kwargs = {
            "pickup_address": {"required": False, "allow_blank": True},
            "dropoff_address": {"required": False, "allow_blank": True},
        }

    def validate_full_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Full name is required.")
        return value

    def validate_requested_datetime(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError(
                "Requested pickup time must be in the future."
            )
        return value

    def email_is_proven(self, attrs) -> bool:
        return token_is_valid(attrs.pop("verification_token", ""), attrs.get("email", ""))

    def validate(self, attrs):
        errors = {}
        if not self.email_is_proven(attrs):
            errors["verification_token"] = ["Please confirm your email address again."]

        for side, label in (("pickup", "Pickup"), ("dropoff", "Drop-off")):
            parts = {part: attrs.get(f"{side}_{part}", "") for part in PARTS}
            line = attrs.get(f"{side}_address", "").strip()
            if any(parts.values()) or not line:
                cleaned, part_errors = validate_parts(parts, prefix=f"{side}_")
                errors.update(part_errors)
                attrs.update({f"{side}_{part}": value for part, value in cleaned.items()})
                # The model composes the one-line address from the parts.
                attrs[f"{side}_address"] = ""
            else:
                attrs[f"{side}_address"] = line
            if not line and errors.get(f"{side}_street"):
                errors[f"{side}_street"] = [f"{label} street address is required."]

        if not errors and _one_line(attrs, "pickup").casefold() == _one_line(attrs, "dropoff").casefold():
            errors["dropoff_street"] = ["The drop-off is the same as the pickup. Enter where the rider is going."]

        return_ride = attrs.get("return_ride", ReturnRide.NONE)
        if return_ride != ReturnRide.NONE and attrs.get("service_type") == ServiceType.EQUIPMENT_DELIVERY:
            errors["return_ride"] = ["Equipment deliveries don't need a return ride."]
        if return_ride == ReturnRide.SCHEDULED:
            back = attrs.get("return_datetime")
            pickup = attrs.get("requested_datetime")
            if back is None:
                errors["return_datetime"] = ["Choose a time for the return pickup."]
            elif pickup is not None and back <= pickup:
                errors["return_datetime"] = ["The return pickup must be after the first pickup."]
        else:
            attrs["return_datetime"] = None

        if errors:
            raise serializers.ValidationError(errors)
        return attrs
