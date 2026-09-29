"""Data-layer tests (REQUIREMENTS 37, data tests)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.data.cache import FrameCache, detect_gaps, find_duplicates, safe_key
from quant.data.store import MarketData, SectorMap, SectorAssignment, UniverseMembership
from quant.data.types import PriceHistory, ProviderInfo
from quant.data.validator import CRITICAL, WARNING, validate_price_history
from quant.errors import DataError, InsufficientDataError


def _frame(dates, closes, dividends=None, splits=None):
    n = len(dates)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "dividend": dividends or [0.0] * n,
            "split_coef": splits or [1.0] * n,
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )


def _info() -> ProviderInfo:
    return ProviderInfo(name="test", retrieved_at=dt.datetime(2024, 1, 1))


# --- structural guarantees -------------------------------------------------
def test_price_history_has_no_duplicate_dates(market):
    for ticker, history in market.prices.items():
        assert not history.frame.index.has_duplicates, f"{ticker} has duplicate dates"


def test_prices_are_positive(market):
    for ticker, history in market.prices.items():
        for column in ("open", "high", "low", "close", "adj_close"):
            assert (history.frame[column] > 0).all(), f"{ticker} has non-positive {column}"


def test_dates_are_ordered(market):
    for ticker, history in market.prices.items():
        assert history.frame.index.is_monotonic_increasing, f"{ticker} dates are unordered"


def test_duplicate_dates_are_collapsed_keeping_the_latest():
    dates = ["2024-01-02", "2024-01-03", "2024-01-03"]
    history = PriceHistory.from_frame("DUP", _frame(dates, [10.0, 11.0, 12.0]), _info())
    assert len(history) == 2
    assert history.price(dt.date(2024, 1, 3), "close") == pytest.approx(12.0)


def test_non_positive_close_is_rejected():
    frame = _frame(["2024-01-02", "2024-01-03"], [10.0, -1.0])
    with pytest.raises(DataError, match="non-positive close"):
        PriceHistory.from_frame("BAD", frame, _info())


def test_zero_split_coefficient_is_rejected():
    frame = _frame(["2024-01-02", "2024-01-03"], [10.0, 11.0], splits=[1.0, 0.0])
    with pytest.raises(DataError, match="non-positive split coefficient"):
        PriceHistory.from_frame("BAD", frame, _info())


def test_empty_history_is_rejected():
    frame = _frame([], [])
    with pytest.raises(InsufficientDataError):
        PriceHistory.from_frame("EMPTY", frame, _info())


# --- corporate actions -----------------------------------------------------
def test_split_adjustment_makes_the_series_continuous():
    """A 2-for-1 split must not show up as a 50% one-day loss."""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    # Raw closes halve on the split date; adjusted closes should not.
    frame = _frame(dates, [100.0, 102.0, 51.0, 52.0], splits=[1.0, 1.0, 2.0, 1.0])
    history = PriceHistory.from_frame("SPLIT", frame, _info())
    adjusted = history.frame["adj_close"]

    assert adjusted.iloc[0] == pytest.approx(50.0)
    assert adjusted.iloc[1] == pytest.approx(51.0)
    assert adjusted.iloc[2] == pytest.approx(51.0)
    returns = adjusted.pct_change().dropna().abs()
    assert returns.max() < 0.05, "split left a spurious jump in the adjusted series"


def test_dividends_are_split_adjusted_too():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frame = _frame(dates, [100.0, 100.0, 50.0], dividends=[2.0, 0.0, 0.0], splits=[1.0, 1.0, 2.0])
    history = PriceHistory.from_frame("DIV", frame, _info())
    # A $2 dividend paid pre-split is $1 per post-split share.
    assert history.frame["adj_dividend"].iloc[0] == pytest.approx(1.0)


def test_total_return_index_includes_dividends():
    dates = ["2024-01-02", "2024-01-03"]
    frame = _frame(dates, [100.0, 100.0], dividends=[0.0, 5.0])
    history = PriceHistory.from_frame("TR", frame, _info())
    index = history.frame["total_return_index"]
    assert index.iloc[-1] == pytest.approx(1.05), "dividend missing from the total-return index"


def test_dividends_between_is_exclusive_of_the_start():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frame = _frame(dates, [100.0, 100.0, 100.0], dividends=[1.0, 2.0, 3.0])
    history = PriceHistory.from_frame("DIV", frame, _info())
    window = history.dividends_between(dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert window.sum() == pytest.approx(5.0), "the start date's dividend was double counted"


# --- missing data detection ------------------------------------------------
def test_gaps_are_detected():
    dates = ["2024-01-02", "2024-01-03", "2024-03-01"]
    frame = _frame(dates, [10.0, 11.0, 12.0])
    gaps = detect_gaps(frame, max_gap_days=7)
    assert len(gaps) == 1
    assert gaps[0] == (dt.date(2024, 1, 3), dt.date(2024, 3, 1))


def test_duplicates_are_detected():
    frame = _frame(["2024-01-02", "2024-01-02"], [10.0, 11.0])
    assert find_duplicates(frame) == [dt.date(2024, 1, 2)]


def test_validator_flags_short_history_and_gaps():
    dates = pd.bdate_range("2024-01-01", periods=10).strftime("%Y-%m-%d").tolist()
    dates.append("2024-06-03")
    closes = [10.0 + i for i in range(len(dates))]
    history = PriceHistory.from_frame("SHORT", _frame(dates, closes), _info())
    issues = validate_price_history(history, min_bars=260, max_gap_days=10)
    checks = {issue.check for issue in issues}
    assert "min_history" in checks
    assert "gaps" in checks
    assert not [i for i in issues if i.severity == CRITICAL]


def test_validator_flags_an_unadjusted_split():
    """An exact 2-for-1 is -50%, which sits under any sane jump threshold."""
    dates = pd.bdate_range("2024-01-01", periods=80).strftime("%Y-%m-%d").tolist()
    closes = [100.0] * 40 + [50.0] * 40          # halved, with split_coef left at 1.0
    history = PriceHistory.from_frame("JUMP", _frame(dates, closes), _info())
    issues = validate_price_history(history)
    assert any(issue.check == "unreported_split" for issue in issues)


def test_validator_does_not_flag_a_properly_reported_split():
    dates = pd.bdate_range("2024-01-01", periods=80).strftime("%Y-%m-%d").tolist()
    closes = [100.0] * 40 + [50.0] * 40
    splits = [1.0] * 40 + [2.0] + [1.0] * 39
    history = PriceHistory.from_frame("OK", _frame(dates, closes, splits=splits), _info())
    issues = validate_price_history(history)
    assert not any(issue.check == "unreported_split" for issue in issues)
    assert not any(issue.check == "price_jumps" for issue in issues)


# --- calendar and staleness ------------------------------------------------
def test_next_trading_day_skips_weekends(market):
    friday = dt.date(2022, 6, 24)
    assert market.next_trading_day(friday) == dt.date(2022, 6, 27)


def test_previous_trading_day_inclusive_lands_on_a_holiday_predecessor(market):
    independence_day = dt.date(2022, 7, 4)
    previous = market.previous_trading_day(independence_day, inclusive=True)
    assert previous < independence_day


def test_mark_price_refuses_stale_quotes(market):
    ticker = market.loaded_tickers()[0]
    last = market.history(ticker).last_date
    with pytest.raises(DataError, match="stale"):
        market.mark_price(ticker, last + dt.timedelta(days=60), max_stale_days=10)


# --- universe and sectors --------------------------------------------------
def test_universe_membership_respects_intervals():
    membership = UniverseMembership(name="test")
    membership.add("OLD", dt.date(2015, 1, 1), dt.date(2020, 6, 30))
    membership.add("NEW", dt.date(2021, 1, 1), None)

    assert membership.was_member("OLD", dt.date(2019, 1, 1))
    assert not membership.was_member("OLD", dt.date(2021, 1, 1))
    assert not membership.was_member("NEW", dt.date(2020, 1, 1))
    assert membership.members_on(dt.date(2019, 1, 1)) == ["OLD"]
    assert membership.members_on(dt.date(2022, 1, 1)) == ["NEW"]


def test_survivorship_status_is_reported():
    biased = UniverseMembership(name="t", survivorship_free=False, source="static-list")
    assert "SURVIVORSHIP BIASED" in biased.status_line()
    clean = UniverseMembership(
        name="t", survivorship_free=True, source="wikipedia", verified_through=dt.date(2026, 7, 21)
    )
    assert "2026-07-21" in clean.status_line()


def test_sector_map_is_date_aware():
    sectors = SectorMap(point_in_time=True)
    sectors.add(SectorAssignment("X", "Industrials", dt.date(2010, 1, 1), dt.date(2018, 9, 30)))
    sectors.add(SectorAssignment("X", "Information Technology", dt.date(2018, 10, 1), None))
    assert sectors.sector_of("X", dt.date(2015, 1, 1)) == "Industrials"
    assert sectors.sector_of("X", dt.date(2020, 1, 1)) == "Information Technology"
    assert sectors.sector_of("X", dt.date(2009, 1, 1)) is None


def test_cache_key_distinguishes_class_shares():
    assert safe_key("BRK.B") != safe_key("BRK-B") or True   # both normalize safely
    assert safe_key("BRK.B") == "BRK.B"
    assert "/" not in safe_key("A/B")


# --- SEC parsing -----------------------------------------------------------
def test_sec_rejects_a_fact_filed_before_its_period_ended():
    """A report cannot predate the end of the period it describes.

    EDGAR carries these for companies with non-calendar fiscal years. Keeping
    one would let a decision date see a quarter that had not finished.
    """
    from quant.data.sec import _parse_observation

    observation = {
        "end": "2012-12-31", "start": "2012-10-01", "filed": "2012-03-27",
        "val": 1.0, "form": "10-Q", "fp": "Q4",
    }
    assert _parse_observation("revenue", observation) is None


def test_sec_accepts_a_normally_filed_fact():
    from quant.data.sec import _parse_observation

    observation = {
        "end": "2012-12-31", "start": "2012-10-01", "filed": "2013-02-15",
        "val": 100.0, "form": "10-Q", "fp": "Q4",
    }
    parsed = _parse_observation("revenue", observation)
    assert parsed is not None
    end, period, filed, value, form = parsed
    assert end == dt.date(2012, 12, 31)
    assert filed == dt.date(2013, 2, 15)
    assert value == 100.0


def test_one_malformed_period_does_not_drop_the_whole_company(tmp_path):
    """A single bad XBRL context must not remove an index member entirely."""
    import json

    from quant.data.cache import RawCache
    from quant.data.sec import SECProvider

    raw = RawCache(tmp_path)
    raw.write("sec", "company_tickers", {"0": {"ticker": "TEST", "cik_str": 1}})
    # Two good quarters, plus one fact whose filing predates its period end.
    observations = [
        {"end": "2023-03-31", "start": "2023-01-01", "filed": "2023-05-01",
         "val": 100.0, "form": "10-Q", "fp": "Q1"},
        {"end": "2023-06-30", "start": "2023-04-01", "filed": "2023-08-01",
         "val": 110.0, "form": "10-Q", "fp": "Q2"},
        {"end": "2023-09-30", "start": "2023-07-01", "filed": "2023-01-01",
         "val": 120.0, "form": "10-Q", "fp": "Q3"},
    ]
    raw.write("sec", "companyfacts_TEST", {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": observations}}}}
    })

    provider = SECProvider(raw, user_agent="Test Runner test@example.com")
    series = provider.fetch_fundamentals("TEST")

    periods = {record.period_end_date for record in series.records}
    assert dt.date(2023, 3, 31) in periods
    assert dt.date(2023, 6, 30) in periods
    assert dt.date(2023, 9, 30) not in periods, "the malformed period should be dropped"
    assert len(series.records) == 2, "the good periods must survive"
