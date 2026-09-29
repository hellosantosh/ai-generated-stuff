"""Risk factors (REQUIREMENTS 12).

Risk enters the composite score as a penalty, not an exclusion: the scoring
layer inverts these so that a lower realized volatility scores higher. Only
the explicitly configured risk controls (REQUIREMENTS 55) can remove a stock
from consideration outright, and those are off by default.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.store import PointInTimeView

TRADING_DAYS_PER_YEAR = 252
MIN_RETURN_OBSERVATIONS = 60


def _daily_returns(series: pd.Series, lookback: int) -> pd.Series | None:
    window = series.iloc[-(lookback + 1) :]
    if len(window) < MIN_RETURN_OBSERVATIONS:
        return None
    returns = window.pct_change().dropna()
    returns = returns[np.isfinite(returns)]
    return returns if len(returns) >= MIN_RETURN_OBSERVATIONS else None


def annualized_volatility(returns: pd.Series) -> float:
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def downside_volatility(returns: pd.Series, threshold: float = 0.0) -> float:
    """Annualized standard deviation of returns below ``threshold``.

    Divided by the full observation count, not just the negative ones, which
    is the convention the Sortino ratio expects.
    """
    shortfall = np.minimum(returns.to_numpy() - threshold, 0.0)
    return float(np.sqrt(np.mean(shortfall**2) * TRADING_DAYS_PER_YEAR))


def max_drawdown(series: pd.Series) -> float:
    """Worst peak-to-trough decline of a value series, as a positive fraction."""
    values = series.to_numpy(dtype=float)
    if len(values) < 2:
        return 0.0
    running_peak = np.maximum.accumulate(values)
    drawdowns = np.where(running_peak > 0, values / running_peak - 1.0, 0.0)
    return float(-drawdowns.min())


def risk_factors(view: PointInTimeView, ticker: str) -> dict[str, float | None]:
    frame = view.history(ticker)
    factors: dict[str, float | None] = {
        "volatility_1y": None,
        "volatility_3y": None,
        "downside_volatility": None,
        "beta": None,
        "max_drawdown_1y": None,
        "max_drawdown_3y": None,
        "drawdown_52w": None,
        "sharpe_1y": None,
        "sortino_1y": None,
    }
    if frame.empty:
        return factors

    total_return = frame["total_return_index"]
    returns_1y = _daily_returns(total_return, TRADING_DAYS_PER_YEAR)
    if returns_1y is not None:
        volatility = annualized_volatility(returns_1y)
        factors["volatility_1y"] = volatility
        downside = downside_volatility(returns_1y)
        factors["downside_volatility"] = downside
        annualized = float((1.0 + returns_1y).prod() ** (TRADING_DAYS_PER_YEAR / len(returns_1y)) - 1.0)
        if volatility > 0:
            factors["sharpe_1y"] = annualized / volatility
        if downside > 0:
            factors["sortino_1y"] = annualized / downside

    returns_3y = _daily_returns(total_return, TRADING_DAYS_PER_YEAR * 3)
    if returns_3y is not None:
        factors["volatility_3y"] = annualized_volatility(returns_3y)

    if len(total_return) > TRADING_DAYS_PER_YEAR:
        factors["max_drawdown_1y"] = max_drawdown(total_return.iloc[-TRADING_DAYS_PER_YEAR:])
    if len(total_return) > TRADING_DAYS_PER_YEAR * 3:
        factors["max_drawdown_3y"] = max_drawdown(total_return.iloc[-TRADING_DAYS_PER_YEAR * 3 :])

    close = frame["adj_close"]
    window = close.iloc[-TRADING_DAYS_PER_YEAR:]
    if len(window) >= 21:
        high = float(window.max())
        if high > 0:
            factors["drawdown_52w"] = max(0.0, 1.0 - float(close.iloc[-1]) / high)

    benchmark = view.benchmark
    if view.has(benchmark) and ticker.upper() != benchmark and returns_1y is not None:
        market_series = view.total_return_series(benchmark)
        market_returns = _daily_returns(market_series, TRADING_DAYS_PER_YEAR)
        if market_returns is not None:
            aligned = pd.concat(
                [returns_1y.rename("stock"), market_returns.rename("market")], axis=1
            ).dropna()
            if len(aligned) >= MIN_RETURN_OBSERVATIONS:
                market_variance = float(aligned["market"].var(ddof=1))
                if market_variance > 0:
                    covariance = float(aligned["stock"].cov(aligned["market"]))
                    factors["beta"] = covariance / market_variance

    return factors
