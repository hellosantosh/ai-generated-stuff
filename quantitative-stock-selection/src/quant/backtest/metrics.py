"""Performance and risk metrics (REQUIREMENTS 24).

Two return measures are reported side by side because they answer different
questions about a DCA strategy:

* **Time-weighted return (TWR)** removes the effect of contribution timing. It
  is what you compare against an index, and it is what the risk statistics
  (volatility, Sharpe, beta, drawdown) are computed from.
* **Money-weighted return (XIRR)** keeps the contribution timing in. It is
  what the investor actually earned on the money they put in, and in a rising
  market with steady contributions it is usually the lower of the two.

Quoting only one of them would flatter or penalize the strategy depending on
which way the market trended, so both appear in every report.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..logging_config import get_logger
from .drawdown import (
    DrawdownEpisode,
    drawdown_series,
    max_drawdown_detail,
    underwater_duration,
)

log = get_logger(__name__)

WEEKS_PER_YEAR = 52.0
# Elapsed-time day count for CAGR, averaged over the leap cycle.
DAYS_PER_YEAR = 365.25
# XIRR discounting uses actual/365 to match Excel and Google Sheets.
XIRR_DAYS_PER_YEAR = 365.0


# --- money-weighted return -------------------------------------------------
def xirr(
    cashflows: Sequence[tuple[dt.date, float]],
    guess: float = 0.10,
    tolerance: float = 1e-7,
    max_iterations: int = 200,
) -> float:
    """Internal rate of return for irregular cash flows.

    Sign convention: contributions are negative, the terminal value positive.
    Solved by bisection over a wide bracket rather than Newton's method, which
    diverges readily on the long flat NPV curves that DCA schedules produce.

    Day count is actual/365, matching the XIRR function in Excel and Google
    Sheets so the figure reconciles against a spreadsheet. Note this means a
    span crossing 29 February is genuinely longer than a year, so a 10% gain
    over a leap year annualizes slightly below 10%.
    """
    flows = [(d, float(a)) for d, a in cashflows if a != 0.0]
    if len(flows) < 2:
        return float("nan")
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return float("nan")

    base = min(d for d, _ in flows)

    def npv(rate: float) -> float:
        if rate <= -1.0:
            return float("inf")
        total = 0.0
        for date, amount in flows:
            years = (date - base).days / XIRR_DAYS_PER_YEAR
            total += amount / ((1.0 + rate) ** years)
        return total

    low, high = -0.9999, 10.0
    npv_low, npv_high = npv(low), npv(high)
    if not np.isfinite(npv_low) or not np.isfinite(npv_high) or npv_low * npv_high > 0:
        log.debug("XIRR bracket failed: npv(%.4f)=%s npv(%.4f)=%s", low, npv_low, high, npv_high)
        return float("nan")

    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        value = npv(mid)
        if abs(value) < tolerance or (high - low) < tolerance:
            return mid
        if value * npv_low > 0:
            low, npv_low = mid, value
        else:
            high = mid
    return (low + high) / 2.0


# --- time-weighted return --------------------------------------------------
def time_weighted_returns(
    value_before: Sequence[float], value_after: Sequence[float]
) -> pd.Series:
    """Per-period TWR from values measured either side of each contribution.

    ``value_before[t]`` is the mark just before contribution *t*;
    ``value_after[t-1]`` is the mark straight after the previous contribution
    was invested. Their ratio isolates market performance from cash flow.
    """
    if len(value_before) != len(value_after) or len(value_before) < 2:
        return pd.Series(dtype=float)
    returns = []
    for position in range(1, len(value_before)):
        previous = value_after[position - 1]
        current = value_before[position]
        if previous <= 0:
            returns.append(np.nan)
        else:
            returns.append(current / previous - 1.0)
    return pd.Series(returns, dtype=float)


def twr_index(returns: pd.Series, start: float = 100.0) -> pd.Series:
    clean = returns.fillna(0.0)
    return start * (1.0 + clean).cumprod()


# --- risk ------------------------------------------------------------------
def annualize_return(total_growth: float, years: float) -> float:
    if years <= 0 or total_growth <= 0:
        return float("nan")
    return total_growth ** (1.0 / years) - 1.0


def annualized_volatility(returns: pd.Series, periods_per_year: float = WEEKS_PER_YEAR) -> float:
    clean = returns.dropna()
    if len(clean) < 3:
        return float("nan")
    return float(clean.std(ddof=1) * np.sqrt(periods_per_year))


def downside_volatility(
    returns: pd.Series, threshold: float = 0.0, periods_per_year: float = WEEKS_PER_YEAR
) -> float:
    clean = returns.dropna()
    if len(clean) < 3:
        return float("nan")
    shortfall = np.minimum(clean.to_numpy() - threshold, 0.0)
    return float(np.sqrt(np.mean(shortfall**2) * periods_per_year))


def sharpe_ratio(
    returns: pd.Series, risk_free_rate: float = 0.0, periods_per_year: float = WEEKS_PER_YEAR
) -> float:
    clean = returns.dropna()
    if len(clean) < 3:
        return float("nan")
    periodic_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = clean - periodic_rf
    std = float(excess.std(ddof=1))
    if std <= 0:
        return float("nan")
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(
    returns: pd.Series, risk_free_rate: float = 0.0, periods_per_year: float = WEEKS_PER_YEAR
) -> float:
    clean = returns.dropna()
    if len(clean) < 3:
        return float("nan")
    periodic_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = clean - periodic_rf
    downside = downside_volatility(excess, 0.0, periods_per_year)
    if not np.isfinite(downside) or downside <= 0:
        return float("nan")
    return float(excess.mean() * periods_per_year / downside)


def beta_and_tracking_error(
    returns: pd.Series, benchmark_returns: pd.Series, periods_per_year: float = WEEKS_PER_YEAR
) -> tuple[float, float]:
    aligned = pd.concat(
        [returns.rename("portfolio"), benchmark_returns.rename("benchmark")], axis=1
    ).dropna()
    if len(aligned) < 3:
        return float("nan"), float("nan")
    variance = float(aligned["benchmark"].var(ddof=1))
    beta = float(aligned["portfolio"].cov(aligned["benchmark"]) / variance) if variance > 0 else float("nan")
    active = aligned["portfolio"] - aligned["benchmark"]
    tracking_error = float(active.std(ddof=1) * np.sqrt(periods_per_year))
    return beta, tracking_error


def rolling_alpha(
    returns: pd.Series, benchmark_returns: pd.Series, window: int
) -> pd.Series:
    """Rolling excess of cumulative portfolio return over the benchmark."""
    aligned = pd.concat(
        [returns.rename("portfolio"), benchmark_returns.rename("benchmark")], axis=1
    ).dropna()
    if len(aligned) < window:
        return pd.Series(dtype=float)
    portfolio = (1.0 + aligned["portfolio"]).rolling(window).apply(np.prod, raw=True) - 1.0
    benchmark = (1.0 + aligned["benchmark"]).rolling(window).apply(np.prod, raw=True) - 1.0
    return (portfolio - benchmark).dropna()


def win_rate(returns: pd.Series, benchmark_returns: pd.Series) -> float:
    aligned = pd.concat(
        [returns.rename("portfolio"), benchmark_returns.rename("benchmark")], axis=1
    ).dropna()
    if aligned.empty:
        return float("nan")
    return float((aligned["portfolio"] > aligned["benchmark"]).mean())


def resample_returns(returns: pd.Series, rule: str) -> pd.Series:
    """Compound periodic returns onto a coarser calendar (``ME``, ``YE``)."""
    clean = returns.dropna()
    if clean.empty or not isinstance(clean.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    return clean.resample(rule).apply(lambda block: float((1.0 + block).prod() - 1.0))


# --- container -------------------------------------------------------------
@dataclass
class PerformanceMetrics:
    """Every headline number for one portfolio variant."""

    variant: str
    start_date: dt.date
    end_date: dt.date
    years: float
    num_contributions: int
    weekly_contribution: float
    total_contributions: float
    ending_value: float
    total_gain: float
    return_on_contributions: float
    time_weighted_return: float
    cagr_twr: float
    xirr: float
    volatility: float
    downside_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_drawdown_days: int
    max_drawdown_peak: dt.date | None
    max_drawdown_trough: dt.date | None
    max_drawdown_recovery: dt.date | None
    longest_underwater_days: int
    beta: float
    tracking_error: float
    excess_return_vs_benchmark: float
    weeks_outperforming: float
    months_outperforming: float
    num_trades: int
    turnover: float
    num_holdings: int
    average_holding_period_days: float
    dividends_received: float
    commission_paid: float
    slippage_paid: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compute_metrics(
    variant: str,
    values: pd.DataFrame,
    returns: pd.Series,
    cashflows: Sequence[tuple[dt.date, float]],
    portfolio_summary: Mapping[str, float],
    benchmark_returns: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    weekly_contribution: float = 0.0,
    turnover: float = float("nan"),
    average_holding_period_days: float = float("nan"),
) -> PerformanceMetrics:
    """Assemble the full metric set for one variant."""
    dates = pd.DatetimeIndex(values.index)
    start_date = dates[0].date()
    end_date = dates[-1].date()
    years = max((end_date - start_date).days / DAYS_PER_YEAR, 1e-9)

    index = twr_index(returns)
    index.index = dates[1:] if len(index) == len(dates) - 1 else index.index
    total_growth = float(index.iloc[-1] / 100.0) if len(index) else float("nan")

    episode = max_drawdown_detail(index) if len(index) else None
    drawdowns = drawdown_series(index) if len(index) else pd.Series(dtype=float)

    beta = tracking_error = float("nan")
    excess = weeks_out = months_out = float("nan")
    if benchmark_returns is not None and not benchmark_returns.empty:
        beta, tracking_error = beta_and_tracking_error(returns, benchmark_returns)
        benchmark_growth = float((1.0 + benchmark_returns.fillna(0.0)).prod())
        if np.isfinite(total_growth) and benchmark_growth > 0:
            excess = total_growth - benchmark_growth
        weeks_out = win_rate(returns, benchmark_returns)
        portfolio_monthly = resample_returns(_with_dates(returns, dates), "ME")
        benchmark_monthly = resample_returns(_with_dates(benchmark_returns, dates), "ME")
        months_out = win_rate(portfolio_monthly, benchmark_monthly)

    ending_value = float(portfolio_summary.get("total_value", np.nan))
    contributions = float(portfolio_summary.get("total_contributions", np.nan))

    return PerformanceMetrics(
        variant=variant,
        start_date=start_date,
        end_date=end_date,
        years=years,
        num_contributions=len(cashflows) - 1 if cashflows else 0,
        weekly_contribution=weekly_contribution,
        total_contributions=contributions,
        ending_value=ending_value,
        total_gain=ending_value - contributions,
        return_on_contributions=float(portfolio_summary.get("return_on_contributions", np.nan)),
        time_weighted_return=total_growth - 1.0 if np.isfinite(total_growth) else float("nan"),
        cagr_twr=annualize_return(total_growth, years),
        xirr=xirr(cashflows),
        volatility=annualized_volatility(returns),
        downside_volatility=downside_volatility(returns),
        sharpe=sharpe_ratio(returns, risk_free_rate),
        sortino=sortino_ratio(returns, risk_free_rate),
        max_drawdown=-episode.depth if episode else float("nan"),
        max_drawdown_days=episode.days_to_trough if episode else 0,
        max_drawdown_peak=episode.peak_date if episode else None,
        max_drawdown_trough=episode.trough_date if episode else None,
        max_drawdown_recovery=episode.recovery_date if episode else None,
        longest_underwater_days=underwater_duration(index) if len(index) else 0,
        beta=beta,
        tracking_error=tracking_error,
        excess_return_vs_benchmark=excess,
        weeks_outperforming=weeks_out,
        months_outperforming=months_out,
        num_trades=int(portfolio_summary.get("num_trades", 0)),
        turnover=turnover,
        num_holdings=int(portfolio_summary.get("num_holdings", 0)),
        average_holding_period_days=average_holding_period_days,
        dividends_received=float(portfolio_summary.get("dividends_received", 0.0)),
        commission_paid=float(portfolio_summary.get("commission_paid", 0.0)),
        slippage_paid=float(portfolio_summary.get("slippage_paid", 0.0)),
    )


def _with_dates(returns: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Attach the contribution dates to a periodic return series."""
    if isinstance(returns.index, pd.DatetimeIndex):
        return returns
    if len(returns) == len(dates) - 1:
        return pd.Series(returns.to_numpy(), index=dates[1:])
    return returns


def holding_periods(transactions: pd.DataFrame) -> float:
    """Average days between a ticker first being bought and fully sold.

    Positions still open at the end of the backtest are measured to the last
    transaction date, so long-held winners are not excluded from the average.
    """
    if transactions.empty:
        return float("nan")
    trades = transactions[transactions["action"].isin(["BUY", "SELL"])].copy()
    if trades.empty:
        return float("nan")
    trades["trade_date"] = pd.to_datetime(trades["trade_date"])
    last_date = trades["trade_date"].max()

    spans: list[float] = []
    for (sleeve, ticker), group in trades.groupby(["sleeve", "ticker"]):
        group = group.sort_values("trade_date")
        shares = 0.0
        opened: pd.Timestamp | None = None
        for row in group.itertuples():
            if row.action == "BUY":
                if shares <= 1e-9:
                    opened = row.trade_date
                shares += row.shares
            else:
                shares -= row.shares
                if shares <= 1e-9 and opened is not None:
                    spans.append((row.trade_date - opened).days)
                    opened = None
        if shares > 1e-9 and opened is not None:
            spans.append((last_date - opened).days)
    return float(np.mean(spans)) if spans else float("nan")


def annualized_turnover(transactions: pd.DataFrame, average_value: float, years: float) -> float:
    """One-way turnover: sale proceeds over average portfolio value, per year.

    Contribution-driven buys are excluded from the numerator, so a pure
    buy-and-hold DCA strategy reports near-zero turnover rather than a figure
    that just reflects how much new cash arrived.
    """
    if transactions.empty or average_value <= 0 or years <= 0:
        return 0.0
    sells = transactions[transactions["action"] == "SELL"]
    if sells.empty:
        return 0.0
    return float(sells["gross_amount"].sum() / average_value / years)
