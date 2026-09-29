"""Data validation and the data-quality report (REQUIREMENTS 37, 41, 59).

Every check returns an ``Issue`` rather than raising, so one bad ticker does
not abort a 500-name update. The caller decides: ``DataQualityReport.raise_if_critical``
turns critical issues into a hard failure, which is what ``validate-data`` and
strict backtests do.

The guiding rule from REQUIREMENTS 41: never replace a missing value with zero
unless zero is economically correct. Missing data is reported, not patched.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..errors import DataError
from ..logging_config import get_logger
from .types import FundamentalSeries, PriceHistory

log = get_logger(__name__)

INFO = "info"
WARNING = "warning"
CRITICAL = "critical"


@dataclass(frozen=True)
class Issue:
    scope: str
    check: str
    severity: str
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.scope}/{self.check} {self.subject}: {self.detail}"


@dataclass
class DataQualityReport:
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)
    checked_at: dt.datetime = field(default_factory=dt.datetime.now)

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    def extend(self, issues: Iterable[Issue]) -> None:
        self.issues.extend(issues)

    def by_severity(self, severity: str) -> list[Issue]:
        return [i for i in self.issues if i.severity == severity]

    @property
    def critical(self) -> list[Issue]:
        return self.by_severity(CRITICAL)

    @property
    def warnings(self) -> list[Issue]:
        return self.by_severity(WARNING)

    def raise_if_critical(self) -> None:
        critical = self.critical
        if critical:
            head = "\n".join(f"  - {issue}" for issue in critical[:10])
            more = f"\n  ... and {len(critical) - 10} more" if len(critical) > 10 else ""
            raise DataError(f"data validation found {len(critical)} critical issue(s):\n{head}{more}")

    def to_frame(self) -> pd.DataFrame:
        if not self.issues:
            return pd.DataFrame(columns=["scope", "check", "severity", "subject", "detail"])
        return pd.DataFrame(
            [
                {
                    "scope": i.scope,
                    "check": i.check,
                    "severity": i.severity,
                    "subject": i.subject,
                    "detail": i.detail,
                }
                for i in self.issues
            ]
        )

    def summary(self) -> str:
        counts = Counter(i.severity for i in self.issues)
        lines = [
            "Data Quality",
            "------------",
            "",
        ]
        for key, label in (
            ("universe_size", "Universe tickers"),
            ("price_complete", "Price data complete"),
            ("fundamentals_complete", "Fundamental data complete"),
            ("price_missing", "Missing price data"),
            ("fundamentals_missing", "Missing fundamental data"),
            ("api_failures", "API failures"),
        ):
            if key in self.stats:
                lines.append(f"{label + ':':<28}{int(self.stats[key]):>6}")
        lines.append(f"{'Warnings:':<28}{counts.get(WARNING, 0):>6}")
        lines.append(f"{'Critical errors:':<28}{counts.get(CRITICAL, 0):>6}")
        return "\n".join(lines)


# --- price checks ---------------------------------------------------------
# Ratios of a day's price to the previous day's that correspond to common
# split factors. If a bar moves by one of these and carries no split
# coefficient, the provider almost certainly failed to report the split.
SPLIT_LIKE_RATIOS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2.0, 3.0, 4.0, 5.0, 10.0)
SPLIT_RATIO_TOLERANCE = 0.01


def validate_price_history(
    history: PriceHistory,
    min_bars: int = 0,
    max_gap_days: int = 10,
    max_daily_move: float = 0.60,
) -> list[Issue]:
    """Structural checks on one price series.

    Two separate move checks, because they catch different things:

    * ``max_daily_move`` flags any very large day. Genuine 60% days happen, so
      it warns rather than excludes.
    * the split-ratio check flags a day whose move lands on a common split
      factor while the bar reports no split. An unadjusted 2-for-1 is exactly
      -50%, which slips under any threshold loose enough not to fire on real
      crashes - so it needs its own test rather than a lower threshold.
    """
    ticker = history.ticker
    frame = history.frame
    issues: list[Issue] = []

    def issue(check: str, severity: str, detail: str) -> None:
        issues.append(Issue("price", check, severity, ticker, detail))

    if frame.empty:
        issue("non_empty", CRITICAL, "price history is empty")
        return issues

    if not frame.index.is_monotonic_increasing:
        issue("date_order", CRITICAL, "dates are not in ascending order")
    if frame.index.has_duplicates:
        dupes = frame.index[frame.index.duplicated()]
        issue("duplicate_dates", CRITICAL, f"{len(dupes)} duplicate date(s), first {dupes[0].date()}")

    for column in ("open", "high", "low", "close"):
        values = frame[column]
        if (values <= 0).any():
            first = frame.index[values <= 0][0].date()
            issue("positive_prices", CRITICAL, f"non-positive {column} on {first}")
        if values.isna().any():
            issue("no_nan_prices", CRITICAL, f"{int(values.isna().sum())} NaN value(s) in {column}")

    inconsistent = (frame["high"] < frame["low"]) | (frame["close"] > frame["high"] * 1.0001) | (
        frame["close"] < frame["low"] * 0.9999
    )
    if inconsistent.any():
        first = frame.index[inconsistent][0].date()
        issue(
            "ohlc_consistency",
            WARNING,
            f"{int(inconsistent.sum())} bar(s) where close falls outside high/low, first {first}",
        )

    if len(frame) < min_bars:
        issue(
            "min_history",
            WARNING,
            f"only {len(frame)} bars, fewer than the required {min_bars}",
        )

    deltas = frame.index.to_series().diff().dt.days
    long_gaps = deltas[deltas > max_gap_days]
    if len(long_gaps):
        first_gap = long_gaps.index[0].date()
        issue(
            "gaps",
            WARNING,
            f"{len(long_gaps)} gap(s) longer than {max_gap_days} days, first ending {first_gap}",
        )

    returns = frame["adj_close"].pct_change().abs()
    jumps = returns[returns > max_daily_move]
    if len(jumps):
        first = jumps.index[0].date()
        issue(
            "price_jumps",
            WARNING,
            f"{len(jumps)} day(s) moving more than {max_daily_move:.0%}, first {first} "
            f"({returns.loc[jumps.index[0]]:.1%}); check for an unadjusted corporate action",
        )

    ratios = frame["adj_close"] / frame["adj_close"].shift(1)
    unreported = frame["split_coef"] == 1.0
    for expected in SPLIT_LIKE_RATIOS:
        suspicious = ((ratios - expected).abs() / expected < SPLIT_RATIO_TOLERANCE) & unreported
        if suspicious.any():
            dates = frame.index[suspicious]
            issue(
                "unreported_split",
                WARNING,
                f"{len(dates)} bar(s) moved by a factor of {expected:.3g} with no split "
                f"reported, first {dates[0].date()}; the provider may have missed a "
                f"corporate action",
            )

    if (frame["split_coef"] != 1.0).any():
        splits = frame.index[frame["split_coef"] != 1.0]
        issue(
            "splits",
            INFO,
            f"{len(splits)} split(s) applied, most recent {splits[-1].date()}",
        )

    negative_dividends = frame["dividend"] < 0
    if negative_dividends.any():
        issue("dividends", CRITICAL, "negative dividend amount")

    if "volume" in frame.columns:
        zero_volume = (frame["volume"].fillna(0) <= 0).sum()
        if zero_volume > len(frame) * 0.05:
            issue(
                "volume",
                WARNING,
                f"{zero_volume} bars ({zero_volume / len(frame):.1%}) report zero volume",
            )

    return issues


def check_staleness(history: PriceHistory, as_of: dt.date, max_stale_days: int) -> Issue | None:
    staleness = (as_of - history.last_date).days
    if staleness > max_stale_days:
        return Issue(
            "price",
            "stale",
            WARNING,
            history.ticker,
            f"last bar is {history.last_date} ({staleness} days before {as_of})",
        )
    return None


# --- fundamental checks ---------------------------------------------------
def validate_fundamentals(series: FundamentalSeries) -> list[Issue]:
    """Sanity checks on a fundamental series, including the filing-date order."""
    issues: list[Issue] = []
    ticker = series.ticker

    def issue(check: str, severity: str, detail: str) -> None:
        issues.append(Issue("fundamentals", check, severity, ticker, detail))

    if not series.records:
        issue("present", WARNING, "no fundamental records")
        return issues

    for record in series.records:
        if record.filing_date < record.period_end_date:
            issue(
                "filing_after_period",
                CRITICAL,
                f"period ending {record.period_end_date} was filed on {record.filing_date}, "
                f"which is before the period ended",
            )
        lag = (record.filing_date - record.period_end_date).days
        if lag > 400:
            issue(
                "filing_lag",
                WARNING,
                f"period ending {record.period_end_date} filed {lag} days later",
            )
        equity = record.get("total_equity")
        assets = record.get("total_assets")
        liabilities = record.get("total_liabilities")
        if None not in (equity, assets, liabilities):
            residual = abs(assets - (liabilities + equity)) / max(abs(assets), 1.0)
            if residual > 0.15:
                issue(
                    "balance_sheet_identity",
                    WARNING,
                    f"period ending {record.period_end_date}: assets differ from "
                    f"liabilities + equity by {residual:.1%}",
                )
        revenue = record.get("revenue")
        if revenue is not None and revenue < 0:
            issue(
                "negative_revenue",
                WARNING,
                f"period ending {record.period_end_date} reports revenue {revenue:,.0f}",
            )

    periods = [r.period_end_date for r in series.records]
    duplicates = [p for p, count in Counter(periods).items() if count > 1]
    if duplicates:
        issue(
            "duplicate_periods",
            INFO,
            f"{len(duplicates)} period(s) reported more than once (restatements or amendments)",
        )
    return issues


# --- cross-sectional checks -----------------------------------------------
def build_report(
    prices: Mapping[str, PriceHistory],
    fundamentals: Mapping[str, FundamentalSeries] | None = None,
    expected_tickers: Sequence[str] | None = None,
    as_of: dt.date | None = None,
    min_bars: int = 0,
    max_stale_days: int = 10,
    failures: Mapping[str, str] | None = None,
) -> DataQualityReport:
    """Assemble the full data-quality picture for a universe."""
    report = DataQualityReport()
    fundamentals = fundamentals or {}
    failures = failures or {}
    expected = list(expected_tickers or prices.keys())

    for ticker, history in prices.items():
        report.extend(validate_price_history(history, min_bars=min_bars))
        if as_of is not None:
            stale = check_staleness(history, as_of, max_stale_days)
            if stale is not None:
                report.add(stale)

    for ticker, series in fundamentals.items():
        report.extend(validate_fundamentals(series))

    missing_prices = [t for t in expected if t not in prices]
    for ticker in missing_prices:
        report.add(Issue("coverage", "price_missing", WARNING, ticker, "no price history loaded"))

    missing_fundamentals = [
        t for t in expected if t in prices and not fundamentals.get(t, FundamentalSeries(t)).records
    ]
    for ticker in missing_fundamentals:
        report.add(
            Issue("coverage", "fundamentals_missing", INFO, ticker, "no fundamental records loaded")
        )

    for ticker, message in failures.items():
        report.add(Issue("api", "download_failed", WARNING, ticker, message))

    report.stats.update(
        {
            "universe_size": float(len(expected)),
            "price_complete": float(len(expected) - len(missing_prices)),
            "price_missing": float(len(missing_prices)),
            "fundamentals_complete": float(
                len([t for t in expected if fundamentals.get(t, FundamentalSeries(t)).records])
            ),
            "fundamentals_missing": float(len(missing_fundamentals)),
            "api_failures": float(len(failures)),
        }
    )
    return report
