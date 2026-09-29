"""Rebalancing rules and position weighting (REQUIREMENTS 18, 21).

Rankings are produced every week, but the portfolio does not have to act on
them every week. Between rebalance dates the new contribution is simply spread
across the holdings chosen at the last rebalance; on a rebalance date the
holding set is replaced by the current selection and existing positions are
traded back to target weights.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import SleeveConfig
from ..data.store import PointInTimeView
from ..errors import ConfigError
from ..logging_config import get_logger

log = get_logger(__name__)

_PERIOD_MONTHS = {
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "annual": 12,
}


@dataclass
class RebalanceCalendar:
    """Tracks when each sleeve last rebalanced and decides the next one."""

    frequency: str
    last_rebalance: dt.date | None = None
    last_period_key: tuple[int, int] | None = None

    def due(self, date: dt.date, extra_trigger: bool = False) -> bool:
        if self.last_rebalance is None:
            return True                      # the first contribution always buys
        if self.frequency == "weekly":
            return True
        if self.frequency == "threshold":
            return extra_trigger
        months = _PERIOD_MONTHS.get(self.frequency)
        if months is None:
            raise ConfigError(f"unsupported rebalance frequency: {self.frequency!r}")
        key = _period_key(date, months)
        return key != self.last_period_key

    def mark(self, date: dt.date) -> None:
        self.last_rebalance = date
        months = _PERIOD_MONTHS.get(self.frequency)
        self.last_period_key = _period_key(date, months) if months else None


def _period_key(date: dt.date, months: int) -> tuple[int, int]:
    """Bucket a date into its rebalance period, e.g. (2024, 3) for Q3."""
    return (date.year, (date.month - 1) // months)


def is_rebalance_date(
    frequency: str, date: dt.date, last_rebalance: dt.date | None, extra_trigger: bool = False
) -> bool:
    """Stateless form of :meth:`RebalanceCalendar.due`, used by tests."""
    calendar = RebalanceCalendar(frequency, last_rebalance)
    if last_rebalance is not None:
        months = _PERIOD_MONTHS.get(frequency)
        calendar.last_period_key = _period_key(last_rebalance, months) if months else None
    return calendar.due(date, extra_trigger)


def threshold_triggered(
    config: SleeveConfig,
    current_weights: Mapping[str, float],
    target_holdings: Sequence[str],
    current_ranks: Mapping[str, int],
) -> tuple[bool, str]:
    """Evaluate the threshold rebalance rules (REQUIREMENTS 21).

    Fires when a holding has drifted too far from its target weight, or has
    fallen out of the acceptable rank band.
    """
    if not target_holdings:
        return False, ""
    target = 1.0 / len(target_holdings)
    for ticker in target_holdings:
        weight = current_weights.get(ticker, 0.0)
        if target > 0 and abs(weight - target) / target > config.threshold.max_weight_drift:
            return True, (
                f"{ticker} weight {weight:.2%} drifted more than "
                f"{config.threshold.max_weight_drift:.0%} from the {target:.2%} target"
            )
    for ticker in current_weights:
        rank = current_ranks.get(ticker)
        if rank is not None and rank > config.threshold.max_rank:
            return True, f"{ticker} fell to rank {rank}, beyond the limit of {config.threshold.max_rank}"
    return False, ""


def target_weights(
    tickers: Sequence[str],
    method: str,
    scores: Mapping[str, float] | None = None,
    market_caps: Mapping[str, float] | None = None,
    volatilities: Mapping[str, float] | None = None,
    custom: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Target portfolio weights, summing to 1.0.

    Every method degrades to equal weight when its inputs are unavailable, and
    says so in the log rather than producing a silently lopsided portfolio.
    """
    tickers = [t for t in tickers]
    if not tickers:
        return {}
    equal = {t: 1.0 / len(tickers) for t in tickers}

    if method == "equal":
        return equal

    if method == "score":
        values = {t: float(scores.get(t, np.nan)) for t in tickers} if scores else {}
        usable = {t: v for t, v in values.items() if np.isfinite(v) and v > 0}
        if len(usable) < len(tickers):
            log.debug("score weighting: missing scores for %d name(s), using equal weight", len(tickers) - len(usable))
            return equal
        total = sum(usable.values())
        return {t: v / total for t, v in usable.items()}

    if method == "market_cap":
        values = {t: float(market_caps.get(t, np.nan)) for t in tickers} if market_caps else {}
        usable = {t: v for t, v in values.items() if np.isfinite(v) and v > 0}
        if len(usable) < len(tickers):
            log.debug("market-cap weighting: missing caps, using equal weight")
            return equal
        total = sum(usable.values())
        return {t: v / total for t, v in usable.items()}

    if method == "volatility":
        # Inverse-volatility: each name contributes a similar risk budget.
        values = {t: float(volatilities.get(t, np.nan)) for t in tickers} if volatilities else {}
        usable = {t: 1.0 / v for t, v in values.items() if np.isfinite(v) and v > 0}
        if len(usable) < len(tickers):
            log.debug("volatility weighting: missing volatilities, using equal weight")
            return equal
        total = sum(usable.values())
        return {t: v / total for t, v in usable.items()}

    if method == "custom":
        if not custom:
            raise ConfigError("weighting method 'custom' requires explicit weights")
        usable = {t: float(custom[t]) for t in tickers if t in custom}
        total = sum(usable.values())
        if total <= 0:
            raise ConfigError("custom weights must sum to a positive number")
        return {t: v / total for t, v in usable.items()}

    raise ConfigError(f"unknown weighting method: {method!r}")


@dataclass
class ConcentrationReport:
    """Limit breaches found at a decision date (REQUIREMENTS 54).

    Each breach carries a stable ``key`` - the rule and its subject, without
    the numbers - so a limit that is exceeded every week for the same ticker
    is reported once rather than 360 times.
    """

    breaches: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)

    def add(self, key: str, message: str) -> None:
        self.keys.append(key)
        self.breaches.append(message)

    def items(self) -> list[tuple[str, str]]:
        return list(zip(self.keys, self.breaches))

    def __bool__(self) -> bool:
        return bool(self.breaches)


def check_limits(
    weights: Mapping[str, float],
    sectors: Mapping[str, str | None],
    max_single: float,
    max_sector: float,
    max_positions: int,
) -> ConcentrationReport:
    """Report concentration-limit breaches without altering the selection."""
    report = ConcentrationReport()
    for ticker, weight in weights.items():
        if weight > max_single:
            report.add(
                f"single_stock:{ticker}",
                f"{ticker} at {weight:.2%} exceeds the {max_single:.2%} single-stock limit",
            )
    by_sector: dict[str, float] = {}
    for ticker, weight in weights.items():
        sector = sectors.get(ticker)
        if sector:
            by_sector[sector] = by_sector.get(sector, 0.0) + weight
    for sector, weight in by_sector.items():
        if weight > max_sector:
            report.add(
                f"sector:{sector}",
                f"sector {sector} at {weight:.2%} exceeds the {max_sector:.2%} sector limit",
            )
    if len(weights) > max_positions:
        report.add(
            "max_positions",
            f"{len(weights)} positions exceeds the maximum of {max_positions}",
        )
    return report
