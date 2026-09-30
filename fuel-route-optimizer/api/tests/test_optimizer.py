import itertools
import random
import unittest
from functools import cache

from api.services.exceptions import InfeasibleFuelPlanError
from api.services.optimizer import Candidate, plan_fuel_purchases


def exhaustive_minimum(candidates, distance, range_miles, window):
    """Exact optimum by dynamic programming over integer fuel levels.

    With integer mile markers, distances and range, the refuelling LP has an
    integral optimum (its constraint matrix has consecutive ones), so searching
    integer purchase amounts finds the true minimum. mpg = 1 here, so one unit
    of fuel is one mile.
    """
    stations = sorted((c for c in candidates if c.mile < distance), key=lambda c: (c.mile, c.price))
    count = len(stations)

    @cache
    def best_from(index, fuel):
        here = stations[index]
        following = stations[index + 1].mile if index + 1 < count else None
        best = float("inf")
        for bought in range(range_miles - fuel + 1):
            tank = fuel + bought
            if distance - here.mile <= tank:
                best = min(best, bought * here.price)
            if following is not None and following - here.mile <= tank:
                best = min(best, bought * here.price + best_from(index + 1, tank - (following - here.mile)))
        return best

    limit = min(window, range_miles, distance)
    starts = [i for i, c in enumerate(stations) if c.mile <= limit]
    return min((stations[i].mile * stations[i].price + best_from(i, 0) for i in starts), default=float("inf"))


class GreedyOptimizerExamplesTests(unittest.TestCase):
    def plan(self, stations, distance, range_miles=480.0, mpg=10.0, window=50.0, tolerance=0.0, fallback=None):
        candidates = [Candidate(key, mile, price) for key, (mile, price) in enumerate(stations)]
        return plan_fuel_purchases(candidates, distance, range_miles, mpg, window, fallback, tolerance)

    def test_short_trip_buys_exactly_the_fuel_needed(self):
        plan = self.plan([(0.0, 3.00)], distance=200.0)
        self.assertEqual(len(plan.purchases), 1)
        self.assertAlmostEqual(plan.purchases[0].gallons, 20.0)  # 200 mi / 10 mpg
        self.assertAlmostEqual(plan.total_cost, 60.0)  # 20 gal * $3.00

    def test_every_mile_is_paid_for(self):
        plan = self.plan([(0.0, 3.2), (300.0, 3.1), (700.0, 3.4), (1100.0, 2.9)], distance=1400.0)
        self.assertAlmostEqual(plan.total_gallons, 140.0)
        self.assertAlmostEqual(
            plan.total_cost,
            plan.origin_approach.cost + sum(p.cost for p in plan.purchases),
        )

    def test_buys_only_enough_to_reach_a_cheaper_station(self):
        # Start at $4.00; a $3.00 station 100 miles later is in range.
        plan = self.plan([(0.0, 4.00), (100.0, 3.00)], distance=500.0)
        first, second = plan.purchases
        self.assertAlmostEqual(first.gallons, 10.0)  # just the 100 mile leg
        self.assertAlmostEqual(second.gallons, 40.0)  # remaining 400 miles
        self.assertAlmostEqual(plan.total_cost, 10 * 4.00 + 40 * 3.00)

    def test_fills_up_when_nothing_cheaper_is_in_range(self):
        # $3.00 at the start, only pricier stations later, trip longer than a tank.
        plan = self.plan([(0.0, 3.00), (400.0, 3.50), (450.0, 3.60)], distance=800.0)
        self.assertAlmostEqual(plan.purchases[0].gallons, 48.0)  # full planning range
        self.assertAlmostEqual(plan.purchases[0].fuel_on_departure_gallons, 48.0)
        # Arrive at mile 400 with 8 gal left, buy the 32 gal still needed.
        self.assertAlmostEqual(plan.purchases[1].fuel_on_arrival_gallons, 8.0)
        self.assertAlmostEqual(plan.purchases[1].gallons, 32.0)

    def test_no_leg_exceeds_the_planning_range(self):
        stations = [(float(m), 3.0 + (m % 7) / 10) for m in range(0, 3000, 90)]
        plan = self.plan(stations, distance=3000.0)
        for purchase in plan.purchases:
            self.assertLessEqual(purchase.fuel_on_departure_gallons, 48.0 + 1e-9)
            self.assertGreaterEqual(purchase.fuel_on_arrival_gallons, -1e-9)

    def test_origin_fill_uses_cheapest_station_in_window_and_bills_the_approach(self):
        plan = self.plan([(5.0, 3.50), (30.0, 3.10), (80.0, 2.00)], distance=300.0)
        approach = plan.origin_approach
        self.assertAlmostEqual(approach.miles, 30.0)
        self.assertAlmostEqual(approach.price, 3.10)
        self.assertAlmostEqual(approach.cost, 3.0 * 3.10)  # 30 mi = 3 gal
        self.assertEqual(plan.purchases[0].key, 1)

    def test_origin_fallback_used_when_no_station_near_start(self):
        fallback = Candidate(key=99, mile=0.0, price=3.25)
        plan = self.plan([(200.0, 3.00)], distance=400.0, fallback=fallback)
        self.assertTrue(plan.origin_approach.off_route_fallback)
        self.assertEqual(plan.purchases[0].key, 99)
        self.assertAlmostEqual(plan.purchases[0].gallons, 20.0)  # just enough to reach $3.00
        self.assertAlmostEqual(plan.total_gallons, 40.0)

    def test_raises_when_start_has_no_fuel(self):
        with self.assertRaises(InfeasibleFuelPlanError):
            self.plan([(200.0, 3.00)], distance=400.0)

    def test_raises_on_gap_longer_than_range(self):
        with self.assertRaises(InfeasibleFuelPlanError) as ctx:
            self.plan([(0.0, 3.0), (100.0, 3.0), (700.0, 3.0)], distance=900.0)
        self.assertEqual(ctx.exception.details["stranded_after_mile"], 100.0)

    def test_zero_distance_costs_nothing(self):
        plan = self.plan([(0.0, 3.0)], distance=0.0)
        self.assertEqual(plan.total_cost, 0.0)
        self.assertEqual(plan.purchases, ())

    def test_tolerance_skips_micro_top_ups(self):
        # Exact optimum tops up at the $3.00 station, then again at $3.01.
        stations = [(0.0, 2.50), (470.0, 3.00), (480.0, 3.01), (900.0, 3.20)]
        exact = self.plan(stations, distance=1300.0, tolerance=0.0)
        relaxed = self.plan(stations, distance=1300.0, tolerance=0.05)
        self.assertLess(len(relaxed.purchases), len(exact.purchases))
        self.assertLessEqual(relaxed.total_cost - exact.total_cost, 0.05 * relaxed.total_gallons)


class GreedyOptimizerAgainstExhaustiveSearchTests(unittest.TestCase):
    """Randomised comparison with the exact dynamic program."""

    def random_instance(self, rng):
        range_miles = rng.randint(3, 12)
        distance = rng.randint(1, 45)
        # Keep stations dense enough that most instances are feasible.
        count = rng.randint(max(1, distance // range_miles), max(2, distance // 2))
        stations = [Candidate(k, rng.randint(0, distance), round(rng.uniform(2.5, 4.0), 2)) for k in range(count)]
        window = rng.randint(0, range_miles)
        return stations, distance, range_miles, window

    def test_matches_exact_optimum_with_zero_tolerance(self):
        rng = random.Random(20260930)
        feasible = 0
        for _ in range(1500):
            stations, distance, range_miles, window = self.random_instance(rng)
            expected = exhaustive_minimum(stations, distance, range_miles, window)
            if expected == float("inf"):
                with self.assertRaises(InfeasibleFuelPlanError):
                    plan_fuel_purchases(stations, distance, range_miles, 1.0, window)
                continue
            feasible += 1
            plan = plan_fuel_purchases(stations, distance, range_miles, 1.0, window)
            self.assertAlmostEqual(plan.total_cost, expected, places=6)
            self.assertAlmostEqual(plan.total_gallons, distance, places=6)
        self.assertGreater(feasible, 500)

    def test_tolerance_extra_cost_is_bounded(self):
        rng = random.Random(7)
        for tolerance, _ in itertools.product((0.01, 0.05, 0.2), range(400)):
            stations, distance, range_miles, window = self.random_instance(rng)
            expected = exhaustive_minimum(stations, distance, range_miles, window)
            if expected == float("inf"):
                continue
            plan = plan_fuel_purchases(stations, distance, range_miles, 1.0, window, None, tolerance)
            self.assertGreaterEqual(plan.total_cost, expected - 1e-6)
            self.assertLessEqual(plan.total_cost - expected, tolerance * distance + 1e-6)


def assert_plan_is_drivable(test, plan, distance, range_miles, mpg):
    """Replay the plan mile by mile: the tank never runs dry or overflows."""
    fuel = 0.0  # gallons
    position = plan.purchases[0].mile if plan.purchases else 0.0
    for purchase in plan.purchases:
        fuel -= (purchase.mile - position) / mpg
        test.assertGreaterEqual(fuel, -1e-6, "ran out of fuel before a stop")
        test.assertAlmostEqual(fuel, purchase.fuel_on_arrival_gallons, places=6)
        fuel += purchase.gallons
        test.assertLessEqual(fuel, range_miles / mpg + 1e-6, "bought more than the tank holds")
        position = purchase.mile
    fuel -= (distance - position) / mpg
    test.assertGreaterEqual(fuel, -1e-6, "ran out of fuel before the destination")


class SmallPurchaseFoldingTests(unittest.TestCase):
    def test_top_up_is_moved_to_the_next_stop(self):
        # Exact plan: fill at $2.50, top up ~1 gal at $3.00 (mile 470) and 1 gal
        # at $3.01 (mile 480), then buy at $3.20. Folding removes the top-ups.
        candidates = [
            Candidate(0, 0.0, 2.50),
            Candidate(1, 470.0, 3.00),
            Candidate(2, 480.0, 3.01),
            Candidate(3, 900.0, 3.20),
        ]
        exact = plan_fuel_purchases(candidates, 1300.0, 480.0, 10.0, 50.0)
        folded = plan_fuel_purchases(candidates, 1300.0, 480.0, 10.0, 50.0, min_purchase_gallons=5.0)
        self.assertTrue(any(p.gallons < 5 for p in exact.purchases))
        self.assertTrue(all(p.gallons >= 5 for p in folded.purchases))
        self.assertLess(len(folded.purchases), len(exact.purchases))
        self.assertAlmostEqual(folded.total_gallons, exact.total_gallons)
        assert_plan_is_drivable(self, folded, 1300.0, 480.0, 10.0)

    def test_required_small_purchase_is_kept(self):
        # Mile 0 -> 490 is out of range, so 1 gallon at mile 400 is unavoidable.
        candidates = [Candidate(0, 0.0, 3.00), Candidate(1, 400.0, 3.50), Candidate(2, 490.0, 2.00)]
        plan = plan_fuel_purchases(candidates, 700.0, 480.0, 10.0, 50.0, min_purchase_gallons=5.0)
        self.assertEqual([p.key for p in plan.purchases], [0, 1, 2])
        self.assertAlmostEqual(plan.purchases[1].gallons, 1.0)

    def test_random_plans_stay_drivable_and_complete(self):
        rng = random.Random(99)
        checked = 0
        for _ in range(600):
            distance = rng.randint(100, 3000)
            count = rng.randint(distance // 150, distance // 25)
            candidates = [Candidate(k, rng.uniform(0, distance), round(rng.uniform(2.6, 4.2), 3)) for k in range(count)]
            tolerance = rng.choice([0.0, 0.05])
            try:
                exact = plan_fuel_purchases(candidates, distance, 480.0, 10.0, 50.0)
                plan = plan_fuel_purchases(
                    candidates, distance, 480.0, 10.0, 50.0, price_tolerance=tolerance, min_purchase_gallons=5.0
                )
            except InfeasibleFuelPlanError:
                continue
            checked += 1
            assert_plan_is_drivable(self, plan, distance, 480.0, 10.0)
            self.assertAlmostEqual(plan.total_gallons, distance / 10.0, places=6)
            self.assertGreaterEqual(plan.total_cost, exact.total_cost - 1e-6)
            self.assertLessEqual(len(plan.purchases), len(exact.purchases))
        self.assertGreater(checked, 300)
