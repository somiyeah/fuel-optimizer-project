"""Typed access to the FUEL_OPTIMIZER settings block."""

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

_DEFAULTS = {
    "MAX_RANGE_MILES": 500.0,
    "SAFETY_BUFFER_MILES": 20.0,
    "MILES_PER_GALLON": 10.0,
    "PRICE_TOLERANCE_PER_GALLON": 0.05,
    "MIN_PURCHASE_GALLONS": 5.0,
    "ORIGIN_FILL_WINDOW_MILES": 50.0,
    "ORIGIN_FALLBACK_RADIUS_MILES": 50.0,
    "CORRIDOR_MILES_HIGHWAY_MATCH": 15.0,
    "CORRIDOR_MILES_DEFAULT": 5.0,
    "CORRIDOR_MILES_HIGHWAY_MISMATCH": 2.0,
    "HIGHWAY_MATCH_WINDOW_MILES": 30.0,
    "ROUTE_DENSIFY_MILES": 0.5,
    "MAX_OUTPUT_GEOMETRY_POINTS": 2500,
    "ROUTE_CACHE_SECONDS": 6 * 3600,
    "GEOCODE_CACHE_SECONDS": 24 * 3600,
    "OSRM_BASE_URL": "https://router.project-osrm.org",
    "OSRM_PROFILE": "driving",
    "OSRM_USE_STEPS": True,
    "OSRM_CONNECT_TIMEOUT_SECONDS": 5.0,
    "OSRM_READ_TIMEOUT_SECONDS": 25.0,
    "GEOCODER_ONLINE_FALLBACK": True,
    "NOMINATIM_URL": "https://nominatim.openstreetmap.org/search",
    "NOMINATIM_EMAIL": "",
    "NOMINATIM_TIMEOUT_SECONDS": 8.0,
    "HTTP_USER_AGENT": "fuel-route-optimizer/1.0",
    "US_POINT_MAX_DISTANCE_MILES": 30.0,
    "MAP_TILE_URL": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "MAP_TILE_ATTRIBUTION": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    "FUEL_PRICES_CSV": Path("data/fuel-prices-for-be-assessment.csv"),
    "GAZETTEER_PLACES_FILE": Path("data/gazetteer/us_places.csv.gz"),
    "GAZETTEER_ZIPS_FILE": Path("data/gazetteer/us_zips.csv.gz"),
    "WARM_CACHES_ON_STARTUP": True,
}


@dataclass(frozen=True)
class OptimizerConfig:
    max_range_miles: float
    safety_buffer_miles: float
    miles_per_gallon: float
    price_tolerance_per_gallon: float
    min_purchase_gallons: float
    origin_fill_window_miles: float
    origin_fallback_radius_miles: float
    corridor_miles_highway_match: float
    corridor_miles_default: float
    corridor_miles_highway_mismatch: float
    highway_match_window_miles: float
    route_densify_miles: float
    max_output_geometry_points: int
    route_cache_seconds: int
    geocode_cache_seconds: int
    osrm_base_url: str
    osrm_profile: str
    osrm_use_steps: bool
    osrm_connect_timeout_seconds: float
    osrm_read_timeout_seconds: float
    geocoder_online_fallback: bool
    nominatim_url: str
    nominatim_email: str
    nominatim_timeout_seconds: float
    http_user_agent: str
    us_point_max_distance_miles: float
    map_tile_url: str
    map_tile_attribution: str
    fuel_prices_csv: Path
    gazetteer_places_file: Path
    gazetteer_zips_file: Path
    warm_caches_on_startup: bool

    @property
    def planning_range_miles(self) -> float:
        """Longest distance planned between two fuel purchases.

        A full tank lasts MAX_RANGE_MILES (500). The planner never lets a leg
        use the last SAFETY_BUFFER_MILES (20), so legs are at most 480 miles.
        """
        return self.max_range_miles - self.safety_buffer_miles

    @property
    def tank_capacity_gallons(self) -> float:
        """Physical tank size: 500 miles / 10 mpg = 50 gallons."""
        return self.max_range_miles / self.miles_per_gallon

    @property
    def max_corridor_miles(self) -> float:
        return max(
            self.corridor_miles_highway_match,
            self.corridor_miles_default,
            self.corridor_miles_highway_mismatch,
        )


def get_config() -> OptimizerConfig:
    """Build the config on each call so override_settings works in tests.

    It is a dict merge over ~30 keys, so the cost per request is negligible.
    """
    merged = {**_DEFAULTS, **getattr(settings, "FUEL_OPTIMIZER", {})}
    config = OptimizerConfig(**{key.lower(): value for key, value in merged.items()})
    if config.miles_per_gallon <= 0:
        raise ValueError("FUEL_OPTIMIZER['MILES_PER_GALLON'] must be positive.")
    if config.planning_range_miles <= 0:
        raise ValueError("SAFETY_BUFFER_MILES must be smaller than MAX_RANGE_MILES.")
    return config
