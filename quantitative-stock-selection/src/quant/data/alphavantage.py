"""Alpha Vantage price provider (REQUIREMENTS 6.1).

The API key is read from ``ALPHAVANTAGE_API_KEY`` and is never accepted as a
literal in configuration. Alpha Vantage signals trouble inside a 200 response
body (``Note``, ``Information``, ``Error Message``), so every payload is
inspected before it is parsed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ..config import require_env
from ..errors import InsufficientDataError, ProviderError, RateLimitError
from ..logging_config import get_logger
from .cache import FrameCache, RawCache, safe_key
from .http import HttpClient, RateLimiter
from .types import PriceHistory, ProviderInfo

log = get_logger(__name__)

BASE_URL = "https://www.alphavantage.co/query"

# The free tier allows 5 calls/minute; 13s between calls stays inside it with
# margin. Premium keys can lower this through `calls_per_minute`.
DEFAULT_CALLS_PER_MINUTE = 5


class AlphaVantageProvider:
    name = "alphavantage"

    def __init__(
        self,
        cache: FrameCache,
        raw_cache: RawCache | None = None,
        api_key: str | None = None,
        calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE,
        max_retries: int = 4,
        backoff_base: float = 1.5,
        timeout: float = 30.0,
    ) -> None:
        self.cache = cache
        self.raw_cache = raw_cache
        self._api_key = api_key
        interval = 60.0 / max(1, int(calls_per_minute))
        self.http = HttpClient(
            user_agent="quant-stock-selector/1.0",
            max_retries=max_retries,
            backoff_base=backoff_base,
            timeout=timeout,
            rate_limiter=RateLimiter(interval),
        )

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = require_env("ALPHAVANTAGE_API_KEY", "Alpha Vantage")
        return self._api_key

    # --- prices ----------------------------------------------------------
    def fetch_prices(
        self, ticker: str, start: dt.date, end: dt.date, force: bool = False
    ) -> PriceHistory:
        ticker = ticker.upper()
        key = safe_key(ticker)
        if not force:
            cached = self.cache.load(self.name, key)
            if cached is not None:
                frame, info = cached
                window = self._slice(frame, start, end)
                if not window.empty:
                    return PriceHistory.from_frame(ticker, window, info)

        payload = self._request(
            {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": ticker,
                "outputsize": "full",
                "datatype": "json",
            },
            subject=ticker,
        )
        if self.raw_cache is not None:
            self.raw_cache.write(self.name, f"daily_{key}", payload)

        frame = self._parse_daily(ticker, payload)
        info = self.cache.store(
            self.name,
            key,
            frame,
            endpoint=f"{BASE_URL}?function=TIME_SERIES_DAILY_ADJUSTED&symbol={ticker}",
        )
        window = self._slice(frame, start, end)
        if window.empty:
            raise InsufficientDataError(
                f"Alpha Vantage has no {ticker} bars between {start} and {end}"
            )
        return PriceHistory.from_frame(ticker, window, info)

    @staticmethod
    def _slice(frame: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
        df = frame.copy()
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
        return df.loc[pd.Timestamp(start) : pd.Timestamp(end)]

    def _request(self, params: Mapping[str, Any], subject: str) -> dict[str, Any]:
        payload = self.http.get_json(BASE_URL, params={**params, "apikey": self.api_key})
        if not isinstance(payload, dict):
            raise ProviderError(f"Alpha Vantage returned a non-object body for {subject}")

        if "Error Message" in payload:
            raise ProviderError(f"Alpha Vantage rejected {subject}: {payload['Error Message']}")
        if "Note" in payload:
            raise RateLimitError(f"Alpha Vantage rate limit hit for {subject}: {payload['Note']}")
        if "Information" in payload and not any(k.startswith("Time Series") for k in payload):
            # Premium-endpoint and quota messages both arrive this way.
            raise ProviderError(
                f"Alpha Vantage declined the request for {subject}: {payload['Information']}"
            )
        return payload

    @staticmethod
    def _parse_daily(ticker: str, payload: Mapping[str, Any]) -> pd.DataFrame:
        series_key = next((k for k in payload if k.startswith("Time Series")), None)
        if series_key is None:
            raise ProviderError(
                f"Alpha Vantage response for {ticker} has no time series: "
                f"keys={sorted(payload)[:6]}"
            )
        series = payload[series_key]
        if not isinstance(series, dict) or not series:
            raise InsufficientDataError(f"Alpha Vantage returned an empty series for {ticker}")

        rows = []
        for date_str, values in series.items():
            try:
                rows.append(
                    {
                        "date": pd.Timestamp(date_str).normalize(),
                        "open": float(values["1. open"]),
                        "high": float(values["2. high"]),
                        "low": float(values["3. low"]),
                        "close": float(values["4. close"]),
                        "adj_close_provider": float(values.get("5. adjusted close", "nan")),
                        "volume": float(values.get("6. volume", "nan")),
                        "dividend": float(values.get("7. dividend amount", 0.0)),
                        "split_coef": float(values.get("8. split coefficient", 1.0)),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Alpha Vantage bar for {ticker} on {date_str} is malformed: {exc}"
                ) from exc

        frame = pd.DataFrame(rows).set_index("date").sort_index()
        return frame[["open", "high", "low", "close", "volume", "dividend", "split_coef"]]

    def close(self) -> None:
        self.http.close()
