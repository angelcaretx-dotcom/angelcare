from django.urls import path

from .views import TripRequestCreateView

app_name = "transportation"

urlpatterns = [
    path("trip-requests/", TripRequestCreateView.as_view(), name="trip-request-create"),
]
