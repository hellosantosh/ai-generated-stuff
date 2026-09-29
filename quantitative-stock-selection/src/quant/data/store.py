"""Point-in-time market data access.

This module is the structural guard against look-ahead bias (REQUIREMENTS 22,
38, 66). The rule it enforces:

* Anything that *decides* (factors, scoring, ranking, selection) receives a
  ``PointInTimeView``. A view is pinned to one ``as_of`` date and physically
  cannot hand out a bar dated after it, or a fundamental record whose
  ``data_available_date`` is later than it.
* Anything that *executes* (the portfolio engine pricing a fill) uses
  ``MarketData`` directly with an explicit trade date. Trading after the
  decision is legitimate; the two roles are kept in separate objects so the
  distinction cannot be blurred by accident.

In strict mode a view raises ``LookAheadError`` when asked for a date beyond
its ``as_of`` instead of quietly clamping.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd

from ..errors import DataError, InsufficientDataError, LookAheadError
from ..logging_config import get_logger
from .types import FundamentalRecord, FundamentalSeries, PriceHistory

log = get_logger(__name__)


@dataclass(frozen=True)
class SectorAssignment:
    ticker: str
    sector: str
    effective_date: dt.date
    end_date: dt.date | None = None
    source: str = "unknown"

    def covers(self, date: dt.date) -> bool:
        if date < self.effective_date:
            return False
        return self.end_date is None or date <= self.end_date


class SectorMap:
    """Date-aware sector classification (REQUIREMENTS 8).

    ``point_in_time`` records the honest answer to "did we know this stock's
    sector on that date?". When only today's classification is known, the map
    is built with ``point_in_time=False`` and every report says so rather than
    pretending the classification was always current.
    """

    def __init__(self, point_in_time: bool = False) -> None:
        self._by_ticker: dict[str, list[SectorAssignment]] = {}
        self.point_in_time = point_in_time

    def add(self, assignment: SectorAssignment) -> None:
        self._by_ticker.setdefault(assignment.ticker.upper(), []).append(assignment)
        self._by_ticker[assignment.ticker.upper()].sort(key=lambda a: a.effective_date)

    def add_current(self, ticker: str, sector: str, source: str = "provider") -> None:
        """Record a classification with no known start date.

        ``dt.date.min`` is used so the assignment covers all history. This is
        the survivorship-style shortcut the requirements warn about, so the
        map's ``point_in_time`` flag stays False.
        """
        self.add(SectorAssignment(ticker.upper(), sector, dt.date.min, None, source))

    def sector_of(self, ticker: str, as_of: dt.date) -> str | None:
        for assignment in reversed(self._by_ticker.get(ticker.upper(), [])):
            if assignment.covers(as_of):
                return assignment.sector
        return None

    def tickers(self) -> list[str]:
        return sorted(self._by_ticker)

    def __len__(self) -> int:
        return len(self._by_ticker)


@dataclass
class UniverseMembership:
    """Historical index membership (REQUIREMENTS 7).

    ``survivorship_free`` is True only when the source genuinely supplied
    historical add/drop dates. Otherwise every backtest report must state that
    results are survivorship biased.

    ``verified_through`` is the last date for which removals are known. Beyond
    it the reconstruction may still carry a stock the index has since dropped,
    so the reports quote the date rather than implying the whole window is
    equally trustworthy. ``bias_note`` carries the human-readable caveat.
    """

    name: str
    intervals: dict[str, list[tuple[dt.date, dt.date | None]]] = field(default_factory=dict)
    survivorship_free: bool = False
    source: str = "unknown"
    verified_through: dt.date | None = None
    bias_note: str = ""
    # Short reason the survivorship-free claim does not hold. Set by whichever
    # check withdrew it, so the headline never guesses at the cause.
    bias_summary: str = ""

    def status_line(self) -> str:
        """One-line provenance statement for report headers."""
        if not self.survivorship_free:
            reason = self.bias_summary or (
                f"membership from {self.source} is a fixed snapshot; stocks dropped "
                f"from the index are absent"
            )
            return f"SURVIVORSHIP BIASED - {reason}."
        through = self.verified_through.isoformat() if self.verified_through else "unknown"
        return (
            f"Point-in-time membership from {self.source}; additions and removals "
            f"verified through {through}."
        )

    def add(self, ticker: str, start: dt.date, end: dt.date | None = None) -> None:
        self.intervals.setdefault(ticker.upper(), []).append((start, end))

    def members_on(self, date: dt.date) -> list[str]:
        members = []
        for ticker, spans in self.intervals.items():
            for start, end in spans:
                if start <= date and (end is None or date <= end):
                    members.append(ticker)
                    break
        return sorted(members)

    def was_member(self, ticker: str, date: dt.date) -> bool:
        for start, end in self.intervals.get(ticker.upper(), []):
            if start <= date and (end is None or date <= end):
                return True
        return False

    def all_tickers(self) -> list[str]:
        return sorted(self.intervals)

    def __len__(self) -> int:
        return len(self.intervals)


class MarketData:
    """Everything the engine knows, with explicit date handling.

    Construct once per run, then derive a ``PointInTimeView`` per decision date.
    """

    def __init__(
        self,
        prices: Mapping[str, PriceHistory],
        fundamentals: Mapping[str, FundamentalSeries] | None = None,
        sectors: SectorMap | None = None,
        universe: UniverseMembership | None = None,
        benchmark: str = "IVV",
        company_names: Mapping[str, str] | None = None,
        strict: bool = True,
    ) -> None:
        self.prices: dict[str, PriceHistory] = {k.upper(): v for k, v in prices.items()}
        self.fundamentals: dict[str, FundamentalSeries] = {
            k.upper(): v for k, v in (fundamentals or {}).items()
        }
        self.sectors = sectors or SectorMap()
        self.universe = universe or UniverseMembership(name="unknown")
        self.benchmark = benchmark.upper()
        self.company_names = {k.upper(): v for k, v in (company_names or {}).items()}
        self.strict = strict
        self._calendar: pd.DatetimeIndex | None = None

    # --- calendar --------------------------------------------------------
    @property
    def calendar(self) -> pd.DatetimeIndex:
        """Trading calendar, taken from the benchmark when available.

        The benchmark is the one series guaranteed to exist for the whole
        backtest window, which makes it the natural calendar. Without it we
        fall back to the union of every loaded series.
        """
        if self._calendar is None:
            if self.benchmark in self.prices:
                self._calendar = self.prices[self.benchmark].index
            elif self.prices:
                union = pd.DatetimeIndex([])
                for history in self.prices.values():
                    union = union.union(history.index)
                self._calendar = union.sort_values()
            else:
                raise DataError("cannot build a trading calendar: no price series loaded")
        return self._calendar

    def is_trading_day(self, date: dt.date) -> bool:
        return pd.Timestamp(date) in self.calendar

    def next_trading_day(self, after: dt.date, inclusive: bool = False) -> dt.date:
        ts = pd.Timestamp(after)
        calendar = self.calendar
        position = calendar.searchsorted(ts, side="left" if inclusive else "right")
        if position >= len(calendar):
            raise InsufficientDataError(
                f"no trading day on the calendar after {after} "
                f"(calendar ends {calendar[-1].date()})"
            )
        return calendar[position].date()

    def previous_trading_day(self, before: dt.date, inclusive: bool = False) -> dt.date:
        ts = pd.Timestamp(before)
        calendar = self.calendar
        position = calendar.searchsorted(ts, side="right" if inclusive else "left") - 1
        if position < 0:
            raise InsufficientDataError(
                f"no trading day on the calendar before {before} "
                f"(calendar starts {calendar[0].date()})"
            )
        return calendar[position].date()

    def trading_days_between(self, start: dt.date, end: dt.date) -> pd.DatetimeIndex:
        return self.calendar[
            (self.calendar >= pd.Timestamp(start)) & (self.calendar <= pd.Timestamp(end))
        ]

    # --- price access (execution side) -----------------------------------
    def has(self, ticker: str) -> bool:
        return ticker.upper() in self.prices

    def history(self, ticker: str) -> PriceHistory:
        key = ticker.upper()
        if key not in self.prices:
            raise DataError(f"no price history loaded for {key}")
        return self.prices[key]

    def execution_price(
        self, ticker: str, trade_date: dt.date, field: str = "adj_open"
    ) -> float:
        """Price for a fill on ``trade_date``.

        Deliberately exact: if the ticker did not trade that day the caller
        must handle it, because silently using a nearby day's price would
        misstate the fill.
        """
        return self.history(ticker).price(trade_date, field)

    def mark_price(
        self, ticker: str, date: dt.date, field: str = "adj_close", max_stale_days: int = 10
    ) -> float:
        """Valuation price, allowing a bounded carry-forward for holidays.

        Carrying a quote further than ``max_stale_days`` is treated as an
        error: REQUIREMENTS 31 forbids silently valuing on stale data.
        """
        history = self.history(ticker)
        price_date, price = history.last_price_on_or_before(date, field)
        staleness = (date - price_date).days
        if staleness > max_stale_days:
            raise DataError(
                f"{ticker}: most recent price on or before {date} is from {price_date} "
                f"({staleness} days stale, limit {max_stale_days})"
            )
        return price

    def sector_of(self, ticker: str, as_of: dt.date) -> str | None:
        return self.sectors.sector_of(ticker, as_of)

    def company_name(self, ticker: str) -> str:
        return self.company_names.get(ticker.upper(), ticker.upper())

    # --- view factory ----------------------------------------------------
    def view(self, as_of: dt.date, strict: bool | None = None) -> "PointInTimeView":
        return PointInTimeView(self, as_of, self.strict if strict is None else strict)

    def loaded_tickers(self) -> list[str]:
        return sorted(self.prices)

    def provenance(self) -> dict[str, dict[str, object]]:
        """Per-series provider and retrieval time, saved with each run."""
        return {ticker: h.provider.to_dict() for ticker, h in sorted(self.prices.items())}


class PointInTimeView:
    """A read-only window on ``MarketData`` frozen at one decision date."""

    __slots__ = ("_market", "as_of", "strict", "_cache")

    def __init__(self, market: MarketData, as_of: dt.date, strict: bool = True) -> None:
        self._market = market
        self.as_of = as_of
        self.strict = strict
        self._cache: dict[str, pd.DataFrame] = {}

    def __repr__(self) -> str:
        return f"PointInTimeView(as_of={self.as_of}, strict={self.strict})"

    # --- guards ----------------------------------------------------------
    def _guard(self, date: dt.date, what: str) -> None:
        if date > self.as_of:
            message = (
                f"look-ahead: {what} for {date} was requested from a view pinned to "
                f"{self.as_of}"
            )
            if self.strict:
                raise LookAheadError(message)
            log.warning(message)

    # --- prices ----------------------------------------------------------
    def has(self, ticker: str) -> bool:
        return self._market.has(ticker)

    def history(self, ticker: str) -> pd.DataFrame:
        """All bars for ``ticker`` dated on or before ``as_of``."""
        key = ticker.upper()
        cached = self._cache.get(key)
        if cached is None:
            cached = self._market.history(key).up_to(self.as_of)
            self._cache[key] = cached
        return cached

    def close_series(self, ticker: str) -> pd.Series:
        return self.history(ticker)["adj_close"]

    def total_return_series(self, ticker: str) -> pd.Series:
        return self.history(ticker)["total_return_index"]

    def bars_available(self, ticker: str) -> int:
        try:
            return len(self.history(ticker))
        except DataError:
            return 0

    def latest_price(self, ticker: str, field: str = "adj_close") -> tuple[dt.date, float]:
        frame = self.history(ticker)
        if frame.empty:
            raise InsufficientDataError(
                f"{ticker}: no price history on or before {self.as_of}"
            )
        return frame.index[-1].date(), float(frame.iloc[-1][field])

    def price_on(self, ticker: str, date: dt.date, field: str = "adj_close") -> float:
        self._guard(date, f"price of {ticker}")
        return self._market.history(ticker).price(date, field)

    # --- fundamentals ----------------------------------------------------
    def fundamentals(self, ticker: str) -> list[FundamentalRecord]:
        """Records whose ``data_available_date`` is on or before ``as_of``."""
        series = self._market.fundamentals.get(ticker.upper())
        if series is None:
            return []
        return series.available_as_of(self.as_of)

    def latest_fundamental(self, ticker: str) -> FundamentalRecord | None:
        records = self.fundamentals(ticker)
        return records[-1] if records else None

    def has_fundamentals(self, ticker: str) -> bool:
        return bool(self.fundamentals(ticker))

    # --- classification and universe -------------------------------------
    def sector_of(self, ticker: str) -> str | None:
        return self._market.sector_of(ticker, self.as_of)

    def company_name(self, ticker: str) -> str:
        return self._market.company_name(ticker)

    def universe(self) -> list[str]:
        """Index members on ``as_of``, restricted to tickers we have prices for."""
        members = self._market.universe.members_on(self.as_of)
        if not members:
            members = self._market.loaded_tickers()
        return [t for t in members if self._market.has(t)]

    @property
    def benchmark(self) -> str:
        return self._market.benchmark

    @property
    def market(self) -> MarketData:
        """Escape hatch for execution code. Never used by selection logic."""
        return self._market


def assert_no_future_rows(frame: pd.DataFrame, as_of: dt.date, label: str) -> None:
    """Belt-and-braces check used by the look-ahead test suite."""
    if frame.empty:
        return
    index = pd.DatetimeIndex(frame.index if frame.index.name == "date" else frame["date"])
    future = index[index > pd.Timestamp(as_of)]
    if len(future):
        raise LookAheadError(
            f"{label}: {len(future)} row(s) dated after {as_of}, first is {future[0].date()}"
        )
