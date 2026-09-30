"""End-to-end check on real data: a captured OSRM response plus the real CSV.

The OSRM response was captured from router.project-osrm.org for
New York, NY -> Los Angeles, CA (2,800 miles). Its geometry is thinned to
~2 mile spacing to keep the fixture small; steps and refs are unchanged.
"""

import json
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from api.services.station_index import reset_station_index
from api.tests.fakes import FakeSession

FIXTURE = Path(__file__).parent / "data" / "osrm_nyc_to_la.json"
OFFLINE = {**settings.FUEL_OPTIMIZER, "GEOCODER_ONLINE_FALLBACK": False}


@override_settings(FUEL_OPTIMIZER=OFFLINE)
class RealRouteRealStationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_fuel_stations", stdout=StringIO())

    def setUp(self):
        cache.clear()
        reset_station_index()
        self.addCleanup(reset_station_index)
        self.session = FakeSession(osrm_payload=json.loads(FIXTURE.read_text()))
        patcher = mock.patch("api.services.routing.get_http_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan(self, **params):
        query = {"start_location": "New York, NY", "finish_location": "Los Angeles, CA", **params}
        response = self.client.get("/api/v1/route/", query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def assert_valid_plan(self, body):
        distance = body["route"]["distance_miles"]
        self.assertAlmostEqual(distance, 2799.9, delta=0.2)
        stops = body["fuel_stops"]
        # A 2,800 mile trip on a 480 mile planning range needs at least 6 fills.
        self.assertGreaterEqual(len(stops), 6)
        self.assertAlmostEqual(body["summary"]["total_gallons"], distance / 10, delta=0.05)

        # Replay the trip: never empty before a stop, never above 48 gallons.
        fuel, position = 0.0, stops[0]["mile_marker"]
        for stop in stops:
            fuel -= (stop["mile_marker"] - position) / 10
            self.assertGreaterEqual(fuel, -0.05)
            fuel += stop["gallons_purchased"]
            self.assertLessEqual(fuel, 48.05)
            position = stop["mile_marker"]
            self.assertLessEqual(stop["distance_from_route_miles"], 15.0)
        self.assertGreaterEqual(fuel - (distance - position) / 10, -0.05)
        self.assertEqual(
            {s["state"] for s in stops} - {"NJ", "PA", "OH", "IN", "IL", "IA", "NE", "CO", "UT", "NV", "CA"}, set()
        )
        return stops

    def test_default_plan(self):
        body = self.plan()
        stops = self.assert_valid_plan(body)
        # Only unavoidable small purchases remain (here: one stop needed to bridge a 487 mile gap).
        self.assertLessEqual(sum(1 for s in stops if s["gallons_purchased"] < 5), 1)
        self.assertEqual(self.session.osrm_calls, 1)
        self.assertLess(body["meta"]["timings_ms"]["station_matching"], 500)

    def test_exact_plan_is_cheapest(self):
        exact = self.plan(price_tolerance=0, min_purchase_gallons=0)
        default = self.plan()
        self.assert_valid_plan(exact)
        self.assertLessEqual(exact["summary"]["total_fuel_cost"], default["summary"]["total_fuel_cost"])
        self.assertLessEqual(default["summary"]["number_of_stops"], exact["summary"]["number_of_stops"])
        # Both plans reuse one cached route.
        self.assertEqual(self.session.osrm_calls, 1)
