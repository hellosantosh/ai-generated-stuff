"""Technical and momentum factors (REQUIREMENTS 10).

All returns are total returns: they run off ``total_return_index``, which
reinvests dividends. Using price alone would systematically penalize
high-yield sectors such as Utilities and Energy in the momentum ranking.

Lookbacks are counted in trading days, not calendar days, so a holiday-heavy
stretch does not silently shorten the window.
"""

from __future__ import annotations

import datetime as dt
from typing import Mapping

import numpy as np
import pandas as pd

from ..data.store import PointInTimeView

TRADING_DAYS_PER_MONTH = 21
TRADING_DAYS_PER_YEAR = 252

LOOKBACKS = {
    "return_1m": TRADING_DAYS_PER_MONTH,
    "return_3m": TRADING_DAYS_PER_MONTH * 3,
    "return_6m": TRADING_DAYS_PER_MONTH * 6,
    "return_12m": TRADING_DAYS_PER_YEAR,
    "return_18m": int(TRADING_DAYS_PER_YEAR * 1.5),
    "return_24m": TRADING_DAYS_PER_YEAR * 2,
}


def trailing_return(series: pd.Series, lookback: int) -> float | None:
    """Total return over the last ``lookback`` trading days."""
    if len(series) <= lookback:
        return None
    end = float(series.iloc[-1])
    start = float(series.iloc[-1 - lookback])
    if start <= 0 or not np.isfinite(start) or not np.isfinite(end):
        return None
    return end / start - 1.0


def momentum_factors(view: PointInTimeView, ticker: str) -> dict[str, float | None]:
    """Momentum, trend and relative-strength factors for one stock."""
    frame = view.history(ticker)
    factors: dict[str, float | None] = {name: None for name in LOOKBACKS}
    factors.update(
        {
            "ma50": None,
            "ma200": None,
            "price_to_50dma": None,
            "price_to_200dma": None,
            "ma50_to_ma200": None,
            "dist_52w_high": None,
            "dist_52w_low": None,
            "rs_vs_market_12m": None,
            "rs_vs_market_6m": None,
            "rs_vs_market_3m": None,
            "rs_vs_sector_12m": None,
            "above_200dma": None,
            "atr_14": None,
        }
    )
    if frame.empty:
        return factors

    total_return = frame["total_return_index"]
    close = frame["adj_close"]

    for name, lookback in LOOKBACKS.items():
        factors[name] = trailing_return(total_return, lookback)

    if len(close) >= 50:
        ma50 = float(close.iloc[-50:].mean())
        factors["ma50"] = ma50
        factors["price_to_50dma"] = float(close.iloc[-1]) / ma50 - 1.0 if ma50 > 0 else None
    if len(close) >= 200:
        ma200 = float(close.iloc[-200:].mean())
        factors["ma200"] = ma200
        if ma200 > 0:
            factors["price_to_200dma"] = float(close.iloc[-1]) / ma200 - 1.0
            factors["above_200dma"] = 1.0 if float(close.iloc[-1]) > ma200 else 0.0
            if factors["ma50"] is not None:
                factors["ma50_to_ma200"] = factors["ma50"] / ma200 - 1.0

    window = close.iloc[-TRADING_DAYS_PER_YEAR:]
    if len(window) >= TRADING_DAYS_PER_MONTH:
        high = float(window.max())
        low = float(window.min())
        price = float(close.iloc[-1])
        if high > 0:
            # Distance below the 52-week high; 0 means sitting at the high.
            factors["dist_52w_high"] = (high - price) / high
        if low > 0:
            factors["dist_52w_low"] = (price - low) / low

    if len(frame) >= 15:
        recent = frame.iloc[-15:]
        previous_close = recent["adj_close"].shift(1)
        true_range = pd.concat(
            [
                recent["adj_high"] - recent["adj_low"],
                (recent["adj_high"] - previous_close).abs(),
                (recent["adj_low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = float(true_range.iloc[1:].mean())
        price = float(recent["adj_close"].iloc[-1])
        factors["atr_14"] = atr / price if price > 0 else None

    benchmark = view.benchmark
    if view.has(benchmark) and ticker.upper() != benchmark:
        market = view.total_return_series(benchmark)
        for name, lookback in (
            ("rs_vs_market_12m", TRADING_DAYS_PER_YEAR),
            ("rs_vs_market_6m", TRADING_DAYS_PER_MONTH * 6),
            ("rs_vs_market_3m", TRADING_DAYS_PER_MONTH * 3),
        ):
            stock_return = trailing_return(total_return, lookback)
            market_return = trailing_return(market, lookback)
            if stock_return is not None and market_return is not None:
                factors[name] = stock_return - market_return

    return factors


def sector_relative_strength(
    factors_by_ticker: Mapping[str, Mapping[str, float | None]],
    sectors: Mapping[str, str | None],
    source_factor: str = "return_12m",
    target_factor: str = "rs_vs_sector_12m",
) -> dict[str, float | None]:
    """Each stock's 12-month return less its sector's equal-weighted average.

    Computed cross-sectionally after per-stock factors exist, since it needs
    every peer's return. Sectors with fewer than three usable names are left
    as ``None`` rather than compared against a one-stock "average".
    """
    grouped: dict[str, list[float]] = {}
    for ticker, factors in factors_by_ticker.items():
        sector = sectors.get(ticker)
        value = factors.get(source_factor)
        if sector and value is not None and np.isfinite(value):
            grouped.setdefault(sector, []).append(float(value))

    averages = {
        sector: float(np.mean(values)) for sector, values in grouped.items() if len(values) >= 3
    }

    out: dict[str, float | None] = {}
    for ticker, factors in factors_by_ticker.items():
        sector = sectors.get(ticker)
        value = factors.get(source_factor)
        if sector in averages and value is not None and np.isfinite(value):
            out[ticker] = float(value) - averages[sector]
        else:
            out[ticker] = None
    return out
