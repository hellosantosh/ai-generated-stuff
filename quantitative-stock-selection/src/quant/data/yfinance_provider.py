"""yfinance price and sector provider.

yfinance needs no API key, which makes it the practical default for getting a
backtest running. REQUIREMENTS 44 is explicit that it must not be the sole
source of truth, so it is one interchangeable implementation of
``PriceProvider`` and fundamentals still come from SEC EDGAR by default.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from ..errors import DataError, InsufficientDataError, ProviderError
from ..logging_config import get_logger
from .cache import FrameCache, safe_key
from .types import FundamentalRecord, FundamentalSeries, PriceHistory, ProviderInfo

log = get_logger(__name__)


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, cache: FrameCache, raw_dir: str | Path | None = None) -> None:
        self.cache = cache
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self._yf: Any | None = None
        self._info_cache: dict[str, dict[str, Any]] = {}

    def _module(self) -> Any:
        if self._yf is None:
            try:
                import yfinance
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise ProviderError(
                    "yfinance is not installed. Run `pip install yfinance`, or set "
                    "data.provider to `alphavantage` or `synthetic`."
                ) from exc
            self._yf = yfinance
        return self._yf

    # --- prices ----------------------------------------------------------
    def fetch_prices(
        self, ticker: str, start: dt.date, end: dt.date, force: bool = False
    ) -> PriceHistory:
        ticker = ticker.upper()
        key = f"{safe_key(ticker)}_{start:%Y%m%d}_{end:%Y%m%d}"
        if not force:
            cached = self.cache.load(self.name, key)
            if cached is not None:
                frame, info = cached
                log.debug("price cache hit for %s (%s)", ticker, info.retrieved_at)
                return PriceHistory.from_frame(ticker, frame, info)

        yf = self._module()
        # auto_adjust=False keeps raw OHLC alongside explicit Dividends and
        # Stock Splits columns, which is what the split/dividend model needs.
        try:
            raw = yf.Ticker(self._yahoo_symbol(ticker)).history(
                start=start.isoformat(),
                end=(end + dt.timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=True,
                raise_errors=True,
            )
        except Exception as exc:  # yfinance raises a grab-bag of exception types
            raise ProviderError(f"yfinance download failed for {ticker}: {exc}") from exc

        if raw is None or raw.empty:
            raise InsufficientDataError(f"yfinance returned no rows for {ticker} in {start}..{end}")

        frame = self._normalize(raw)
        info = self.cache.store(
            self.name,
            key,
            frame,
            endpoint=f"yfinance://history/{ticker}?start={start}&end={end}",
        )
        if self.raw_dir is not None:
            target = self.raw_dir / self.name / f"{safe_key(ticker)}.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            raw.to_csv(target)
        return PriceHistory.from_frame(ticker, frame, info)

    def fetch_prices_bulk(
        self, tickers: Sequence[str], start: dt.date, end: dt.date, force: bool = False
    ) -> tuple[dict[str, PriceHistory], dict[str, str]]:
        """Download many tickers in one threaded request.

        Yahoo serves a multi-ticker download far faster than N sequential
        calls, which matters for a 500-name universe. Cached tickers are
        served from disk and only the remainder is requested. Returns the
        histories plus a per-ticker failure map, so one bad symbol never
        aborts the whole update.
        """
        histories: dict[str, PriceHistory] = {}
        failures: dict[str, str] = {}
        pending: list[str] = []

        for ticker in tickers:
            ticker = ticker.upper()
            key = f"{safe_key(ticker)}_{start:%Y%m%d}_{end:%Y%m%d}"
            cached = None if force else self.cache.load(self.name, key)
            if cached is None:
                pending.append(ticker)
                continue
            frame, info = cached
            try:
                histories[ticker] = PriceHistory.from_frame(ticker, frame, info)
            except (DataError, InsufficientDataError) as exc:
                failures[ticker] = f"cached data unusable: {exc}"
                pending.append(ticker)

        if not pending:
            return histories, failures

        yf = self._module()
        symbols = {self._yahoo_symbol(t): t for t in pending}
        log.info("downloading %d ticker(s) from yfinance", len(symbols))
        try:
            raw = yf.download(
                list(symbols),
                start=start.isoformat(),
                end=(end + dt.timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            raise ProviderError(f"yfinance bulk download failed: {exc}") from exc

        if raw is None or raw.empty:
            for ticker in pending:
                failures[ticker] = "yfinance returned no rows"
            return histories, failures

        for symbol, ticker in symbols.items():
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if symbol not in raw.columns.get_level_values(0):
                        failures[ticker] = "not present in the bulk response"
                        continue
                    slice_ = raw[symbol]
                else:
                    slice_ = raw
                frame = self._normalize(slice_).dropna(subset=["close"])
                if frame.empty:
                    failures[ticker] = "no usable bars in the bulk response"
                    continue
                key = f"{safe_key(ticker)}_{start:%Y%m%d}_{end:%Y%m%d}"
                info = self.cache.store(
                    self.name, key, frame,
                    endpoint=f"yfinance://download/{ticker}?start={start}&end={end}",
                )
                histories[ticker] = PriceHistory.from_frame(ticker, frame, info)
            except (DataError, InsufficientDataError, KeyError, ValueError) as exc:
                failures[ticker] = f"parse failed: {exc}"

        for ticker in pending:
            if ticker not in histories and ticker not in failures:
                failures[ticker] = "missing from the bulk response"
        return histories, failures

    @staticmethod
    def _yahoo_symbol(ticker: str) -> str:
        """Yahoo writes class shares with a dash: BRK.B -> BRK-B."""
        return ticker.replace(".", "-")

    @staticmethod
    def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        frame.index.name = "date"
        rename = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "provider_adj_close",
            "Volume": "volume",
            "Dividends": "dividend",
            "Stock Splits": "split_coef",
        }
        frame = frame.rename(columns=rename)
        for column in ("open", "high", "low", "close", "volume", "dividend", "split_coef"):
            if column not in frame.columns:
                frame[column] = 0.0 if column in ("dividend",) else None
        # yfinance encodes "no split" as 0.0; the ratio is 1.0.
        frame["split_coef"] = frame["split_coef"].fillna(0.0).replace(0.0, 1.0)
        frame["dividend"] = frame["dividend"].fillna(0.0)
        keep = ["open", "high", "low", "close", "volume", "dividend", "split_coef"]
        return frame[keep]

    # --- classification ---------------------------------------------------
    def _info(self, ticker: str) -> dict[str, Any]:
        key = ticker.upper()
        if key not in self._info_cache:
            yf = self._module()
            try:
                self._info_cache[key] = dict(yf.Ticker(self._yahoo_symbol(key)).info or {})
            except Exception as exc:
                log.warning("yfinance info lookup failed for %s: %s", key, exc)
                self._info_cache[key] = {}
        return self._info_cache[key]

    def sector_of(self, ticker: str, as_of: dt.date | None = None) -> str | None:
        """Current sector only.

        Yahoo exposes no history for this field, so the caller must record the
        classification as non-point-in-time (REQUIREMENTS 8).
        """
        return self._info(ticker).get("sector")

    def company_name(self, ticker: str) -> str | None:
        info = self._info(ticker)
        return info.get("longName") or info.get("shortName")

    def market_cap(self, ticker: str) -> float | None:
        value = self._info(ticker).get("marketCap")
        return float(value) if value else None

    # --- fundamentals (secondary source) ----------------------------------
    def fetch_fundamentals(self, ticker: str, force: bool = False) -> FundamentalSeries:
        """Quarterly statements from Yahoo.

        Yahoo does not publish filing dates. Rather than invent one we apply a
        conservative 45-day availability lag after period end and label the
        provider, so the point-in-time filter still has something defensible to
        work with. SEC EDGAR remains the preferred source.
        """
        ticker = ticker.upper()
        yf = self._module()
        series = FundamentalSeries(ticker=ticker)
        try:
            handle = yf.Ticker(self._yahoo_symbol(ticker))
            income = handle.quarterly_income_stmt
            balance = handle.quarterly_balance_sheet
            cashflow = handle.quarterly_cashflow
        except Exception as exc:
            raise ProviderError(f"yfinance fundamentals failed for {ticker}: {exc}") from exc

        if income is None or income.empty:
            return series

        field_map = {
            "revenue": ("Total Revenue", income),
            "gross_profit": ("Gross Profit", income),
            "operating_income": ("Operating Income", income),
            "net_income": ("Net Income", income),
            "eps_diluted": ("Diluted EPS", income),
            "interest_expense": ("Interest Expense", income),
            "ebitda": ("EBITDA", income),
            "total_assets": ("Total Assets", balance),
            "total_liabilities": ("Total Liabilities Net Minority Interest", balance),
            "total_equity": ("Stockholders Equity", balance),
            "total_debt": ("Total Debt", balance),
            "cash_and_equivalents": ("Cash And Cash Equivalents", balance),
            "current_assets": ("Current Assets", balance),
            "current_liabilities": ("Current Liabilities", balance),
            "shares_outstanding": ("Diluted Average Shares", income),
            "operating_cash_flow": ("Operating Cash Flow", cashflow),
            "capital_expenditures": ("Capital Expenditure", cashflow),
        }

        for column in income.columns:
            period_end = pd.Timestamp(column).date()
            metrics: dict[str, float] = {}
            for metric, (label, frame) in field_map.items():
                if frame is None or frame.empty or label not in frame.index:
                    continue
                if column not in frame.columns:
                    continue
                value = frame.loc[label, column]
                if pd.notna(value):
                    metrics[metric] = float(value)
            if "capital_expenditures" in metrics:
                # Yahoo reports capex as a negative cash outflow.
                metrics["capital_expenditures"] = abs(metrics["capital_expenditures"])
            if "operating_cash_flow" in metrics and "capital_expenditures" in metrics:
                metrics["free_cash_flow"] = (
                    metrics["operating_cash_flow"] - metrics["capital_expenditures"]
                )
            if not metrics:
                continue
            available = period_end + dt.timedelta(days=45)
            series.add(
                FundamentalRecord(
                    ticker=ticker,
                    period_end_date=period_end,
                    fiscal_period=f"Q{(period_end.month - 1) // 3 + 1}",
                    filing_date=available,
                    data_available_date=available,
                    metrics=metrics,
                    provider=self.name,
                    form="yahoo-quarterly",
                )
            )
        return series
