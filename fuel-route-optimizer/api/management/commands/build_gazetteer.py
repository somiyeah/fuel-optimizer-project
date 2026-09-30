"""Build the offline gazetteer used to geocode cities, ZIP codes and stations.

Run once (the generated files are committed under data/gazetteer/):

    pip install -r requirements-dev.txt
    python manage.py build_gazetteer

Source: the `zipcodes` package (MIT licensed), which bundles every US ZIP code
with its centroid, primary city name and accepted alternate city names.

Output:
    us_zips.csv.gz    zip, city, state, latitude, longitude     (one row per ZIP)
    us_places.csv.gz  city, state, latitude, longitude, kind    (one row per city name)

A city's coordinates are the mean of its standard (non PO box) ZIP centroids,
which lands close to the town centre for small towns and near the middle of
the urban area for large cities.
"""

import csv
import gzip
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.conf import get_config
from api.services.text import US_STATES


class Command(BaseCommand):
    help = "Generate the offline US city and ZIP gazetteer from the `zipcodes` package."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", type=Path, help="Directory for the generated .csv.gz files.")

    def handle(self, *args, **options):
        try:
            import zipcodes
        except ImportError as exc:
            raise CommandError("Install the dev requirements first: pip install -r requirements-dev.txt") from exc

        config = get_config()
        places_path = Path(config.gazetteer_places_file)
        zips_path = Path(config.gazetteer_zips_file)
        if options["output_dir"]:
            places_path = options["output_dir"] / places_path.name
            zips_path = options["output_dir"] / zips_path.name
        places_path.parent.mkdir(parents=True, exist_ok=True)
        zips_path.parent.mkdir(parents=True, exist_ok=True)

        zip_rows = []
        # (state, city) -> {"standard": [(lat, lon)], "other": [(lat, lon)]}
        primary = defaultdict(lambda: {"standard": [], "other": []})
        aliases = defaultdict(lambda: {"standard": [], "other": []})

        for record in zipcodes.list_all():
            state = record.get("state")
            if record.get("country") != "US" or state not in US_STATES:
                continue
            try:
                latitude, longitude = float(record["lat"]), float(record["long"])
            except (KeyError, TypeError, ValueError):
                continue
            if latitude == 0.0 and longitude == 0.0:
                continue
            city = record["city"].strip()
            bucket = "standard" if record.get("zip_code_type") == "STANDARD" else "other"
            zip_rows.append((record["zip_code"], city, state, round(latitude, 5), round(longitude, 5)))
            primary[(state, city)][bucket].append((latitude, longitude))
            for alias in record.get("acceptable_cities") or []:
                aliases[(state, alias.strip())][bucket].append((latitude, longitude))

        def centroid(points: dict) -> tuple[float, float]:
            chosen = points["standard"] or points["other"]
            return (
                round(sum(p[0] for p in chosen) / len(chosen), 5),
                round(sum(p[1] for p in chosen) / len(chosen), 5),
            )

        place_rows = [(city, state, *centroid(points), "primary") for (state, city), points in sorted(primary.items())]
        place_rows += [
            (city, state, *centroid(points), "alias")
            for (state, city), points in sorted(aliases.items())
            if (state, city) not in primary
        ]

        with gzip.open(zips_path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["zip", "city", "state", "latitude", "longitude"])
            writer.writerows(sorted(zip_rows))

        with gzip.open(places_path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["city", "state", "latitude", "longitude", "kind"])
            writer.writerows(place_rows)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(zip_rows):,} ZIP codes to {zips_path} and {len(place_rows):,} city names to {places_path}."
            )
        )
