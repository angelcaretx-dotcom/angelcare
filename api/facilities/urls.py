from django.urls import path

from . import api

app_name = "facilities"

urlpatterns = [
    path("csrf/", api.CsrfView.as_view(), name="csrf"),
    path("login/", api.LoginView.as_view(), name="login"),
    path("login/code/", api.LoginCodeView.as_view(), name="login-code"),
    path("logout/", api.LogoutView.as_view(), name="logout"),
    path("forgot-password/", api.ForgotPasswordView.as_view(), name="forgot-password"),
    path("set-password/", api.SetPasswordView.as_view(), name="set-password"),
    path("me/", api.MeView.as_view(), name="me"),
    path("trips/", api.TripsView.as_view(), name="trips"),
    path("book/", api.BookView.as_view(), name="book"),
    path("trips/<uuid:request_id>/cancel/", api.CancelView.as_view(), name="cancel"),
]
