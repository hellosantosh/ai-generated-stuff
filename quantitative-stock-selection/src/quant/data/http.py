"""HTTP access with retries, exponential backoff and rate limiting.

REQUIREMENTS 31: the application must survive rate limits, HTTP errors,
timeouts and malformed responses, and must never silently pretend a failed
download succeeded.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Mapping

import requests

from ..errors import ProviderError, RateLimitError
from ..logging_config import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class RateLimiter:
    """Simple thread-safe minimum-interval limiter.

    The SEC asks for no more than 10 requests per second; Alpha Vantage's free
    tier is far slower. Both are expressed here as a minimum gap between calls.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            remaining = self.min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.monotonic()


class HttpClient:
    """A requests session that retries transient failures with backoff."""

    def __init__(
        self,
        user_agent: str,
        max_retries: int = 4,
        backoff_base: float = 1.5,
        timeout: float = 30.0,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.max_retries = max(1, int(max_retries))
        self.backoff_base = float(backoff_base)
        self.timeout = float(timeout)
        self.rate_limiter = rate_limiter
        self._sleep = sleep

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self.rate_limiter is not None:
                self.rate_limiter.wait()
            try:
                log.debug("GET %s params=%s attempt=%d", url, params, attempt)
                response = self.session.get(
                    url, params=params, headers=dict(headers or {}), timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                log.warning("request to %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, exc)
            else:
                if response.status_code == 429:
                    last_error = RateLimitError(f"{url} returned 429 (rate limited)")
                    log.warning("rate limited by %s (attempt %d/%d)", url, attempt, self.max_retries)
                elif response.status_code in RETRYABLE_STATUS:
                    last_error = ProviderError(f"{url} returned HTTP {response.status_code}")
                    log.warning(
                        "transient HTTP %d from %s (attempt %d/%d)",
                        response.status_code,
                        url,
                        attempt,
                        self.max_retries,
                    )
                elif not response.ok:
                    # 4xx other than 429 will not improve by retrying.
                    raise ProviderError(
                        f"{url} returned HTTP {response.status_code}: {response.text[:300]}"
                    )
                else:
                    return response

            if attempt < self.max_retries:
                delay = self.backoff_base**attempt + random.uniform(0, 0.5)
                log.debug("backing off %.2fs before retrying %s", delay, url)
                self._sleep(delay)

        raise ProviderError(
            f"giving up on {url} after {self.max_retries} attempts: {last_error}"
        ) from last_error

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        response = self.get(url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{url} returned a malformed JSON body: {response.text[:300]}"
            ) from exc

    def close(self) -> None:
        self.session.close()
