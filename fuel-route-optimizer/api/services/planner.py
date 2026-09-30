"""End-to-end trip planning: geocode -> route (1 OSRM call) -> match -> optimise.

This module is the single entry point the views call. It returns a plain
dict shaped exactly like the API response, so the same result feeds both the
JSON endpoint and the HTML map page.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..conf import OptimizerConfig, get_config
from .exceptions import DatasetNotLoadedError
from .geo import haversine_miles
from .geocoding import LocationResolver, ResolvedLocation
from .optimizer import Candidate, FuelPlan, plan_fuel_purchases
from .route_matching import RouteStation, match_stations_to_route, sample_geometry
from .routing import OSRMClient, Route
from .station_index import StationIndex, get_station_index
from .stats import RequestStats

logger = logging.getLogger(__name__)


@dataclass
class TripPlanner:
    config: OptimizerConfig
    resolver: LocationResolver
    router: OSRMClient

    @classmethod
    def default(cls) -> "TripPlanner":
        config = get_config()
        return cls(config=config, resolver=LocationResolver(config), router=OSRMClient(config))

    def plan(
        self,
        start_location: str,
        finish_location: str,
        include_geometry: bool = True,
        price_tolerance: float | None = None,
        min_purchase_gallons: float | None = None,
    ) -> dict:
        started = time.perf_counter()
        options = {
            "price_tolerance_per_gallon": (
                self.config.price_tolerance_per_gallon if price_tolerance is None else price_tolerance
            ),
            "min_purchase_gallons": (
                self.config.min_purchase_gallons if min_purchase_gallons is None else min_purchase_gallons
            ),
        }
        stats = RequestStats()

        index = get_station_index()
        if index.size == 0:
            raise DatasetNotLoadedError()

        with _timer(stats, "geocoding"):
            start = self.resolver.resolve(start_location, stats)
            finish = self.resolver.resolve(finish_location, stats)

        with _timer(stats, "routing"):
            route = self.router.get_route(start, finish, stats)

        with _timer(stats, "station_matching"):
            on_route = match_stations_to_route(route, index, self.config)

        with _timer(stats, "optimization"):
            fuel_plan = self._optimise(route, on_route, index, start, options)

        result = self._build_response(start, finish, route, on_route, index, fuel_plan, include_geometry, options)
        stats.timings_ms["total"] = round((time.perf_counter() - started) * 1000, 1)
        result["meta"] = {
            "routing_api_calls": stats.routing_api_calls,
            "geocoding_api_calls": stats.geocoding_api_calls,
            "route_cache_hit": stats.route_cache_hit,
            "stations_on_route": len(on_route),
            "timings_ms": stats.timings_ms,
        }
        logger.info(
            "Planned %s -> %s: %.1f mi, %d stops, $%.2f, osrm_calls=%d, %.1f ms",
            start.label,
            finish.label,
            route.distance_miles,
            len(fuel_plan.purchases),
            fuel_plan.total_cost,
            stats.routing_api_calls,
            stats.timings_ms["total"],
        )
        return result

    # ------------------------------------------------------------------ #

    def _optimise(
        self,
        route: Route,
        on_route: list[RouteStation],
        index: StationIndex,
        start: ResolvedLocation,
        options: dict,
    ) -> FuelPlan:
        candidates = [
            Candidate(key=item.station_index, mile=item.mile_marker, price=float(index.prices[item.station_index]))
            for item in on_route
        ]
        fallback = None
        nearest = index.nearest(start.latitude, start.longitude, self.config.origin_fallback_radius_miles)
        if nearest is not None:
            fallback_index, _ = nearest
            fallback = Candidate(key=fallback_index, mile=0.0, price=float(index.prices[fallback_index]))
        return plan_fuel_purchases(
            candidates,
            total_distance_miles=route.distance_miles,
            range_miles=self.config.planning_range_miles,
            miles_per_gallon=self.config.miles_per_gallon,
            origin_window_miles=self.config.origin_fill_window_miles,
            origin_fallback=fallback,
            price_tolerance=options["price_tolerance_per_gallon"],
            min_purchase_gallons=options["min_purchase_gallons"],
        )

    def _build_response(
        self,
        start: ResolvedLocation,
        finish: ResolvedLocation,
        route: Route,
        on_route: list[RouteStation],
        index: StationIndex,
        plan: FuelPlan,
        include_geometry: bool,
        options: dict,
    ) -> dict:
        config = self.config
        by_key = {item.station_index: item for item in on_route}

        stops = []
        for sequence, purchase in enumerate(plan.purchases, start=1):
            station = index.records[purchase.key]
            matched = by_key.get(purchase.key)
            if matched is not None:
                distance_from_route = matched.distance_from_route_miles
                highway_match = matched.highway_match
            else:  # origin fallback station: off the route corridor, near the start
                distance_from_route = float(
                    haversine_miles(start.latitude, start.longitude, station.latitude, station.longitude)
                )
                highway_match = False
            stops.append(
                {
                    "sequence": sequence,
                    "opis_id": station.opis_id,
                    "name": station.name,
                    "address": station.address,
                    "city": station.city,
                    "state": station.state,
                    "latitude": round(station.latitude, 5),
                    "longitude": round(station.longitude, 5),
                    "price_per_gallon": round(purchase.price, 3),
                    "mile_marker": round(purchase.mile, 1),
                    "distance_from_route_miles": round(distance_from_route, 1),
                    "highway_match": highway_match,
                    "gallons_purchased": round(purchase.gallons, 2),
                    "cost": round(purchase.cost, 2),
                    "fuel_on_arrival_gallons": round(purchase.fuel_on_arrival_gallons, 2),
                    "fuel_on_departure_gallons": round(purchase.fuel_on_departure_gallons, 2),
                }
            )

        approach = plan.origin_approach
        approach_block = None
        if approach is not None:
            approach_block = {
                "miles": round(approach.miles, 1),
                "gallons": round(approach.gallons, 2),
                "price_per_gallon": round(approach.price, 3),
                "cost": round(approach.cost, 2),
                "priced_at_opis_id": index.records[approach.key].opis_id,
                "off_route_fallback": approach.off_route_fallback,
            }

        total_gallons = plan.total_gallons
        response = {
            "start": _location_block(start),
            "finish": _location_block(finish),
            "route": {
                "distance_miles": round(route.distance_miles, 1),
                "duration_hours": round(route.duration_seconds / 3600.0, 2),
                "geometry": (
                    {
                        "type": "LineString",
                        "coordinates": sample_geometry(route.coordinates, config.max_output_geometry_points),
                    }
                    if include_geometry
                    else None
                ),
            },
            "vehicle": {
                "max_range_miles": config.max_range_miles,
                "planning_range_miles": config.planning_range_miles,
                "miles_per_gallon": config.miles_per_gallon,
                "tank_capacity_gallons": config.tank_capacity_gallons,
            },
            "fuel_stops": stops,
            "summary": {
                "total_distance_miles": round(route.distance_miles, 1),
                "total_gallons": round(total_gallons, 2),
                "total_fuel_cost": round(plan.total_cost, 2),
                "average_price_per_gallon": round(plan.total_cost / total_gallons, 3) if total_gallons else 0.0,
                "number_of_stops": len(stops),
                "origin_approach": approach_block,
            },
            "optimization": options,
        }
        return response


def _location_block(location: ResolvedLocation) -> dict:
    return {
        "query": location.query,
        "resolved_as": location.label,
        "latitude": round(location.latitude, 5),
        "longitude": round(location.longitude, 5),
        "source": location.source,
    }


@contextmanager
def _timer(stats: RequestStats, name: str) -> Iterator[None]:
    """Record how long a block took, in milliseconds, under stats.timings_ms[name]."""
    started = time.perf_counter()
    try:
        yield
    finally:
        stats.timings_ms[name] = round((time.perf_counter() - started) * 1000, 1)


def plan_trip(
    start_location: str,
    finish_location: str,
    include_geometry: bool = True,
    price_tolerance: float | None = None,
    min_purchase_gallons: float | None = None,
) -> dict:
    """Convenience wrapper used by the views."""
    return TripPlanner.default().plan(
        start_location, finish_location, include_geometry, price_tolerance, min_purchase_gallons
    )
