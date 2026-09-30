"""Index and size-cohort performance over a user-chosen window.

Two questions, one window:

1. How did the major US indices do? Each is represented by the ETF that tracks
   it, because an ETF is what a person can actually buy and because the price
   series is the one the rest of this application already knows how to read.
2. How did the largest N companies in the S&P 500 do against the S&P 500
   itself? A cohort is formed **on the start date** from the companies that
   were index members then, weighted by market cap as it was known then, and
   held to the end date. Nothing about the future is used to pick the members,
   which is the whole point: a "top 10" chosen with hindsight is a list of
   known winners and will beat any index ever assembled.

Every series here is a total-return series - dividends reinvested at the close
on which they went ex - so a high-yield index is not quietly penalized against
a low-yield one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..data.types import PriceHistory
from ..errors import DataError
from ..logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class IndexProxy:
    """An index and the ETF used to price it."""

    ticker: str
    label: str
    note: str


# Ordered from broadest-known to narrowest; the UI shows them in this order and
# assigns its categorical colors by position, so the order is part of the API.
MAJOR_INDICES: tuple[IndexProxy, ...] = (
    IndexProxy("IVV", "S&P 500", "500 US large caps, cap weighted"),
    IndexProxy("QQQ", "Nasdaq-100", "the 100 largest non-financial Nasdaq listings"),
    IndexProxy(
        "QQQM", "Nasdaq-100 (QQQM)",
        "the same index as QQQ at a lower fee; launched October 2020, so it has no "
        "history before then",
    ),
    IndexProxy("DIA", "Dow Jones Industrial Average", "30 US blue chips, price weighted"),
    IndexProxy("IWM", "Russell 2000", "US small caps"),
    IndexProxy("IJH", "S&P MidCap 400", "US mid caps"),
    IndexProxy("IJR", "S&P SmallCap 600", "US small caps, profitability screened"),
    IndexProxy("VTI", "Total US market", "essentially every listed US company"),
)

PROXY_BY_TICKER = {proxy.ticker: proxy for proxy in MAJOR_INDICES}

# What the tab charts before anyone touches it. QQQM is left off deliberately:
# it tracks the same index as QQQ, so the two plot as one line drawn twice, and
# its 2020 inception would flag a late start on any longer window.
DEFAULT_INDICES: tuple[str, ...] = ("IVV", "QQQ", "DIA", "IWM")

# The cohort sizes the UI offers. Anything larger than about half the index
# stops being a "top N" and starts being the index; at the other end, "top 1"
# is a single company rather than a portfolio, which the UI says out loud.
COHORT_SIZES: tuple[int, ...] = (1, 5, 10, 20, 50, 75, 100, 150, 200, 250)

# Daily data over twenty years is 5,000 points per line, which no screen can
# resolve and no browser enjoys drawing. Thin to this many, keeping the first
# and last so the endpoints - the numbers people read off - stay exact.
MAX_POINTS = 420

TRADING_DAYS_PER_YEAR = 252.0


# --- series helpers --------------------------------------------------------
def window(series: pd.Series, start: dt.date, end: dt.date) -> pd.Series:
    return series.loc[pd.Timestamp(start) : pd.Timestamp(end)].dropna()


def rebase(series: pd.Series, base: float = 100.0) -> pd.Series:
    """Index a series to ``base`` at its first observation."""
    if series.empty:
        return series
    first = float(series.iloc[0])
    if not np.isfinite(first) or first <= 0:
        raise DataError("cannot rebase a series whose first value is not positive")
    return series / first * base


def thin(series: pd.Series, max_points: int = MAX_POINTS) -> pd.Series:
    """Even sample down to ``max_points``, always keeping both endpoints."""
    if len(series) <= max_points:
        return series
    step = int(np.ceil(len(series) / max_points))
    sampled = series.iloc[::step]
    if sampled.index[-1] != series.index[-1]:
        sampled = pd.concat([sampled, series.iloc[[-1]]])
    return sampled


def points(series: pd.Series, digits: int = 3) -> list[list[object]]:
    return [
        [ts.date().isoformat(), round(float(value), digits)]
        for ts, value in series.items()
        if np.isfinite(value)
    ]


def max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    running_peak = series.cummax()
    return float((series / running_peak - 1.0).min())


def summarize(rebased: pd.Series) -> dict[str, object]:
    """Headline numbers for one rebased series."""
    if rebased.empty:
        return {}
    total = float(rebased.iloc[-1] / rebased.iloc[0] - 1.0)
    days = (rebased.index[-1] - rebased.index[0]).days
    years = days / 365.25
    # Annualizing a window shorter than a year extrapolates a few weeks into a
    # yearly rate, which reads as a forecast. Report it only past one year.
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0) if years >= 1.0 else None
    returns = rebased.pct_change().dropna()
    volatility = (
        float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(returns) > 2 else None
    )
    return {
        "total_return": total,
        "cagr": cagr,
        "volatility": volatility,
        "max_drawdown": max_drawdown(rebased),
        "start_date": rebased.index[0].date().isoformat(),
        "end_date": rebased.index[-1].date().isoformat(),
        "observations": int(len(rebased)),
        "years": round(years, 2),
    }


# --- major indices ---------------------------------------------------------
def index_performance(
    histories: Mapping[str, PriceHistory],
    start: dt.date,
    end: dt.date,
) -> dict[str, object]:
    """Rebase each index proxy to 100 at ``start``.

    An ETF younger than the window is reported with its own inception date
    rather than being silently rebased to a later start, which would make it
    look like it matched the others up to that point.
    """
    series: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for ticker, history in histories.items():
        proxy = PROXY_BY_TICKER.get(ticker.upper())
        label = proxy.label if proxy else ticker.upper()
        total_return = window(history.frame["total_return_index"], start, end)
        if len(total_return) < 2:
            skipped.append({
                "ticker": ticker.upper(),
                "label": label,
                "reason": f"no usable price history between {start} and {end}",
            })
            continue
        rebased = rebase(total_return)
        entry: dict[str, object] = {
            "ticker": ticker.upper(),
            "label": label,
            "note": proxy.note if proxy else "",
            "points": points(thin(rebased)),
            **summarize(rebased),
        }
        actual_start = total_return.index[0].date()
        if actual_start > start:
            entry["late_start"] = (
                f"{label} has no data before {actual_start}; its line starts there, "
                f"so it is not comparable with the others over the full window."
            )
        series.append(entry)

    series.sort(key=lambda row: MAJOR_INDICES.index(PROXY_BY_TICKER[row["ticker"]])
                if row["ticker"] in PROXY_BY_TICKER else 99)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "series": series,
        "skipped": skipped,
        "basis": "total return, dividends reinvested, rebased to 100 at the start date",
    }


# --- size cohorts ----------------------------------------------------------
def cohort_performance(
    total_returns: pd.DataFrame,
    market_caps: Mapping[str, float],
    benchmark: pd.Series,
    start: dt.date,
    end: dt.date,
    sizes: Sequence[int] = COHORT_SIZES,
    benchmark_label: str = "S&P 500",
) -> dict[str, object]:
    """Cap-weighted buy-and-hold cohorts of the N largest members.

    ``total_returns`` is a frame of per-company total-return series already
    restricted to the window and to companies that were index members on the
    start date. ``market_caps`` is each company's capitalization as known on
    that date - known, not restated, so a later restatement cannot reorder the
    cohort after the fact.

    Weights are set once, on the start date, and never touched again. That is
    what "buy the 50 biggest and hold" means, and it is why a cohort's weights
    drift: the point of the chart is to show that drift paying off or not.
    """
    frame = total_returns.dropna(axis=1, how="all")
    if frame.empty or benchmark.empty:
        raise DataError("no usable price history in the requested window")

    # A company that stops trading mid-window is carried forward at its last
    # price: the position is assumed liquidated into cash and left there. It is
    # the least flattering assumption available without delisting returns.
    carried = [str(c) for c in frame.columns if frame[c].isna().any()]
    frame = frame.ffill()
    started = frame.iloc[0]
    usable = [str(c) for c in frame.columns if np.isfinite(started[c]) and started[c] > 0]
    frame = frame[usable]
    rebased = frame.divide(frame.iloc[0], axis=1)

    ranked = sorted(
        (t for t in usable if np.isfinite(market_caps.get(t, float("nan")))),
        key=lambda t: market_caps[t],
        reverse=True,
    )
    total_cap = float(sum(market_caps[t] for t in ranked))
    benchmark_rebased = rebase(benchmark)

    cohorts: list[dict[str, object]] = []
    for size in sizes:
        if size > len(ranked):
            continue
        members = ranked[:size]
        caps = np.array([market_caps[t] for t in members], dtype=float)
        weights = caps / caps.sum()
        cohort_index = rebase((rebased[members] * weights).sum(axis=1))

        # Relative index: 100 means "kept pace with the benchmark". Dividing the
        # two indices, rather than subtracting returns, keeps it multiplicative,
        # so the last value is exactly the ratio of the two ending balances.
        aligned = cohort_index.reindex(benchmark_rebased.index).ffill().dropna()
        relative = (aligned / benchmark_rebased.reindex(aligned.index) * 100.0).dropna()

        summary = summarize(cohort_index)
        cohorts.append({
            "size": size,
            "label": f"Top {size}",
            "members": members[:  min(size, 250)],
            "cap_share": (caps.sum() / total_cap) if total_cap > 0 else None,
            "points": points(thin(cohort_index)),
            "relative_points": points(thin(relative)),
            "relative_end": round(float(relative.iloc[-1]), 2) if len(relative) else None,
            "excess_return": (
                summary["total_return"] - float(benchmark_rebased.iloc[-1] / 100.0 - 1.0)
            ),
            **summary,
        })

    benchmark_summary = summarize(benchmark_rebased)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "benchmark": {
            "label": benchmark_label,
            "points": points(thin(benchmark_rebased)),
            **benchmark_summary,
        },
        "cohorts": cohorts,
        "universe_size": len(ranked),
        "carried_forward": carried,
        "basis": (
            "cap weighted on the start date, held unchanged to the end date, "
            "dividends reinvested"
        ),
    }
