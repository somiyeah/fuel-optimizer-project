"""Place fuel stations along a route.

Station coordinates in the dataset are not exact (the CSV has no coordinates;
each station is placed at its city's centroid, see `load_fuel_stations`). So a
station is accepted as "on the route" using two signals:

1. Spatial: great-circle distance from the station to the nearest point of the
   route polyline, and the route mile where that nearest point sits (the
   station's "mile marker").
2. Highway: the Interstate / US routes parsed from the station address
   ("I-44, EXIT 283 & US-69") compared with the roads OSRM says the route
   drives on within +/- HIGHWAY_MATCH_WINDOW_MILES of that mile marker.

Corridor width per station:
    highway matches the route nearby          -> CORRIDOR_MILES_HIGHWAY_MATCH   (15 mi)
    no highway info on either side            -> CORRIDOR_MILES_DEFAULT         (5 mi)
    station is on other Interstates/US routes -> CORRIDOR_MILES_HIGHWAY_MISMATCH (2 mi)

The wide corridor for matching stations absorbs the city-centroid error (a
truck stop on I-44 can sit a few miles from the town centre), while the
narrow one keeps out stations in the same town that sit on a different road.

Performance: the route is densified to one point every ROUTE_DENSIFY_MILES
and loaded into a KD-tree on the unit sphere; all stations inside the route's
bounding box are then matched in a single vectorised query.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from ..conf import OptimizerConfig
from .geo import chord_to_miles, haversine_miles, miles_to_chord, to_unit_xyz
from .routing import Route
from .station_index import StationIndex

MILES_PER_DEGREE_LATITUDE = 69.05


@dataclass(frozen=True)
class RouteStation:
    station_index: int  # position in StationIndex.records
    mile_marker: float  # route miles from the start to the station's nearest route point
    distance_from_route_miles: float
    highway_match: bool


def densify(coordinates: np.ndarray, max_step_miles: float) -> tuple[np.ndarray, np.ndarray]:
    """Insert points so consecutive route points are at most max_step_miles apart.

    Returns the dense (m, 2) lat/lon array and the cumulative miles at each point.
    Dense points make "nearest route vertex" a good stand-in for "nearest point
    on the route line": the error is at most half the step (0.25 mi).
    """
    if coordinates.shape[0] < 2:
        return coordinates.copy(), np.zeros(coordinates.shape[0])

    start, end = coordinates[:-1], coordinates[1:]
    segment_miles = haversine_miles(start[:, 0], start[:, 1], end[:, 0], end[:, 1])
    pieces = np.maximum(1, np.ceil(segment_miles / max_step_miles)).astype(np.int64)

    segment_of_point = np.repeat(np.arange(pieces.size), pieces)
    first_point_of_segment = np.cumsum(pieces) - pieces
    fraction = (np.arange(pieces.sum()) - np.repeat(first_point_of_segment, pieces)) / np.repeat(pieces, pieces)

    dense = start[segment_of_point] + (end[segment_of_point] - start[segment_of_point]) * fraction[:, None]
    miles_at_segment_start = np.concatenate(([0.0], np.cumsum(segment_miles)[:-1]))
    cumulative = miles_at_segment_start[segment_of_point] + fraction * segment_miles[segment_of_point]

    dense = np.vstack((dense, coordinates[-1:]))
    cumulative = np.append(cumulative, segment_miles.sum())
    return dense, cumulative


class _RouteHighways:
    """Answers "which Interstates/US routes does the route use near mile m?"."""

    def __init__(self, route: Route, window_miles: float):
        self.window = window_miles
        self.steps = route.steps
        self.starts = np.fromiter((s.start_mile for s in route.steps), dtype=np.float64, count=len(route.steps))
        self.ends = np.fromiter((s.end_mile for s in route.steps), dtype=np.float64, count=len(route.steps))

    def near(self, mile: float) -> frozenset[str]:
        first = int(np.searchsorted(self.ends, mile - self.window, side="left"))
        last = int(np.searchsorted(self.starts, mile + self.window, side="right"))
        found: set[str] = set()
        for step in self.steps[first:last]:
            found |= step.highways
        return frozenset(found)


def match_stations_to_route(route: Route, index: StationIndex, config: OptimizerConfig) -> list[RouteStation]:
    """Stations close enough to the route to be used, ordered by mile marker."""
    if index.size == 0 or route.coordinates.shape[0] == 0:
        return []

    dense, cumulative = densify(route.coordinates, config.route_densify_miles)
    geometric_length = float(cumulative[-1])
    if geometric_length > 0:
        # Use OSRM's road distance as the source of truth for mile markers.
        cumulative = cumulative * (route.distance_miles / geometric_length)

    max_corridor = config.max_corridor_miles

    # Cheap bounding-box filter before the KD-tree query.
    lat_pad = max_corridor / MILES_PER_DEGREE_LATITUDE
    max_abs_lat = min(89.0, float(np.abs(dense[:, 0]).max()) + lat_pad)
    lon_pad = max_corridor / (MILES_PER_DEGREE_LATITUDE * math.cos(math.radians(max_abs_lat)))
    in_box = (
        (index.latitudes >= dense[:, 0].min() - lat_pad)
        & (index.latitudes <= dense[:, 0].max() + lat_pad)
        & (index.longitudes >= dense[:, 1].min() - lon_pad)
        & (index.longitudes <= dense[:, 1].max() + lon_pad)
    )
    candidates = np.flatnonzero(in_box)
    if candidates.size == 0:
        return []

    route_tree = cKDTree(to_unit_xyz(dense[:, 0], dense[:, 1]))
    chord, nearest_point = route_tree.query(
        index.xyz[candidates], k=1, distance_upper_bound=miles_to_chord(max_corridor)
    )
    within = np.isfinite(chord)
    candidates, chord, nearest_point = candidates[within], chord[within], nearest_point[within]
    miles_off_route = chord_to_miles(chord)
    mile_markers = cumulative[nearest_point]

    highways = _RouteHighways(route, config.highway_match_window_miles) if route.has_highway_refs else None

    matched: list[RouteStation] = []
    for station_index, off_route, mile in zip(
        candidates.tolist(), miles_off_route.tolist(), mile_markers.tolist(), strict=True
    ):
        station = index.records[station_index]
        highway_match = False
        if highways is not None and station.highways:
            highway_match = bool(station.highways & highways.near(mile))
            limit = config.corridor_miles_highway_match if highway_match else config.corridor_miles_highway_mismatch
        else:
            limit = config.corridor_miles_default
        if off_route <= limit:
            matched.append(RouteStation(station_index, mile, off_route, highway_match))

    matched.sort(key=lambda item: (item.mile_marker, index.prices[item.station_index]))
    return matched


def sample_geometry(coordinates: np.ndarray, max_points: int) -> list[list[float]]:
    """Evenly thinned GeoJSON-style [lon, lat] list for the response and map.

    The full OSRM geometry can hold 50k+ points; ~2.5k is plenty to draw a
    country-scale route and keeps the JSON payload small. First and last
    points are always kept.
    """
    count = coordinates.shape[0]
    if count == 0:
        return []
    if count > max_points:
        keep = np.unique(np.linspace(0, count - 1, max_points).round().astype(np.int64))
        coordinates = coordinates[keep]
    return np.round(coordinates[:, ::-1], 5).tolist()
