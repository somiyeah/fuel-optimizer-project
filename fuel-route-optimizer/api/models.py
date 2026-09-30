from django.db import models
from django.db.models import Q


class FuelStation(models.Model):
    """One truck stop from the OPIS price list, deduplicated by OPIS ID.

    The source CSV has no coordinates, so latitude/longitude are the centroid
    of the station's city (from the offline gazetteer). `highways` holds the
    Interstate/US routes parsed from the address, e.g. "I-44,US-69", which the
    route matcher uses to confirm a station sits on a road the route drives.
    """

    class GeocodePrecision(models.TextChoices):
        CITY_CENTROID = "city_centroid", "City centroid"
        EXACT = "exact", "Exact"

    opis_id = models.PositiveIntegerField(unique=True, help_text="OPIS Truckstop ID")
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=12, decimal_places=8, help_text="USD per gallon")
    latitude = models.FloatField()
    longitude = models.FloatField()
    highways = models.CharField(
        max_length=255, blank=True, help_text="Comma separated Interstate/US routes, e.g. I-44,US-69"
    )
    geocode_precision = models.CharField(
        max_length=20, choices=GeocodePrecision.choices, default=GeocodePrecision.CITY_CENTROID
    )
    source_row_count = models.PositiveSmallIntegerField(
        default=1, help_text="CSV rows merged into this station (duplicates share an OPIS ID)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["opis_id"]
        indexes = [models.Index(fields=["latitude", "longitude"], name="fuelstation_lat_lon_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(retail_price__gt=0), name="fuelstation_price_positive"),
            models.CheckConstraint(
                condition=Q(latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180),
                name="fuelstation_coordinates_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price:.3f}"

    @property
    def highway_list(self) -> list[str]:
        return [item for item in self.highways.split(",") if item]
