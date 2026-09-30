"""Small, dependency-light geometry helpers (spherical earth model).

Distances use a spherical earth with the mean radius, which is accurate to
about 0.5% and is far more precise than the city-level station coordinates.
"""

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def to_unit_xyz(lat_deg, lon_deg) -> np.ndarray:
    """Convert latitude/longitude (degrees) to 3D points on the unit sphere.

    Nearest-neighbour search in 3D (with a KD-tree) is exact on a sphere,
    unlike search on raw lat/lon degrees, which distorts east-west distances.
    """
    lat = np.radians(np.asarray(lat_deg, dtype=np.float64))
    lon = np.radians(np.asarray(lon_deg, dtype=np.float64))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def chord_to_miles(chord) -> np.ndarray:
    """Straight-line (chord) distance on the unit sphere -> great-circle miles.

    For a central angle theta: chord = 2 * sin(theta / 2), so
    theta = 2 * arcsin(chord / 2) and arc length = R * theta.
    """
    chord = np.clip(np.asarray(chord, dtype=np.float64), 0.0, 2.0)
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(chord / 2.0)


def miles_to_chord(miles: float) -> float:
    """Inverse of chord_to_miles, used as a KD-tree search radius."""
    return float(2.0 * np.sin(min(miles / EARTH_RADIUS_MILES, np.pi) / 2.0))


def haversine_miles(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in miles. Works on scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=np.float64)) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def decode_polyline(encoded: str, precision: int = 6) -> np.ndarray:
    """Decode a Google encoded polyline into an (n, 2) array of (lat, lon).

    OSRM returns `polyline6` (precision 6) geometries, which are about 4x
    smaller than GeoJSON. The decode is vectorised with numpy, so a
    cross-country route of ~50k points decodes in a few milliseconds.

    Format recap: each coordinate delta is zig-zag encoded, split into 5-bit
    chunks (least significant first), and each chunk is stored as chr(value + 63),
    with 0x20 set on every chunk except the last one of a number.
    """
    if not encoded:
        return np.empty((0, 2), dtype=np.float64)
    try:
        raw = np.frombuffer(encoded.encode("ascii"), dtype=np.uint8).astype(np.int64) - 63
    except UnicodeEncodeError as exc:
        raise ValueError("Polyline contains non-ASCII characters.") from exc
    if raw.min() < 0 or raw.max() > 63:
        raise ValueError("Polyline contains characters outside the encoding alphabet.")

    is_last_chunk = raw < 0x20
    if not is_last_chunk[-1]:
        raise ValueError("Polyline is truncated.")

    group_ends = np.flatnonzero(is_last_chunk)
    group_starts = np.concatenate(([0], group_ends[:-1] + 1))
    group_sizes = group_ends - group_starts + 1
    if group_sizes.max() > 12:  # 12 chunks * 5 bits = 60 bits, fits in int64
        raise ValueError("Polyline value is too large.")

    group_ids = np.repeat(np.arange(group_ends.size), group_sizes)
    chunk_positions = np.arange(raw.size) - group_starts[group_ids]
    shifted = (raw & 0x1F) << (5 * chunk_positions)
    values = np.add.reduceat(shifted, group_starts)

    # Undo zig-zag encoding: even -> positive, odd -> negative.
    deltas = np.where(values & 1, ~(values >> 1), values >> 1)
    if deltas.size % 2:
        raise ValueError("Polyline has an odd number of values.")
    return np.cumsum(deltas.reshape(-1, 2), axis=0) / float(10**precision)
