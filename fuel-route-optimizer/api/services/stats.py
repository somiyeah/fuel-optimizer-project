"""Per-request counters reported in the API response `meta` block."""

from dataclasses import dataclass, field


@dataclass
class RequestStats:
    routing_api_calls: int = 0
    geocoding_api_calls: int = 0
    route_cache_hit: bool = False
    timings_ms: dict[str, float] = field(default_factory=dict)
