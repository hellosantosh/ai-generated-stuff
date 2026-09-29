"""Deterministic synthetic data provider.

Purpose: let the whole pipeline - universe, factors, scoring, backtest,
reports - run end to end with no network access and no API key, and give the
test suite stable inputs.

This data is generated from a seeded RNG. Price paths and fundamentals are
drawn **independently** of each other on purpose: nothing here is rigged so
that the strategy appears to work. Any backtest run against this provider is a
plumbing check, not investment research, and every report generated from it is
labelled ``SYNTHETIC``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Sequence

import numpy as np
import pandas as pd

from ..logging_config import get_logger
from .types import FundamentalRecord, FundamentalSeries, PriceHistory, ProviderInfo

log = get_logger(__name__)

SECTORS = [
    "Information Technology",
    "Health Care",
    "Financials",
    "Consumer Discretionary",
    "Communication Services",
    "Industrials",
    "Consumer Staples",
    "Energy",
    "Utilities",
    "Real Estate",
    "Materials",
]

# Broad, deliberately unremarkable per-sector drift/vol settings.
SECTOR_PROFILE = {
    "Information Technology": (0.11, 0.28),
    "Health Care": (0.07, 0.20),
    "Financials": (0.07, 0.24),
    "Consumer Discretionary": (0.08, 0.26),
    "Communication Services": (0.07, 0.25),
    "Industrials": (0.07, 0.22),
    "Consumer Staples": (0.05, 0.15),
    "Energy": (0.05, 0.32),
    "Utilities": (0.04, 0.16),
    "Real Estate": (0.04, 0.22),
    "Materials": (0.06, 0.23),
}

BENCHMARKS = {"IVV": (0.09, 0.17), "SPY": (0.09, 0.17), "QQQ": (0.12, 0.22)}


def _seed_for(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**32)


def trading_days(start: dt.date, end: dt.date) -> pd.DatetimeIndex:
    """Weekdays between the two dates, minus fixed-date US market holidays.

    A full exchange calendar is out of scope for synthetic data; this only
    needs to look like a plausible trading calendar.
    """
    days = pd.bdate_range(start=start, end=end, freq="C")
    holidays = set()
    for year in range(start.year, end.year + 1):
        for month, day in ((1, 1), (7, 4), (12, 25)):
            holidays.add(pd.Timestamp(year=year, month=month, day=day).normalize())
    return pd.DatetimeIndex([d for d in days if d.normalize() not in holidays])


class SyntheticProvider:
    """Generates reproducible prices, sectors and fundamentals."""

    name = "synthetic"

    def __init__(
        self,
        seed: int = 20260929,
        num_stocks: int = 120,
        history_start: dt.date = dt.date(2012, 1, 2),
    ) -> None:
        self.seed = int(seed)
        self.num_stocks = int(num_stocks)
        self.history_start = history_start
        self._tickers = self._make_tickers()
        self._sectors = {
            ticker: SECTORS[index % len(SECTORS)] for index, ticker in enumerate(self._tickers)
        }

    # --- universe -------------------------------------------------------
    def _make_tickers(self) -> list[str]:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        tickers: list[str] = []
        index = 0
        while len(tickers) < self.num_stocks:
            first = alphabet[index // len(alphabet) % len(alphabet)]
            second = alphabet[index % len(alphabet)]
            tickers.append(f"SY{first}{second}")
            index += 1
        return tickers

    @property
    def tickers(self) -> list[str]:
        return list(self._tickers)

    def constituents(self, as_of: dt.date) -> Sequence[str]:
        """Membership churns slowly so the universe is not static over time."""
        cutoff = self.num_stocks
        # Roughly one entrant per quarter after the start, capped at num_stocks.
        quarters = max(0, (as_of.year - self.history_start.year) * 4 + as_of.month // 3)
        active = min(cutoff, 60 + quarters)
        return self._tickers[:active]

    def sector_of(self, ticker: str, as_of: dt.date | None = None) -> str | None:
        return self._sectors.get(ticker.upper())

    def company_name(self, ticker: str) -> str:
        return f"{ticker} Synthetic Industries Inc."

    # --- prices ---------------------------------------------------------
    def fetch_prices(
        self, ticker: str, start: dt.date, end: dt.date, force: bool = False
    ) -> PriceHistory:
        ticker = ticker.upper()
        gen_start = min(start, self.history_start)
        dates = trading_days(gen_start, end)
        if len(dates) == 0:
            from ..errors import InsufficientDataError

            raise InsufficientDataError(f"{ticker}: synthetic range {start}..{end} has no trading days")

        rng = np.random.default_rng(_seed_for(self.seed, ticker))
        if ticker in BENCHMARKS:
            drift, vol = BENCHMARKS[ticker]
        else:
            sector = self.sector_of(ticker) or "Industrials"
            base_drift, base_vol = SECTOR_PROFILE[sector]
            drift = base_drift + rng.normal(0.0, 0.05)
            vol = max(0.08, base_vol + rng.normal(0.0, 0.05))

        n = len(dates)
        dt_step = 1.0 / 252.0
        shocks = rng.normal(0.0, 1.0, n)
        # A shared market factor keeps cross-sectional correlation plausible,
        # which matters for beta and relative-strength factors.
        market_rng = np.random.default_rng(_seed_for(self.seed, "MARKET"))
        market = market_rng.normal(0.0, 1.0, n)
        beta = 1.0 if ticker in BENCHMARKS else float(np.clip(rng.normal(1.0, 0.35), 0.2, 2.2))
        combined = beta * 0.6 * market + np.sqrt(max(1e-9, 1 - (0.6 * beta) ** 2 / 4)) * shocks

        log_returns = (drift - 0.5 * vol**2) * dt_step + vol * np.sqrt(dt_step) * combined
        start_price = float(rng.uniform(15.0, 240.0))
        close = start_price * np.exp(np.cumsum(log_returns))

        intraday = np.abs(rng.normal(0.0, 0.004, n))
        open_ = close * (1.0 + rng.normal(0.0, 0.003, n))
        high = np.maximum(close, open_) * (1.0 + intraday)
        low = np.minimum(close, open_) * (1.0 - intraday)
        volume = np.abs(rng.normal(3_000_000, 900_000, n)) + 50_000

        dividend = np.zeros(n)
        pays_dividend = ticker in BENCHMARKS or rng.random() < 0.6
        if pays_dividend:
            yield_pa = 0.015 if ticker in BENCHMARKS else float(rng.uniform(0.004, 0.035))
            # Quarterly ex-dates: the first trading day of each quarter.
            quarter_starts = pd.Series(dates).groupby(
                [pd.DatetimeIndex(dates).year, pd.DatetimeIndex(dates).quarter]
            ).head(1)
            positions = np.searchsorted(dates, pd.DatetimeIndex(quarter_starts))
            for position in positions:
                if 0 <= position < n:
                    dividend[position] = close[position] * yield_pa / 4.0

        split_coef = np.ones(n)
        if ticker not in BENCHMARKS and rng.random() < 0.15:
            position = int(rng.integers(int(n * 0.3), int(n * 0.9)))
            factor = float(rng.choice([2.0, 3.0, 4.0]))
            split_coef[position] = factor
            # Raw prices after a split trade at the divided level.
            close[position:] /= factor
            open_[position:] /= factor
            high[position:] /= factor
            low[position:] /= factor

        frame = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "dividend": dividend,
                "split_coef": split_coef,
            },
            index=dates,
        )
        frame.index.name = "date"
        frame = frame.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        info = ProviderInfo(
            name=self.name,
            retrieved_at=dt.datetime.now(),
            endpoint=f"synthetic://prices/{ticker}?seed={self.seed}",
            notes="SYNTHETIC DATA - generated, not market data",
        )
        return PriceHistory.from_frame(ticker, frame, info)

    # --- fundamentals ---------------------------------------------------
    def fetch_fundamentals(self, ticker: str, force: bool = False) -> FundamentalSeries:
        ticker = ticker.upper()
        series = FundamentalSeries(ticker=ticker)
        if ticker in BENCHMARKS:
            return series

        rng = np.random.default_rng(_seed_for(self.seed, "FUND", ticker))
        revenue = float(rng.uniform(500e6, 40e9))
        growth = float(rng.normal(0.08, 0.10))
        gross_margin = float(np.clip(rng.normal(0.45, 0.15), 0.08, 0.90))
        operating_margin = float(np.clip(gross_margin * rng.uniform(0.25, 0.65), 0.01, 0.55))
        net_margin = operating_margin * float(rng.uniform(0.55, 0.85))
        equity = revenue * float(rng.uniform(0.4, 1.6))
        debt = equity * float(rng.uniform(0.05, 1.5))
        shares = float(rng.uniform(80e6, 4e9))

        period = dt.date(self.history_start.year - 3, 3, 31)
        while period < dt.date.today() + dt.timedelta(days=120):
            quarter = (period.month - 1) // 3 + 1
            drift = float(rng.normal(growth / 4.0, 0.05))
            revenue *= 1.0 + drift
            q_revenue = revenue / 4.0
            q_operating_income = q_revenue * operating_margin * float(rng.uniform(0.85, 1.15))
            q_net_income = q_revenue * net_margin * float(rng.uniform(0.8, 1.2))
            q_ocf = q_net_income * float(rng.uniform(1.0, 1.7))
            q_capex = q_revenue * float(rng.uniform(0.02, 0.09))
            # Filing lag: 10-Q around 35 days, 10-K around 55 days.
            is_annual = quarter == 4
            lag = int(rng.integers(45, 70)) if is_annual else int(rng.integers(25, 45))
            filing_date = period + dt.timedelta(days=lag)

            series.add(
                FundamentalRecord(
                    ticker=ticker,
                    period_end_date=period,
                    fiscal_period="FY" if is_annual else f"Q{quarter}",
                    filing_date=filing_date,
                    data_available_date=filing_date,
                    provider=self.name,
                    form="10-K" if is_annual else "10-Q",
                    metrics={
                        "revenue": q_revenue,
                        "gross_profit": q_revenue * gross_margin,
                        "operating_income": q_operating_income,
                        "net_income": q_net_income,
                        "eps_diluted": q_net_income / shares,
                        "operating_cash_flow": q_ocf,
                        "capital_expenditures": q_capex,
                        "free_cash_flow": q_ocf - q_capex,
                        "total_assets": equity + debt + q_revenue,
                        "total_liabilities": debt + q_revenue * 0.3,
                        "total_equity": equity,
                        "total_debt": debt,
                        "cash_and_equivalents": q_revenue * float(rng.uniform(0.1, 0.9)),
                        "current_assets": q_revenue * float(rng.uniform(0.8, 2.0)),
                        "current_liabilities": q_revenue * float(rng.uniform(0.4, 1.2)),
                        "interest_expense": debt * 0.045 / 4.0,
                        "shares_outstanding": shares,
                        "ebitda": q_operating_income * float(rng.uniform(1.05, 1.35)),
                    },
                )
            )
            equity += q_net_income * 0.6
            shares *= float(rng.uniform(0.995, 1.002))
            month = period.month + 3
            year = period.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = 31 if month in (3, 12) else 30
            period = dt.date(year, month, day)

        return series
