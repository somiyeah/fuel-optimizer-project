"""Test doubles for the OSRM and Nominatim HTTP APIs.

`build_osrm_payload` produces a response in the exact OSRM v5 format
(polyline6 geometry, legs, steps with ref/name/distance), so tests exercise
the real parsing code without network access.
"""

from dataclasses import dataclass, field

import numpy as np
import requests

from api.services.geo import METERS_PER_MILE, haversine_miles


def encode_polyline(points: list[tuple[float, float]], precision: int = 6) -> str:
    """Reference (pure Python) Google polyline encoder for (lat, lon) points."""
    factor = 10**precision
    output: list[str] = []
    previous = (0, 0)
    for lat, lon in points:
        current = (int(round(lat * factor)), int(round(lon * factor)))
        for delta in (current[0] - previous[0], current[1] - previous[1]):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
        previous = current
    return "".join(output)


def interpolate(waypoints: list[tuple[float, float]], step_miles: float = 2.0) -> list[tuple[float, float]]:
    """Straight segments between waypoints, sampled every ~step_miles."""
    points: list[tuple[float, float]] = []
    for (lat1, lon1), (lat2, lon2) in zip(waypoints[:-1], waypoints[1:], strict=True):
        length = float(haversine_miles(lat1, lon1, lat2, lon2))
        pieces = max(1, int(np.ceil(length / step_miles)))
        for t in np.linspace(0.0, 1.0, pieces, endpoint=False):
            points.append((lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t))
    points.append(waypoints[-1])
    return points


def build_osrm_payload(
    waypoints: list[tuple[float, float]],
    highway_refs: list[str] | None = None,
    step_miles: float = 2.0,
) -> dict:
    """OSRM /route response for a path through `waypoints`.

    highway_refs[i] is the OSRM `ref` for the segment waypoints[i] -> waypoints[i+1].
    """
    points = interpolate(waypoints, step_miles)
    segment_miles = [
        float(haversine_miles(a[0], a[1], b[0], b[1])) for a, b in zip(waypoints[:-1], waypoints[1:], strict=True)
    ]
    total_meters = sum(segment_miles) * METERS_PER_MILE
    refs = highway_refs or [""] * len(segment_miles)
    steps = [
        {"distance": miles * METERS_PER_MILE, "ref": ref, "name": "", "geometry": ""}
        for miles, ref in zip(segment_miles, refs, strict=True)
    ]
    steps.append({"distance": 0.0, "ref": "", "name": "", "geometry": ""})
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": total_meters,
                "duration": total_meters / 29.0,
                "geometry": encode_polyline(points),
                "legs": [{"distance": total_meters, "steps": steps}],
            }
        ],
        "waypoints": [],
    }


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON body")
        return self._payload


@dataclass
class FakeSession:
    """Stands in for requests.Session. Records every GET it receives."""

    osrm_payload: dict | None = None
    osrm_status: int = 200
    osrm_exception: Exception | None = None
    nominatim_payload: list | None = None
    calls: list[str] = field(default_factory=list)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if "/route/v1/" in url:
            if self.osrm_exception is not None:
                raise self.osrm_exception
            return FakeResponse(self.osrm_payload, self.osrm_status)
        if "nominatim" in url:
            return FakeResponse(self.nominatim_payload or [], 200)
        raise requests.ConnectionError(f"Unexpected URL in test: {url}")

    @property
    def osrm_calls(self) -> int:
        return sum(1 for url in self.calls if "/route/v1/" in url)

    @property
    def nominatim_calls(self) -> int:
        return sum(1 for url in self.calls if "nominatim" in url)


# Rough I-80 / I-15 / I-10 corridor from New York City to Los Angeles.
NYC_TO_LA_WAYPOINTS = [
    (40.7128, -74.0060),
    (40.7357, -74.1724),
    (40.9860, -75.1950),
    (40.9580, -75.9740),
    (41.0270, -78.4390),
    (41.0998, -80.6495),
    (41.3680, -82.1070),
    (41.6528, -83.5379),
    (41.6764, -86.2520),
    (41.5250, -88.0817),
    (41.5236, -90.5776),
    (41.6611, -91.5302),
    (41.5868, -93.6250),
    (41.2565, -95.9345),
    (40.8136, -96.7026),
    (40.6993, -99.0832),
    (41.1403, -100.7601),
    (41.1280, -101.7190),
    (41.1420, -102.9770),
    (41.1400, -104.8202),
    (41.3114, -105.5911),
    (41.7910, -107.2387),
    (41.5875, -109.2029),
    (41.2683, -110.9632),
    (40.7608, -111.8910),
    (40.2338, -111.6585),
    (38.2769, -112.6410),
    (37.6775, -113.0619),
    (37.0965, -113.5684),
    (36.1699, -115.1398),
    (34.8958, -117.0173),
    (34.1083, -117.2898),
    (34.0522, -118.2437),
]
NYC_TO_LA_REFS = ["I 95"] + ["I 80"] * 23 + ["I 15"] * 7 + ["I 10"]
