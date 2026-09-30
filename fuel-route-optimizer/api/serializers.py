from rest_framework import serializers


class RoutePlanRequestSerializer(serializers.Serializer):
    """Input for GET query params or a POST JSON body."""

    start_location = serializers.CharField(
        max_length=300,
        trim_whitespace=True,
        help_text="Start inside the USA: 'City, ST', ZIP, 'lat,lon' or a street address.",
    )
    finish_location = serializers.CharField(
        max_length=300,
        trim_whitespace=True,
        help_text="Finish inside the USA, same formats as start_location.",
    )
    include_geometry = serializers.BooleanField(
        default=True,
        help_text="Include the route line (GeoJSON) in the response.",
    )
    price_tolerance = serializers.FloatField(
        required=False,
        allow_null=True,
        default=None,
        min_value=0.0,
        max_value=1.0,
        help_text="USD/gal difference treated as the same price (default 0.05). 0 = exact prices.",
    )
    min_purchase_gallons = serializers.FloatField(
        required=False,
        allow_null=True,
        default=None,
        min_value=0.0,
        max_value=48.0,
        help_text="Fold stops buying less than this into a neighbouring stop (default 5). 0 = off.",
    )

    def validate(self, attrs):
        if attrs["start_location"].casefold() == attrs["finish_location"].casefold():
            raise serializers.ValidationError(
                {"finish_location": "Finish location must differ from the start location."}
            )
        return attrs


# ---- Response (documents and enforces the output shape) ------------------ #


class LocationSerializer(serializers.Serializer):
    query = serializers.CharField()
    resolved_as = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    source = serializers.ChoiceField(choices=["coordinates", "zip_code", "city_state", "nominatim"])


class GeometrySerializer(serializers.Serializer):
    type = serializers.CharField()
    # [[lon, lat], ...] passed through as-is: running thousands of points
    # through per-item FloatFields would cost several milliseconds per request.
    coordinates = serializers.JSONField()


class RouteSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry = GeometrySerializer(allow_null=True)


class VehicleSerializer(serializers.Serializer):
    max_range_miles = serializers.FloatField()
    planning_range_miles = serializers.FloatField()
    miles_per_gallon = serializers.FloatField()
    tank_capacity_gallons = serializers.FloatField()


class FuelStopSerializer(serializers.Serializer):
    sequence = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    mile_marker = serializers.FloatField()
    distance_from_route_miles = serializers.FloatField()
    highway_match = serializers.BooleanField()
    gallons_purchased = serializers.FloatField()
    cost = serializers.FloatField()
    fuel_on_arrival_gallons = serializers.FloatField()
    fuel_on_departure_gallons = serializers.FloatField()


class OriginApproachSerializer(serializers.Serializer):
    miles = serializers.FloatField()
    gallons = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    cost = serializers.FloatField()
    priced_at_opis_id = serializers.IntegerField()
    off_route_fallback = serializers.BooleanField()


class SummarySerializer(serializers.Serializer):
    total_distance_miles = serializers.FloatField()
    total_gallons = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
    average_price_per_gallon = serializers.FloatField()
    number_of_stops = serializers.IntegerField()
    origin_approach = OriginApproachSerializer(allow_null=True)


class OptimizationSerializer(serializers.Serializer):
    price_tolerance_per_gallon = serializers.FloatField()
    min_purchase_gallons = serializers.FloatField()


class MetaSerializer(serializers.Serializer):
    routing_api_calls = serializers.IntegerField()
    geocoding_api_calls = serializers.IntegerField()
    route_cache_hit = serializers.BooleanField()
    stations_on_route = serializers.IntegerField()
    timings_ms = serializers.DictField(child=serializers.FloatField())


class RoutePlanResponseSerializer(serializers.Serializer):
    start = LocationSerializer()
    finish = LocationSerializer()
    route = RouteSerializer()
    vehicle = VehicleSerializer()
    fuel_stops = FuelStopSerializer(many=True)
    summary = SummarySerializer()
    optimization = OptimizationSerializer()
    map_url = serializers.CharField()
    meta = MetaSerializer()
