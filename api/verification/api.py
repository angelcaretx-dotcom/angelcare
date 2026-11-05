"""
POST /api/v1/verify/start/ and /api/v1/verify/check/ (ADR 0033).

Called by the website's booking and contact forms, straight to the API
like the booking form itself (not through the website's proxy), so the
per-IP limits see the visitor's own address.
"""

import uuid

from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import bots, services
from .models import Purpose


class VerifyView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify"


class StartSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=Purpose.choices)
    turnstile_token = serializers.CharField(required=False, allow_blank=True)


class StartView(VerifyView):
    def post(self, request):
        if bots.tripped_trap(request.data):
            # Look like it worked; send nothing.
            return Response({"verification_id": str(uuid.uuid4())})
        data = StartSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if bots.too_fast(request.data):
            return Response(
                {"detail": "That was quick! Please check the form, then try again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not bots.passes_turnstile(data.validated_data.get("turnstile_token", ""), bots.client_ip(request)):
            return Response(
                {"detail": "We couldn't confirm you're not a robot. Please try again, or call us."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            verification = services.start(data.validated_data["email"], data.validated_data["purpose"])
        except services.VerificationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response({"verification_id": str(verification.id)})


class CheckSerializer(serializers.Serializer):
    verification_id = serializers.UUIDField()
    code = serializers.RegexField(r"^\s*\d{6}\s*$", error_messages={"invalid": "Enter the 6-digit code."})


class CheckView(VerifyView):
    def post(self, request):
        data = CheckSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            token, verification = services.check(
                data.validated_data["verification_id"], data.validated_data["code"]
            )
        except services.VerificationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"token": token, "expires_at": verification.token_expires_at.isoformat()})
