"""Domain errors raised by the service layer.

Each error carries the HTTP status and a stable machine readable code, so the
API layer can turn any of them into a consistent JSON error response without
the services importing anything from Django REST Framework.
"""

from typing import Any


class FuelOptimizerError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


# ---- Location input ------------------------------------------------------- #


class InvalidLocationError(FuelOptimizerError):
    status_code = 400
    code = "invalid_location"
    default_message = "The location could not be parsed."


class LocationNotFoundError(FuelOptimizerError):
    status_code = 400
    code = "location_not_found"
    default_message = "The location could not be found in the USA."


class LocationOutsideUSAError(FuelOptimizerError):
    status_code = 400
    code = "location_outside_usa"
    default_message = "The location is outside the USA."


# ---- External services ---------------------------------------------------- #


class RoutingTimeoutError(FuelOptimizerError):
    status_code = 504
    code = "routing_timeout"
    default_message = "The routing service did not respond in time. Please retry."


class RoutingServiceError(FuelOptimizerError):
    status_code = 502
    code = "routing_unavailable"
    default_message = "The routing service returned an error."


class NoRouteFoundError(FuelOptimizerError):
    status_code = 422
    code = "no_route"
    default_message = "No drivable route exists between the two locations."


class GeocodingTimeoutError(FuelOptimizerError):
    status_code = 504
    code = "geocoding_timeout"
    default_message = "The geocoding service did not respond in time. Please retry."


class GeocodingServiceError(FuelOptimizerError):
    status_code = 502
    code = "geocoding_unavailable"
    default_message = "The geocoding service returned an error."


# ---- Planning ------------------------------------------------------------- #


class InfeasibleFuelPlanError(FuelOptimizerError):
    status_code = 422
    code = "no_feasible_fuel_plan"
    default_message = "The route has a stretch longer than the vehicle range with no fuel station."


class DatasetNotLoadedError(FuelOptimizerError):
    status_code = 503
    code = "fuel_data_not_loaded"
    default_message = "No fuel stations are loaded. Run `python manage.py load_fuel_stations` first."


class GazetteerMissingError(FuelOptimizerError):
    status_code = 503
    code = "gazetteer_missing"
    default_message = "The offline gazetteer files are missing. Run `python manage.py build_gazetteer`."
