"""POST /api/v1/contact/: the website's contact form (ADR 0033)."""

import uuid

from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.services import NotificationService
from verification import bots
from verification.services import token_is_valid

from .models import ContactMessage


class ContactSerializer(serializers.ModelSerializer):
    verification_token = serializers.CharField(write_only=True)

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "phone", "message", "verification_token"]
        extra_kwargs = {
            "phone": {"validators": []},
            "message": {"max_length": 5000},
        }

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Please enter your name.")
        return value

    def validate_message(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Please write a message.")
        return value

    def validate(self, attrs):
        if not token_is_valid(attrs.pop("verification_token"), attrs["email"]):
            raise serializers.ValidationError(
                {"verification_token": ["Please confirm your email address again."]}
            )
        attrs["email"] = attrs["email"].lower()
        return attrs


class ContactView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        if bots.tripped_trap(request.data):
            return Response({"id": str(uuid.uuid4())}, status=status.HTTP_201_CREATED)
        data = ContactSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        message = data.save()
        NotificationService().notify_contact_message(message)
        return Response({"id": str(message.id)}, status=status.HTTP_201_CREATED)
