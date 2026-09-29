"""Dollar-cost-averaging schedule (REQUIREMENTS 5, 20, 23).

The schedule is where look-ahead bias is either introduced or prevented, so
the two supported conventions are spelled out explicitly:

``next_open``  (default)
    Decide on the contribution day's close, execute at the **next** trading
    day's open. Signals use only data through the decision close; the fill
    happens strictly afterwards.

``same_close``
    Decide on the **previous** trading day's close, execute at the
    contribution day's close.

Both are leak free. The convention REQUIREMENTS 5 explicitly forbids - signal
from Friday's close, fill at Friday's open - is not representable here,
because ``execution_date`` is always strictly after ``decision_date``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import pandas as pd

from ..config import AppConfig, BacktestConfig
from ..data.store import MarketData
from ..errors import ConfigError, InsufficientDataError
from ..logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ContributionEvent:
    """One weekly contribution: when it is decided and when it is filled."""

    index: int
    decision_date: dt.date
    execution_date: dt.date
    amount: float
    price_field: str

    def __post_init__(self) -> None:
        if self.execution_date <= self.decision_date:
            raise ConfigError(
                f"contribution {self.index}: execution date {self.execution_date} must be "
                f"strictly after the decision date {self.decision_date}; otherwise the "
                f"fill would use information from the signal bar"
            )


def _calendar_anchors(
    market: MarketData, config: BacktestConfig, start: dt.date, end: dt.date
) -> list[dt.date]:
    """Every occurrence of the configured contribution weekday in the window."""
    anchors = pd.date_range(start=start, end=end, freq="D")
    weekday = config.contribution_day
    return [d.date() for d in anchors if d.weekday() == weekday]


def build_schedule(
    market: MarketData,
    config: AppConfig,
    amount: float | None = None,
) -> list[ContributionEvent]:
    """Build the list of contribution events for the configured window.

    When ``backtest.start_date`` is null the window is the last
    ``backtest.num_weeks`` contributions ending at or before ``end_date``.
    """
    backtest = config.backtest
    amount = config.strategy.weekly_contribution if amount is None else amount
    calendar = market.calendar
    if len(calendar) == 0:
        raise InsufficientDataError("cannot build a contribution schedule: empty trading calendar")

    window_start = backtest.start_date or calendar[0].date()
    window_end = min(backtest.end_date, calendar[-1].date())

    events: list[ContributionEvent] = []
    price_field = "adj_open" if backtest.execution == "next_open" else "adj_close"

    for anchor in _calendar_anchors(market, backtest, window_start, window_end):
        try:
            if backtest.execution == "next_open":
                # The anchor weekday may be a holiday; step back to the last
                # session so the decision always sits on a real close.
                decision = market.previous_trading_day(anchor, inclusive=True)
                execution = market.next_trading_day(decision)
            else:
                execution = market.previous_trading_day(anchor, inclusive=True)
                decision = market.previous_trading_day(execution)
        except InsufficientDataError:
            continue
        if execution > window_end or decision < window_start:
            continue
        if events and decision <= events[-1].decision_date:
            # A short holiday week can map two anchors onto one session.
            continue
        events.append(
            ContributionEvent(
                index=len(events),
                decision_date=decision,
                execution_date=execution,
                amount=amount,
                price_field=price_field,
            )
        )

    if backtest.start_date is None and backtest.num_weeks > 0:
        events = events[-backtest.num_weeks :]
    events = [
        ContributionEvent(i, e.decision_date, e.execution_date, e.amount, e.price_field)
        for i, e in enumerate(events)
    ]

    if not events:
        raise InsufficientDataError(
            f"no contribution dates between {window_start} and {window_end}; "
            f"check backtest.start_date/end_date against the loaded price history"
        )
    log.info(
        "contribution schedule: %d weekly contributions of %s from %s to %s "
        "(decide on %s close, fill at %s)",
        len(events),
        f"${amount:,.2f}",
        events[0].execution_date,
        events[-1].execution_date,
        backtest.contribution_day_name,
        "the next open" if backtest.execution == "next_open" else "that close",
    )
    return events


def split_contribution(
    amount: float, allocations: Mapping[str, float], active_sleeves: Sequence[str]
) -> dict[str, float]:
    """Divide a contribution across sleeves.

    When a variant runs only some sleeves (the "100% sector leaders"
    experiment, say), the named sleeves receive the full amount in proportion
    to their configured weights rather than a fraction of it.
    """
    weights = {s: allocations.get(s, 0.0) for s in active_sleeves}
    total = sum(weights.values())
    if total <= 0:
        raise ConfigError(
            f"sleeves {list(active_sleeves)} carry no allocation weight; "
            f"check strategy allocations in config/settings.yaml"
        )
    return {sleeve: amount * weight / total for sleeve, weight in weights.items()}
