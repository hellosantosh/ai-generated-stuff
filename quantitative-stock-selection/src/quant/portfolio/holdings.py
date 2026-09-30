"""Live position tracking.

The backtest owns a simulated portfolio. This module owns the *real* one: what
the user actually holds, so the weekly report can say what to sell rather than
only what to buy.

Deliberately minimal. It records share counts, cost basis and the date of the
last rebalance in a single JSON file the user can read and edit. It is not an
accounting system: it does not reconcile with a broker, model corporate
actions, or track tax lots. It exists so the weekly report can diff a target
portfolio against a real one.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from ..errors import DataError
from ..logging_config import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 1


@dataclass
class Lot:
    """One position. ``shares`` may be fractional."""

    ticker: str
    shares: float
    cost_basis: float = 0.0

    def market_value(self, price: float) -> float:
        return self.shares * price

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "shares": round(self.shares, 8),
            "cost_basis": round(self.cost_basis, 2),
        }


@dataclass
class Holdings:
    """The user's real portfolio."""

    positions: dict[str, Lot] = field(default_factory=dict)
    cash: float = 0.0
    updated_at: dt.date | None = None
    last_rebalance: dt.date | None = None
    note: str = ""

    # --- access ----------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self.positions)

    def tickers(self) -> list[str]:
        return sorted(t for t, lot in self.positions.items() if lot.shares > 0)

    def shares(self, ticker: str) -> float:
        lot = self.positions.get(ticker.upper())
        return lot.shares if lot else 0.0

    def market_values(self, prices: Mapping[str, float]) -> dict[str, float]:
        """Value per ticker. A ticker with no price is omitted, not zeroed."""
        values: dict[str, float] = {}
        for ticker, lot in self.positions.items():
            if lot.shares <= 0:
                continue
            price = prices.get(ticker)
            if price is None:
                log.warning("no price for held ticker %s; excluded from valuation", ticker)
                continue
            values[ticker] = lot.shares * price
        return values

    def total_value(self, prices: Mapping[str, float]) -> float:
        return sum(self.market_values(prices).values()) + self.cash

    def current_weights(self, prices: Mapping[str, float]) -> dict[str, float]:
        values = self.market_values(prices)
        total = sum(values.values())
        if total <= 0:
            return {}
        return {ticker: value / total for ticker, value in values.items()}

    def unpriced(self, prices: Mapping[str, float]) -> list[str]:
        return sorted(t for t, lot in self.positions.items() if lot.shares > 0 and t not in prices)

    # --- mutation --------------------------------------------------------
    def apply(self, ticker: str, shares_delta: float, amount: float, when: dt.date) -> None:
        """Record a fill. Positive ``shares_delta`` is a buy."""
        ticker = ticker.upper()
        lot = self.positions.setdefault(ticker, Lot(ticker, 0.0, 0.0))
        if shares_delta >= 0:
            lot.shares += shares_delta
            lot.cost_basis += abs(amount)
        else:
            sold = min(-shares_delta, lot.shares)
            if lot.shares > 0:
                # Average-cost basis reduction; tax lots are out of scope.
                lot.cost_basis -= lot.cost_basis * (sold / lot.shares)
            lot.shares -= sold
        if lot.shares <= 1e-9:
            self.positions.pop(ticker, None)
        self.updated_at = when

    def set_position(self, ticker: str, shares: float, cost_basis: float | None = None) -> None:
        ticker = ticker.upper()
        if shares <= 0:
            self.positions.pop(ticker, None)
            return
        existing = self.positions.get(ticker)
        self.positions[ticker] = Lot(
            ticker,
            float(shares),
            float(cost_basis if cost_basis is not None else (existing.cost_basis if existing else 0.0)),
        )

    # --- persistence -----------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_rebalance": self.last_rebalance.isoformat() if self.last_rebalance else None,
            "cash": round(self.cash, 2),
            "note": self.note,
            "positions": [lot.to_dict() for lot in sorted(self.positions.values(), key=lambda l: l.ticker)],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        log.info("wrote holdings for %d position(s) to %s", len(self.positions), path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Holdings":
        """Read the holdings file, returning an empty portfolio if absent."""
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DataError(f"{path} is not valid JSON: {exc}") from exc

        version = int(payload.get("schema_version", SCHEMA_VERSION))
        if version > SCHEMA_VERSION:
            raise DataError(
                f"{path} was written by a newer version (schema {version}, this build "
                f"understands {SCHEMA_VERSION})"
            )

        holdings = cls(
            cash=float(payload.get("cash", 0.0) or 0.0),
            note=str(payload.get("note", "")),
        )
        for key, attribute in (("updated_at", "updated_at"), ("last_rebalance", "last_rebalance")):
            raw = payload.get(key)
            if raw:
                try:
                    setattr(holdings, attribute, dt.date.fromisoformat(str(raw)))
                except ValueError as exc:
                    raise DataError(f"{path}: {key} {raw!r} is not an ISO date") from exc

        for entry in payload.get("positions", []):
            try:
                ticker = str(entry["ticker"]).upper()
                shares = float(entry["shares"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DataError(f"{path}: malformed position entry {entry!r}") from exc
            if shares < 0:
                raise DataError(f"{path}: {ticker} has negative shares ({shares})")
            if shares > 0:
                holdings.positions[ticker] = Lot(
                    ticker, shares, float(entry.get("cost_basis", 0.0) or 0.0)
                )
        return holdings

    @classmethod
    def from_csv(cls, path: str | Path) -> "Holdings":
        """Import a broker export with ticker/shares[/cost_basis] columns."""
        import csv

        path = Path(path)
        if not path.exists():
            raise DataError(f"holdings CSV not found: {path}")
        holdings = cls(updated_at=dt.date.today())
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise DataError(f"{path} has no header row")
            lowered = {name.strip().lower(): name for name in reader.fieldnames}
            ticker_col = next((lowered[k] for k in ("ticker", "symbol") if k in lowered), None)
            shares_col = next(
                (lowered[k] for k in ("shares", "quantity", "qty") if k in lowered), None
            )
            basis_col = next(
                (lowered[k] for k in ("cost_basis", "cost", "basis") if k in lowered), None
            )
            if ticker_col is None or shares_col is None:
                raise DataError(
                    f"{path}: expected a ticker/symbol column and a shares/quantity column; "
                    f"found {reader.fieldnames}"
                )
            for row in reader:
                ticker = str(row[ticker_col] or "").strip().upper()
                if not ticker:
                    continue
                try:
                    shares = float(str(row[shares_col]).replace(",", "").strip() or 0)
                except ValueError:
                    log.warning("skipping %s: unparseable share count %r", ticker, row[shares_col])
                    continue
                basis = 0.0
                if basis_col:
                    try:
                        basis = float(str(row[basis_col]).replace("$", "").replace(",", "").strip() or 0)
                    except ValueError:
                        basis = 0.0
                if shares > 0:
                    holdings.set_position(ticker, shares, basis)
        log.info("imported %d position(s) from %s", len(holdings.positions), path)
        return holdings


def default_path(project_root: Path) -> Path:
    return project_root / "data" / "holdings.json"
