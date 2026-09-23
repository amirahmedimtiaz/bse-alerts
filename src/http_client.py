"""Pooled, bounded exchange requests. Each thread owns its requests session."""
from __future__ import annotations

import os
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

_local = threading.local()
_lock = threading.Lock()
_next_request: dict[str, float] = {}


def pooled_session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _pace(host: str) -> None:
    rate = float(os.environ.get("EXCHANGE_REQUESTS_PER_SECOND", "2"))
    if not 0 < rate <= 10:
        raise ValueError("EXCHANGE_REQUESTS_PER_SECOND must be in (0, 10]")
    while True:
        with _lock:
            now = time.monotonic()
            delay = _next_request.get(host, now) - now
            if delay <= 0:
                _next_request[host] = now + 1 / rate
                return
        if delay > 60:
            raise requests.exceptions.RetryError(f"Exchange {host} is in a Retry-After cooldown")
        # Recheck on wake: another thread may have received Retry-After.
        time.sleep(delay)


def get_json(url: str, *, params: dict, headers: dict,
             session: requests.Session | None = None):
    client = session or pooled_session()
    host = urlparse(url).netloc
    for attempt in range(3):
        _pace(host)
        try:
            response = client.get(url, params=params, headers=headers, timeout=(10, 30))
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = exc.response.status_code if exc.response is not None else None
            if attempt == 2 or (status is not None and status not in (429, 500, 502, 503, 504)):
                raise
            delay = 2 ** attempt
            retry_after = exc.response.headers.get("Retry-After") if exc.response is not None else None
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
            with _lock:
                _next_request[host] = max(_next_request.get(host, 0), time.monotonic() + delay)
            # Do not occupy an entire worker on a long exchange cooldown.
            if delay > 60:
                raise
    raise AssertionError("unreachable")
