"""Load the OPIS fuel price CSV into the FuelStation table.

    python manage.py load_fuel_stations
    python manage.py load_fuel_stations --csv path/to/prices.csv --duplicate-price mean

What it does, in order:

1. Reads the CSV (columns: OPIS Truckstop ID, Truckstop Name, Address, City,
   State, Rack ID, Retail Price) and validates every row.
2. Keeps US rows only. The file also lists ~600 Canadian stations (ON, AB,
   BC, ...), which a route inside the USA should not use.
3. Merges duplicate OPIS IDs. The same truck stop appears several times with
   different prices (one per supply rack); by default the lowest listed price
   is kept, configurable with --duplicate-price {min,mean,max}.
4. Geocodes each station to its city centroid with the offline gazetteer
   (no network calls), and parses Interstate/US routes from the address.
5. Replaces the table contents in one transaction, so a failed load never
   leaves a half-empty table behind.
"""

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.conf import get_config
from api.models import FuelStation
from api.services.exceptions import GazetteerMissingError
from api.services.geocoding import get_gazetteer
from api.services.station_index import reset_station_index
from api.services.text import US_STATES, parse_station_highways

REQUIRED_COLUMNS = {
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
}


@dataclass
class CsvRow:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    rack_id: int | None
    price: Decimal


class Command(BaseCommand):
    help = "Load (or reload) fuel stations and prices from the OPIS CSV file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv", type=Path, help="Path to the CSV. Defaults to data/fuel-prices-for-be-assessment.csv."
        )
        parser.add_argument(
            "--duplicate-price",
            choices=["min", "mean", "max"],
            default="min",
            help="How to merge rows sharing an OPIS ID with different prices (default: min).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing to the database.")

    def handle(self, *args, **options):
        config = get_config()
        csv_path: Path = options["csv"] or Path(config.fuel_prices_csv)
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        try:
            gazetteer = get_gazetteer(config)
        except GazetteerMissingError as exc:
            raise CommandError(exc.message) from exc

        rows, skipped = self._read_rows(csv_path)
        grouped: dict[int, list[CsvRow]] = defaultdict(list)
        for row in rows:
            grouped[row.opis_id].append(row)

        stations: list[FuelStation] = []
        unresolved: Counter = Counter()
        for opis_id, group in grouped.items():
            chosen, price = self._merge(group, options["duplicate_price"])
            place = gazetteer.lookup_city(chosen.city, chosen.state)
            if place is None:
                unresolved[f"{chosen.city}, {chosen.state}"] += 1
                continue
            stations.append(
                FuelStation(
                    opis_id=opis_id,
                    name=chosen.name,
                    address=chosen.address,
                    city=chosen.city,
                    state=chosen.state,
                    rack_id=chosen.rack_id,
                    retail_price=price,
                    latitude=place.latitude,
                    longitude=place.longitude,
                    highways=",".join(parse_station_highways(chosen.address)),
                    geocode_precision=FuelStation.GeocodePrecision.CITY_CENTROID,
                    source_row_count=len(group),
                )
            )

        self.stdout.write(f"CSV rows read:              {sum(skipped.values()) + len(rows):,}")
        for reason, count in sorted(skipped.items()):
            self.stdout.write(f"  skipped ({reason}): {count:,}")
        self.stdout.write(f"Unique US stations:         {len(grouped):,}")
        self.stdout.write(f"Geocoded (city centroid):   {len(stations):,}")
        with_highways = sum(1 for station in stations if station.highways)
        self.stdout.write(f"With Interstate/US route:   {with_highways:,}")
        if unresolved:
            sample = ", ".join(f"{name} ({count})" for name, count in unresolved.most_common(10))
            self.stdout.write(
                self.style.WARNING(f"Not geocoded, skipped:      {sum(unresolved.values()):,} [{sample}]")
            )

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("Dry run: database not modified."))
            return
        if not stations:
            raise CommandError("No stations to load; the database was left unchanged.")

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        reset_station_index()
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(stations):,} fuel stations."))

    # ------------------------------------------------------------------ #

    def _read_rows(self, csv_path: Path) -> tuple[list[CsvRow], Counter]:
        rows: list[CsvRow] = []
        skipped: Counter = Counter()
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"CSV is missing columns: {', '.join(sorted(missing))}")
            for raw in reader:
                state = (raw["State"] or "").strip().upper()
                if state not in US_STATES:
                    skipped["outside USA"] += 1
                    continue
                try:
                    opis_id = int(raw["OPIS Truckstop ID"])
                    price = Decimal((raw["Retail Price"] or "").strip())
                except (ValueError, InvalidOperation):
                    skipped["invalid id or price"] += 1
                    continue
                if not price.is_finite() or price <= 0:
                    skipped["non-positive price"] += 1
                    continue
                city = " ".join((raw["City"] or "").split())
                if not city:
                    skipped["missing city"] += 1
                    continue
                rack = (raw["Rack ID"] or "").strip()
                rows.append(
                    CsvRow(
                        opis_id=opis_id,
                        name=" ".join((raw["Truckstop Name"] or "").split())[:255],
                        address=" ".join((raw["Address"] or "").split())[:255],
                        city=city[:100],
                        state=state,
                        rack_id=int(rack) if rack.isdigit() else None,
                        price=price.quantize(Decimal("0.00000001")),
                    )
                )
        return rows, skipped

    @staticmethod
    def _merge(group: list[CsvRow], strategy: str) -> tuple[CsvRow, Decimal]:
        """Pick the representative row and the price for one OPIS ID."""
        cheapest = min(group, key=lambda row: row.price)
        if strategy == "min":
            return cheapest, cheapest.price
        if strategy == "max":
            priciest = max(group, key=lambda row: row.price)
            return priciest, priciest.price
        mean = sum((row.price for row in group), Decimal(0)) / len(group)
        return group[0], mean.quantize(Decimal("0.00000001"))
