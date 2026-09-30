"""Shared HTTP session with connection pooling and no automatic retries.

Retries are disabled on purpose: the routing API must be called exactly once
per request, and a silent retry would double the call count.
"""

import threading

import requests
from requests.adapters import HTTPAdapter

_session: requests.Session | None = None
_lock = threading.Lock()


def get_http_session() -> requests.Session:
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                session = requests.Session()
                adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                _session = session
    return _session
