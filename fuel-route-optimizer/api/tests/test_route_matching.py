import unittest

import numpy as np

from api.conf import OptimizerConfig
from api.services.geo import haversine_miles
from api.services.route_matching import densify, match_stations_to_route, sample_geometry
from api.services.routing import OSRMClient
from api.services.station_index import StationIndex, StationRecord
from api.tests.fakes import build_osrm_payload


def make_config(**overrides) -> OptimizerConfig:
    from api.conf import _DEFAULTS

    values = {key.lower(): value for key, value in _DEFAULTS.items()}
    values.update(overrides)
    return OptimizerConfig(**values)


def station(opis_id, lat, lon, highways=(), price=3.0):
    return StationRecord(opis_id, f"S{opis_id}", "", "Town", "NE", lat, lon, price, frozenset(highways))


class DensifyTests(unittest.TestCase):
    def test_spacing_and_cumulative_distance(self):
        coords = np.array([[40.0, -100.0], [40.0, -99.0], [41.0, -99.0]])
        dense, cumulative = densify(coords, 0.5)
        steps = haversine_miles(dense[:-1, 0], dense[:-1, 1], dense[1:, 0], dense[1:, 1])
        self.assertLessEqual(steps.max(), 0.5 + 1e-6)
        expected = haversine_miles(40, -100, 40, -99) + haversine_miles(40, -99, 41, -99)
        self.assertAlmostEqual(cumulative[-1], float(expected), places=6)
        self.assertTrue(np.all(np.diff(cumulative) >= 0))
        np.testing.assert_allclose(dense[-1], coords[-1])

    def test_sample_geometry_keeps_endpoints_and_limits_size(self):
        coords = np.column_stack((np.linspace(30, 40, 10_000), np.linspace(-100, -90, 10_000)))
        sampled = sample_geometry(coords, 500)
        self.assertLessEqual(len(sampled), 500)
        self.assertEqual(sampled[0], [-100.0, 30.0])
        self.assertEqual(sampled[-1], [-90.0, 40.0])


class MatchStationsTests(unittest.TestCase):
    """Route along latitude 40: I-80 from lon -100 to -95, then US-34 to lon -90."""

    def setUp(self):
        payload = build_osrm_payload([(40.0, -100.0), (40.0, -95.0), (40.0, -90.0)], ["I 80", "US 34"], 1.0)
        self.route = OSRMClient._parse(payload)
        self.index = StationIndex(
            [
                station(1, 40.05, -99.0, ["I-80"]),  # 3.5 mi off, highway matches     -> keep
                station(2, 40.15, -98.0, ["I-80"]),  # 10.4 mi off, highway matches    -> keep
                station(3, 40.30, -97.0, ["I-80"]),  # 20.7 mi off                     -> drop
                station(4, 40.02, -94.0, ["I-35"]),  # 1.4 mi off, other interstate    -> keep
                station(5, 40.05, -93.0, ["I-35"]),  # 3.5 mi off, other interstate    -> drop
                station(6, 40.06, -92.0),  # 4.1 mi off, no highway info               -> keep
                station(7, 40.10, -91.0),  # 6.9 mi off, no highway info               -> drop
                station(8, 42.00, -95.0, ["I-80"]),  # far away                        -> drop
            ]
        )

    def matched_ids(self, route, config):
        return [self.index.records[m.station_index].opis_id for m in match_stations_to_route(route, self.index, config)]

    def test_corridor_depends_on_highway_match(self):
        matched = match_stations_to_route(self.route, self.index, make_config())
        ids = [self.index.records[m.station_index].opis_id for m in matched]
        self.assertEqual(ids, [1, 2, 4, 6])
        by_id = {self.index.records[m.station_index].opis_id: m for m in matched}
        self.assertTrue(by_id[1].highway_match)
        self.assertFalse(by_id[4].highway_match)
        # Mile markers follow the route: 1 degree of longitude at 40N is ~53 miles.
        self.assertAlmostEqual(by_id[1].mile_marker, 53.0, delta=1.0)
        self.assertAlmostEqual(by_id[1].distance_from_route_miles, 3.45, delta=0.3)
        markers = [m.mile_marker for m in matched]
        self.assertEqual(markers, sorted(markers))

    def test_without_highway_refs_uses_default_corridor(self):
        payload = build_osrm_payload([(40.0, -100.0), (40.0, -90.0)], [""], 1.0)
        route = OSRMClient._parse(payload)
        self.assertFalse(route.has_highway_refs)
        self.assertEqual(self.matched_ids(route, make_config()), [1, 4, 5, 6])

    def test_mile_markers_are_scaled_to_osrm_distance(self):
        payload = build_osrm_payload([(40.0, -100.0), (40.0, -95.0), (40.0, -90.0)], ["I 80", "US 34"], 1.0)
        payload["routes"][0]["distance"] *= 1.1  # roads are longer than straight lines
        route = OSRMClient._parse(payload)
        matched = match_stations_to_route(route, self.index, make_config())
        self.assertAlmostEqual(matched[0].mile_marker, 53.0 * 1.1, delta=1.5)
