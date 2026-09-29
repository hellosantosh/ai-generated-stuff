"""Core data containers.

``PriceHistory`` is the single representation of a price series used by the
whole application. It is built once per ticker and is treated as immutable.

Split handling: the raw close is the price actually printed on that day. A
2-for-1 split on day S carries ``split_coef = 2`` on S, and every price before
S sits on the pre-split scale. We divide pre-split prices (and pre-split
dividends per share) by the cumulative coefficient of all later splits, which
makes the series continuous and lets the portfolio engine hold a constant
share count across splits. Dividends stay separate from prices so the backtest
can credit them as cash and reinvest them explicitly, rather than burying them
in an adjusted-close series.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from ..errors import DataError, InsufficientDataError

PRICE_COLUMNS = ["open", "high", "low", "close", "volume", "dividend", "split_coef"]
DERIVED_COLUMNS = ["adj_open", "adj_close", "adj_dividend", "total_return_index"]


@dataclass(frozen=True)
class ProviderInfo:
    """Where a series came from, recorded for reproducibility (REQUIREMENTS 39)."""

    name: str
    retrieved_at: dt.datetime
    endpoint: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "retrieved_at": self.retrieved_at.isoformat(timespec="seconds"),
            "endpoint": self.endpoint,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class PriceHistory:
    """Daily bars for one ticker, sorted ascending with a unique date index."""

    ticker: str
    frame: pd.DataFrame
    provider: ProviderInfo

    @classmethod
    def from_frame(
        cls, ticker: str, frame: pd.DataFrame, provider: ProviderInfo
    ) -> "PriceHistory":
        df = frame.copy()
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
        df.index.name = "date"

        missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
        if missing:
            raise DataError(f"{ticker}: price frame is missing columns {missing}")
        if "volume" not in df.columns:
            df["volume"] = np.nan
        if "dividend" not in df.columns:
            df["dividend"] = 0.0
        if "split_coef" not in df.columns:
            df["split_coef"] = 1.0

        df["dividend"] = df["dividend"].fillna(0.0).astype(float)
        # A split coefficient of 0 is corrupt data, not "no split".
        split = df["split_coef"].astype(float)
        if (split <= 0).any():
            bad = df.index[split <= 0][0].date()
            raise DataError(f"{ticker}: non-positive split coefficient on {bad}")
        df["split_coef"] = split.fillna(1.0)

        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df.dropna(subset=["close"])
        if df.empty:
            raise InsufficientDataError(f"{ticker}: price history is empty after cleaning")
        if (df["close"] <= 0).any():
            bad = df.index[df["close"] <= 0][0].date()
            raise DataError(f"{ticker}: non-positive close price on {bad}")

        # Open is used for next-day execution; fall back to close when the
        # provider omits it rather than dropping the bar.
        df["open"] = df["open"].astype(float).fillna(df["close"].astype(float))
        for col in ("high", "low"):
            df[col] = df[col].astype(float).fillna(df["close"].astype(float))
        df["close"] = df["close"].astype(float)

        df = _apply_split_adjustment(df)
        return cls(ticker=ticker, frame=df, provider=provider)

    # --- access ---------------------------------------------------------
    @property
    def index(self) -> pd.DatetimeIndex:
        return self.frame.index  # type: ignore[return-value]

    @property
    def first_date(self) -> dt.date:
        return self.frame.index[0].date()

    @property
    def last_date(self) -> dt.date:
        return self.frame.index[-1].date()

    def __len__(self) -> int:
        return len(self.frame)

    def up_to(self, as_of: dt.date) -> pd.DataFrame:
        """Bars dated on or before ``as_of``. The point-in-time primitive."""
        return self.frame.loc[: pd.Timestamp(as_of)]

    def between(self, start: dt.date, end: dt.date) -> pd.DataFrame:
        return self.frame.loc[pd.Timestamp(start) : pd.Timestamp(end)]

    def has_bar(self, date: dt.date) -> bool:
        return pd.Timestamp(date) in self.frame.index

    def bar(self, date: dt.date) -> pd.Series:
        ts = pd.Timestamp(date)
        if ts not in self.frame.index:
            raise DataError(f"{self.ticker}: no price bar on {date}")
        return self.frame.loc[ts]

    def price(self, date: dt.date, field: str = "adj_close") -> float:
        value = float(self.bar(date)[field])
        if not np.isfinite(value) or value <= 0:
            raise DataError(f"{self.ticker}: unusable {field} on {date}: {value!r}")
        return value

    def last_price_on_or_before(self, date: dt.date, field: str = "adj_close") -> tuple[dt.date, float]:
        """Most recent usable price at or before ``date``, with its own date.

        The returned date lets callers decide whether the quote is too stale,
        instead of silently carrying a price forward.
        """
        window = self.up_to(date)
        if window.empty:
            raise InsufficientDataError(
                f"{self.ticker}: no price history on or before {date}"
            )
        row = window.iloc[-1]
        return window.index[-1].date(), float(row[field])

    def dividends_between(self, start_exclusive: dt.date, end_inclusive: dt.date) -> pd.Series:
        """Split-adjusted dividends per share with ex-date in (start, end]."""
        window = self.frame.loc[
            pd.Timestamp(start_exclusive) + pd.Timedelta(days=1) : pd.Timestamp(end_inclusive)
        ]
        divs = window["adj_dividend"]
        return divs[divs > 0]


def _apply_split_adjustment(df: pd.DataFrame) -> pd.DataFrame:
    """Add split-adjusted price/dividend columns and a total-return index."""
    split = df["split_coef"].astype(float).to_numpy()
    # cum_future[i] = product of split coefficients strictly after bar i.
    reversed_cum = np.cumprod(split[::-1])[::-1]
    cum_future = reversed_cum / split
    df = df.copy()
    df["adj_open"] = df["open"].to_numpy() / cum_future
    df["adj_close"] = df["close"].to_numpy() / cum_future
    df["adj_high"] = df["high"].to_numpy() / cum_future
    df["adj_low"] = df["low"].to_numpy() / cum_future
    df["adj_dividend"] = df["dividend"].to_numpy() / cum_future

    # Total-return index: reinvest each dividend at that day's adjusted close.
    adj_close = df["adj_close"].to_numpy()
    adj_div = df["adj_dividend"].to_numpy()
    prev_close = np.concatenate([[adj_close[0]], adj_close[:-1]])
    daily_tr = (adj_close + adj_div) / prev_close
    daily_tr[0] = 1.0
    df["total_return_index"] = np.cumprod(daily_tr)
    return df


@dataclass(frozen=True)
class FundamentalRecord:
    """One fiscal period of fundamentals for one company.

    ``data_available_date`` is the only date the backtest is allowed to filter
    on. It defaults to ``filing_date`` but a provider may push it later (for
    example to model an ingestion delay).
    """

    ticker: str
    period_end_date: dt.date
    fiscal_period: str
    filing_date: dt.date
    data_available_date: dt.date
    metrics: Mapping[str, float]
    provider: str = "unknown"
    form: str = ""

    def __post_init__(self) -> None:
        if self.data_available_date < self.period_end_date:
            raise DataError(
                f"{self.ticker}: data_available_date {self.data_available_date} precedes "
                f"period_end_date {self.period_end_date}; this would leak future information"
            )
        if self.data_available_date < self.filing_date:
            raise DataError(
                f"{self.ticker}: data_available_date {self.data_available_date} precedes "
                f"filing_date {self.filing_date}"
            )

    def get(self, metric: str) -> float | None:
        value = self.metrics.get(metric)
        if value is None:
            return None
        value = float(value)
        return value if np.isfinite(value) else None


@dataclass
class FundamentalSeries:
    """All known fundamental records for one ticker."""

    ticker: str
    records: list[FundamentalRecord] = field(default_factory=list)

    def add(self, record: FundamentalRecord) -> None:
        self.records.append(record)

    def available_as_of(self, as_of: dt.date) -> list[FundamentalRecord]:
        """Records a decision maker could legitimately have read on ``as_of``.

        Sorted by period end, oldest first. When the same fiscal period was
        filed more than once (an amended 10-K, say) the latest *visible*
        filing wins - never a restatement published after the decision date.
        """
        visible = [r for r in self.records if r.data_available_date <= as_of]
        by_period: dict[tuple[dt.date, str], FundamentalRecord] = {}
        for record in sorted(visible, key=lambda r: (r.period_end_date, r.filing_date)):
            by_period[(record.period_end_date, record.fiscal_period)] = record
        return sorted(by_period.values(), key=lambda r: r.period_end_date)


def frame_from_records(records: Iterable[FundamentalRecord]) -> pd.DataFrame:
    rows = []
    for record in records:
        row = {
            "ticker": record.ticker,
            "period_end_date": record.period_end_date,
            "fiscal_period": record.fiscal_period,
            "filing_date": record.filing_date,
            "data_available_date": record.data_available_date,
        }
        row.update(record.metrics)
        rows.append(row)
    return pd.DataFrame(rows)
