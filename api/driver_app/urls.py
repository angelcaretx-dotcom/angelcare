from django.urls import path

from . import views

app_name = "driver_app"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/", views.password_view, name="password"),
    path("trip/<uuid:trip_id>/", views.trip_detail, name="trip"),
    path("trip/<uuid:trip_id>/step/", views.record_step, name="step"),
    path("vehicle/<uuid:vehicle_id>/check/", views.vehicle_check, name="vehicle_check"),
    path("vehicle-day/<uuid:check_id>/end/", views.vehicle_day_end, name="vehicle_day_end"),
]
