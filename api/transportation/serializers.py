from django.utils import timezone
from rest_framework import serializers

from .models import TripRequest


class TripRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripRequest
        fields = [
            "id",
            "full_name",
            "phone",
            "email",
            "pickup_address",
            "dropoff_address",
            "requested_datetime",
            "service_type",
            "mobility_notes",
            "additional_notes",
        ]
        read_only_fields = ["id"]

    def validate_full_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Full name is required.")
        return value

    def validate_pickup_address(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Pickup address is required.")
        return value

    def validate_dropoff_address(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Drop-off address is required.")
        return value

    def validate_requested_datetime(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError(
                "Requested pickup time must be in the future."
            )
        return value
