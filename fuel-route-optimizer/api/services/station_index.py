"""In-memory, numpy-backed view of the fuel stations table.

The table holds ~6.7k US stations. Loading them once per process into flat
arrays (plus a KD-tree) lets every request match stations to its route with
vectorised math instead of thousands of ORM objects or SQL queries.

After reloading the CSV with `load_fuel_stations`, restart the web server (or
call `reset_station_index()`) so workers pick up the new data.
"""

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from ..models import FuelStation
from .geo import chord_to_miles, miles_to_chord, to_unit_xyz


@dataclass(frozen=True)
class StationRecord:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    price: float
    highways: frozenset[str]


class StationIndex:
    def __init__(self, records: list[StationRecord]):
        self.records = records
        self.size = len(records)
        self.latitudes = np.fromiter((r.latitude for r in records), dtype=np.float64, count=self.size)
        self.longitudes = np.fromiter((r.longitude for r in records), dtype=np.float64, count=self.size)
        self.prices = np.fromiter((r.price for r in records), dtype=np.float64, count=self.size)
        self.xyz = to_unit_xyz(self.latitudes, self.longitudes) if self.size else np.empty((0, 3))
        self.tree = cKDTree(self.xyz) if self.size else None

    @classmethod
    def from_database(cls) -> "StationIndex":
        rows = FuelStation.objects.order_by("opis_id").values_list(
            "opis_id", "name", "address", "city", "state", "latitude", "longitude", "retail_price", "highways"
        )
        records = [
            StationRecord(
                opis_id=opis_id,
                name=name,
                address=address,
                city=city,
                state=state,
                latitude=latitude,
                longitude=longitude,
                price=float(price),
                highways=frozenset(filter(None, highways.split(","))),
            )
            for opis_id, name, address, city, state, latitude, longitude, price, highways in rows
        ]
        return cls(records)

    def nearest(self, latitude: float, longitude: float, max_miles: float) -> tuple[int, float] | None:
        """Index and distance (miles) of the closest station within max_miles."""
        if self.tree is None:
            return None
        chord, index = self.tree.query(
            to_unit_xyz([latitude], [longitude])[0], k=1, distance_upper_bound=miles_to_chord(max_miles)
        )
        if not np.isfinite(chord):
            return None
        return int(index), float(chord_to_miles(chord))


_index: StationIndex | None = None
_lock = threading.Lock()


def get_station_index() -> StationIndex:
    """Process-wide station index. An empty index is re-read on every call,
    so a server started before the CSV was loaded recovers without a restart."""
    global _index
    if _index is None or _index.size == 0:
        with _lock:
            if _index is None or _index.size == 0:
                _index = StationIndex.from_database()
    return _index


def reset_station_index() -> None:
    global _index
    with _lock:
        _index = None
