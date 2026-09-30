"""Portfolio, DCA and rebalancing tests (REQUIREMENTS 37, portfolio tests)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.config import TransactionCostConfig
from quant.data.store import MarketData
from quant.data.types import PriceHistory, ProviderInfo
from quant.errors import ConfigError
from quant.portfolio.dca import ContributionEvent, build_schedule, split_contribution
from quant.portfolio.portfolio import Portfolio
from quant.portfolio.rebalance import (
    RebalanceCalendar,
    check_limits,
    is_rebalance_date,
    target_weights,
    threshold_triggered,
)

FREE = TransactionCostConfig(commission_per_trade=0.0, slippage_bps=0.0)


def _flat_market(price: float = 100.0, dividend_on=None, days: int = 60) -> MarketData:
    """A market where one stock trades at a constant price, for exact arithmetic."""
    dates = pd.bdate_range("2024-01-01", periods=days)
    dividends = [0.0] * days
    if dividend_on is not None:
        dividends[dividend_on] = 1.0
    frame = pd.DataFrame(
        {
            "open": [price] * days,
            "high": [price] * days,
            "low": [price] * days,
            "close": [price] * days,
            "volume": [1e6] * days,
            "dividend": dividends,
            "split_coef": [1.0] * days,
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    info = ProviderInfo(name="test", retrieved_at=dt.datetime(2024, 1, 1))
    histories = {t: PriceHistory.from_frame(t, frame.copy(), info) for t in ("IVV", "AAA", "BBB")}
    return MarketData(histories, benchmark="IVV")


def _portfolio(market: MarketData, costs: TransactionCostConfig = FREE) -> Portfolio:
    return Portfolio("test", ["ivv"], costs, market)


# --- contributions and fractional shares -----------------------------------
def test_a_1000_dollar_contribution_buys_fractional_shares():
    market = _flat_market(price=123.45)
    portfolio = _portfolio(market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, trade_date)
    portfolio.buy("ivv", "IVV", 1000.0, trade_date, dt.date(2024, 1, 2), "adj_open")

    position = portfolio.sleeves["ivv"].positions["IVV"]
    assert position.shares == pytest.approx(1000.0 / 123.45)
    assert position.shares % 1 != 0, "fractional shares must be supported"
    assert portfolio.sleeves["ivv"].cash == pytest.approx(0.0)


def test_portfolio_value_is_shares_times_price_plus_cash():
    market = _flat_market(price=50.0)
    portfolio = _portfolio(market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, trade_date)
    portfolio.buy("ivv", "IVV", 600.0, trade_date, dt.date(2024, 1, 2), "adj_open")

    assert portfolio.value(trade_date) == pytest.approx(1000.0)
    assert portfolio.sleeves["ivv"].cash == pytest.approx(400.0)
    assert portfolio.sleeves["ivv"].positions["IVV"].shares == pytest.approx(12.0)


def test_buying_never_overdraws_the_sleeve():
    market = _flat_market()
    portfolio = _portfolio(market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 100.0, trade_date)
    portfolio.buy("ivv", "IVV", 500.0, trade_date, dt.date(2024, 1, 2), "adj_open")
    assert portfolio.sleeves["ivv"].cash >= 0.0
    assert portfolio.sleeves["ivv"].cash == pytest.approx(0.0)


def test_contributions_accumulate():
    market = _flat_market()
    portfolio = _portfolio(market)
    for day in (3, 4, 5):
        portfolio.contribute("ivv", 1000.0, dt.date(2024, 1, day))
    assert portfolio.total_contributions == pytest.approx(3000.0)


# --- transaction costs -----------------------------------------------------
def test_slippage_moves_the_fill_against_the_trade():
    market = _flat_market(price=100.0)
    costs = TransactionCostConfig(commission_per_trade=0.0, slippage_bps=5.0)
    portfolio = Portfolio("t", ["ivv"], costs, market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, trade_date)
    buy = portfolio.buy("ivv", "IVV", 1000.0, trade_date, dt.date(2024, 1, 2), "adj_open")

    assert buy.price == pytest.approx(100.0 * 1.0005)
    assert buy.slippage > 0
    # Fewer shares than a frictionless fill would have bought.
    assert buy.shares < 10.0


def test_commission_is_taken_out_of_the_contribution():
    market = _flat_market(price=100.0)
    costs = TransactionCostConfig(commission_per_trade=5.0, slippage_bps=0.0)
    portfolio = Portfolio("t", ["ivv"], costs, market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, trade_date)
    buy = portfolio.buy("ivv", "IVV", 1000.0, trade_date, dt.date(2024, 1, 2), "adj_open")

    assert buy.commission == pytest.approx(5.0)
    assert buy.shares == pytest.approx(995.0 / 100.0)
    assert portfolio.sleeves["ivv"].cash == pytest.approx(0.0)


def test_selling_reduces_cost_basis_proportionally():
    market = _flat_market(price=100.0)
    portfolio = _portfolio(market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, trade_date)
    portfolio.buy("ivv", "IVV", 1000.0, trade_date, dt.date(2024, 1, 2), "adj_open")
    portfolio.sell("ivv", "IVV", 5.0, trade_date, dt.date(2024, 1, 2), "adj_open")

    position = portfolio.sleeves["ivv"].positions["IVV"]
    assert position.shares == pytest.approx(5.0)
    assert position.cost_basis == pytest.approx(500.0)
    assert portfolio.sleeves["ivv"].cash == pytest.approx(500.0)


def test_selling_everything_closes_the_position():
    market = _flat_market(price=100.0)
    portfolio = _portfolio(market)
    trade_date = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, trade_date)
    portfolio.buy("ivv", "IVV", 1000.0, trade_date, dt.date(2024, 1, 2), "adj_open")
    portfolio.sell_all("ivv", "IVV", trade_date, dt.date(2024, 1, 2), "adj_open")
    assert "IVV" not in portfolio.sleeves["ivv"].positions
    assert portfolio.sleeves["ivv"].cash == pytest.approx(1000.0)


# --- dividends -------------------------------------------------------------
def test_dividends_are_credited_once_per_share():
    market = _flat_market(price=100.0, dividend_on=10)
    portfolio = _portfolio(market)
    start = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, start)
    portfolio.buy("ivv", "IVV", 1000.0, start, dt.date(2024, 1, 2), "adj_open")

    portfolio.credit_dividends(start, start)          # establishes the watermark
    ex_date = market.calendar[20].date()
    credited = portfolio.credit_dividends(ex_date, start)

    assert credited == pytest.approx(10.0)            # 10 shares x $1
    assert portfolio.sleeves["ivv"].dividends_received == pytest.approx(10.0)


def test_dividends_are_not_credited_twice():
    market = _flat_market(price=100.0, dividend_on=10)
    portfolio = _portfolio(market)
    start = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, start)
    portfolio.buy("ivv", "IVV", 1000.0, start, dt.date(2024, 1, 2), "adj_open")
    portfolio.credit_dividends(start, start)

    first = portfolio.credit_dividends(market.calendar[20].date(), start)
    second = portfolio.credit_dividends(market.calendar[30].date(), start)
    assert first == pytest.approx(10.0)
    assert second == pytest.approx(0.0)


def test_dividend_cash_raises_portfolio_value():
    market = _flat_market(price=100.0, dividend_on=10)
    portfolio = _portfolio(market)
    start = dt.date(2024, 1, 3)
    portfolio.contribute("ivv", 1000.0, start)
    portfolio.buy("ivv", "IVV", 1000.0, start, dt.date(2024, 1, 2), "adj_open")
    portfolio.credit_dividends(start, start)

    later = market.calendar[25].date()
    portfolio.credit_dividends(later, start)
    # Price never moved, so all of the gain is the dividend.
    assert portfolio.value(later) == pytest.approx(1010.0)


# --- schedule --------------------------------------------------------------
def test_execution_is_always_after_the_decision(market, config):
    schedule = build_schedule(market, config)
    assert schedule
    for event in schedule:
        assert event.execution_date > event.decision_date


def test_schedule_rejects_a_same_day_fill():
    with pytest.raises(ConfigError, match="strictly after"):
        ContributionEvent(0, dt.date(2024, 1, 5), dt.date(2024, 1, 5), 1000.0, "adj_open")


def test_schedule_honors_the_requested_week_count(market, config):
    schedule = build_schedule(market, config)
    assert len(schedule) == config.backtest.num_weeks


def test_schedule_uses_the_configured_contribution_day(market, config):
    schedule = build_schedule(market, config)
    # Decisions land on Friday unless that Friday was a market holiday.
    weekdays = {event.decision_date.weekday() for event in schedule}
    assert 4 in weekdays
    assert all(day <= 4 for day in weekdays), "a decision landed on a weekend"


def test_same_close_convention_also_avoids_overlap(market, config, project_root):
    from quant.config import load_config

    alternative = load_config(
        project_root / "config",
        overrides={"settings": {
            "backtest": {"execution": "same_close", "num_weeks": 20,
                         "end_date": dt.date(2024, 12, 31), "start_date": None},
            "data": {"provider": "synthetic", "fundamentals_provider": "synthetic"},
            "universe": {"source": "synthetic"},
        }},
        project_root=project_root,
        allow_synthetic=True,
    )
    schedule = build_schedule(market, alternative)
    for event in schedule:
        assert event.execution_date > event.decision_date
        assert event.price_field == "adj_close"


def test_split_contribution_matches_the_configured_allocations(config):
    allocations = config.strategy.allocations
    split = split_contribution(1000.0, allocations, ["ivv", "sector_leaders", "high_growth"])
    assert split["ivv"] == pytest.approx(500.0)
    assert split["sector_leaders"] == pytest.approx(300.0)
    assert split["high_growth"] == pytest.approx(200.0)
    assert sum(split.values()) == pytest.approx(1000.0)


def test_a_single_sleeve_receives_the_whole_amount(config):
    split = split_contribution(300.0, config.strategy.allocations, ["sector_leaders"])
    assert split["sector_leaders"] == pytest.approx(300.0)


# --- weighting -------------------------------------------------------------
def test_equal_weighting_matches_the_documented_arithmetic():
    """$300 across 11 sectors x 3 stocks is about $9.09 each."""
    tickers = [f"T{i}" for i in range(33)]
    weights = target_weights(tickers, "equal")
    assert sum(weights.values()) == pytest.approx(1.0)
    assert 300.0 * weights["T0"] == pytest.approx(9.0909, abs=0.001)


def test_score_weighting_favors_higher_scores():
    weights = target_weights(["A", "B"], "score", scores={"A": 90.0, "B": 30.0})
    assert weights["A"] == pytest.approx(0.75)


def test_volatility_weighting_is_inverse():
    weights = target_weights(["A", "B"], "volatility", volatilities={"A": 0.10, "B": 0.20})
    assert weights["A"] == pytest.approx(2 / 3)


def test_weighting_degrades_to_equal_when_inputs_are_missing():
    weights = target_weights(["A", "B"], "score", scores={"A": 90.0})
    assert weights["A"] == pytest.approx(0.5)


def test_unknown_weighting_method_is_rejected():
    with pytest.raises(ConfigError, match="unknown weighting method"):
        target_weights(["A"], "astrology")


# --- rebalancing -----------------------------------------------------------
def test_first_contribution_always_trades():
    assert is_rebalance_date("quarterly", dt.date(2024, 1, 5), None)


def test_quarterly_rebalance_fires_once_per_quarter():
    assert not is_rebalance_date("quarterly", dt.date(2024, 2, 9), dt.date(2024, 1, 5))
    assert is_rebalance_date("quarterly", dt.date(2024, 4, 5), dt.date(2024, 1, 5))


def test_monthly_rebalance_fires_on_a_new_month():
    assert not is_rebalance_date("monthly", dt.date(2024, 1, 26), dt.date(2024, 1, 5))
    assert is_rebalance_date("monthly", dt.date(2024, 2, 2), dt.date(2024, 1, 5))


def test_weekly_rebalance_always_fires():
    assert is_rebalance_date("weekly", dt.date(2024, 1, 12), dt.date(2024, 1, 5))


def test_annual_rebalance_spans_the_year():
    assert not is_rebalance_date("annual", dt.date(2024, 11, 1), dt.date(2024, 1, 5))
    assert is_rebalance_date("annual", dt.date(2025, 1, 3), dt.date(2024, 1, 5))


def test_rebalance_calendar_tracks_state():
    calendar = RebalanceCalendar("quarterly")
    assert calendar.due(dt.date(2024, 1, 5))
    calendar.mark(dt.date(2024, 1, 5))
    assert not calendar.due(dt.date(2024, 3, 1))
    assert calendar.due(dt.date(2024, 4, 1))


def test_threshold_rebalance_fires_on_weight_drift(config):
    triggered, reason = threshold_triggered(
        config.sector_strategy,
        current_weights={"A": 0.80, "B": 0.20},
        target_holdings=["A", "B"],
        current_ranks={"A": 1, "B": 2},
    )
    assert triggered
    assert "drifted" in reason


def test_threshold_rebalance_fires_on_a_rank_breach(config):
    triggered, reason = threshold_triggered(
        config.sector_strategy,
        current_weights={"A": 0.5, "B": 0.5},
        target_holdings=["A", "B"],
        current_ranks={"A": 1, "B": 99},
    )
    assert triggered
    assert "rank" in reason


# --- limits ----------------------------------------------------------------
def test_concentration_limits_are_reported_with_stable_keys():
    report = check_limits(
        weights={"A": 0.30, "B": 0.05},
        sectors={"A": "Energy", "B": "Energy"},
        max_single=0.10, max_sector=0.20, max_positions=10,
    )
    assert report
    keys = [key for key, _ in report.items()]
    assert "single_stock:A" in keys
    assert "sector:Energy" in keys
    # The key omits the percentage, so a repeat breach dedupes cleanly.
    assert all("%" not in key for key in keys)


def test_no_breach_when_within_limits():
    report = check_limits({"A": 0.05}, {"A": "Energy"}, 0.10, 0.20, 10)
    assert not report
