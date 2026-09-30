import random
import unittest

import numpy as np

from api.services.geo import chord_to_miles, decode_polyline, haversine_miles, miles_to_chord
from api.services.text import (
    normalize_city,
    parse_osrm_highways,
    parse_station_highways,
    to_state_code,
)
from api.tests.fakes import encode_polyline


class GeometryTests(unittest.TestCase):
    def test_polyline_round_trip(self):
        rng = random.Random(1)
        points = [(rng.uniform(25, 49), rng.uniform(-124, -67)) for _ in range(500)]
        decoded = decode_polyline(encode_polyline(points, precision=6), precision=6)
        np.testing.assert_allclose(decoded, np.array(points), atol=1e-6)

    def test_polyline_known_value(self):
        # Example from Google's polyline documentation (precision 5).
        decoded = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        np.testing.assert_allclose(decoded, [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]], atol=1e-9)

    def test_polyline_rejects_garbage(self):
        with self.assertRaises(ValueError):
            decode_polyline("_p~iF~ps|U_", precision=5)  # truncated / odd count
        with self.assertRaises(ValueError):
            decode_polyline("abc def", precision=5)  # space is outside the alphabet

    def test_haversine_new_york_to_los_angeles(self):
        miles = float(haversine_miles(40.7128, -74.0060, 34.0522, -118.2437))
        self.assertAlmostEqual(miles, 2445, delta=5)

    def test_chord_conversion_round_trip(self):
        for miles in (0.5, 5.0, 150.0, 2000.0):
            self.assertAlmostEqual(float(chord_to_miles(miles_to_chord(miles))), miles, places=6)


class TextTests(unittest.TestCase):
    def test_station_highways(self):
        cases = {
            "I-44, EXIT 283 & US-69": ("I-44", "US-69"),
            "I-35,  EXIT 271": ("I-35",),
            "I-29 & I-80, EXIT 3": ("I-29", "I-80"),
            "US 70": ("US-70",),
            "US HWY 151": ("US-151",),
            "I-75, EXIT 144-B": ("I-75",),
            "1-40 EXIT 77": ("I-40",),
            "SR-29/SR-55, EXIT 234 & SR-47/SR-117": (),
            "BUS 71": (),
            "": (),
        }
        for address, expected in cases.items():
            with self.subTest(address=address):
                self.assertEqual(parse_station_highways(address), expected)

    def test_osrm_highways(self):
        self.assertEqual(parse_osrm_highways("I 44;US 69", ""), frozenset({"I-44", "US-69"}))
        self.assertEqual(parse_osrm_highways("I 35E", None), frozenset({"I-35"}))
        self.assertEqual(parse_osrm_highways("", "Interstate 80"), frozenset({"I-80"}))
        self.assertEqual(parse_osrm_highways(None, "US Highway 6"), frozenset({"US-6"}))
        self.assertEqual(parse_osrm_highways("CA 99", "Main Street"), frozenset())

    def test_normalize_city(self):
        self.assertEqual(normalize_city("St. Louis"), normalize_city("SAINT LOUIS"))
        self.assertEqual(normalize_city("Ft Worth"), "FORT WORTH")
        self.assertEqual(normalize_city("  Winston-Salem "), "WINSTON SALEM")
        self.assertEqual(normalize_city("N Platte"), "NORTH PLATTE")

    def test_state_codes(self):
        self.assertEqual(to_state_code("tx"), "TX")
        self.assertEqual(to_state_code("Texas"), "TX")
        self.assertEqual(to_state_code("new york"), "NY")
        self.assertEqual(to_state_code("D.C."), "DC")
        self.assertIsNone(to_state_code("ON"))
