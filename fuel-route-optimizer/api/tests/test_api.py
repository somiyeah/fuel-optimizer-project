from decimal import Decimal
from unittest import mock

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from api.models import FuelStation
from api.services.station_index import reset_station_index
from api.tests.fakes import FakeSession, build_osrm_payload

# Chicago -> Denver along I-80 / I-76 (about 1,000 miles, so at least 2 fills).
CHICAGO_TO_DENVER = [
    (41.8781, -87.6298),
    (41.5250, -88.0817),
    (41.5236, -90.5776),
    (41.5868, -93.6250),
    (41.2565, -95.9345),
    (40.8136, -96.7026),
    (40.6993, -99.0832),
    (41.1403, -100.7601),
    (40.9880, -102.2640),
    (40.6255, -103.2077),
    (39.7392, -104.9903),
]
CHICAGO_TO_DENVER_REFS = ["I 55"] + ["I 80"] * 8 + ["I 76"]

STATIONS = [
    # opis_id, name, city, state, lat, lon, price, highways
    (1, "JOLIET TRAVEL CENTER", "Joliet", "IL", 41.525, -88.082, "3.40", "I-80"),
    (2, "DAVENPORT FUEL", "Davenport", "IA", 41.524, -90.578, "3.10", "I-80"),
    (3, "DES MOINES PLAZA", "Des Moines", "IA", 41.587, -93.625, "3.30", "I-80,I-35"),
    (4, "OMAHA STOP", "Omaha", "NE", 41.257, -95.935, "2.95", "I-80"),
    (5, "KEARNEY TRUCK STOP", "Kearney", "NE", 40.699, -99.083, "3.20", "I-80"),
    (6, "NORTH PLATTE FUEL", "North Platte", "NE", 41.140, -100.760, "3.05", "I-80"),
    (7, "STERLING STATION", "Sterling", "CO", 40.626, -103.208, "3.50", "I-76"),
    (8, "FAR AWAY FUEL", "Wichita", "KS", 37.687, -97.330, "1.99", "I-35"),  # not on route
]


def create_stations(rows=STATIONS):
    FuelStation.objects.bulk_create(
        FuelStation(
            opis_id=opis_id,
            name=name,
            address=highways.replace(",", " & "),
            city=city,
            state=state,
            retail_price=Decimal(price),
            latitude=lat,
            longitude=lon,
            highways=highways,
        )
        for opis_id, name, city, state, lat, lon, price, highways in rows
    )


OFFLINE = {**settings.FUEL_OPTIMIZER, "GEOCODER_ONLINE_FALLBACK": False}


@override_settings(FUEL_OPTIMIZER=OFFLINE)
class RoutePlanApiTests(TestCase):
    url = "/api/v1/route/"

    def setUp(self):
        cache.clear()
        reset_station_index()
        create_stations()
        self.session = FakeSession(osrm_payload=build_osrm_payload(CHICAGO_TO_DENVER, CHICAGO_TO_DENVER_REFS))
        patchers = [
            mock.patch("api.services.routing.get_http_session", return_value=self.session),
            mock.patch("api.services.geocoding.get_http_session", return_value=self.session),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(reset_station_index)

    def get_plan(self, **params):
        query = {"start_location": "Chicago, IL", "finish_location": "Denver, CO", **params}
        return self.client.get(self.url, query)

    # ---- happy path -------------------------------------------------------- #

    def test_get_returns_plan_with_expected_shape(self):
        response = self.get_plan()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()

        self.assertEqual(body["start"]["resolved_as"], "Chicago, IL")
        self.assertEqual(body["finish"]["resolved_as"], "Denver, CO")
        self.assertGreater(body["route"]["distance_miles"], 900)
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertEqual(body["vehicle"]["max_range_miles"], 500.0)
        self.assertEqual(body["vehicle"]["planning_range_miles"], 480.0)

        stops = body["fuel_stops"]
        self.assertGreaterEqual(len(stops), 2)
        self.assertNotIn("FAR AWAY FUEL", [s["name"] for s in stops])
        for key in ("name", "address", "city", "state", "price_per_gallon", "gallons_purchased", "cost", "mile_marker"):
            self.assertIn(key, stops[0])
        for stop in stops:
            self.assertLessEqual(stop["fuel_on_departure_gallons"], 48.0 + 0.01)

        summary = body["summary"]
        # Every mile is paid for: gallons = distance / 10 mpg.
        self.assertAlmostEqual(summary["total_gallons"], body["route"]["distance_miles"] / 10, delta=0.05)
        # Total = sum of stop costs + the short drive to the first stop.
        expected_total = sum(s["cost"] for s in stops) + summary["origin_approach"]["cost"]
        self.assertAlmostEqual(summary["total_fuel_cost"], expected_total, delta=0.05)
        self.assertIn("/api/v1/route/map/?", body["map_url"])

    def test_calls_routing_api_exactly_once(self):
        response = self.get_plan()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.osrm_calls, 1)
        self.assertEqual(response.json()["meta"]["routing_api_calls"], 1)
        self.assertEqual(response.json()["meta"]["geocoding_api_calls"], 0)

    def test_repeat_request_and_map_page_reuse_the_cached_route(self):
        self.get_plan()
        second = self.get_plan()
        self.assertTrue(second.json()["meta"]["route_cache_hit"])
        map_page = self.client.get(
            reverse("api:route-map"), {"start_location": "Chicago, IL", "finish_location": "Denver, CO"}
        )
        self.assertEqual(map_page.status_code, 200)
        self.assertEqual(self.session.osrm_calls, 1)

    def test_post_json_and_omit_geometry(self):
        response = self.client.post(
            self.url,
            {"start_location": "Chicago, IL", "finish_location": "Denver, CO", "include_geometry": False},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["route"]["geometry"])

    def test_cheapest_stations_are_preferred(self):
        stops = self.get_plan(price_tolerance=0).json()["fuel_stops"]
        names = [s["name"] for s in stops]
        self.assertIn("OMAHA STOP", names)  # $2.95, the cheapest on the route
        self.assertNotIn("STERLING STATION", names)  # $3.50 and not needed

    def test_price_tolerance_is_validated(self):
        response = self.get_plan(price_tolerance=5)
        self.assertEqual(response.status_code, 400)
        self.assertIn("price_tolerance", response.json()["error"]["details"])

    # ---- input errors --------------------------------------------------- #

    def test_missing_parameters(self):
        response = self.client.get(self.url, {"start_location": "Chicago, IL"})
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_request")
        self.assertIn("finish_location", error["details"])
        self.assertEqual(self.session.calls, [])

    def test_same_start_and_finish(self):
        response = self.get_plan(finish_location="chicago, il")
        self.assertEqual(response.status_code, 400)

    def test_unknown_location(self):
        response = self.get_plan(finish_location="Atlantis")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "location_not_found")
        self.assertEqual(self.session.osrm_calls, 0)

    def test_location_outside_usa(self):
        response = self.get_plan(finish_location="48.8566,2.3522")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "location_outside_usa")

    # ---- upstream and planning errors ------------------------------------ #

    def test_routing_timeout(self):
        self.session.osrm_exception = requests.Timeout("slow")
        response = self.get_plan()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "routing_timeout")
        self.assertEqual(self.session.osrm_calls, 1)  # no silent retries

    def test_routing_connection_error(self):
        self.session.osrm_exception = requests.ConnectionError("down")
        response = self.get_plan()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "routing_unavailable")

    def test_routing_server_error(self):
        self.session.osrm_payload = None
        self.session.osrm_status = 503
        response = self.get_plan()
        self.assertEqual(response.status_code, 502)

    def test_no_route(self):
        self.session.osrm_payload = {"code": "NoRoute", "message": "Impossible route between points"}
        self.session.osrm_status = 400
        response = self.get_plan()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "no_route")

    def test_gap_longer_than_range(self):
        FuelStation.objects.exclude(opis_id__in=[1, 2]).delete()  # nothing west of Iowa
        reset_station_index()
        response = self.get_plan()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "no_feasible_fuel_plan")

    def test_no_stations_loaded(self):
        FuelStation.objects.all().delete()
        reset_station_index()
        response = self.get_plan()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "fuel_data_not_loaded")


@override_settings(FUEL_OPTIMIZER=OFFLINE)
class MapAndHealthTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_station_index()
        self.addCleanup(reset_station_index)

    def test_map_page_renders_route_and_stops(self):
        create_stations()
        session = FakeSession(osrm_payload=build_osrm_payload(CHICAGO_TO_DENVER, CHICAGO_TO_DENVER_REFS))
        with mock.patch("api.services.routing.get_http_session", return_value=session):
            response = self.client.get(
                reverse("api:route-map"), {"start_location": "Chicago, IL", "finish_location": "Denver, CO"}
            )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("leaflet", html)
        self.assertIn("Total fuel cost", html)
        self.assertIn("OMAHA STOP", html)
        # OpenStreetMap blocks tile requests without a Referer, so the page must
        # not use Django's default "same-origin" policy.
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("https://tile.openstreetmap.org/{z}/{x}/{y}.png", html)

    def test_map_page_shows_errors(self):
        response = self.client.get(reverse("api:route-map"), {"start_location": "Chicago, IL"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish_location", response.content.decode())

    def test_health(self):
        self.assertEqual(self.client.get("/api/v1/health/").status_code, 503)
        create_stations()
        reset_station_index()
        body = self.client.get("/api/v1/health/").json()
        self.assertEqual(body, {"status": "ok", "fuel_stations_loaded": len(STATIONS)})
