"""OSRM routing client: one HTTP request per route, with caching.

Request:
    GET {OSRM_BASE_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}
        ?overview=full&geometries=polyline6&steps=true&alternatives=false

* overview=full       full-resolution geometry, needed to measure how far
                      each station is from the road.
* geometries=polyline6 compact encoding (about 4x smaller than GeoJSON).
* steps=true          per-step road refs ("I 44;US 69"), used to confirm that
                      a station's highway is one the route really drives on.

The result is cached by rounded coordinates, so asking for the JSON plan and
then the HTML map of the same trip costs one OSRM call in total.
"""

import logging
from dataclasses import dataclass

import numpy as np
import requests
from django.core.cache import cache

from ..conf import OptimizerConfig
from .exceptions import NoRouteFoundError, RoutingServiceError, RoutingTimeoutError
from .geo import METERS_PER_MILE, decode_polyline
from .geocoding import ResolvedLocation
from .http import get_http_session
from .stats import RequestStats
from .text import parse_osrm_highways

logger = logging.getLogger(__name__)

_NO_ROUTE_CODES = {"NoRoute", "NoSegment", "NoMatch", "NoTrips"}


@dataclass(frozen=True)
class RouteStep:
    start_mile: float
    end_mile: float
    highways: frozenset[str]


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: np.ndarray  # shape (n, 2): latitude, longitude
    steps: tuple[RouteStep, ...]

    @property
    def has_highway_refs(self) -> bool:
        return any(step.highways for step in self.steps)


class OSRMClient:
    def __init__(self, config: OptimizerConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or get_http_session()

    def get_route(self, start: ResolvedLocation, finish: ResolvedLocation, stats: RequestStats) -> Route:
        """Cached route lookup. Makes at most one OSRM request."""
        cache_key = (
            f"route:v2:{self.config.osrm_profile}:{int(self.config.osrm_use_steps)}:"
            f"{start.latitude:.5f},{start.longitude:.5f}:{finish.latitude:.5f},{finish.longitude:.5f}"
        )
        route = cache.get(cache_key)
        if route is not None:
            stats.route_cache_hit = True
            return route
        route = self._fetch(start, finish, stats)
        cache.set(cache_key, route, self.config.route_cache_seconds)
        return route

    def _fetch(self, start: ResolvedLocation, finish: ResolvedLocation, stats: RequestStats) -> Route:
        base_url = self.config.osrm_base_url.rstrip("/")
        url = (
            f"{base_url}/route/v1/{self.config.osrm_profile}/"
            f"{start.longitude:.6f},{start.latitude:.6f};{finish.longitude:.6f},{finish.latitude:.6f}"
        )
        params = {
            "overview": "full",
            "geometries": "polyline6",
            "steps": "true" if self.config.osrm_use_steps else "false",
            "alternatives": "false",
            "annotations": "false",
        }
        stats.routing_api_calls += 1
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"User-Agent": self.config.http_user_agent, "Accept": "application/json"},
                timeout=(self.config.osrm_connect_timeout_seconds, self.config.osrm_read_timeout_seconds),
            )
        except requests.Timeout as exc:
            logger.warning("OSRM timeout for %s", url)
            raise RoutingTimeoutError() from exc
        except requests.RequestException as exc:
            logger.warning("OSRM connection error for %s: %s", url, exc)
            raise RoutingServiceError(
                "Could not connect to the routing service.", details={"reason": str(exc)}
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        code = payload.get("code") if isinstance(payload, dict) else None
        if code in _NO_ROUTE_CODES:
            raise NoRouteFoundError(
                payload.get("message") or NoRouteFoundError.default_message, details={"osrm_code": code}
            )
        if response.status_code == 429:
            raise RoutingServiceError(
                "The routing service is rate limiting requests. Please retry shortly.",
                details={"status": 429},
            )
        if response.status_code != 200 or code != "Ok":
            raise RoutingServiceError(
                f"The routing service returned HTTP {response.status_code}.",
                details={"status": response.status_code, "osrm_code": code},
            )
        return self._parse(payload)

    @staticmethod
    def _parse(payload: dict) -> Route:
        try:
            best = payload["routes"][0]
            coordinates = decode_polyline(best["geometry"], precision=6)
            distance_miles = float(best["distance"]) / METERS_PER_MILE
            duration_seconds = float(best["duration"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingServiceError("The routing service returned an unexpected response.") from exc
        if coordinates.shape[0] < 2 and distance_miles > 0.01:
            raise RoutingServiceError("The routing service returned an empty route geometry.")

        steps: list[RouteStep] = []
        travelled = 0.0
        for leg in best.get("legs") or []:
            for step in leg.get("steps") or []:
                length = float(step.get("distance") or 0.0) / METERS_PER_MILE
                highways = parse_osrm_highways(step.get("ref"), step.get("name"))
                steps.append(RouteStep(travelled, travelled + length, highways))
                travelled += length
        return Route(distance_miles, duration_seconds, coordinates, tuple(steps))
