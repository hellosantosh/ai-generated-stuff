"""Portfolio accounting with fractional shares and dividend reinvestment.

Structure: a portfolio holds one or more named **sleeves** (``ivv``,
``sector_leaders``, ``high_growth``), each with its own cash balance and
positions. Keeping sleeve cash separate means the weekly allocation split is
enforced by construction, and per-sleeve attribution falls out for free.

Share accounting is done in split-adjusted terms (see ``data/types.py``), so a
stock split changes no share count here. Dividends are credited as cash on the
ex-date and redeployed at the next contribution, which is the reinvestment
convention documented in the reports.

Transaction costs follow REQUIREMENTS 52: slippage moves the fill price
against the trade, and commission is charged per executed order.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from ..config import TransactionCostConfig
from ..data.store import MarketData
from ..errors import DataError
from ..logging_config import get_logger
from .transactions import Transaction, TransactionLedger

log = get_logger(__name__)

# Positions below this value are closed out rather than left as dust that
# would otherwise generate perpetual sub-cent rebalancing trades.
DUST_THRESHOLD = 0.01


@dataclass
class Position:
    ticker: str
    shares: float = 0.0
    cost_basis: float = 0.0

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized(self, price: float) -> float:
        return self.market_value(price) - self.cost_basis


@dataclass
class Sleeve:
    name: str
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    contributions: float = 0.0
    realized_gain: float = 0.0
    dividends_received: float = 0.0

    def position(self, ticker: str) -> Position:
        return self.positions.setdefault(ticker, Position(ticker))

    def holdings(self) -> list[str]:
        return sorted(t for t, p in self.positions.items() if p.shares > 0)

    def holdings_value(self, prices: Mapping[str, float]) -> float:
        return sum(p.shares * prices[t] for t, p in self.positions.items() if p.shares > 0 and t in prices)

    def total_value(self, prices: Mapping[str, float]) -> float:
        return self.cash + self.holdings_value(prices)


class Portfolio:
    """A multi-sleeve portfolio with an audit trail of every transaction."""

    def __init__(
        self,
        name: str,
        sleeves: Iterable[str],
        costs: TransactionCostConfig,
        market: MarketData,
        max_stale_days: int = 10,
    ) -> None:
        self.name = name
        self.sleeves: dict[str, Sleeve] = {s: Sleeve(s) for s in sleeves}
        self.costs = costs
        self.market = market
        self.max_stale_days = max_stale_days
        self.ledger = TransactionLedger()
        self.value_history: list[dict[str, object]] = []
        self.position_history: list[dict[str, object]] = []
        self._last_dividend_date: dt.date | None = None

    # --- cash ------------------------------------------------------------
    def contribute(self, sleeve_name: str, amount: float, date: dt.date) -> None:
        sleeve = self.sleeves[sleeve_name]
        sleeve.cash += amount
        sleeve.contributions += amount

    @property
    def total_contributions(self) -> float:
        return sum(s.contributions for s in self.sleeves.values())

    # --- trading ---------------------------------------------------------
    def buy(
        self,
        sleeve_name: str,
        ticker: str,
        amount: float,
        trade_date: dt.date,
        decision_date: dt.date,
        price_field: str,
        reason: str = "",
    ) -> Transaction | None:
        """Spend ``amount`` of sleeve cash on ``ticker``.

        ``amount`` is the total cash committed: commission comes out of it
        first, and the remainder buys shares at the slipped price. This keeps
        the sleeve's cash from going negative.
        """
        sleeve = self.sleeves[sleeve_name]
        if amount <= 0:
            return None
        amount = min(amount, sleeve.cash)
        if amount <= DUST_THRESHOLD:
            return None

        commission = min(self.costs.commission_per_trade, amount)
        investable = amount - commission
        if investable <= 0:
            return None

        base_price = self.market.execution_price(ticker, trade_date, price_field)
        fill_price = base_price * (1.0 + self.costs.slippage_fraction)
        shares = investable / fill_price
        slippage_cost = shares * (fill_price - base_price)

        position = sleeve.position(ticker)
        position.shares += shares
        position.cost_basis += investable
        sleeve.cash -= amount

        transaction = Transaction(
            sequence=self.ledger.next_sequence(),
            decision_date=decision_date,
            trade_date=trade_date,
            sleeve=sleeve_name,
            ticker=ticker,
            action="BUY",
            shares=shares,
            price=fill_price,
            gross_amount=investable,
            commission=commission,
            slippage=slippage_cost,
            net_amount=-amount,
            reason=reason,
        )
        self.ledger.append(transaction)
        return transaction

    def sell(
        self,
        sleeve_name: str,
        ticker: str,
        shares: float,
        trade_date: dt.date,
        decision_date: dt.date,
        price_field: str,
        reason: str = "",
    ) -> Transaction | None:
        sleeve = self.sleeves[sleeve_name]
        position = sleeve.positions.get(ticker)
        if position is None or position.shares <= 0 or shares <= 0:
            return None
        shares = min(shares, position.shares)

        base_price = self.market.execution_price(ticker, trade_date, price_field)
        fill_price = base_price * (1.0 - self.costs.slippage_fraction)
        gross = shares * fill_price
        commission = min(self.costs.commission_per_trade, gross)
        proceeds = gross - commission
        slippage_cost = shares * (base_price - fill_price)

        basis_sold = position.cost_basis * (shares / position.shares) if position.shares else 0.0
        position.shares -= shares
        position.cost_basis -= basis_sold
        sleeve.realized_gain += proceeds - basis_sold
        sleeve.cash += proceeds
        if position.shares <= 1e-9:
            sleeve.positions.pop(ticker, None)

        transaction = Transaction(
            sequence=self.ledger.next_sequence(),
            decision_date=decision_date,
            trade_date=trade_date,
            sleeve=sleeve_name,
            ticker=ticker,
            action="SELL",
            shares=shares,
            price=fill_price,
            gross_amount=gross,
            commission=commission,
            slippage=slippage_cost,
            net_amount=proceeds,
            reason=reason,
        )
        self.ledger.append(transaction)
        return transaction

    def sell_all(
        self,
        sleeve_name: str,
        ticker: str,
        trade_date: dt.date,
        decision_date: dt.date,
        price_field: str,
        reason: str = "",
    ) -> Transaction | None:
        position = self.sleeves[sleeve_name].positions.get(ticker)
        if position is None:
            return None
        return self.sell(
            sleeve_name, ticker, position.shares, trade_date, decision_date, price_field, reason
        )

    # --- dividends -------------------------------------------------------
    def credit_dividends(self, through_date: dt.date, decision_date: dt.date) -> float:
        """Credit cash dividends with ex-dates since the last call.

        Returns the total credited. In total-return mode this cash is
        redeployed by the next contribution, which is the reinvestment
        convention stated in the reports.
        """
        if self._last_dividend_date is None:
            self._last_dividend_date = through_date
            return 0.0
        if through_date <= self._last_dividend_date:
            return 0.0

        total = 0.0
        for sleeve in self.sleeves.values():
            for ticker, position in list(sleeve.positions.items()):
                if position.shares <= 0 or not self.market.has(ticker):
                    continue
                dividends = self.market.history(ticker).dividends_between(
                    self._last_dividend_date, through_date
                )
                if dividends.empty:
                    continue
                amount = float(dividends.sum()) * position.shares
                if amount <= 0:
                    continue
                sleeve.cash += amount
                sleeve.dividends_received += amount
                total += amount
                self.ledger.append(
                    Transaction(
                        sequence=self.ledger.next_sequence(),
                        decision_date=decision_date,
                        trade_date=through_date,
                        sleeve=sleeve.name,
                        ticker=ticker,
                        action="DIVIDEND",
                        shares=position.shares,
                        price=float(dividends.sum()),
                        gross_amount=amount,
                        commission=0.0,
                        slippage=0.0,
                        net_amount=amount,
                        reason="cash dividend",
                    )
                )
        self._last_dividend_date = through_date
        return total

    # --- valuation -------------------------------------------------------
    def prices_for_holdings(self, date: dt.date) -> dict[str, float]:
        prices: dict[str, float] = {}
        for sleeve in self.sleeves.values():
            for ticker, position in sleeve.positions.items():
                if position.shares > 0 and ticker not in prices:
                    prices[ticker] = self.market.mark_price(
                        ticker, date, "adj_close", self.max_stale_days
                    )
        return prices

    def value(self, date: dt.date) -> float:
        prices = self.prices_for_holdings(date)
        return sum(s.total_value(prices) for s in self.sleeves.values())

    def sleeve_values(self, date: dt.date) -> dict[str, float]:
        prices = self.prices_for_holdings(date)
        return {name: sleeve.total_value(prices) for name, sleeve in self.sleeves.items()}

    def record_value(
        self, date: dt.date, contribution: float = 0.0, twr_index: float | None = None
    ) -> dict[str, object]:
        prices = self.prices_for_holdings(date)
        cash = sum(s.cash for s in self.sleeves.values())
        holdings_value = sum(s.holdings_value(prices) for s in self.sleeves.values())
        row = {
            "date": date,
            "contribution": contribution,
            "cash": cash,
            "holdings_value": holdings_value,
            "total_value": cash + holdings_value,
            "cumulative_contributions": self.total_contributions,
            "twr_index": twr_index,
        }
        for name, sleeve in self.sleeves.items():
            row[f"value_{name}"] = sleeve.total_value(prices)
        self.value_history.append(row)
        return row

    def record_positions(self, date: dt.date) -> None:
        prices = self.prices_for_holdings(date)
        for sleeve in self.sleeves.values():
            for ticker, position in sleeve.positions.items():
                if position.shares <= 0:
                    continue
                price = prices.get(ticker)
                if price is None:
                    continue
                self.position_history.append(
                    {
                        "date": date,
                        "sleeve": sleeve.name,
                        "ticker": ticker,
                        "shares": position.shares,
                        "price": price,
                        "value": position.shares * price,
                        "cost_basis": position.cost_basis,
                    }
                )

    # --- frames ----------------------------------------------------------
    def values_frame(self) -> pd.DataFrame:
        if not self.value_history:
            return pd.DataFrame()
        frame = pd.DataFrame(self.value_history)
        return frame.set_index(pd.DatetimeIndex(pd.to_datetime(frame["date"])))

    def positions_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.position_history)

    def transactions_frame(self) -> pd.DataFrame:
        return self.ledger.to_frame()

    def current_weights(self, date: dt.date) -> dict[str, float]:
        prices = self.prices_for_holdings(date)
        total = sum(s.total_value(prices) for s in self.sleeves.values())
        if total <= 0:
            return {}
        weights: dict[str, float] = {}
        for sleeve in self.sleeves.values():
            for ticker, position in sleeve.positions.items():
                if position.shares > 0 and ticker in prices:
                    weights[ticker] = weights.get(ticker, 0.0) + position.shares * prices[ticker] / total
        return weights

    def summary(self, date: dt.date) -> dict[str, float]:
        value = self.value(date)
        contributions = self.total_contributions
        commission, slippage = self.ledger.total_costs()
        return {
            "total_value": value,
            "total_contributions": contributions,
            "total_gain": value - contributions,
            "return_on_contributions": (value / contributions - 1.0) if contributions > 0 else float("nan"),
            "dividends_received": sum(s.dividends_received for s in self.sleeves.values()),
            "commission_paid": commission,
            "slippage_paid": slippage,
            "num_trades": len(self.ledger.trades()),
            "num_holdings": len({t for s in self.sleeves.values() for t in s.holdings()}),
        }
