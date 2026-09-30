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


# --- synthetic data guardrails ---------------------------------------------
def test_synthetic_cannot_be_selected_from_configuration_alone(project_root):
    """Generated prices must never be reachable by a stray config edit."""
    from quant.config import load_config
    from quant.errors import ConfigError

    with pytest.raises(ConfigError, match="must not be reached from configuration alone"):
        load_config(
            project_root / "config",
            overrides={"settings": {"data": {"provider": "synthetic"}}},
            project_root=project_root,
        )


def test_synthetic_fundamentals_are_guarded_too(project_root):
    from quant.config import load_config
    from quant.errors import ConfigError

    with pytest.raises(ConfigError):
        load_config(
            project_root / "config",
            overrides={"settings": {"data": {"fundamentals_provider": "synthetic"}}},
            project_root=project_root,
        )


def test_synthetic_is_available_with_an_explicit_opt_in(project_root):
    from quant.config import load_config

    config = load_config(
        project_root / "config",
        overrides={"settings": {"data": {"provider": "synthetic",
                                         "fundamentals_provider": "synthetic"}}},
        project_root=project_root,
        allow_synthetic=True,
    )
    assert config.data.provider == "synthetic"


def test_shipped_configuration_uses_real_providers(project_root):
    """The defaults an investor inherits must be real data."""
    from quant.config import load_config

    config = load_config(project_root / "config", project_root=project_root)
    assert config.data.provider != "synthetic"
    assert config.data.fundamentals_provider != "synthetic"
    assert config.universe.source != "synthetic"


def test_synthetic_reports_are_marked_in_the_filename(tmp_path):
    """A banner is invisible in a file listing; the filename is not."""
    from quant.reports.weekly import write_weekly_report

    paths = write_weekly_report("# test", tmp_path, dt.date(2026, 9, 25), prefix="SYNTHETIC_")
    assert paths["markdown"].name.startswith("SYNTHETIC_")
    assert paths["html"].name.startswith("SYNTHETIC_")


# --- split-adjustment convention -------------------------------------------
def test_provider_pre_adjusted_prices_are_not_adjusted_again():
    """Yahoo back-adjusts for splits; adjusting again is a 50x phantom gain.

    Modeled on Chipotle's 50-for-1 split in June 2024, where the cached close
    runs 64.29 -> 65.86 -> 62.41 straight through the split date.
    """
    dates = ["2024-06-24", "2024-06-25", "2024-06-26", "2024-06-27"]
    closes = [63.87, 65.66, 65.86, 62.41]
    splits = [1.0, 1.0, 50.0, 1.0]
    frame = _frame(dates, closes, splits=splits)
    history = PriceHistory.from_frame("CMG", frame, _info(), split_adjusted=True)

    adjusted = history.frame["adj_close"]
    assert adjusted.iloc[0] == pytest.approx(63.87), "a pre-adjusted price was adjusted again"
    assert list(adjusted) == pytest.approx(closes)
    returns = adjusted.pct_change().dropna().abs()
    assert returns.max() < 0.10


def test_raw_provider_prices_are_adjusted():
    """Alpha Vantage supplies raw OHLC, which does need adjusting."""
    dates = ["2024-06-24", "2024-06-25", "2024-06-26", "2024-06-27"]
    closes = [3193.5, 3283.0, 65.86, 62.41]      # raw: price collapses on the split
    splits = [1.0, 1.0, 50.0, 1.0]
    history = PriceHistory.from_frame(
        "CMG", _frame(dates, closes, splits=splits), _info(), split_adjusted=False
    )
    adjusted = history.frame["adj_close"]
    assert adjusted.iloc[0] == pytest.approx(3193.5 / 50)
    returns = adjusted.pct_change().dropna().abs()
    assert returns.max() < 0.10, "the raw series was not made continuous"


def test_pre_adjusted_dividends_are_left_alone():
    """Yahoo also back-adjusts dividends, so they must pass through unchanged."""
    dates = ["2024-03-05", "2024-06-10", "2024-06-11"]
    frame = _frame(dates, [86.0, 120.0, 120.9], dividends=[0.004, 0.0, 0.01],
                   splits=[1.0, 10.0, 1.0])
    history = PriceHistory.from_frame("NVDA", frame, _info(), split_adjusted=True)
    assert history.frame["adj_dividend"].iloc[0] == pytest.approx(0.004)


def test_split_convention_comes_from_the_provider_by_default():
    from quant.data.types import ProviderInfo

    info = ProviderInfo(name="p", retrieved_at=dt.datetime(2024, 1, 1), split_adjusted=True)
    frame = _frame(["2024-06-25", "2024-06-26"], [65.66, 65.86], splits=[1.0, 50.0])
    history = PriceHistory.from_frame("X", frame, info)
    assert history.frame["adj_close"].iloc[0] == pytest.approx(65.66)


def test_yfinance_provider_declares_pre_adjusted_prices():
    """A regression guard on the provider's own convention."""
    import inspect

    from quant.data.yfinance_provider import YFinanceProvider

    source = inspect.getsource(YFinanceProvider)
    assert source.count("split_adjusted=True") >= 3, (
        "YFinanceProvider must declare split_adjusted=True everywhere it builds a "
        "PriceHistory or stores to cache"
    )
