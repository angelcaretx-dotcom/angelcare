from rest_framework import permissions
from rest_framework.generics import CreateAPIView

from notifications.services import NotificationService

from .serializers import TripRequestCreateSerializer


class TripRequestCreateView(CreateAPIView):
    """
    POST /api/v1/trip-requests/

    Public endpoint: accepts a transportation request from the marketing
    site. Intentionally create-only — there is no public list/retrieve/
    update/delete here. Staff review and manage requests through the
    Django admin (or a future internal dispatch UI), not this endpoint.
    """

    serializer_class = TripRequestCreateSerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        trip_request = serializer.save()
        # Notification failure must never break the request -- the
        # trip request is already safely saved by this point.
        # See notifications/services.py for why this can't raise.
        NotificationService().notify_new_trip_request(trip_request)
