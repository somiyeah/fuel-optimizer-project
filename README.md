[README.md](https://github.com/user-attachments/files/32862562/README.md)
# Fuel Route Optimizer

A Django REST API that takes a start and finish location in the USA and returns:

- the driving route (GeoJSON line plus an interactive map page),
- the cheapest places to refuel along it for a vehicle with a 500 mile range,
- how many gallons to buy at each stop and what it costs,
- the total fuel cost for the trip at 10 miles per gallon.

It calls the routing API (OSRM) **once per trip**, geocodes most inputs offline, and plans a coast-to-coast trip in about 35 ms of server time plus the single OSRM call.

```
GET /api/v1/route/?start_location=New York, NY&finish_location=Los Angeles, CA

2,799.9 miles, 10 fuel stops, 279.99 gallons, total $853.46
routing_api_calls: 1   geocoding_api_calls: 0   server time: 33 ms
```

A full sample response is in [`docs/sample_response_nyc_to_la.json`](docs/sample_response_nyc_to_la.json).

---

## 1. Quick start

Requires **Python 3.12+** (Django 6.1 needs it).

```bash
# 1. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Create the database tables
python manage.py migrate

# 3. Load the fuel price CSV (data/fuel-prices-for-be-assessment.csv) into the database
python manage.py load_fuel_stations

# 4. Start the server
python manage.py runserver
```

Step 3 prints what it loaded:

```
CSV rows read:              8,151
  skipped (outside USA): 620
Unique US stations:         6,626
Geocoded (city centroid):   6,626
With Interstate/US route:   5,693
Loaded 6,626 fuel stations.
```

Then try it:

```bash
# JSON plan (GET)
curl "http://127.0.0.1:8000/api/v1/route/?start_location=Chicago,%20IL&finish_location=Denver,%20CO"

# JSON plan (POST)
curl -X POST http://127.0.0.1:8000/api/v1/route/ \
  -H "Content-Type: application/json" \
  -d '{"start_location": "New York, NY", "finish_location": "Los Angeles, CA", "include_geometry": false}'
```

Open the map in a browser (it reuses the cached route, so no extra OSRM call):

```
http://127.0.0.1:8000/api/v1/route/map/?start_location=New York, NY&finish_location=Los Angeles, CA
```

Every JSON response also contains a ready-made `map_url`.

Run the tests (64 tests, about 2 seconds, no network needed):

```bash
python manage.py test api
```

---

## 2. API reference

### `GET /api/v1/route/` and `POST /api/v1/route/`

| Parameter | Required | Description |
|---|---|---|
| `start_location` | yes | A place in the USA (formats below) |
| `finish_location` | yes | A place in the USA |
| `include_geometry` | no | `true` (default) returns the route as a GeoJSON LineString (up to 2,500 points) |
| `price_tolerance` | no | USD/gal treated as "same price", default `0.05`. `0` = exact prices |
| `min_purchase_gallons` | no | Fold stops that buy less than this into a neighbouring stop, default `5`. `0` = off |

Accepted location formats:

| Format | Example | Resolved |
|---|---|---|
| City, state code | `Chicago, IL`, `chicago il` | offline |
| City, state name | `Austin, Texas`, `Fargo North Dakota` | offline |
| ZIP code | `60601` | offline |
| Coordinates (latitude first) | `41.8781,-87.6298` | offline |
| Street address | `1600 Pennsylvania Ave NW, Washington, DC 20500` | Nominatim (1 call, cached), falls back to its ZIP or city if Nominatim fails |

### Response (trimmed)

```json
{
  "start":  {"query": "New York, NY", "resolved_as": "New York, NY", "latitude": 40.7568, "longitude": -73.97967, "source": "city_state"},
  "finish": {"query": "Los Angeles, CA", "resolved_as": "Los Angeles, CA", "latitude": 34.03655, "longitude": -118.29492, "source": "city_state"},
  "route": {"distance_miles": 2799.9, "duration_hours": 49.84, "geometry": {"type": "LineString", "coordinates": [[-73.97949, 40.75705], "..."]}},
  "vehicle": {"max_range_miles": 500.0, "planning_range_miles": 480.0, "miles_per_gallon": 10.0, "tank_capacity_gallons": 50.0},
  "fuel_stops": [
    {
      "sequence": 1,
      "opis_id": 62790,
      "name": "7-ELEVEN #40084",
      "address": "US-46/US-1/US-9",
      "city": "Palisades Park",
      "state": "NJ",
      "latitude": 40.8462,
      "longitude": -73.9954,
      "price_per_gallon": 3.099,
      "mile_marker": 5.3,
      "distance_from_route_miles": 4.9,
      "highway_match": true,
      "gallons_purchased": 48.0,
      "cost": 148.75,
      "fuel_on_arrival_gallons": 0.0,
      "fuel_on_departure_gallons": 48.0
    }
  ],
  "summary": {
    "total_distance_miles": 2799.9,
    "total_gallons": 279.99,
    "total_fuel_cost": 853.46,
    "average_price_per_gallon": 3.048,
    "number_of_stops": 10,
    "origin_approach": {"miles": 5.3, "gallons": 0.53, "price_per_gallon": 3.099, "cost": 1.65, "priced_at_opis_id": 62790, "off_route_fallback": false}
  },
  "optimization": {"price_tolerance_per_gallon": 0.05, "min_purchase_gallons": 5.0},
  "map_url": "http://127.0.0.1:8000/api/v1/route/map/?start_location=New+York%2C+NY&finish_location=Los+Angeles%2C+CA",
  "meta": {
    "routing_api_calls": 1,
    "geocoding_api_calls": 0,
    "route_cache_hit": false,
    "stations_on_route": 323,
    "timings_ms": {"geocoding": 0.1, "routing": 15.9, "station_matching": 15.2, "optimization": 0.7, "total": 33.2}
  }
}
```

(`routing` above was measured against a local copy of the OSRM response. The public OSRM server itself took 0.5 s for Chicago to Denver and 0.8 s for New York to Los Angeles when tested.)

### `GET /api/v1/route/map/`

Same parameters. Returns an HTML page (Leaflet + OpenStreetMap tiles) with the route, numbered fuel stops, and a sidebar listing each stop's price, gallons and cost.

### `GET /api/v1/health/`

`{"status": "ok", "fuel_stations_loaded": 6626}`, or HTTP 503 before the CSV is loaded.

### Errors

Every error has the same shape: `{"error": {"code": "...", "message": "...", "details": {...}}}`

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Missing or invalid parameters (details lists each field) |
| 400 | `invalid_location` | Unparseable input, such as latitude 139 |
| 400 | `location_not_found` | Place not found in the USA (details lists accepted formats) |
| 400 | `location_outside_usa` | Coordinates outside the USA (with a hint if latitude and longitude look swapped) |
| 422 | `no_route` | OSRM finds no drivable route between the two points |
| 422 | `no_feasible_fuel_plan` | Some stretch is longer than the range with no station (details give the mile) |
| 502 | `routing_unavailable` / `geocoding_unavailable` | Upstream error, connection failure or rate limit |
| 503 | `fuel_data_not_loaded` | `load_fuel_stations` has not been run |
| 504 | `routing_timeout` / `geocoding_timeout` | Upstream did not answer in time |

The routing call is never retried automatically, so a failure never turns into a second call.

---

## 3. How it works

```
start, finish ──> geocode (offline) ──> OSRM /route (1 call, cached) ──> place stations on the route ──> greedy optimizer ──> JSON / map
```

### 3.1 Loading the CSV (`python manage.py load_fuel_stations`)

The CSV has no coordinates, so the loader:

1. **Keeps US rows only.** 620 rows are Canadian (ON, AB, BC and others) and are skipped.
2. **Merges duplicate OPIS IDs.** 6,626 unique stations appear across 7,531 US rows; the same truck stop is listed with different prices (one per supply rack). The lowest listed price is kept by default (`--duplicate-price mean|max` to change).
3. **Geocodes each station to its city's centroid** using a bundled offline gazetteer (41,704 US ZIP code centroids and 39,371 city names, built from the MIT-licensed `zipcodes` package). All 6,626 stations resolve, with no network calls. Name variants such as "St. Louis" / "Saint Louis" and "De Forest" / "Deforest" are normalised.
4. **Parses highways from the address.** `"I-44, EXIT 283 & US-69"` becomes `I-44, US-69`. Only Interstates and US routes are kept, because their numbers are unique nationwide. 5,693 stations have one.
5. **Replaces the table in one transaction**, so a failed load never leaves it half empty.

### 3.2 Routing: exactly one OSRM call

```
GET https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}
    ?overview=full&geometries=polyline6&steps=true&alternatives=false
```

- `overview=full` gives the full road geometry (about 35,000 points coast to coast), decoded with vectorised numpy in about 10 ms.
- `steps=true` gives the road refs per stretch (`"I 80"`, `"I 80; US 6"`), used to confirm a station is on a highway the route really uses.
- The route is cached (6 hours, keyed by rounded coordinates). Repeating a trip or opening its map makes zero OSRM calls.
- Start and finish are geocoded offline for City/State, ZIP and coordinates, so the common case is **1 external call in total**. Street addresses add at most 2 Nominatim calls (cached), which stays within "two or three calls is acceptable".

### 3.3 Placing stations along the route

1. The route is densified to one point every 0.5 miles and loaded into a KD-tree on the unit sphere (exact great-circle distances, no lat/lon distortion).
2. All stations inside the route's bounding box are matched in one vectorised query, giving each station its **distance from the road** and its **mile marker** (route miles from the start to its nearest route point, scaled to OSRM's road distance).
3. The allowed corridor depends on the highway check, because station positions are city centroids, not exact exits:

| Station's parsed highway | Corridor |
|---|---|
| Matches a road the route uses within 30 miles of that point | 15 miles |
| Station has no Interstate/US info | 5 miles |
| Station is on other Interstates/US routes | 2 miles |

This keeps a truck stop listed on I-80 when the route drives I-80 past its town, while leaving out a station in the same town that sits on a different interstate. On the real New York to Los Angeles route, 323 stations qualify.

### 3.4 The optimizer (`api/services/optimizer.py`)

**Vehicle math**

```
tank capacity    = 500 miles / 10 mpg      = 50 gallons
planning range   = 500 - 20 mile margin    = 480 miles  (never plan a leg longer than this)
gallons for a leg = leg miles / 10
cost of a purchase = gallons bought * that station's price per gallon
total fuel cost  = origin approach cost + sum of all purchase costs
```

The 20 mile margin covers the city-centroid position error and short detours to the pump, and matches the brief (stop before 450 to 480 miles).

**Starting assumption.** Every mile is paid for, so total gallons always equal `distance / 10` exactly (279.99 gallons for 2,799.9 miles). The vehicle starts empty and fills at the cheapest station within the first 50 route miles; the few miles to reach it are billed at that station's price (`origin_approach` in the response). If no station lies on the first 50 miles, the nearest station within 50 miles of the start is used (`off_route_fallback: true`).

**Greedy rule**, applied at each station in route order:

1. If a cheaper station is within 480 miles ahead, buy only enough to reach the first one.
2. Otherwise, if the destination is within 480 miles, buy exactly enough to finish.
3. Otherwise, fill the tank and drive to the cheapest station within 480 miles.

With both practical knobs at 0 this is the classic optimal strategy for refuelling on a fixed route. The tests compare it with an exhaustive dynamic program on 1,500 random instances, and it matches the true minimum on every feasible one.

**Practical refinements (defaults).** The exact optimum happily stops for 1.3 gallons to save a fraction of a cent. Two knobs fix that:

- `price_tolerance = $0.05`: prices within 5 cents count as equal, so the plan does not divert for trivial savings, and among near-equal stations it drives to the farthest one. Tests check the extra cost never exceeds `tolerance * gallons`.
- `min_purchase_gallons = 5`: any stop buying under 5 gallons is folded into the previous stop (if the tank has room) or the next one (if the vehicle still reaches it), whichever is cheaper. Stops needed to physically bridge a gap stay.

On the real New York to Los Angeles route:

| Settings | Stops | Total cost |
|---|---|---|
| Exact (`price_tolerance=0&min_purchase_gallons=0`) | 17 | $851.26 |
| Default | 10 | $853.46 (+$2.20, 0.26%) |

**Infeasible routes.** If any stretch is longer than 480 miles with no station, the API returns 422 with the mile where the vehicle would be stranded.

### 3.5 Performance

| Step | Time |
|---|---|
| Geocoding (offline) | < 1 ms |
| OSRM call | 0.5 to 0.9 s on the public demo server; cached repeats 0 ms |
| Decode the 35,000 point route geometry | about 10 ms |
| Match 6,626 stations to the route | about 15 ms |
| Optimizer | < 1 ms |
| **Server time, coast to coast, excluding OSRM** | **about 35 ms** |

The gazetteer (about 400 ms to build) and the station index (about 50 ms) are loaded in a background thread when the server starts, so the first request is as fast as the rest.

---

## 4. Assumptions and limitations

- **Station positions are city centroids**, because the CSV has no coordinates. Mile markers can be off by a few miles in large cities; the 20 mile safety margin and the highway check absorb this. Exact coordinates can be added by setting `latitude`/`longitude` and `geocode_precision="exact"` on `FuelStation`.
- **Detours to stations are not added to the distance.** Most chosen stations are within 5 miles of the route.
- **Duplicate OPIS IDs use the lowest listed price** by default.
- **Canadian stations are excluded.** A US route that briefly crosses Canada simply has no stations on that stretch.
- **The public OSRM server is a demo service** with rate limits. Set `OSRM_BASE_URL` to a self-hosted OSRM for real traffic.
- **Map tiles come from OpenStreetMap's volunteer tile servers**, which block requests that carry no `Referer` header. Django's default referrer policy (`same-origin`) strips it on cross-site requests, so the map view sends `Referrer-Policy: strict-origin-when-cross-origin` instead. For heavy use, point `MAP_TILE_URL` at a commercial tile provider.
- **The cache is per process** (LocMemCache). With several workers, switch `CACHES` to Redis or Memcached so they share routes.

---

## 5. Configuration

All settings live in `FUEL_OPTIMIZER` in `fuel_optimizer/settings.py`; these can be set through environment variables (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `FUEL_MAX_RANGE_MILES` | 500 | Full-tank range |
| `FUEL_SAFETY_BUFFER_MILES` | 20 | Range kept in reserve when planning |
| `FUEL_MILES_PER_GALLON` | 10 | Fuel efficiency |
| `FUEL_PRICE_TOLERANCE_PER_GALLON` | 0.05 | Default for `price_tolerance` |
| `FUEL_MIN_PURCHASE_GALLONS` | 5 | Default for `min_purchase_gallons` |
| `OSRM_BASE_URL` | public demo server | Routing server |
| `OSRM_USE_STEPS` | true | Request road refs for the highway check |
| `GEOCODER_ONLINE_FALLBACK` | true | Use Nominatim for street addresses |
| `MAP_TILE_URL`, `MAP_TILE_ATTRIBUTION` | OpenStreetMap | Background tiles for the map page |
| `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` | dev defaults | Standard Django settings |

---

## 6. Project layout

```
fuel_optimizer_project/
├── manage.py
├── requirements.txt, requirements-dev.txt, pyproject.toml, .env.example
├── data/
│   ├── fuel-prices-for-be-assessment.csv       source data
│   └── gazetteer/us_places.csv.gz, us_zips.csv.gz   offline geocoding tables
├── docs/sample_response_nyc_to_la.json
├── fuel_optimizer/                              Django project (settings, urls, wsgi, asgi)
└── api/
    ├── models.py              FuelStation
    ├── serializers.py         request validation and response shape
    ├── views.py               route plan, map page, health
    ├── urls.py
    ├── exceptions.py          uniform JSON errors
    ├── conf.py                typed settings
    ├── apps.py                background cache warm-up
    ├── admin.py
    ├── management/commands/
    │   ├── load_fuel_stations.py
    │   └── build_gazetteer.py
    ├── services/
    │   ├── planner.py         orchestrates one request end to end
    │   ├── routing.py         OSRM client (1 call, cached, no retries)
    │   ├── geocoding.py       offline gazetteer + Nominatim fallback
    │   ├── route_matching.py  stations -> mile markers (KD-tree, highway check)
    │   ├── optimizer.py       greedy refuelling plan
    │   ├── station_index.py   in-memory numpy view of FuelStation
    │   ├── geo.py, text.py    geometry, polyline decoding, name/highway parsing
    │   └── exceptions.py      domain errors with HTTP codes
    ├── templates/api/         map page
    └── tests/                 64 tests, including a captured real OSRM response
```

---

## 7. Production notes

```bash
export DJANGO_DEBUG=false
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_ALLOWED_HOSTS=api.example.com
export DJANGO_SECURE_SSL_REDIRECT=true        # once HTTPS is set up
export DJANGO_SECURE_HSTS_SECONDS=31536000    # once HTTPS is set up
export OSRM_BASE_URL=https://your-osrm.example.com
pip install gunicorn
gunicorn fuel_optimizer.wsgi --workers 4 --bind 0.0.0.0:8000
```

With `DJANGO_DEBUG=false` the API renders JSON only and unexpected errors return a generic 500 JSON body (details go to the log). Use a shared cache (Redis) when running several workers.
