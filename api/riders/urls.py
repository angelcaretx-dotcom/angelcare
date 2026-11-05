from django.urls import path

from . import api

app_name = "riders"

urlpatterns = [
    path("csrf/", api.CsrfView.as_view(), name="csrf"),
    path("login/", api.LoginView.as_view(), name="login"),
    path("logout/", api.LogoutView.as_view(), name="logout"),
    path("forgot-password/", api.ForgotPasswordView.as_view(), name="forgot-password"),
    path("set-password/", api.SetPasswordView.as_view(), name="set-password"),
    path("me/", api.MeView.as_view(), name="me"),
    path("addresses/", api.AddressesView.as_view(), name="addresses"),
    path("addresses/<uuid:address_id>/", api.AddressDetailView.as_view(), name="address"),
    path("trips/", api.TripsView.as_view(), name="trips"),
    path("trips/<uuid:request_id>/cancel/", api.CancelTripView.as_view(), name="cancel"),
]
