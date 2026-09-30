"""Service layer for the fuel route optimizer.

Modules:
    planner.py         end-to-end orchestration used by the views
    geocoding.py       user input -> coordinates (offline gazetteer, Nominatim fallback)
    routing.py         OSRM client (one request per uncached route)
    route_matching.py  places fuel stations along the route (spatial + highway match)
    optimizer.py       greedy minimum-cost refuelling plan
    station_index.py   in-memory numpy/KD-tree view of the FuelStation table
    geo.py, text.py    geometry and text helpers
    exceptions.py      domain errors with HTTP status codes
"""

__all__ = ["plan_trip"]


def plan_trip(
    start_location: str,
    finish_location: str,
    include_geometry: bool = True,
    price_tolerance: float | None = None,
    min_purchase_gallons: float | None = None,
) -> dict:
    """Plan a trip. Imported lazily so loading this package never touches the ORM."""
    from .planner import plan_trip as _plan_trip

    return _plan_trip(start_location, finish_location, include_geometry, price_tolerance, min_purchase_gallons)
