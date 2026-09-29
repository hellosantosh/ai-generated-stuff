"""Shared table builders used by the Excel, CSV and Markdown reports."""

from __future__ import annotations

import datetime as dt
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..backtest.metrics import resample_returns


def rankings_frame(rankings: Mapping[dt.date, object], limit_dates: int | None = None) -> pd.DataFrame:
    """Flatten every weekly ranking into one long frame."""
    items = sorted(rankings.items())
    if limit_dates:
        items = items[-limit_dates:]
    frames = []
    for decision_date, result in items:
        table = getattr(result, "table", None)
        if table is None or table.empty:
            continue
        frame = table.reset_index()
        frame["decision_date"] = decision_date
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    columns = [
        "decision_date", "sleeve", "ticker", "company_name", "sector", "rank",
        "total_score", "growth", "momentum", "quality", "valuation", "risk",
        "relative_strength", "selected",
    ]
    present = [c for c in columns if c in combined.columns]
    return combined[present].sort_values(["decision_date", "sleeve", "rank"])


def factor_scores_frame(factors: pd.DataFrame, decision_date: dt.date) -> pd.DataFrame:
    """Raw factor values for one decision date, in long form."""
    if factors.empty:
        return pd.DataFrame()
    frame = factors.reset_index()
    id_columns = [c for c in ("ticker", "sector", "company_name") if c in frame.columns]
    value_columns = [
        c for c in frame.columns
        if c not in id_columns and c not in ("price_as_of", "fundamentals_as_of")
    ]
    melted = frame.melt(
        id_vars=id_columns, value_vars=value_columns, var_name="factor", value_name="raw_value"
    )
    melted.insert(0, "decision_date", decision_date)
    return melted.dropna(subset=["raw_value"])


def monthly_returns_table(returns: pd.Series) -> pd.DataFrame:
    """Calendar-month returns laid out year by month."""
    monthly = resample_returns(returns, "ME")
    if monthly.empty:
        return pd.DataFrame()
    frame = monthly.to_frame("return")
    frame["year"] = frame.index.year
    frame["month"] = frame.index.strftime("%b")
    pivot = frame.pivot_table(index="year", columns="month", values="return", aggfunc="first")
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    pivot = pivot.reindex(columns=[m for m in order if m in pivot.columns])
    yearly = resample_returns(returns, "YE")
    if not yearly.empty:
        pivot["Year"] = [
            float(yearly[yearly.index.year == year].iloc[0]) if (yearly.index.year == year).any() else np.nan
            for year in pivot.index
        ]
    return pivot.reset_index()


def annual_returns_table(variants: Mapping[str, object]) -> pd.DataFrame:
    """Year-by-year time-weighted return for every variant."""
    columns: dict[str, pd.Series] = {}
    for name, variant in variants.items():
        yearly = resample_returns(getattr(variant, "returns"), "YE")
        if yearly.empty:
            continue
        columns[name] = pd.Series(yearly.to_numpy(), index=yearly.index.year)
    if not columns:
        return pd.DataFrame()
    frame = pd.DataFrame(columns)
    frame.index.name = "year"
    return frame.reset_index()


def metrics_frame(variants: Mapping[str, object]) -> pd.DataFrame:
    rows = [getattr(v, "metrics").to_dict() for v in variants.values()]
    return pd.DataFrame(rows)


def ending_positions_frame(result, final_date: dt.date) -> pd.DataFrame:
    """Final holdings across every variant."""
    rows = []
    for name, variant in result.variants.items():
        portfolio = variant.portfolio
        for sleeve in portfolio.sleeves.values():
            for ticker, position in sleeve.positions.items():
                if position.shares <= 0:
                    continue
                try:
                    price = portfolio.market.mark_price(ticker, final_date)
                except Exception:
                    price = float("nan")
                rows.append(
                    {
                        "variant": name,
                        "sleeve": sleeve.name,
                        "ticker": ticker,
                        "shares": position.shares,
                        "price": price,
                        "value": position.shares * price,
                        "cost_basis": position.cost_basis,
                        "unrealized": position.shares * price - position.cost_basis,
                    }
                )
    return pd.DataFrame(rows)


def ending_sector_allocation(
    result, sectors: Mapping[str, str | None], final_date: dt.date, variant: str = "combined"
) -> dict[str, float]:
    """Sector weights of the final portfolio for one variant."""
    if variant not in result.variants:
        variant = next(iter(result.variants))
    portfolio = result.variants[variant].portfolio
    weights: dict[str, float] = {}
    total = 0.0
    for sleeve in portfolio.sleeves.values():
        for ticker, position in sleeve.positions.items():
            if position.shares <= 0:
                continue
            try:
                value = position.shares * portfolio.market.mark_price(ticker, final_date)
            except Exception:
                continue
            label = sectors.get(ticker) or ("Benchmark ETF" if ticker == result.config.benchmark.ticker else "Unclassified")
            weights[label] = weights.get(label, 0.0) + value
            total += value
    if total <= 0:
        return {}
    return {k: v / total for k, v in sorted(weights.items(), key=lambda kv: -kv[1])}
