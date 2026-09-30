import logging
import os
import sys
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _is_web_server_process() -> bool:
    """True for the process that actually serves requests.

    `runserver` with autoreload starts a watcher process and a child process;
    only the child (RUN_MAIN=true) serves traffic. WSGI servers such as
    gunicorn or uwsgi import the project directly.
    """
    program = os.path.basename(sys.argv[0]) if sys.argv else ""
    if "runserver" in sys.argv:
        return os.environ.get("RUN_MAIN") == "true" or "--noreload" in sys.argv
    return program in {"gunicorn", "uwsgi", "uvicorn", "daphne"}


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "Fuel route optimizer"

    def ready(self) -> None:
        from .conf import get_config

        if not get_config().warm_caches_on_startup or not _is_web_server_process():
            return
        threading.Thread(target=_warm_caches, name="fuel-optimizer-warmup", daemon=True).start()


def _warm_caches() -> None:
    """Build the gazetteer and station index before the first request arrives."""
    from .conf import get_config
    from .services.exceptions import FuelOptimizerError
    from .services.geocoding import get_gazetteer
    from .services.station_index import get_station_index

    try:
        get_gazetteer(get_config())
        stations = get_station_index().size
        logger.info("Warm-up complete: gazetteer loaded, %d fuel stations indexed.", stations)
    except FuelOptimizerError as exc:
        logger.warning("Warm-up skipped: %s", exc.message)
    except Exception:  # noqa: BLE001 - never crash the server over a warm-up
        logger.exception("Warm-up failed; caches will be built on the first request.")
