from django.urls import path

from . import api

app_name = "verification"

urlpatterns = [
    path("start/", api.StartView.as_view(), name="start"),
    path("check/", api.CheckView.as_view(), name="check"),
]
