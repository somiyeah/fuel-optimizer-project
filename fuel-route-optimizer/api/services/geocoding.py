"""Location resolution: turn user input into coordinates inside the USA.

Accepted inputs, in the order they are tried:

1. Coordinates           "41.8781,-87.6298" or "41.8781 -87.6298" (latitude first)
2. Street addresses      "1600 Pennsylvania Ave NW, Washington, DC 20500"
                         -> Nominatim (one HTTP call, cached), if enabled
3. ZIP codes             "60601", or any input ending in a ZIP
4. City and state        "Chicago, IL", "chicago il", "St. Louis, Missouri"
5. Anything else         -> Nominatim, if enabled

Steps 1, 3 and 4 run fully offline against a bundled gazetteer built from US
ZIP code centroids (see `build_gazetteer`), so the common inputs cost zero
external API calls and resolve in microseconds. If Nominatim fails for a
street address, the resolver falls back to the address's ZIP or city.
"""

import csv
import gzip
import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import requests
from django.core.cache import cache
from scipy.spatial import cKDTree

from ..conf import OptimizerConfig
from .exceptions import (
    GazetteerMissingError,
    GeocodingServiceError,
    GeocodingTimeoutError,
    InvalidLocationError,
    LocationNotFoundError,
    LocationOutsideUSAError,
)
from .geo import chord_to_miles, to_unit_xyz
from .http import get_http_session
from .stats import RequestStats
from .text import compact_city, normalize_city, to_state_code

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedLocation:
    query: str
    latitude: float
    longitude: float
    label: str
    source: str  # coordinates | zip_code | city_state | nominatim


@dataclass(frozen=True)
class Place:
    latitude: float
    longitude: float
    city: str
    state: str

    @property
    def label(self) -> str:
        return f"{self.city}, {self.state}"


# --------------------------------------------------------------------------- #
# Offline gazetteer
# --------------------------------------------------------------------------- #


class Gazetteer:
    """In-memory lookup tables for US cities and ZIP codes.

    Built once per process from two small gzip CSV files (~40k ZIPs, ~35k city
    names). Loading takes ~150 ms; every lookup afterwards is a dict access.
    """

    def __init__(self, places_file: Path, zips_file: Path):
        if not places_file.exists() or not zips_file.exists():
            raise GazetteerMissingError(details={"places_file": str(places_file), "zips_file": str(zips_file)})
        self._cities: dict[tuple[str, str], Place] = {}
        self._compact_cities: dict[tuple[str, str], Place] = {}
        self._zips: dict[str, Place] = {}

        with gzip.open(places_file, "rt", encoding="utf-8", newline="") as handle:
            # Rows are sorted so primary city names come before postal aliases;
            # setdefault keeps the first (primary) entry for each key.
            for row in csv.DictReader(handle):
                place = Place(float(row["latitude"]), float(row["longitude"]), row["city"], row["state"])
                self._cities.setdefault((row["state"], normalize_city(row["city"])), place)
                self._compact_cities.setdefault((row["state"], compact_city(row["city"])), place)

        zip_codes, zip_lat, zip_lon = [], [], []
        with gzip.open(zips_file, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                place = Place(float(row["latitude"]), float(row["longitude"]), row["city"], row["state"])
                self._zips[row["zip"]] = place
                zip_codes.append(row["zip"])
                zip_lat.append(place.latitude)
                zip_lon.append(place.longitude)

        self._zip_codes = zip_codes
        self._zip_tree = cKDTree(to_unit_xyz(zip_lat, zip_lon))

    def lookup_city(self, city: str, state: str) -> Place | None:
        return self._cities.get((state, normalize_city(city))) or self._compact_cities.get((state, compact_city(city)))

    def lookup_zip(self, zip_code: str) -> Place | None:
        return self._zips.get(zip_code)

    def distance_to_nearest_zip_miles(self, latitude: float, longitude: float) -> float:
        chord, _ = self._zip_tree.query(to_unit_xyz([latitude], [longitude])[0], k=1)
        return float(chord_to_miles(chord))


_gazetteer: Gazetteer | None = None
_gazetteer_lock = threading.Lock()


def get_gazetteer(config: OptimizerConfig) -> Gazetteer:
    """Process-wide gazetteer, built on first use (thread safe)."""
    global _gazetteer
    if _gazetteer is None:
        with _gazetteer_lock:
            if _gazetteer is None:
                _gazetteer = Gazetteer(Path(config.gazetteer_places_file), Path(config.gazetteer_zips_file))
    return _gazetteer


def reset_gazetteer() -> None:
    global _gazetteer
    with _gazetteer_lock:
        _gazetteer = None


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #

_COORDINATES_RE = re.compile(
    r"^\s*\(?\s*(?P<lat>[-+]?\d{1,3}(?:\.\d+)?)\s*(?:,|\s)\s*(?P<lon>[-+]?\d{1,3}(?:\.\d+)?)\s*\)?\s*$"
)
_TRAILING_ZIP_RE = re.compile(r"(?:^|[\s,])(?P<zip>\d{5})(?:-\d{4})?\s*$")
_COUNTRY_SUFFIX_RE = re.compile(r"[\s,]+(?:USA|U\.S\.A\.?|US|U\.S\.?|UNITED STATES(?: OF AMERICA)?)\s*$", re.IGNORECASE)
_STREET_ADDRESS_RE = re.compile(r"^\s*\d+[A-Za-z]?(?:-\d+)?\s+[A-Za-z]")

SUPPORTED_FORMATS = [
    "City, ST (e.g. 'Chicago, IL')",
    "City, State (e.g. 'Austin, Texas')",
    "ZIP code (e.g. '60601')",
    "Coordinates as 'latitude,longitude' (e.g. '41.8781,-87.6298')",
    "Street address (e.g. '1600 Pennsylvania Ave NW, Washington, DC 20500')",
]


class LocationResolver:
    def __init__(self, config: OptimizerConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or get_http_session()

    # ---- public ----------------------------------------------------------- #

    def resolve(self, raw: str, stats: RequestStats) -> ResolvedLocation:
        query = (raw or "").strip()
        if not query:
            raise InvalidLocationError("Location must not be empty.")
        gazetteer = get_gazetteer(self.config)

        coordinates = self._parse_coordinates(query)
        if coordinates is not None:
            latitude, longitude = coordinates
            self._ensure_in_usa(gazetteer, query, latitude, longitude)
            return ResolvedLocation(query, latitude, longitude, f"{latitude:.5f}, {longitude:.5f}", "coordinates")

        text = _COUNTRY_SUFFIX_RE.sub("", query).strip(" ,")
        tried_online = False

        # Street addresses need street-level precision, so prefer Nominatim.
        if self.config.geocoder_online_fallback and _STREET_ADDRESS_RE.match(text) and "," in text:
            tried_online = True
            try:
                return self._resolve_online(gazetteer, query, stats)
            except (GeocodingServiceError, GeocodingTimeoutError, LocationNotFoundError) as exc:
                logger.warning("Nominatim failed for %r (%s); trying offline lookup.", query, exc.code)

        offline = self._resolve_offline(gazetteer, query, text)
        if offline is not None:
            return offline

        if self.config.geocoder_online_fallback and not tried_online:
            return self._resolve_online(gazetteer, query, stats)

        raise LocationNotFoundError(
            f"Could not find '{query}' in the USA.",
            details={"supported_formats": SUPPORTED_FORMATS},
        )

    # ---- helpers ---------------------------------------------------------- #

    @staticmethod
    def _parse_coordinates(query: str) -> tuple[float, float] | None:
        match = _COORDINATES_RE.match(query)
        if not match:
            return None
        latitude, longitude = float(match["lat"]), float(match["lon"])
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            details = {"expected_format": "latitude,longitude (e.g. 41.8781,-87.6298)"}
            if -90.0 <= longitude <= 90.0 and -180.0 <= latitude <= 180.0:
                details["hint"] = "The values look swapped. Put latitude first."
            raise InvalidLocationError(f"'{query}' is not a valid coordinate pair.", details=details)
        return latitude, longitude

    def _ensure_in_usa(self, gazetteer: Gazetteer, query: str, latitude: float, longitude: float) -> None:
        limit = self.config.us_point_max_distance_miles
        if gazetteer.distance_to_nearest_zip_miles(latitude, longitude) <= limit:
            return
        details: dict = {}
        # Common mistake: longitude and latitude swapped.
        if -90.0 <= longitude <= 90.0 and gazetteer.distance_to_nearest_zip_miles(longitude, latitude) <= limit:
            details["hint"] = "Coordinates must be 'latitude,longitude'. The swapped pair is inside the USA."
        raise LocationOutsideUSAError(f"'{query}' is not inside the USA.", details=details)

    def _resolve_offline(self, gazetteer: Gazetteer, query: str, text: str) -> ResolvedLocation | None:
        # ZIP code (alone, or at the end of an address).
        zip_match = _TRAILING_ZIP_RE.search(text)
        if zip_match:
            place = gazetteer.lookup_zip(zip_match["zip"])
            if place is not None:
                return ResolvedLocation(
                    query, place.latitude, place.longitude, f"{place.label} {zip_match['zip']}", "zip_code"
                )
            text = text[: zip_match.start()].strip(" ,")

        for city, state in self._city_state_candidates(text):
            place = gazetteer.lookup_city(city, state)
            if place is not None:
                return ResolvedLocation(query, place.latitude, place.longitude, place.label, "city_state")
        return None

    @staticmethod
    def _city_state_candidates(text: str) -> list[tuple[str, str]]:
        """Possible (city, state_code) splits of the input, most likely first."""
        candidates: list[tuple[str, str]] = []
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            state = to_state_code(parts[-1])
            if state:
                candidates.append((parts[-2], state))
        # "Chicago IL", "Austin Texas", "Fargo North Dakota", "Washington DC".
        words = text.replace(",", " ").split()
        for state_words in (1, 2, 3):
            if len(words) > state_words:
                state = to_state_code(" ".join(words[-state_words:]))
                if state:
                    candidates.append((" ".join(words[:-state_words]), state))
        return candidates

    def _resolve_online(self, gazetteer: Gazetteer, query: str, stats: RequestStats) -> ResolvedLocation:
        cache_key = "geocode:v1:" + hashlib.sha256(query.lower().encode("utf-8")).hexdigest()
        cached = cache.get(cache_key)
        if cached is None:
            cached = self._call_nominatim(query, stats)
            cache.set(cache_key, cached, self.config.geocode_cache_seconds)
        latitude, longitude, label = cached
        self._ensure_in_usa(gazetteer, query, latitude, longitude)
        return ResolvedLocation(query, latitude, longitude, label, "nominatim")

    def _call_nominatim(self, query: str, stats: RequestStats) -> tuple[float, float, str]:
        params = {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us", "addressdetails": 0}
        if self.config.nominatim_email:
            params["email"] = self.config.nominatim_email
        stats.geocoding_api_calls += 1
        try:
            response = self.session.get(
                self.config.nominatim_url,
                params=params,
                headers={"User-Agent": self.config.http_user_agent, "Accept": "application/json"},
                timeout=self.config.nominatim_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise GeocodingTimeoutError() from exc
        except requests.RequestException as exc:
            raise GeocodingServiceError(details={"reason": str(exc)}) from exc

        if response.status_code != 200:
            raise GeocodingServiceError(
                f"The geocoding service returned HTTP {response.status_code}.",
                details={"status": response.status_code},
            )
        try:
            results = response.json()
        except ValueError as exc:
            raise GeocodingServiceError("The geocoding service returned invalid JSON.") from exc
        if not results:
            raise LocationNotFoundError(
                f"Could not find '{query}' in the USA.",
                details={"supported_formats": SUPPORTED_FORMATS},
            )
        best = results[0]
        return float(best["lat"]), float(best["lon"]), best.get("display_name") or query
