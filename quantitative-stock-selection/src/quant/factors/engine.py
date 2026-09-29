"""Factor computation across a universe, with eligibility filtering.

Eligibility (REQUIREMENTS 16) is applied before scoring and every rejection
carries a reason, so ``validate-data`` and the weekly report can explain why a
name is absent instead of it silently vanishing.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import AppConfig
from ..data.store import PointInTimeView
from ..errors import DataError
from ..logging_config import get_logger
from .growth import GROWTH_FACTOR_NAMES, growth_factors
from .momentum import momentum_factors, sector_relative_strength
from .panel import FundamentalPanel
from .quality import QUALITY_FACTOR_NAMES, quality_factors
from .risk import risk_factors
from .valuation import VALUATION_FACTOR_NAMES, valuation_factors

log = get_logger(__name__)

TRADING_DAYS_PER_WEEK = 5


@dataclass
class FactorSet:
    """Raw factor values for one stock on one decision date."""

    ticker: str
    decision_date: dt.date
    sector: str | None
    company_name: str
    values: dict[str, float | None] = field(default_factory=dict)
    eligible: bool = True
    rejection_reason: str | None = None
    fundamentals_as_of: dt.date | None = None
    price_as_of: dt.date | None = None

    def get(self, name: str) -> float | None:
        value = self.values.get(name)
        if value is None:
            return None
        value = float(value)
        return value if np.isfinite(value) else None


def check_eligibility(
    view: PointInTimeView, ticker: str, config: AppConfig
) -> tuple[bool, str | None, dt.date | None]:
    """Apply the configured eligibility rules to one stock.

    Returns ``(eligible, reason_if_not, last_price_date)``.
    """
    rules = config.eligibility
    if not view.has(ticker):
        return False, "no price history loaded", None

    frame = view.history(ticker)
    if frame.empty:
        return False, f"no price bars on or before {view.as_of}", None

    last_date = frame.index[-1].date()
    staleness = (view.as_of - last_date).days
    if staleness > rules.max_stale_days:
        # A series that stops updating usually means the stock was delisted,
        # halted or dropped by the provider. Either way it is not tradeable.
        return False, f"price data stale: last bar {last_date} ({staleness} days)", last_date

    required_bars = rules.min_price_history_weeks * TRADING_DAYS_PER_WEEK
    if len(frame) < required_bars:
        return (
            False,
            f"insufficient history: {len(frame)} bars, need {required_bars} "
            f"({rules.min_price_history_weeks} weeks)",
            last_date,
        )

    last_price = float(frame["close"].iloc[-1])
    if not np.isfinite(last_price) or last_price <= 0:
        return False, f"unusable close price {last_price!r}", last_date
    if last_price < rules.min_price:
        return False, f"price {last_price:.2f} below minimum {rules.min_price:.2f}", last_date

    if rules.require_fundamentals and not view.has_fundamentals(ticker):
        return False, "no fundamental data available at the decision date", last_date

    controls = config.risk_controls
    if controls.min_average_dollar_volume > 0 and "volume" in frame.columns:
        recent = frame.iloc[-21:]
        dollar_volume = float((recent["close"] * recent["volume"]).mean())
        if not np.isfinite(dollar_volume) or dollar_volume < controls.min_average_dollar_volume:
            return (
                False,
                f"average dollar volume {dollar_volume:,.0f} below the required "
                f"{controls.min_average_dollar_volume:,.0f}",
                last_date,
            )

    return True, None, last_date


def compute_factors(
    view: PointInTimeView, ticker: str, config: AppConfig
) -> FactorSet:
    """Every raw factor for one stock, using only data visible at ``view.as_of``."""
    ticker = ticker.upper()
    raw_sector = view.sector_of(ticker)
    sector = config.sectors.normalize(raw_sector)
    factor_set = FactorSet(
        ticker=ticker,
        decision_date=view.as_of,
        sector=sector,
        company_name=view.company_name(ticker),
    )

    eligible, reason, price_date = check_eligibility(view, ticker, config)
    factor_set.price_as_of = price_date
    if not eligible:
        factor_set.eligible = False
        factor_set.rejection_reason = reason
        return factor_set

    if sector is None:
        factor_set.eligible = False
        factor_set.rejection_reason = (
            f"no GICS sector classification (provider said {raw_sector!r})"
        )
        return factor_set
    if sector in config.sectors.excluded:
        factor_set.eligible = False
        factor_set.rejection_reason = f"sector {sector} is excluded by configuration"
        return factor_set

    panel = FundamentalPanel(view.fundamentals(ticker))
    factor_set.fundamentals_as_of = panel.latest_filing_date

    values: dict[str, float | None] = {}
    values.update(momentum_factors(view, ticker))
    values.update(risk_factors(view, ticker))
    growth = growth_factors(view, ticker, panel)
    values.update(growth)
    values.update(quality_factors(view, ticker, panel))
    values.update(
        valuation_factors(view, ticker, panel, eps_growth=growth.get("eps_growth_yoy"))
    )
    factor_set.values = values

    if config.risk_controls.skip_below_200dma:
        above = values.get("above_200dma")
        if above is not None and above < 1.0:
            factor_set.eligible = False
            factor_set.rejection_reason = "risk control: price below the 200-day moving average"

    return factor_set


def compute_universe_factors(
    view: PointInTimeView,
    tickers: Sequence[str],
    config: AppConfig,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Factor matrix for every eligible stock, plus a rejection log.

    The returned frame is indexed by ticker and carries one column per raw
    factor plus ``sector`` and ``company_name``. Cross-sectional factors that
    need peers (sector relative strength) are filled in here, after the
    per-stock pass.
    """
    factor_sets: dict[str, FactorSet] = {}
    rejections: dict[str, str] = {}

    for ticker in tickers:
        try:
            factor_set = compute_factors(view, ticker, config)
        except DataError as exc:
            rejections[ticker] = f"factor computation failed: {exc}"
            continue
        if factor_set.eligible:
            factor_sets[ticker] = factor_set
        else:
            rejections[ticker] = factor_set.rejection_reason or "ineligible"

    if not factor_sets:
        log.warning(
            "no eligible stocks on %s out of %d candidates; top reasons: %s",
            view.as_of,
            len(tickers),
            Counter(rejections.values()).most_common(3),
        )
        return pd.DataFrame(), rejections

    sectors = {t: fs.sector for t, fs in factor_sets.items()}
    values_by_ticker = {t: fs.values for t, fs in factor_sets.items()}
    relative = sector_relative_strength(values_by_ticker, sectors)
    for ticker, value in relative.items():
        factor_sets[ticker].values["rs_vs_sector_12m"] = value

    rows = []
    for ticker, factor_set in factor_sets.items():
        row: dict[str, object] = {
            "ticker": ticker,
            "sector": factor_set.sector,
            "company_name": factor_set.company_name,
            "price_as_of": factor_set.price_as_of,
            "fundamentals_as_of": factor_set.fundamentals_as_of,
        }
        row.update(factor_set.values)
        rows.append(row)

    frame = pd.DataFrame(rows).set_index("ticker").sort_index()
    log.debug(
        "%s: %d eligible / %d candidates, %d factor columns",
        view.as_of,
        len(frame),
        len(tickers),
        len(frame.columns),
    )
    return frame, rejections


def factor_coverage(frame: pd.DataFrame, factor_names: Iterable[str]) -> dict[str, float]:
    """Fraction of stocks with a usable value for each factor."""
    if frame.empty:
        return {}
    total = len(frame)
    coverage: dict[str, float] = {}
    for name in factor_names:
        if name not in frame.columns:
            coverage[name] = 0.0
            continue
        column = pd.to_numeric(frame[name], errors="coerce")
        coverage[name] = float(column.notna().sum()) / total
    return coverage
