from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("route/map/", views.RouteMapView.as_view(), name="route-map"),
    path("health/", views.HealthView.as_view(), name="health"),
]
