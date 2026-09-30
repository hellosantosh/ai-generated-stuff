"""Index and cohort analytics tests.

The cohort chart makes a claim a person could act on - "the biggest companies
beat the index" - so the arithmetic behind it is pinned down here on series
whose answers can be worked out by hand.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.analysis.indices import (
    cohort_performance,
    index_performance,
    max_drawdown,
    points,
    rebase,
    summarize,
    thin,
    window,
)
from quant.data.types import PriceHistory, ProviderInfo
from quant.errors import DataError

DATES = pd.bdate_range("2020-01-01", periods=520)


def _series(values) -> pd.Series:
    return pd.Series(values, index=DATES[: len(values)], dtype=float)


def _history(ticker: str, closes, dividend_on: int | None = None) -> PriceHistory:
    dividends = [0.0] * len(closes)
    if dividend_on is not None:
        dividends[dividend_on] = 1.0
    frame = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1e6] * len(closes), "dividend": dividends,
         "split_coef": [1.0] * len(closes)},
        index=pd.DatetimeIndex(DATES[: len(closes)], name="date"),
    )
    info = ProviderInfo(name="test", retrieved_at=dt.datetime(2024, 1, 1))
    return PriceHistory.from_frame(ticker, frame, info, split_adjusted=True)


# --- series helpers --------------------------------------------------------
def test_rebase_starts_at_one_hundred():
    rebased = rebase(_series([50.0, 60.0, 75.0]))
    assert rebased.iloc[0] == pytest.approx(100.0)
    assert rebased.iloc[-1] == pytest.approx(150.0)


def test_thinning_keeps_both_endpoints():
    long = _series(np.linspace(100.0, 200.0, 500))
    thinned = thin(long, max_points=50)
    assert len(thinned) <= 52
    assert thinned.iloc[0] == pytest.approx(long.iloc[0])
    assert thinned.iloc[-1] == pytest.approx(long.iloc[-1]), "the last value is the one people read"


def test_drawdown_is_measured_from_the_running_peak():
    assert max_drawdown(_series([100.0, 120.0, 60.0, 90.0])) == pytest.approx(-0.5)


def test_a_short_window_reports_no_annualized_rate():
    """Annualizing three weeks into a yearly rate reads as a forecast."""
    short = summarize(rebase(_series([100.0] * 15)))
    assert short["cagr"] is None
    long = summarize(rebase(pd.Series([100.0, 121.0], index=[pd.Timestamp("2020-01-01"),
                                                             pd.Timestamp("2022-01-01")])))
    assert long["cagr"] == pytest.approx(0.1, abs=1e-3)


def test_points_drop_values_that_are_not_finite():
    series = _series([100.0, float("nan"), 120.0])
    assert [p[1] for p in points(series)] == [100.0, 120.0]


def test_window_clips_to_the_requested_dates():
    clipped = window(_series(np.arange(100.0, 200.0)), DATES[10].date(), DATES[20].date())
    assert clipped.index[0] == DATES[10]
    assert clipped.index[-1] == DATES[20]


# --- index performance -----------------------------------------------------
def test_index_performance_rebases_every_proxy_to_the_same_start():
    histories = {
        "IVV": _history("IVV", np.linspace(100.0, 150.0, 300)),
        "QQQ": _history("QQQ", np.linspace(40.0, 80.0, 300)),
    }
    result = index_performance(histories, DATES[0].date(), DATES[299].date())
    by_ticker = {row["ticker"]: row for row in result["series"]}
    assert by_ticker["IVV"]["points"][0][1] == pytest.approx(100.0)
    assert by_ticker["QQQ"]["points"][0][1] == pytest.approx(100.0)
    assert by_ticker["IVV"]["total_return"] == pytest.approx(0.5, abs=1e-6)
    assert by_ticker["QQQ"]["total_return"] == pytest.approx(1.0, abs=1e-6)


def test_dividends_count_toward_an_index_return():
    """Price return would say zero; the chart is a total return."""
    flat_with_dividend = _history("IVV", [100.0] * 60, dividend_on=30)
    result = index_performance({"IVV": flat_with_dividend}, DATES[0].date(), DATES[59].date())
    assert result["series"][0]["total_return"] == pytest.approx(0.01, abs=1e-6)


def test_an_index_younger_than_the_window_says_so():
    late = _history("QQQ", np.linspace(100.0, 110.0, 40))
    result = index_performance({"QQQ": late}, DATES[0].date(), DATES[300].date())
    assert "late_start" not in result["series"][0], "this one starts on the first date"

    result = index_performance({"QQQ": late}, dt.date(2019, 1, 1), DATES[39].date())
    assert "late_start" in result["series"][0]


def test_an_index_with_no_data_in_the_window_is_skipped_not_zeroed():
    history = _history("IJH", np.linspace(100.0, 110.0, 30))
    result = index_performance({"IJH": history}, dt.date(2023, 1, 1), dt.date(2023, 6, 1))
    assert result["series"] == []
    assert result["skipped"][0]["ticker"] == "IJH"


# --- cohorts ---------------------------------------------------------------
def _cohort_inputs():
    """Three stocks: a double, a flat and a halving. The index is the flat one."""
    frame = pd.DataFrame({
        "BIG": np.linspace(100.0, 200.0, 100),
        "MID": np.full(100, 100.0),
        "SMALL": np.linspace(100.0, 50.0, 100),
    }, index=DATES[:100])
    caps = {"BIG": 900.0, "MID": 90.0, "SMALL": 10.0}
    benchmark = pd.Series(np.full(100, 50.0), index=DATES[:100])
    return frame, caps, benchmark


def test_a_cohort_is_cap_weighted_at_formation():
    frame, caps, benchmark = _cohort_inputs()
    result = cohort_performance(frame, caps, benchmark, DATES[0].date(), DATES[99].date(),
                                sizes=(2,))
    cohort = result["cohorts"][0]
    # 900/990 of a double and 90/990 of a flat line.
    expected = 0.9091 * 2.0 + 0.0909 * 1.0 - 1.0
    assert cohort["total_return"] == pytest.approx(expected, abs=1e-3)
    assert cohort["members"] == ["BIG", "MID"], "ranked by cap, largest first"
    assert cohort["cap_share"] == pytest.approx(990 / 1000)


def test_the_relative_line_is_one_hundred_when_a_cohort_matches_the_index():
    frame, caps, benchmark = _cohort_inputs()
    matching = pd.Series(np.linspace(100.0, 200.0, 100), index=DATES[:100])
    result = cohort_performance(frame, caps, matching, DATES[0].date(), DATES[99].date(),
                                sizes=(1,))
    cohort = result["cohorts"][0]
    assert cohort["relative_end"] == pytest.approx(100.0, abs=1e-6)
    assert cohort["excess_return"] == pytest.approx(0.0, abs=1e-9)


def test_a_cohort_larger_than_the_universe_is_omitted_not_padded():
    frame, caps, benchmark = _cohort_inputs()
    result = cohort_performance(frame, caps, benchmark, DATES[0].date(), DATES[99].date(),
                                sizes=(2, 50))
    assert [c["size"] for c in result["cohorts"]] == [2]


def test_a_company_that_stops_trading_is_carried_at_its_last_price():
    frame, caps, benchmark = _cohort_inputs()
    frame.loc[DATES[50]:, "SMALL"] = np.nan
    result = cohort_performance(frame, caps, benchmark, DATES[0].date(), DATES[99].date(),
                                sizes=(3,))
    assert result["carried_forward"] == ["SMALL"]
    # Carried flat from its halfway value, not dropped and not marked to zero.
    assert result["cohorts"][0]["total_return"] > 0


def test_an_empty_window_is_an_error_rather_than_an_empty_chart():
    frame, caps, benchmark = _cohort_inputs()
    with pytest.raises(DataError):
        cohort_performance(frame.iloc[0:0], caps, benchmark, DATES[0].date(), DATES[99].date())
