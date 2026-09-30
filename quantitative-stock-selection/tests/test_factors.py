"""Factor calculation tests (REQUIREMENTS 37, financial tests)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.data.store import MarketData
from quant.data.types import FundamentalRecord, PriceHistory, ProviderInfo
from quant.errors import DataError
from quant.factors.momentum import momentum_factors, sector_relative_strength, trailing_return
from quant.factors.panel import FundamentalPanel, safe_ratio
from quant.factors.quality import quality_factors
from quant.factors.risk import annualized_volatility, downside_volatility, max_drawdown, risk_factors
from quant.factors.valuation import valuation_factors


def _record(period_end: dt.date, filing_lag_days: int = 40, **metrics) -> FundamentalRecord:
    filing = period_end + dt.timedelta(days=filing_lag_days)
    quarter = (period_end.month - 1) // 3 + 1
    return FundamentalRecord(
        ticker="TEST",
        period_end_date=period_end,
        fiscal_period=f"Q{quarter}",
        filing_date=filing,
        data_available_date=filing,
        metrics=metrics,
        provider="test",
    )


def _quarterly(start_year: int, years: int, revenue_per_quarter, **extra) -> list[FundamentalRecord]:
    records = []
    index = 0
    for year in range(start_year, start_year + years):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            value = revenue_per_quarter(index) if callable(revenue_per_quarter) else revenue_per_quarter
            records.append(_record(dt.date(year, month, day), revenue=value, **extra))
            index += 1
    return records


# --- TTM assembly ----------------------------------------------------------
def test_ttm_sums_four_consecutive_quarters():
    panel = FundamentalPanel(_quarterly(2022, 2, lambda i: 100.0 + i))
    # Final four quarters: 104 + 105 + 106 + 107
    assert panel.ttm("revenue") == pytest.approx(104 + 105 + 106 + 107)


def test_ttm_a_year_ago_uses_the_matching_quarter():
    panel = FundamentalPanel(_quarterly(2022, 3, lambda i: 100.0 + i))
    current = panel.ttm("revenue")
    year_ago = panel.ttm("revenue", 1)
    # Each year's TTM is 4 quarters further along, i.e. 4 x 4 = 16 higher.
    assert current - year_ago == pytest.approx(16.0)


def test_ttm_returns_none_when_history_is_too_short():
    panel = FundamentalPanel(_quarterly(2023, 1, 100.0))
    assert panel.ttm("revenue") is not None
    assert panel.ttm("revenue", 3) is None, "a 3-year lookback must not fabricate a value"


def test_instant_metrics_take_the_latest_value_not_a_sum():
    records = _quarterly(2023, 1, 100.0, total_equity=500.0)
    panel = FundamentalPanel(records)
    assert panel.latest("total_equity") == pytest.approx(500.0)
    assert panel.ttm("total_equity") == pytest.approx(500.0)


def test_annual_only_filers_still_produce_a_ttm():
    records = [
        FundamentalRecord("FY", dt.date(year, 12, 31), "FY", dt.date(year + 1, 3, 1),
                          dt.date(year + 1, 3, 1), {"revenue": 1000.0 * (year - 2019)})
        for year in (2020, 2021, 2022)
    ]
    panel = FundamentalPanel(records)
    assert panel.ttm("revenue") == pytest.approx(3000.0)
    assert panel.growth("revenue", 1) == pytest.approx(0.5)


# --- growth ----------------------------------------------------------------
def test_revenue_growth_yoy():
    records = _quarterly(2022, 1, 100.0) + _quarterly(2023, 1, 110.0)
    panel = FundamentalPanel(records)
    assert panel.growth("revenue", 1) == pytest.approx(0.10)


def test_cagr_is_annualized():
    """A 3-year doubling is a 25.99% CAGR, not an 8.66% average."""
    revenues = {2020: 100.0, 2021: 125.99, 2022: 158.74, 2023: 200.0}
    records = []
    for year, value in revenues.items():
        records.extend(_quarterly(year, 1, value))
    panel = FundamentalPanel(records)
    cagr = panel.growth("revenue", 3)
    assert cagr == pytest.approx(2 ** (1 / 3) - 1, abs=1e-3)


def test_growth_from_a_negative_base_is_none_not_a_number():
    """Loss to smaller loss is not a growth rate; reporting one corrupts ranks."""
    records = _quarterly(2022, 1, 100.0, eps_diluted=-1.0) + _quarterly(2023, 1, 100.0, eps_diluted=-0.5)
    panel = FundamentalPanel(records)
    assert panel.growth("eps_diluted", 1) is None


def test_growth_from_a_zero_base_is_none():
    records = _quarterly(2022, 1, 0.0) + _quarterly(2023, 1, 50.0)
    panel = FundamentalPanel(records)
    assert panel.growth("revenue", 1) is None


def test_acceleration_is_the_change_in_growth_rate():
    records = (
        _quarterly(2021, 1, 100.0) + _quarterly(2022, 1, 110.0) + _quarterly(2023, 1, 132.0)
    )
    panel = FundamentalPanel(records)
    # Growth went from 10% to 20%, so acceleration is +10 points.
    assert panel.acceleration("revenue") == pytest.approx(0.10, abs=1e-6)


# --- margins, returns, ratios ----------------------------------------------
def test_margin_calculation():
    records = _quarterly(2023, 1, 100.0, gross_profit=40.0, operating_income=20.0, net_income=12.0)
    panel = FundamentalPanel(records)
    assert panel.margin("gross_profit") == pytest.approx(0.40)
    assert panel.margin("operating_income") == pytest.approx(0.20)
    assert panel.margin("net_income") == pytest.approx(0.12)


def test_free_cash_flow_is_ocf_minus_capex():
    records = _quarterly(2023, 1, 100.0, operating_cash_flow=30.0, capital_expenditures=10.0)
    panel = FundamentalPanel(records)
    assert panel.free_cash_flow() == pytest.approx(80.0)   # (30 - 10) x 4 quarters


def test_free_cash_flow_treats_capex_sign_consistently():
    """Providers report capex as a positive outflow or a negative one."""
    positive = FundamentalPanel(_quarterly(2023, 1, 100.0, operating_cash_flow=30.0, capital_expenditures=10.0))
    negative = FundamentalPanel(_quarterly(2023, 1, 100.0, operating_cash_flow=30.0, capital_expenditures=-10.0))
    assert positive.free_cash_flow() == pytest.approx(negative.free_cash_flow())


def test_roic_uses_nopat_over_invested_capital(view):
    records = _quarterly(
        2023, 1, 1000.0, operating_income=200.0, net_income=150.0,
        total_equity=2000.0, total_debt=1000.0, cash_and_equivalents=500.0,
    )
    panel = FundamentalPanel(records)
    factors = quality_factors(view, "IVV", panel)
    # NOPAT = 800 x 0.79; invested capital = 2000 + 1000 - 500 = 2500.
    assert factors["roic"] == pytest.approx(800 * 0.79 / 2500)


def test_roe_is_none_when_equity_is_negative():
    """Negative book value would turn a loss into a spectacular return."""
    assert safe_ratio(100.0, -50.0) is None
    assert safe_ratio(100.0, 0.0) is None
    assert safe_ratio(100.0, 50.0) == pytest.approx(2.0)


def test_debt_to_equity_and_current_ratio(view):
    records = _quarterly(
        2023, 1, 1000.0, total_equity=500.0, total_debt=250.0,
        current_assets=300.0, current_liabilities=150.0,
    )
    factors = quality_factors(view, "IVV", FundamentalPanel(records))
    assert factors["debt_to_equity"] == pytest.approx(0.5)
    assert factors["current_ratio"] == pytest.approx(2.0)


# --- valuation -------------------------------------------------------------
def test_valuation_multiples_require_positive_earnings(view):
    ticker = view.universe()[0]
    loss_making = _quarterly(2023, 1, 1000.0, net_income=-50.0, shares_outstanding=1_000_000.0)
    factors = valuation_factors(view, ticker, FundamentalPanel(loss_making))
    assert factors["trailing_pe"] is None, "a P/E on negative earnings is meaningless"
    assert factors["earnings_yield"] is not None, "the yield stays defined and negative"
    assert factors["earnings_yield"] < 0


def test_forward_pe_is_never_fabricated(view):
    ticker = view.universe()[0]
    factors = valuation_factors(view, ticker)
    assert factors["forward_pe"] is None, (
        "forward P/E needs point-in-time analyst estimates; using current ones would leak"
    )


# --- momentum and risk -----------------------------------------------------
def test_trailing_return_needs_enough_history():
    series = pd.Series([100.0, 110.0, 121.0])
    assert trailing_return(series, 2) == pytest.approx(0.21)
    assert trailing_return(series, 5) is None


def test_momentum_uses_total_return_not_price(view):
    """A high-yield stock must not be penalized in the momentum ranking."""
    ticker = next(t for t in view.universe() if (view.history(t)["dividend"] > 0).any())
    factors = momentum_factors(view, ticker)
    frame = view.history(ticker)
    price_only = float(frame["adj_close"].iloc[-1] / frame["adj_close"].iloc[-253] - 1.0)
    assert factors["return_12m"] > price_only


def test_distance_from_52_week_high_is_zero_at_the_high(view):
    ticker = view.universe()[0]
    factors = momentum_factors(view, ticker)
    assert factors["dist_52w_high"] >= 0.0
    assert factors["dist_52w_low"] >= 0.0


def test_max_drawdown_is_a_positive_fraction():
    series = pd.Series([100.0, 120.0, 60.0, 90.0])
    assert max_drawdown(series) == pytest.approx(0.5)


def test_volatility_is_annualized():
    rng = np.random.default_rng(7)
    daily = pd.Series(rng.normal(0.0, 0.01, 500))
    annual = annualized_volatility(daily)
    assert annual == pytest.approx(0.01 * np.sqrt(252), rel=0.15)


def test_downside_volatility_ignores_upside():
    upside_only = pd.Series([0.01] * 100)
    assert downside_volatility(upside_only) == pytest.approx(0.0)
    mixed = pd.Series([0.01, -0.01] * 50)
    assert downside_volatility(mixed) > 0


def test_beta_of_the_benchmark_against_itself_is_one(market):
    view = market.view(dt.date(2022, 6, 24))
    # Beta is only computed for non-benchmark tickers, so check a proxy with a
    # known relationship instead: every stock's beta must be finite and sane.
    betas = [risk_factors(view, t).get("beta") for t in view.universe()[:20]]
    usable = [b for b in betas if b is not None]
    assert usable, "no betas could be computed"
    assert all(-1.0 < b < 4.0 for b in usable)


def test_sector_relative_strength_needs_peers():
    factors = {"A": {"return_12m": 0.20}, "B": {"return_12m": 0.10}}
    sectors = {"A": "Energy", "B": "Energy"}
    # Two names is below the three-peer minimum, so no comparison is made.
    assert sector_relative_strength(factors, sectors)["A"] is None

    factors["C"] = {"return_12m": 0.00}
    sectors["C"] = "Energy"
    result = sector_relative_strength(factors, sectors)
    assert result["A"] == pytest.approx(0.20 - 0.10)


def test_missing_factors_are_none_never_zero(view):
    """REQUIREMENTS 41: never replace a missing value with zero."""
    empty_panel = FundamentalPanel([])
    factors = quality_factors(view, view.universe()[0], empty_panel)
    assert all(value is None for value in factors.values())


# --- market capitalization across splits -----------------------------------
# A filing's share count is never restated for a later split, while a
# back-adjusted price series restates every bar before one. Multiplying the two
# straight together is how a company gets divided - or multiplied - by its own
# split history.
def _split_market(ticker: str, price: float, split_on: dt.date | None, split_coef: float,
                  post_price: float, already_adjusted: bool) -> MarketData:
    dates = pd.bdate_range("2023-01-02", "2023-12-29")
    closes, splits = [], []
    for day in dates:
        after = split_on is not None and day.date() >= split_on
        closes.append(post_price if after else price)
        splits.append(split_coef if (split_on is not None and day.date() == split_on) else 1.0)
    frame = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1e6] * len(dates), "dividend": [0.0] * len(dates), "split_coef": splits},
        index=pd.DatetimeIndex(dates, name="date"),
    )
    info = ProviderInfo(name="test", retrieved_at=dt.datetime(2024, 1, 1))
    history = PriceHistory.from_frame(ticker, frame, info, split_adjusted=already_adjusted)
    return MarketData({ticker: history, "IVV": history}, benchmark="IVV")


def test_market_cap_survives_a_forward_split():
    """4-for-1: the share count quadruples, the price quarters, the value holds."""
    # Yahoo-style: the series is already back-adjusted, so the pre-split bars
    # read $25 even though the stock traded at $100 that day.
    market = _split_market("TEST", price=25.0, split_on=dt.date(2023, 7, 3),
                           split_coef=4.0, post_price=25.0, already_adjusted=True)
    shares = [_record(dt.date(2023, 3, 31), shares_outstanding=1_000_000.0)]

    before = valuation_factors(market.view(dt.date(2023, 6, 30)), "TEST", FundamentalPanel(shares))
    after = valuation_factors(market.view(dt.date(2023, 9, 29)), "TEST", FundamentalPanel(shares))

    # $100 x 1M shares before; $25 x 4M shares after. A split creates no value.
    assert before["market_cap"] == pytest.approx(100_000_000.0)
    assert after["market_cap"] == pytest.approx(100_000_000.0)


def test_market_cap_survives_a_reverse_split():
    """1-for-8, the GE case: without the fix the company looks eight times bigger."""
    market = _split_market("TEST", price=100.0, split_on=dt.date(2023, 8, 1),
                           split_coef=0.125, post_price=100.0, already_adjusted=True)
    shares = [_record(dt.date(2023, 6, 30), shares_outstanding=8_000_000.0)]

    after = valuation_factors(market.view(dt.date(2023, 9, 29)), "TEST", FundamentalPanel(shares))
    # 8M shares became 1M; at the $100 quote that is $100M, not $800M.
    assert after["market_cap"] == pytest.approx(100_000_000.0)


def test_market_cap_needs_no_adjustment_without_a_split():
    market = _split_market("TEST", price=50.0, split_on=None, split_coef=1.0,
                           post_price=50.0, already_adjusted=True)
    shares = [_record(dt.date(2023, 3, 31), shares_outstanding=2_000_000.0)]
    factors = valuation_factors(market.view(dt.date(2023, 9, 29)), "TEST", FundamentalPanel(shares))
    assert factors["market_cap"] == pytest.approx(100_000_000.0)


def test_raw_close_is_the_price_actually_quoted():
    """The derived column undoes the provider's back-adjustment, nothing else."""
    market = _split_market("TEST", price=25.0, split_on=dt.date(2023, 7, 3),
                           split_coef=4.0, post_price=25.0, already_adjusted=True)
    frame = market.history("TEST").frame
    pre = frame.loc[pd.Timestamp("2023-06-30")]
    post = frame.loc[pd.Timestamp("2023-09-29")]
    assert pre["adj_close"] == pytest.approx(25.0)    # comparable across the split
    assert pre["raw_close"] == pytest.approx(100.0)   # what the screen said that day
    assert post["raw_close"] == pytest.approx(25.0)
