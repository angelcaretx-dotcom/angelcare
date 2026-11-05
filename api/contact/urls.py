from django.urls import path

from . import api

app_name = "contact"

urlpatterns = [
    path("", api.ContactView.as_view(), name="create"),
]
