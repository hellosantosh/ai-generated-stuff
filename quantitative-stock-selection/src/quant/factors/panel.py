"""Trailing-twelve-month assembly from point-in-time fundamental records.

Raw filings are per-period. Ratios need trailing-twelve-month flows and the
latest balance-sheet instant, and growth needs the same quantity as it stood
one, three and five years ago.

Two kinds of metric are handled differently:

* **flows** (revenue, net income, cash flow) are summed across four
  consecutive quarters, or taken straight from an annual filing;
* **instants** (assets, equity, debt, share count) are point values, so the
  most recent one wins.

The panel only ever sees records the caller already filtered by
``data_available_date``, so nothing here can reach past the decision date.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..data.types import FundamentalRecord

FLOW_METRICS = frozenset(
    {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "eps_diluted",
        "operating_cash_flow",
        "capital_expenditures",
        "free_cash_flow",
        "interest_expense",
        "depreciation_amortization",
        "ebitda",
    }
)

INSTANT_METRICS = frozenset(
    {
        "total_assets",
        "total_liabilities",
        "total_equity",
        "total_debt",
        "long_term_debt",
        "short_term_debt",
        "current_assets",
        "current_liabilities",
        "cash_and_equivalents",
        "shares_outstanding",
    }
)

# A four-quarter window must really span a year; this tolerance absorbs 52/53
# week fiscal calendars without admitting an 18-month stub period.
_TTM_MIN_DAYS = 300
_TTM_MAX_DAYS = 400


class FundamentalPanel:
    """TTM and instant views over one company's visible filings."""

    def __init__(self, records: Sequence[FundamentalRecord]) -> None:
        self.records = sorted(records, key=lambda r: (r.period_end_date, r.filing_date))
        self._quarterly = [r for r in self.records if r.fiscal_period != "FY"]
        self._annual = [r for r in self.records if r.fiscal_period == "FY"]
        self._ttm_cache: dict[str, pd.Series] = {}

    def __bool__(self) -> bool:
        return bool(self.records)

    @property
    def latest_period_end(self) -> dt.date | None:
        return self.records[-1].period_end_date if self.records else None

    @property
    def latest_filing_date(self) -> dt.date | None:
        return max((r.filing_date for r in self.records), default=None)

    # --- instants --------------------------------------------------------
    def latest(self, metric: str) -> float | None:
        """Most recently reported value of a balance-sheet item."""
        for record in reversed(self.records):
            value = record.get(metric)
            if value is not None:
                return value
        return None

    def latest_as_of_offset(self, metric: str, years_back: int) -> float | None:
        cutoff = self._offset_period(years_back)
        if cutoff is None:
            return None
        for record in reversed([r for r in self.records if r.period_end_date <= cutoff]):
            value = record.get(metric)
            if value is not None:
                return value
        return None

    # --- flows -----------------------------------------------------------
    def ttm_series(self, metric: str) -> pd.Series:
        """TTM value of ``metric`` at each period end where four quarters exist.

        Falls back to annual filings for companies that file only yearly (many
        foreign issuers on 20-F/40-F), in which case each annual figure is its
        own TTM point.
        """
        if metric in self._ttm_cache:
            return self._ttm_cache[metric]

        values: dict[dt.date, float] = {}
        quarters = [
            (r.period_end_date, r.get(metric))
            for r in self._quarterly
            if r.get(metric) is not None
        ]
        # Deduplicate restatements: the latest visible filing for a period wins,
        # and `self.records` is already sorted so later entries overwrite.
        by_period: dict[dt.date, float] = {}
        for period_end, value in quarters:
            by_period[period_end] = float(value)
        ordered = sorted(by_period)

        for position in range(3, len(ordered)):
            window = ordered[position - 3 : position + 1]
            span = (window[-1] - window[0]).days
            # Four quarter-ends span ~270 days from first end to last end.
            if not (200 <= span <= 380):
                continue
            values[window[-1]] = float(sum(by_period[p] for p in window))

        for record in self._annual:
            value = record.get(metric)
            if value is None:
                continue
            # Only use the annual number when quarters did not already cover it.
            if record.period_end_date not in values:
                values[record.period_end_date] = float(value)

        series = pd.Series(values, dtype=float).sort_index()
        self._ttm_cache[metric] = series
        return series

    def ttm(self, metric: str, years_back: int = 0) -> float | None:
        """TTM value now, or as it stood ``years_back`` years ago.

        ``years_back`` looks for the last period ending on or before the
        anniversary of the latest period, so a Q3 figure is compared with a Q3
        figure rather than with a fiscal year.
        """
        if metric in INSTANT_METRICS:
            return self.latest(metric) if years_back == 0 else self.latest_as_of_offset(metric, years_back)

        series = self.ttm_series(metric)
        if series.empty:
            return None
        if years_back == 0:
            return float(series.iloc[-1])

        cutoff = self._offset_period(years_back)
        if cutoff is None:
            return None
        eligible = series[series.index <= cutoff]
        if eligible.empty:
            return None
        # Reject a stale match: a "3 years ago" value must really be near the
        # 3-year mark, not the oldest filing we happen to hold.
        gap_days = abs((cutoff - eligible.index[-1]).days)
        if gap_days > 200:
            return None
        return float(eligible.iloc[-1])

    def _offset_period(self, years_back: int) -> dt.date | None:
        latest = self.latest_period_end
        if latest is None:
            return None
        try:
            return latest.replace(year=latest.year - years_back)
        except ValueError:  # 29 February
            return latest.replace(year=latest.year - years_back, day=28)

    # --- derived helpers -------------------------------------------------
    def growth(self, metric: str, years_back: int = 1) -> float | None:
        """Growth over ``years_back`` years, annualized when longer than one.

        Returns ``None`` when the base is zero or negative: a percentage change
        from a loss to a smaller loss is not a growth rate, and reporting one
        would corrupt the ranking.
        """
        current = self.ttm(metric, 0)
        base = self.ttm(metric, years_back)
        if current is None or base is None or base <= 0:
            return None
        ratio = current / base
        if years_back <= 1:
            return ratio - 1.0
        if ratio <= 0:
            return None
        return ratio ** (1.0 / years_back) - 1.0

    def acceleration(self, metric: str) -> float | None:
        """Change in the year-over-year growth rate versus a year earlier."""
        current = self.growth(metric, 1)
        previous_now = self.ttm(metric, 1)
        previous_base = self.ttm(metric, 2)
        if current is None or previous_now is None or previous_base is None or previous_base <= 0:
            return None
        previous = previous_now / previous_base - 1.0
        return current - previous

    def margin(self, numerator: str, denominator: str = "revenue") -> float | None:
        top = self.ttm(numerator)
        bottom = self.ttm(denominator)
        if top is None or bottom is None or bottom <= 0:
            return None
        return top / bottom

    def total_debt(self) -> float | None:
        explicit = self.latest("total_debt")
        if explicit is not None:
            return explicit
        long_term = self.latest("long_term_debt")
        short_term = self.latest("short_term_debt")
        if long_term is None and short_term is None:
            return None
        return (long_term or 0.0) + (short_term or 0.0)

    def net_debt(self) -> float | None:
        debt = self.total_debt()
        cash = self.latest("cash_and_equivalents")
        if debt is None:
            return None
        return debt - (cash or 0.0)

    def ebitda(self) -> float | None:
        direct = self.ttm("ebitda")
        if direct is not None:
            return direct
        operating = self.ttm("operating_income")
        depreciation = self.ttm("depreciation_amortization")
        if operating is None:
            return None
        return operating + (depreciation or 0.0)

    def free_cash_flow(self) -> float | None:
        direct = self.ttm("free_cash_flow")
        if direct is not None:
            return direct
        ocf = self.ttm("operating_cash_flow")
        capex = self.ttm("capital_expenditures")
        if ocf is None:
            return None
        return ocf - abs(capex or 0.0)

    def coverage(self) -> dict[str, bool]:
        """Which inputs are present, for the data-quality report."""
        return {
            "revenue": self.ttm("revenue") is not None,
            "net_income": self.ttm("net_income") is not None,
            "equity": self.latest("total_equity") is not None,
            "cash_flow": self.free_cash_flow() is not None,
            "shares": self.latest("shares_outstanding") is not None,
        }


def safe_ratio(numerator: float | None, denominator: float | None, allow_negative_denominator: bool = False) -> float | None:
    """Divide, returning None when the result would be meaningless.

    A negative or zero denominator makes most financial ratios uninterpretable
    (negative equity, a company with no revenue), so those cases return None
    rather than a number that would rank alongside genuine values.
    """
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    if denominator < 0 and not allow_negative_denominator:
        return None
    value = numerator / denominator
    return value if np.isfinite(value) else None
