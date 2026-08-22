from rest_framework import permissions
from rest_framework.generics import CreateAPIView

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
