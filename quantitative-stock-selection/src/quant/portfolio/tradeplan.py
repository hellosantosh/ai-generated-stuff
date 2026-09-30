"""Turn a target basket plus real holdings into a proposed trade list.

This closes the gap the weekly report otherwise has: it knows what you should
own, but without your positions it can only ever say BUY. With holdings it can
say SELL, and it can tell you when a name you hold has dropped out of the
selection.

Two modes, matching what the backtest actually does:

``contribute``
    Between rebalances. New money only: buy toward the target, never sell.
    Names you hold that are no longer selected are reported as DRIFT so they
    are visible, but nothing is sold.

``rebalance``
    On a rebalance week. The whole portfolio, existing holdings plus the new
    contribution, is traded toward the target weights. This is what the
    backtest does quarterly for the sector sleeve and monthly for growth.

Every output is a *proposal*. Nothing here places an order (REQUIREMENTS 47,
48): a human reads the list and decides.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping, Sequence

import pandas as pd

from ..config import AppConfig
from ..logging_config import get_logger
from .holdings import Holdings
from .rebalance import _PERIOD_MONTHS, _period_key

log = get_logger(__name__)

Mode = Literal["contribute", "rebalance"]

# Proposals smaller than this are dropped: a $3 rebalancing trade is noise that
# costs spread and clutters the list.
MIN_TRADE_AMOUNT = 5.0
# A holding may sit this far from its target before a rebalance proposes a trim.
REBALANCE_TOLERANCE = 0.10


@dataclass
class PlannedTrade:
    ticker: str
    action: str                  # BUY | SELL | HOLD | DRIFT | NEW | EXIT
    amount: float                # dollars, positive for both buys and sells
    shares: float
    price: float
    current_weight: float
    target_weight: float
    sleeve: str = ""
    sector: str = ""
    score: float = float("nan")
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "action": self.action,
            "amount": round(self.amount, 2),
            "shares": round(self.shares, 6),
            "price": round(self.price, 4),
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "sleeve": self.sleeve,
            "sector": self.sector,
            "score": None if pd.isna(self.score) else round(float(self.score), 1),
            "reason": self.reason,
        }


@dataclass
class TradePlan:
    mode: Mode
    as_of: dt.date
    contribution: float
    trades: list[PlannedTrade] = field(default_factory=list)
    portfolio_value_before: float = 0.0
    portfolio_value_after: float = 0.0
    unpriced: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def buys(self) -> list[PlannedTrade]:
        return [t for t in self.trades if t.action in ("BUY", "NEW")]

    @property
    def sells(self) -> list[PlannedTrade]:
        return [t for t in self.trades if t.action in ("SELL", "EXIT")]

    @property
    def drift(self) -> list[PlannedTrade]:
        return [t for t in self.trades if t.action == "DRIFT"]

    @property
    def total_buys(self) -> float:
        return sum(t.amount for t in self.buys)

    @property
    def total_sells(self) -> float:
        return sum(t.amount for t in self.sells)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "as_of": self.as_of.isoformat(),
            "contribution": self.contribution,
            "portfolio_value_before": round(self.portfolio_value_before, 2),
            "portfolio_value_after": round(self.portfolio_value_after, 2),
            "total_buys": round(self.total_buys, 2),
            "total_sells": round(self.total_sells, 2),
            "trades": [t.to_dict() for t in self.trades],
            "unpriced": self.unpriced,
            "notes": self.notes,
        }


def rebalance_due(
    config: AppConfig, as_of: dt.date, last_rebalance: dt.date | None
) -> tuple[bool, str]:
    """Is a rebalance due this week, per the configured sleeve frequencies?

    The sleeves can differ (quarterly sector, monthly growth). If either is
    due, the week is a rebalance week, because both are traded in one basket.
    """
    if last_rebalance is None:
        return True, "no rebalance has been recorded yet"

    reasons: list[str] = []
    for name, sleeve in (("sector leaders", config.sector_strategy), ("high growth", config.growth_strategy)):
        frequency = sleeve.rebalance_frequency
        if frequency == "weekly":
            reasons.append(f"{name} rebalances weekly")
            continue
        if frequency == "threshold":
            continue
        months = _PERIOD_MONTHS.get(frequency)
        if months is None:
            continue
        if _period_key(as_of, months) != _period_key(last_rebalance, months):
            reasons.append(f"{name} is {frequency}, and a new period started")
    if reasons:
        return True, "; ".join(reasons)
    return False, f"last rebalance {last_rebalance}; none of the sleeves are due"


def build_trade_plan(
    holdings: Holdings,
    target_weights: Mapping[str, float],
    prices: Mapping[str, float],
    contribution: float,
    as_of: dt.date,
    mode: Mode = "contribute",
    sleeves: Mapping[str, str] | None = None,
    scores: Mapping[str, float] | None = None,
    sectors: Mapping[str, str] | None = None,
) -> TradePlan:
    """Diff the target basket against real holdings.

    ``prices`` must cover every target and every held ticker; anything missing
    is reported in ``plan.unpriced`` rather than silently valued at zero.
    """
    sleeves = sleeves or {}
    scores = scores or {}
    sectors = sectors or {}
    plan = TradePlan(mode=mode, as_of=as_of, contribution=contribution)

    held_values = holdings.market_values(prices)
    plan.unpriced = holdings.unpriced(prices)
    if plan.unpriced:
        plan.notes.append(
            f"No price for {', '.join(plan.unpriced)}; excluded from the valuation and "
            f"from the plan. Check the ticker, or whether it has been delisted."
        )

    value_before = sum(held_values.values())
    plan.portfolio_value_before = value_before
    value_after = value_before + contribution
    plan.portfolio_value_after = value_after

    if value_after <= 0:
        plan.notes.append("Nothing to allocate: no holdings and no contribution.")
        return plan

    current_weights = {t: v / value_before for t, v in held_values.items()} if value_before > 0 else {}

    # Orders too small to be worth placing are dropped, which leaves part of the
    # contribution unspent. Track them so the plan can say so out loud rather
    # than quietly reporting a buy total below what was contributed.
    dropped: list[tuple[str, float]] = []

    def add(ticker: str, action: str, amount: float, reason: str) -> None:
        price = prices.get(ticker)
        if price is None or price <= 0:
            return
        if abs(amount) < MIN_TRADE_AMOUNT and action in ("BUY", "SELL", "NEW", "EXIT"):
            if abs(amount) > 0.005:
                dropped.append((ticker, abs(amount)))
            return
        plan.trades.append(
            PlannedTrade(
                ticker=ticker,
                action=action,
                amount=abs(amount),
                shares=abs(amount) / price,
                price=price,
                current_weight=current_weights.get(ticker, 0.0),
                target_weight=float(target_weights.get(ticker, 0.0)),
                sleeve=sleeves.get(ticker, ""),
                sector=sectors.get(ticker, ""),
                score=float(scores.get(ticker, float("nan"))),
                reason=reason,
            )
        )

    if mode == "rebalance":
        # Trade the whole portfolio toward the target.
        for ticker, weight in target_weights.items():
            if ticker not in prices:
                plan.notes.append(f"{ticker} is in the target but has no price; skipped.")
                continue
            target_value = value_after * weight
            current_value = held_values.get(ticker, 0.0)
            delta = target_value - current_value
            if current_value <= 0:
                add(ticker, "NEW", delta, "entered the selection")
            elif delta > 0 and abs(delta) / max(target_value, 1.0) > REBALANCE_TOLERANCE:
                add(ticker, "BUY", delta, "below target weight")
            elif delta < 0 and abs(delta) / max(target_value, 1.0) > REBALANCE_TOLERANCE:
                add(ticker, "SELL", delta, "above target weight")
            else:
                add(ticker, "HOLD", 0.0, "within tolerance of target")

        for ticker, value in held_values.items():
            if ticker in target_weights:
                continue
            add(ticker, "EXIT", value, "no longer in the selection")
    else:
        # New money only. Steer toward the target without selling anything.
        shortfalls: dict[str, float] = {}
        for ticker, weight in target_weights.items():
            if ticker not in prices:
                plan.notes.append(f"{ticker} is in the target but has no price; skipped.")
                continue
            shortfall = value_after * weight - held_values.get(ticker, 0.0)
            if shortfall > 0:
                shortfalls[ticker] = shortfall

        total_shortfall = sum(shortfalls.values())
        if total_shortfall <= 0:
            # Already at or above target everywhere; spread by target weight.
            shortfalls = {t: w for t, w in target_weights.items() if t in prices and w > 0}
            total_shortfall = sum(shortfalls.values())

        for ticker, shortfall in shortfalls.items():
            amount = contribution * shortfall / total_shortfall if total_shortfall > 0 else 0.0
            action = "NEW" if held_values.get(ticker, 0.0) <= 0 else "BUY"
            add(ticker, action, amount, "contribution, weighted toward target")

        for ticker, value in held_values.items():
            if ticker in target_weights:
                continue
            plan.trades.append(
                PlannedTrade(
                    ticker=ticker, action="DRIFT", amount=value,
                    shares=holdings.shares(ticker), price=prices.get(ticker, float("nan")),
                    current_weight=current_weights.get(ticker, 0.0), target_weight=0.0,
                    sector=sectors.get(ticker, ""),
                    reason="held but no longer selected; not sold between rebalances",
                )
            )

    order = {"EXIT": 0, "SELL": 1, "NEW": 2, "BUY": 3, "HOLD": 4, "DRIFT": 5}
    plan.trades.sort(key=lambda t: (order.get(t.action, 9), -t.amount))

    if dropped:
        total_dropped = sum(amount for _, amount in dropped)
        plan.notes.append(
            f"{len(dropped)} target position(s) worth ${total_dropped:,.2f} in total came out "
            f"below the ${MIN_TRADE_AMOUNT:,.0f} minimum order and were dropped "
            f"({', '.join(t for t, _ in dropped[:6])}"
            f"{', and others' if len(dropped) > 6 else ''}). That much is left unspent and "
            f"those names are not held; carry it into next week, or contribute more per week "
            f"so every position in the basket clears the minimum."
        )

    if mode == "contribute" and plan.drift:
        drift_value = sum(t.amount for t in plan.drift)
        plan.notes.append(
            f"{len(plan.drift)} position(s) worth {drift_value:,.0f} are held but no longer "
            f"selected. Between rebalances the strategy does not sell them. They will be "
            f"proposed for exit at the next rebalance."
        )
    return plan


def apply_plan(holdings: Holdings, plan: TradePlan, when: dt.date | None = None) -> Holdings:
    """Record a plan's buys and sells as executed. Used after a real trade."""
    when = when or plan.as_of
    for trade in plan.trades:
        if trade.action in ("BUY", "NEW"):
            holdings.apply(trade.ticker, trade.shares, trade.amount, when)
        elif trade.action in ("SELL", "EXIT"):
            holdings.apply(trade.ticker, -trade.shares, trade.amount, when)
    if plan.mode == "rebalance":
        holdings.last_rebalance = when
    holdings.updated_at = when
    return holdings
