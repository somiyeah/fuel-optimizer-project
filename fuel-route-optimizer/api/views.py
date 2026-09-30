"""HTTP layer. Views stay thin: validate input, call the planner, shape output."""

from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views import View
from rest_framework.response import Response
from rest_framework.views import APIView

from .conf import get_config
from .serializers import RoutePlanRequestSerializer, RoutePlanResponseSerializer
from .services import plan_trip
from .services.exceptions import FuelOptimizerError
from .services.station_index import get_station_index


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops between two US locations.

    GET  /api/v1/route/?start_location=Chicago, IL&finish_location=Denver, CO
    POST /api/v1/route/  {"start_location": "...", "finish_location": "...", "include_geometry": true}
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, data):
        params = RoutePlanRequestSerializer(data=data)
        params.is_valid(raise_exception=True)
        values = params.validated_data

        result = plan_trip(
            values["start_location"],
            values["finish_location"],
            include_geometry=values["include_geometry"],
            price_tolerance=values["price_tolerance"],
            min_purchase_gallons=values["min_purchase_gallons"],
        )
        result["map_url"] = _map_url(request, values)
        return Response(RoutePlanResponseSerializer(result).data)


_TUNING_PARAMS = ("price_tolerance", "min_purchase_gallons")


def _map_url(request, values: dict) -> str:
    query = {"start_location": values["start_location"], "finish_location": values["finish_location"]}
    query.update({name: values[name] for name in _TUNING_PARAMS if values.get(name) is not None})
    return f"{request.build_absolute_uri(reverse('api:route-map'))}?{urlencode(query)}"


class RouteMapView(View):
    """Interactive Leaflet map of the route and the chosen fuel stops.

    Uses the same cached route as the JSON endpoint, so opening the map after
    calling the API does not trigger another OSRM request.
    """

    template_name = "api/route_map.html"
    error_template_name = "api/route_map_error.html"

    def get(self, request):
        params = RoutePlanRequestSerializer(data=request.GET)
        if not params.is_valid():
            messages = [f"{field}: {' '.join(str(e) for e in errors)}" for field, errors in params.errors.items()]
            return render(
                request,
                self.error_template_name,
                {"title": "Invalid request", "messages": messages},
                status=400,
            )
        try:
            plan = plan_trip(
                params.validated_data["start_location"],
                params.validated_data["finish_location"],
                include_geometry=True,
                price_tolerance=params.validated_data["price_tolerance"],
                min_purchase_gallons=params.validated_data["min_purchase_gallons"],
            )
        except FuelOptimizerError as exc:
            return render(
                request,
                self.error_template_name,
                {"title": "Could not plan this trip", "messages": [exc.message]},
                status=exc.status_code,
            )
        config = get_config()
        map_tiles = {"url": config.map_tile_url, "attribution": config.map_tile_attribution}
        response = render(request, self.template_name, {"plan": plan, "map_tiles": map_tiles})
        # Django's default policy ("same-origin") strips the Referer header from
        # cross-site requests, and OpenStreetMap's tile servers answer requests
        # without one with an "Access blocked" image. This policy sends only the
        # page's origin (for example http://127.0.0.1:8000/) to other sites.
        # SecurityMiddleware keeps a header the view has already set.
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


class HealthView(APIView):
    """Liveness check that also reports whether the fuel data is loaded."""

    def get(self, request):
        stations = get_station_index().size
        return Response(
            {"status": "ok" if stations else "degraded", "fuel_stations_loaded": stations},
            status=200 if stations else 503,
        )
