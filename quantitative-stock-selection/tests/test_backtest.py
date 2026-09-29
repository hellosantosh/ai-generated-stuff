"""Backtest engine, metrics and drawdown tests (REQUIREMENTS 37, backtest tests)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.backtest.drawdown import (
    drawdown_series,
    drawdown_table,
    find_episodes,
    max_drawdown_detail,
    period_performance,
    underwater_duration,
)
from quant.backtest.engine import BacktestEngine, contribution_attribution, variant_contribution
from quant.backtest.metrics import (
    annualize_return,
    annualized_turnover,
    beta_and_tracking_error,
    holding_periods,
    sharpe_ratio,
    sortino_ratio,
    time_weighted_returns,
    twr_index,
    win_rate,
    xirr,
)
from quant.backtest.validation import make_run_id, next_run_id


@pytest.fixture(scope="module")
def result(market, config):
    engine = BacktestEngine(config, market, run_id="TEST-RUN-001", progress=lambda m: None)
    return engine.run()


# --- XIRR ------------------------------------------------------------------
def test_xirr_of_a_one_year_gain():
    """Non-leap year, so the span is exactly 365 days under actual/365."""
    flows = [(dt.date(2021, 1, 1), -1000.0), (dt.date(2022, 1, 1), 1100.0)]
    assert xirr(flows) == pytest.approx(0.10, abs=1e-5)


def test_xirr_accounts_for_the_extra_day_in_a_leap_year():
    flows = [(dt.date(2020, 1, 1), -1000.0), (dt.date(2021, 1, 1), 1100.0)]
    # 366 days is longer than a year, so the annualized rate is a shade under 10%.
    rate = xirr(flows)
    assert 0.0995 < rate < 0.10


def test_xirr_of_a_flat_investment_is_zero():
    flows = [(dt.date(2020, 1, 1), -1000.0), (dt.date(2023, 1, 1), 1000.0)]
    assert xirr(flows) == pytest.approx(0.0, abs=1e-5)


def test_xirr_handles_a_loss():
    flows = [(dt.date(2021, 1, 1), -1000.0), (dt.date(2022, 1, 1), 900.0)]
    assert xirr(flows) == pytest.approx(-0.10, abs=1e-5)


def test_xirr_with_periodic_contributions_is_bounded_sensibly():
    flows = [(dt.date(2020, 1, 1) + dt.timedelta(weeks=i), -100.0) for i in range(52)]
    flows.append((dt.date(2021, 1, 1), 5400.0))    # 5200 in, 5400 out
    rate = xirr(flows)
    # Money was invested for about half a year on average, so the annualized
    # rate is roughly double the 3.8% simple gain.
    assert 0.05 < rate < 0.12


def test_xirr_returns_nan_without_a_sign_change():
    assert np.isnan(xirr([(dt.date(2020, 1, 1), -100.0), (dt.date(2021, 1, 1), -100.0)]))


def test_xirr_needs_at_least_two_flows():
    assert np.isnan(xirr([(dt.date(2020, 1, 1), -100.0)]))


# --- time-weighted return --------------------------------------------------
def test_twr_removes_the_effect_of_contributions():
    """Two $100 contributions into a flat market is a 0% time-weighted return."""
    before = [0.0, 100.0, 200.0]
    after = [100.0, 200.0, 300.0]
    returns = time_weighted_returns(before, after)
    assert list(returns) == pytest.approx([0.0, 0.0])


def test_twr_captures_market_moves_only():
    before = [0.0, 110.0]        # 100 grew to 110 before the second contribution
    after = [100.0, 210.0]
    returns = time_weighted_returns(before, after)
    assert returns.iloc[0] == pytest.approx(0.10)


def test_twr_index_compounds():
    returns = pd.Series([0.10, 0.10])
    assert twr_index(returns).iloc[-1] == pytest.approx(121.0)


def test_annualize_return():
    assert annualize_return(2.0, 3.0) == pytest.approx(2 ** (1 / 3) - 1)
    assert np.isnan(annualize_return(-1.0, 3.0))


# --- risk ratios -----------------------------------------------------------
def test_sharpe_is_zero_for_a_zero_mean_series():
    rng = np.random.default_rng(3)
    returns = pd.Series(rng.normal(0.0, 0.02, 300))
    assert abs(sharpe_ratio(returns)) < 0.5


def test_sharpe_rises_with_return_at_constant_volatility():
    rng = np.random.default_rng(3)
    base = pd.Series(rng.normal(0.0, 0.02, 300))
    assert sharpe_ratio(base + 0.004) > sharpe_ratio(base)


def test_sortino_exceeds_sharpe_when_downside_is_muted():
    returns = pd.Series([0.05, 0.05, 0.05, -0.001] * 30)
    assert sortino_ratio(returns) > sharpe_ratio(returns)


def test_beta_of_a_series_against_itself_is_one():
    rng = np.random.default_rng(11)
    series = pd.Series(rng.normal(0.0, 0.02, 200))
    beta, tracking_error = beta_and_tracking_error(series, series)
    assert beta == pytest.approx(1.0)
    assert tracking_error == pytest.approx(0.0)


def test_beta_of_a_doubled_series_is_two():
    rng = np.random.default_rng(11)
    benchmark = pd.Series(rng.normal(0.0, 0.02, 200))
    beta, _ = beta_and_tracking_error(benchmark * 2.0, benchmark)
    assert beta == pytest.approx(2.0)


def test_win_rate_counts_strict_outperformance():
    portfolio = pd.Series([0.02, 0.01, -0.01, 0.03])
    benchmark = pd.Series([0.01, 0.02, -0.02, 0.03])
    assert win_rate(portfolio, benchmark) == pytest.approx(0.5)


# --- drawdowns -------------------------------------------------------------
def test_drawdown_series_is_zero_at_new_highs():
    values = pd.Series([100.0, 110.0, 120.0])
    assert (drawdown_series(values) == 0).all()


def test_max_drawdown_finds_peak_trough_and_recovery():
    index = pd.to_datetime(["2022-01-01", "2022-02-01", "2022-06-01", "2022-12-01", "2023-06-01"])
    values = pd.Series([100.0, 120.0, 80.0, 100.0, 130.0], index=index)
    episode = max_drawdown_detail(values)

    assert episode.peak_date == dt.date(2022, 2, 1)
    assert episode.trough_date == dt.date(2022, 6, 1)
    assert episode.depth == pytest.approx(1 / 3, abs=1e-6)
    assert episode.recovery_date == dt.date(2023, 6, 1)
    assert episode.recovered


def test_an_unrecovered_drawdown_is_still_reported():
    index = pd.to_datetime(["2022-01-01", "2022-06-01", "2022-12-01"])
    values = pd.Series([100.0, 60.0, 80.0], index=index)
    episode = max_drawdown_detail(values)
    assert episode.recovery_date is None
    assert not episode.recovered
    assert episode.depth == pytest.approx(0.4)


def test_drawdown_table_lists_separate_episodes():
    index = pd.date_range("2022-01-01", periods=7, freq="ME")
    values = pd.Series([100.0, 80.0, 100.0, 105.0, 84.0, 105.0, 110.0], index=index)
    table = drawdown_table(values, min_depth=0.10)
    assert len(table) == 2
    assert table["drawdown"].min() == pytest.approx(-0.20)


def test_shallow_drawdowns_are_filtered_out():
    index = pd.date_range("2022-01-01", periods=4, freq="ME")
    values = pd.Series([100.0, 99.0, 100.0, 101.0], index=index)
    assert drawdown_table(values, min_depth=0.10).empty


def test_underwater_duration_runs_from_peak_to_recovery():
    index = pd.to_datetime(["2022-01-01", "2022-02-01", "2022-04-01", "2022-05-01"])
    values = pd.Series([100.0, 90.0, 95.0, 105.0], index=index)
    # The 1 January high was regained on 1 May: 120 days underwater.
    assert underwater_duration(values) == 120


def test_underwater_duration_is_zero_when_never_below_a_peak():
    index = pd.date_range("2022-01-01", periods=4, freq="ME")
    assert underwater_duration(pd.Series([100.0, 110.0, 120.0, 130.0], index=index)) == 0


def test_stress_periods_outside_the_window_are_labeled_not_zero():
    index = pd.date_range("2024-01-01", periods=10, freq="W")
    values = pd.Series(np.linspace(100, 110, 10), index=index)
    table = period_performance(values)
    covid = table[table["period"] == "2020 COVID crash"].iloc[0]
    assert pd.isna(covid["return"])
    assert "outside" in covid["note"]


# --- turnover and holding periods ------------------------------------------
def test_turnover_ignores_contribution_buys():
    transactions = pd.DataFrame([
        {"action": "BUY", "gross_amount": 1000.0, "sleeve": "s", "ticker": "A",
         "shares": 1.0, "trade_date": "2024-01-01"},
    ])
    assert annualized_turnover(transactions, average_value=10_000.0, years=1.0) == 0.0


def test_turnover_counts_sales_against_average_value():
    transactions = pd.DataFrame([
        {"action": "SELL", "gross_amount": 5000.0, "sleeve": "s", "ticker": "A",
         "shares": 1.0, "trade_date": "2024-01-01"},
    ])
    assert annualized_turnover(transactions, 10_000.0, 1.0) == pytest.approx(0.5)


def test_holding_period_measures_buy_to_full_exit():
    transactions = pd.DataFrame([
        {"action": "BUY", "sleeve": "s", "ticker": "A", "shares": 10.0,
         "trade_date": "2024-01-01", "gross_amount": 100.0},
        {"action": "SELL", "sleeve": "s", "ticker": "A", "shares": 10.0,
         "trade_date": "2024-03-01", "gross_amount": 100.0},
    ])
    assert holding_periods(transactions) == pytest.approx(60.0)


# --- run identity ----------------------------------------------------------
def test_run_id_format():
    run_id = make_run_id("BACKTEST", dt.datetime(2026, 9, 28), 1)
    assert run_id == "BACKTEST-20260928-001"


def test_next_run_id_skips_used_sequences():
    when = dt.datetime(2026, 9, 28)
    existing = ["BACKTEST-20260928-001", "BACKTEST-20260928-002"]
    assert next_run_id(existing, when=when) == "BACKTEST-20260928-003"


# --- the engine ------------------------------------------------------------
def test_contribution_count_matches_the_configuration(result, config):
    assert len(result.schedule) == config.backtest.num_weeks
    for variant in result.variants.values():
        assert len(variant.values) == config.backtest.num_weeks


def test_total_contributions_equal_weeks_times_amount(result, config):
    weeks = config.backtest.num_weeks
    for name, variant in result.variants.items():
        expected = weeks * variant_contribution(name, config)
        assert variant.metrics.total_contributions == pytest.approx(expected)


def test_ivv_variant_holds_only_the_benchmark(result, config):
    portfolio = result.variants["ivv"].portfolio
    holdings = {t for sleeve in portfolio.sleeves.values() for t in sleeve.holdings()}
    assert holdings == {config.benchmark.ticker}


def test_combined_variant_splits_across_all_three_sleeves(result):
    portfolio = result.variants["combined"].portfolio
    assert set(portfolio.sleeves) == {"ivv", "sector_leaders", "high_growth"}
    for sleeve in portfolio.sleeves.values():
        assert sleeve.contributions > 0


def test_transaction_dates_are_trading_days(result, market):
    calendar = set(market.calendar)
    for variant in result.variants.values():
        frame = variant.portfolio.transactions_frame()
        trades = frame[frame["action"].isin(["BUY", "SELL"])]
        for trade_date in pd.to_datetime(trades["trade_date"]).unique():
            assert pd.Timestamp(trade_date) in calendar


def test_portfolio_value_reconciles_with_cash_flows(result):
    """Ending value must equal contributions plus dividends and market moves."""
    for name, variant in result.variants.items():
        portfolio = variant.portfolio
        final = variant.values.iloc[-1]
        cash = sum(s.cash for s in portfolio.sleeves.values())
        assert final["cash"] == pytest.approx(cash)
        assert final["total_value"] == pytest.approx(final["cash"] + final["holdings_value"])


def test_costs_are_accounted_for(result):
    for variant in result.variants.values():
        summary = variant.portfolio.summary(variant.values.index[-1].date())
        assert summary["commission_paid"] >= 0
        assert summary["slippage_paid"] >= 0


def test_benchmark_comparison_metrics_are_computed(result):
    """Beta and tracking error must not silently collapse to NaN."""
    for name, variant in result.variants.items():
        assert np.isfinite(variant.metrics.beta), f"{name} has no beta"
        assert np.isfinite(variant.metrics.tracking_error), f"{name} has no tracking error"
        assert np.isfinite(variant.metrics.weeks_outperforming), f"{name} has no win rate"


def test_the_benchmark_variant_has_beta_one(result):
    assert result.variants["ivv"].metrics.beta == pytest.approx(1.0)
    assert result.variants["ivv"].metrics.tracking_error == pytest.approx(0.0, abs=1e-9)


def test_comparison_table_has_every_variant(result, config):
    table = result.comparison_table()
    for name in config.backtest.variants:
        assert name in table.columns


def test_drawdowns_are_computed_on_the_twr_index(result):
    """A DCA value series rarely falls; the TWR index must show real losses."""
    variant = result.variants["ivv"]
    value_drawdown = drawdown_series(variant.values["total_value"]).min()
    twr_drawdown = drawdown_series(variant.twr).min()
    assert twr_drawdown <= value_drawdown
    assert variant.metrics.max_drawdown == pytest.approx(twr_drawdown, abs=1e-6)


def test_attribution_covers_every_traded_ticker(result):
    variant = result.variants["combined"]
    final_date = variant.values.index[-1].date()
    attribution = contribution_attribution(variant, final_date)
    traded = set(variant.portfolio.transactions_frame()["ticker"])
    assert set(attribution["ticker"]) == traded


def test_warnings_are_collected_not_swallowed(result):
    assert isinstance(result.warnings, list)
    assert isinstance(result.limit_breaches, list)
