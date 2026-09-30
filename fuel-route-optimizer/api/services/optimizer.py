"""Minimum-cost refuelling plan along a fixed route (greedy).

Problem
-------
The vehicle drives a fixed route of D miles. Fuel stations sit at known mile
markers with known prices. The tank holds R miles of fuel (R = the planning
range, 480 miles: a 500 mile tank minus a 20 mile safety margin). Driving
uses 1 gallon every MPG (10) miles. Choose where to stop and how much to buy
so the total spend is as small as possible.

Units: internally fuel is counted in *miles of driving*, which keeps the
range checks exact. Conversions happen only when reporting:
    gallons = miles / MPG
    cost    = gallons * price_per_gallon

Starting assumption
-------------------
Every mile of the trip is paid for (so total gallons = D / MPG exactly). The
vehicle starts empty and fills up at the cheapest station within the first
ORIGIN_FILL_WINDOW_MILES (50) of the route. The few miles needed to reach
that station are billed at that station's price ("origin approach").
Picking the cheapest station in that window is optimal: any cheaper choice
later in the window beats buying earlier at a higher price, and every
station past it is reachable from it on a single fill.

Greedy rule (applied at every station i, in route order)
--------------------------------------------------------
1. If a cheaper station lies within R miles ahead, buy only enough fuel to
   reach the first such station, then drive there.
2. Otherwise, if the destination is within R miles, buy just enough to reach
   the destination (arrive with an empty tank).
3. Otherwise, fill the tank completely and drive to the cheapest station
   within R miles ahead (the next place worth buying at).

With price_tolerance = 0 and min_purchase_gallons = 0 this is the classic
optimal strategy for fixed-route refuelling: rule 1 never pays more than
necessary before a cheaper price, and rule 3 carries as much cheap fuel as
the tank allows when nothing cheaper is reachable. The unit tests check it
against an exhaustive dynamic program on random instances.

Practical refinements (on by default)
-------------------------------------
The exact optimum is happy to stop for 1 to 2 gallons to save a fraction of a
cent per gallon, which no driver would do. Two small knobs fix that:

* price_tolerance (default $0.05/gal): prices within this margin count as
  equal. Rule 1 only diverts to a station that saves more than the tolerance,
  and rule 3 drives to the farthest station priced within the tolerance of
  the cheapest one in range. Tests check that the extra cost never exceeds
  tolerance * gallons.
* min_purchase_gallons (default 5): after planning, any stop that buys less
  than this is removed and its gallons are bought at the previous stop (if
  the tank has room) or the next stop (if the vehicle still reaches it),
  whichever is cheaper. Each fold moves under 5 gallons, so it costs cents.

On a real OSRM New York to Los Angeles route (2,800 miles, 323 stations near
the road) the exact plan has 17 stops, several under 2 gallons; the default
plan has 10 stops and costs $2.19 more (0.26%). Both knobs set to 0 give the
exact minimum.

Runtime: O(n * k) numpy work, where n is the number of stations on the route
(a few hundred) and k the number within one tank of range.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .exceptions import InfeasibleFuelPlanError

EPSILON = 1e-9


@dataclass(frozen=True)
class Candidate:
    key: int  # caller-defined identifier (index into the station index)
    mile: float  # position along the route, in miles from the start
    price: float  # USD per gallon


@dataclass(frozen=True)
class Purchase:
    key: int
    mile: float
    price: float
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float
    fuel_on_departure_gallons: float


@dataclass(frozen=True)
class OriginApproach:
    """Fuel burned between the trip start and the first purchase."""

    key: int
    miles: float
    gallons: float
    price: float
    cost: float
    off_route_fallback: bool


@dataclass(frozen=True)
class FuelPlan:
    purchases: tuple[Purchase, ...]
    origin_approach: OriginApproach | None
    total_distance_miles: float
    total_gallons: float
    total_cost: float


@dataclass
class _Stop:
    """Mutable working copy of a purchase. Fuel amounts are in miles."""

    key: int
    mile: float
    price: float
    arrival: float  # fuel in the tank on arrival
    bought: float  # fuel bought here

    @property
    def departure(self) -> float:
        return self.arrival + self.bought


def plan_fuel_purchases(
    candidates: Sequence[Candidate],
    total_distance_miles: float,
    range_miles: float,
    miles_per_gallon: float,
    origin_window_miles: float,
    origin_fallback: Candidate | None = None,
    price_tolerance: float = 0.0,
    min_purchase_gallons: float = 0.0,
) -> FuelPlan:
    """Cheapest set of purchases covering the whole route.

    Args:
        candidates: stations on the route, any order.
        total_distance_miles: route length D.
        range_miles: longest distance allowed between purchases (tank size R).
        miles_per_gallon: fuel efficiency.
        origin_window_miles: the first fill happens within this many route miles.
        origin_fallback: station to price the first fill at when no station lies
            within the origin window (e.g. the nearest station to the start).
        price_tolerance: USD/gal difference treated as "same price" (0 = exact).
        min_purchase_gallons: fold smaller purchases into a neighbouring stop (0 = off).

    Raises:
        InfeasibleFuelPlanError: some stretch of the route is longer than the
            range with no station in between.
    """
    distance = float(total_distance_miles)
    tolerance = max(0.0, float(price_tolerance))
    if distance <= EPSILON:
        return FuelPlan((), None, 0.0, 0.0, 0.0)

    # Stations at or past the destination are useless.
    usable = sorted(
        (c for c in candidates if -EPSILON <= c.mile < distance - EPSILON),
        key=lambda c: (c.mile, c.price),
    )

    # ---- where the trip's first fuel comes from --------------------------- #
    window = min(origin_window_miles, range_miles, distance)
    in_window = [i for i, c in enumerate(usable) if c.mile <= window + EPSILON]
    if in_window:
        # Cheapest in the window; ties go to the earliest station.
        start = min(in_window, key=lambda i: (usable[i].price, usable[i].mile))
        origin = usable[start]
        approach_miles = origin.mile
        stations = usable[start:]
        off_route_origin = False
    elif origin_fallback is not None:
        # No station on the first stretch of the route: treat the nearest
        # station to the start as a virtual station at mile 0.
        origin = Candidate(origin_fallback.key, 0.0, origin_fallback.price)
        approach_miles = 0.0
        stations = [origin, *usable]
        off_route_origin = True
    else:
        raise InfeasibleFuelPlanError(
            f"No fuel station was found within the first {window:.0f} miles of the route or near the start location.",
            details={"origin_window_miles": round(window, 1)},
        )

    approach_gallons = approach_miles / miles_per_gallon
    approach = OriginApproach(
        key=origin.key,
        miles=approach_miles,
        gallons=approach_gallons,
        price=origin.price,
        cost=approach_gallons * origin.price,
        off_route_fallback=off_route_origin,
    )

    stops = _greedy(stations, distance, range_miles, tolerance)
    if min_purchase_gallons > 0:
        _fold_small_purchases(stops, range_miles, min_purchase_gallons * miles_per_gallon)

    purchases = tuple(
        Purchase(
            key=stop.key,
            mile=stop.mile,
            price=stop.price,
            gallons=stop.bought / miles_per_gallon,
            cost=stop.bought / miles_per_gallon * stop.price,
            fuel_on_arrival_gallons=max(0.0, stop.arrival) / miles_per_gallon,
            fuel_on_departure_gallons=stop.departure / miles_per_gallon,
        )
        for stop in stops
    )
    return FuelPlan(
        purchases=purchases,
        origin_approach=approach,
        total_distance_miles=distance,
        total_gallons=approach.gallons + sum(p.gallons for p in purchases),
        total_cost=approach.cost + sum(p.cost for p in purchases),
    )


def _greedy(stations: list[Candidate], distance: float, range_miles: float, tolerance: float) -> list[_Stop]:
    """Walk the route applying rules 1-3. stations[0] is where the trip's first fuel is bought."""
    miles = np.fromiter((c.mile for c in stations), dtype=np.float64, count=len(stations))
    prices = np.fromiter((c.price for c in stations), dtype=np.float64, count=len(stations))

    stops: list[_Stop] = []
    fuel = 0.0  # miles of driving left in the tank on arrival at station i
    i = 0

    def buy(station: int, amount: float) -> None:
        nonlocal fuel
        if amount > EPSILON:
            candidate = stations[station]
            stops.append(_Stop(candidate.key, candidate.mile, candidate.price, fuel, amount))
            fuel += amount

    while True:
        here = miles[i]
        reach = here + range_miles  # farthest mile a full tank gets us from here
        # Stations i+1 .. last are within one tank of range.
        last = int(np.searchsorted(miles, reach + EPSILON, side="right")) - 1
        ahead = prices[i + 1 : last + 1]

        # Rule 1: a cheaper station within range -> buy only enough to reach it.
        cheaper = np.flatnonzero(ahead < prices[i] - tolerance - EPSILON)
        if cheaper.size:
            j = i + 1 + int(cheaper[0])
            leg = miles[j] - here
            buy(i, max(0.0, leg - fuel))
            fuel -= leg
            i = j
            continue

        # Rule 2: nothing cheaper ahead within range and the destination is
        # reachable -> buy exactly enough to finish.
        if distance - here <= range_miles + EPSILON:
            leg = distance - here
            buy(i, max(0.0, leg - fuel))
            return stops

        # Rule 3: fill the tank and go to the cheapest station within range.
        if ahead.size == 0:
            next_mile = float(miles[i + 1]) if i + 1 < len(stations) else distance
            raise InfeasibleFuelPlanError(
                f"No fuel station within {range_miles:.0f} miles after route mile {here:.0f}; "
                f"the next station or destination is at mile {next_mile:.0f}.",
                details={
                    "stranded_after_mile": round(float(here), 1),
                    "next_fuel_or_destination_mile": round(next_mile, 1),
                    "range_miles": range_miles,
                },
            )
        # Among stations priced within the tolerance of the cheapest, take the
        # farthest location (fewer stops) and, at that location, the cheapest
        # station (stations are sorted by mile, then price).
        near_cheapest = i + 1 + np.flatnonzero(ahead <= ahead.min() + tolerance + EPSILON)
        farthest_mile = miles[near_cheapest[-1]]
        k = int(near_cheapest[np.searchsorted(miles[near_cheapest], farthest_mile - EPSILON)])
        buy(i, range_miles - fuel)
        fuel -= miles[k] - here
        i = k


def _fold_small_purchases(stops: list[_Stop], capacity: float, min_amount: float) -> None:
    """Remove stops that buy less than min_amount by moving their fuel to a neighbour.

    Moving amount g from stop s:
      * back to stop s-1: that stop departs with g more fuel, so it needs
        departure + g <= capacity. Every later amount stays the same.
      * forward to stop s+1: the vehicle reaches s+1 with g less fuel, so it
        needs arrival >= g there. Its departure fuel stays the same.
    The cheaper feasible move wins; stops that cannot move are kept. Repeats
    until nothing changes, smallest purchases first.
    """
    changed = True
    while changed:
        changed = False
        for s in sorted(range(len(stops)), key=lambda index: stops[index].bought):
            stop = stops[s]
            amount = stop.bought
            if amount >= min_amount - EPSILON:
                break
            options = []
            if s > 0 and stops[s - 1].departure + amount <= capacity + EPSILON:
                options.append((amount * stops[s - 1].price, "back"))
            if s + 1 < len(stops) and stops[s + 1].arrival + EPSILON >= amount:
                options.append((amount * stops[s + 1].price, "forward"))
            if not options:
                continue
            _, direction = min(options)
            if direction == "back":
                stops[s - 1].bought += amount
                # Stop s is no longer a purchase; later arrivals are unchanged.
            else:
                stops[s + 1].arrival -= amount
                stops[s + 1].bought += amount
            del stops[s]
            changed = True
            break
