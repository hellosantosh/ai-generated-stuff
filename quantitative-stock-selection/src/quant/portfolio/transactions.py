"""Transaction records and the ledger (REQUIREMENTS 20, 52)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Literal

import pandas as pd

Action = Literal["BUY", "SELL", "DIVIDEND", "CONTRIBUTION"]


@dataclass(frozen=True)
class Transaction:
    """One cash-moving event.

    ``price`` is the price actually paid, slippage included. ``gross_amount``
    is shares x price; ``net_amount`` is the signed effect on cash, so a buy is
    negative and a sale or dividend is positive.
    """

    sequence: int
    decision_date: dt.date
    trade_date: dt.date
    sleeve: str
    ticker: str
    action: Action
    shares: float
    price: float
    gross_amount: float
    commission: float
    slippage: float
    net_amount: float
    reason: str = ""

    def to_row(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "decision_date": self.decision_date,
            "trade_date": self.trade_date,
            "sleeve": self.sleeve,
            "ticker": self.ticker,
            "action": self.action,
            "shares": self.shares,
            "price": self.price,
            "gross_amount": self.gross_amount,
            "commission": self.commission,
            "slippage": self.slippage,
            "net_amount": self.net_amount,
            "reason": self.reason,
        }


@dataclass
class TransactionLedger:
    transactions: list[Transaction] = field(default_factory=list)

    def append(self, transaction: Transaction) -> None:
        self.transactions.append(transaction)

    def next_sequence(self) -> int:
        return len(self.transactions) + 1

    def __len__(self) -> int:
        return len(self.transactions)

    def __iter__(self) -> Iterable[Transaction]:
        return iter(self.transactions)

    def trades(self) -> list[Transaction]:
        """Buys and sells only, excluding dividends and contributions."""
        return [t for t in self.transactions if t.action in ("BUY", "SELL")]

    def total_costs(self) -> tuple[float, float]:
        commission = sum(t.commission for t in self.transactions)
        slippage = sum(t.slippage for t in self.transactions)
        return commission, slippage

    def to_frame(self) -> pd.DataFrame:
        if not self.transactions:
            return pd.DataFrame(
                columns=[
                    "sequence", "decision_date", "trade_date", "sleeve", "ticker",
                    "action", "shares", "price", "gross_amount", "commission",
                    "slippage", "net_amount", "reason",
                ]
            )
        return pd.DataFrame([t.to_row() for t in self.transactions])
