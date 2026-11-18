"""Polite HTTP: identifies itself, waits between requests to the same host, retries briefly."""
from __future__ import annotations

import time
from urllib.parse import urlparse

import requests


class PoliteSession:
    def __init__(self, user_agent: str, min_delay: float = 3.0, timeout: float = 30.0):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.min_delay = min_delay
        self.timeout = timeout
        self._last: dict[str, float] = {}

    def get(self, url: str, retries: int = 2) -> requests.Response:
        host = urlparse(url).netloc
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            wait = self.min_delay - (time.time() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()
            try:
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code < 500:
                    r.raise_for_status()
                    return r
                last_err = requests.HTTPError(f"{r.status_code} for {url}")
            except requests.RequestException as e:
                last_err = e
            time.sleep(2 * (attempt + 1))
        raise last_err  # type: ignore[misc]
