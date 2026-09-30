from django.contrib import admin

from .models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "retail_price", "highways", "source_row_count")
    list_filter = ("state", "geocode_precision")
    search_fields = ("opis_id", "name", "city", "address")
    ordering = ("state", "city", "name")
    readonly_fields = ("created_at", "updated_at")
