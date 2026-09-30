from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from api.conf import get_config
from api.services.exceptions import (
    InvalidLocationError,
    LocationNotFoundError,
    LocationOutsideUSAError,
)
from api.services.geocoding import LocationResolver
from api.services.stats import RequestStats
from api.tests.fakes import FakeSession

OFFLINE = {**settings.FUEL_OPTIMIZER, "GEOCODER_ONLINE_FALLBACK": False}
ONLINE = {**settings.FUEL_OPTIMIZER, "GEOCODER_ONLINE_FALLBACK": True}


@override_settings(FUEL_OPTIMIZER=OFFLINE)
class OfflineResolutionTests(SimpleTestCase):
    def setUp(self):
        self.session = FakeSession()
        self.resolver = LocationResolver(get_config(), self.session)
        self.stats = RequestStats()

    def resolve(self, text):
        return self.resolver.resolve(text, self.stats)

    def test_city_state_formats(self):
        for text in ("Chicago, IL", "chicago il", "Chicago, Illinois", "CHICAGO,IL, USA"):
            with self.subTest(text=text):
                location = self.resolve(text)
                self.assertEqual(location.source, "city_state")
                self.assertAlmostEqual(location.latitude, 41.85, delta=0.3)
                self.assertAlmostEqual(location.longitude, -87.65, delta=0.3)

    def test_abbreviations_and_multi_word_states(self):
        self.assertEqual(self.resolve("St. Louis, MO").label, "Saint Louis, MO")
        self.assertEqual(self.resolve("Fargo North Dakota").label, "Fargo, ND")
        self.assertEqual(self.resolve("Washington, DC").label, "Washington, DC")

    def test_zip_code(self):
        location = self.resolve("78701")  # downtown Austin, TX
        self.assertEqual(location.source, "zip_code")
        self.assertAlmostEqual(location.latitude, 30.27, delta=0.1)

    def test_street_address_resolves_through_zip_when_offline(self):
        location = self.resolve("1600 Pennsylvania Ave NW, Washington, DC 20500")
        self.assertEqual(location.source, "zip_code")
        self.assertAlmostEqual(location.latitude, 38.9, delta=0.1)

    def test_coordinates(self):
        location = self.resolve("39.7392, -104.9903")
        self.assertEqual(location.source, "coordinates")
        self.assertEqual((location.latitude, location.longitude), (39.7392, -104.9903))
        self.assertEqual(self.resolve("39.7392 -104.9903").source, "coordinates")

    def test_invalid_coordinates(self):
        with self.assertRaises(InvalidLocationError):
            self.resolve("139.7, -104.9")

    def test_coordinates_outside_usa(self):
        with self.assertRaises(LocationOutsideUSAError):
            self.resolve("51.5074, -0.1278")  # London
        with self.assertRaises(LocationOutsideUSAError):
            self.resolve("43.6532, -79.3832")  # Toronto

    def test_swapped_coordinates_get_a_hint(self):
        with self.assertRaises(InvalidLocationError) as ctx:
            self.resolve("-104.9903, 39.7392")  # Denver as lon,lat: latitude out of range
        self.assertIn("hint", ctx.exception.details)
        with self.assertRaises(LocationOutsideUSAError) as ctx:
            self.resolve("-87.6298, 41.8781")  # Chicago as lon,lat: valid point in Antarctica
        self.assertIn("hint", ctx.exception.details)

    def test_unknown_place(self):
        with self.assertRaises(LocationNotFoundError):
            self.resolve("Toronto, ON")
        with self.assertRaises(LocationNotFoundError):
            self.resolve("Atlantis")
        with self.assertRaises(InvalidLocationError):
            self.resolve("   ")

    def test_offline_lookups_make_no_http_calls(self):
        self.resolve("Chicago, IL")
        self.resolve("60601")
        self.assertEqual(self.session.calls, [])
        self.assertEqual(self.stats.geocoding_api_calls, 0)


@override_settings(FUEL_OPTIMIZER=ONLINE)
class OnlineFallbackTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.stats = RequestStats()

    def test_street_address_uses_nominatim_and_caches(self):
        session = FakeSession(
            nominatim_payload=[{"lat": "38.8977", "lon": "-77.0365", "display_name": "White House, Washington, DC"}]
        )
        resolver = LocationResolver(get_config(), session)
        first = resolver.resolve("1600 Pennsylvania Ave NW, Washington, DC", self.stats)
        second = resolver.resolve("1600 Pennsylvania Ave NW, Washington, DC", self.stats)
        self.assertEqual(first.source, "nominatim")
        self.assertEqual(second.latitude, 38.8977)
        self.assertEqual(session.nominatim_calls, 1)
        self.assertEqual(self.stats.geocoding_api_calls, 1)

    def test_nominatim_failure_falls_back_to_offline_city(self):
        session = FakeSession(nominatim_payload=[])  # "not found"
        resolver = LocationResolver(get_config(), session)
        location = resolver.resolve("123 Main St, Springfield, IL", self.stats)
        self.assertEqual(location.source, "city_state")
        self.assertEqual(location.label, "Springfield, IL")

    def test_city_state_never_calls_nominatim(self):
        session = FakeSession()
        LocationResolver(get_config(), session).resolve("Austin, TX", self.stats)
        self.assertEqual(session.calls, [])
