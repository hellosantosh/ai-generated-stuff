"""Backtest integrity tests (REQUIREMENTS 22, 38).

REQUIREMENTS 38 calls this the highest-priority requirement and names the
exact scenario:

    Decision date:   2021-06-30
    Company filing:  2021-07-15
    The July 15 filing must NOT affect the June 30 decision.

The tests below implement that literally - inject a filing dated after the
decision, re-run the selection, and assert the output is byte-for-byte
identical - and then generalize it to prices, sector changes and index
membership.
"""

from __future__ import annotations

import copy
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.data.store import MarketData, PointInTimeView, SectorAssignment, SectorMap
from quant.data.types import FundamentalRecord, FundamentalSeries, PriceHistory, ProviderInfo
from quant.errors import DataError, LookAheadError
from quant.factors import compute_universe_factors
from quant.ranking import rank_and_select
from quant.backtest.validation import verify_execution_order, verify_point_in_time

DECISION = dt.date(2021, 6, 30)
FILING = dt.date(2021, 7, 15)


def _rank(market: MarketData, config, as_of: dt.date) -> pd.DataFrame:
    view = market.view(as_of, strict=True)
    factors, _ = compute_universe_factors(view, view.universe(), config)
    result = rank_and_select(factors, config, as_of)
    return result.table[["sleeve", "rank", "total_score"]].sort_index()


def _clone(market: MarketData) -> MarketData:
    """A shallow clone whose fundamentals can be mutated independently."""
    fundamentals = {
        ticker: FundamentalSeries(ticker, list(series.records))
        for ticker, series in market.fundamentals.items()
    }
    return MarketData(
        prices=dict(market.prices),
        fundamentals=fundamentals,
        sectors=market.sectors,
        universe=market.universe,
        benchmark=market.benchmark,
        company_names=market.company_names,
        strict=True,
    )


# --- the named scenario ----------------------------------------------------
def test_a_later_filing_does_not_change_an_earlier_decision(market, config):
    """The July 15 filing must not affect the June 30 decision."""
    baseline = _rank(market, config, DECISION)

    poisoned = _clone(market)
    target = sorted(poisoned.fundamentals)[0]
    # A spectacular quarter, filed two weeks after the decision date.
    poisoned.fundamentals[target].add(
        FundamentalRecord(
            ticker=target,
            period_end_date=dt.date(2021, 6, 30),
            fiscal_period="Q2",
            filing_date=FILING,
            data_available_date=FILING,
            metrics={
                "revenue": 1e12, "net_income": 5e11, "eps_diluted": 500.0,
                "gross_profit": 9e11, "operating_income": 6e11,
                "operating_cash_flow": 7e11, "capital_expenditures": 1e9,
                "free_cash_flow": 7e11, "total_equity": 1e12, "total_assets": 2e12,
                "shares_outstanding": 1e9,
            },
            provider="poison",
        )
    )

    after = _rank(poisoned, config, DECISION)
    pd.testing.assert_frame_equal(
        baseline, after,
        obj=f"ranking at {DECISION} changed after injecting a filing dated {FILING}",
    )


def test_the_same_filing_does_change_a_later_decision(market, config):
    """Control: the guard must block the future, not block everything.

    The date is chosen so the injected 15 July filing is the only visible
    report for that fiscal quarter. Later than the provider's own Q2 filing
    (around late July to mid August) and that genuine report would supersede
    the injected one, since the latest *visible* filing for a period wins -
    and the test would pass for the wrong reason.
    """
    later = dt.date(2021, 7, 20)
    baseline = _rank(market, config, later)

    poisoned = _clone(market)
    target = sorted(poisoned.fundamentals)[0]
    poisoned.fundamentals[target].add(
        FundamentalRecord(
            ticker=target, period_end_date=dt.date(2021, 6, 30), fiscal_period="Q2",
            filing_date=FILING, data_available_date=FILING,
            metrics={
                "revenue": 1e12, "net_income": 5e11, "eps_diluted": 500.0,
                "gross_profit": 9e11, "operating_income": 6e11,
                "operating_cash_flow": 7e11, "capital_expenditures": 1e9,
                "free_cash_flow": 7e11, "total_equity": 1e12, "total_assets": 2e12,
                "shares_outstanding": 1e9,
            },
            provider="poison",
        )
    )
    # Confirm the premise: no genuine Q2 report is visible yet.
    visible = poisoned.fundamentals[target].available_as_of(later)
    q2_filings = [r for r in visible if r.period_end_date == dt.date(2021, 6, 30)]
    assert len(q2_filings) == 1 and q2_filings[0].provider == "poison"

    after = _rank(poisoned, config, later)
    assert not baseline.equals(after), (
        "a filing dated before the decision had no effect, so the test above proves nothing"
    )


def test_fiscal_period_end_alone_does_not_make_data_visible(market):
    """The filing date decides visibility, not the period end (REQUIREMENTS 38)."""
    view = market.view(DECISION, strict=True)
    ticker = sorted(market.fundamentals)[0]
    for record in view.fundamentals(ticker):
        assert record.data_available_date <= DECISION
        assert record.filing_date <= DECISION
    # And a record whose period ended before the decision but was filed after
    # it is excluded.
    series = FundamentalSeries("X")
    series.add(FundamentalRecord("X", dt.date(2021, 6, 30), "Q2", FILING, FILING, {"revenue": 1.0}))
    assert series.available_as_of(DECISION) == []
    assert len(series.available_as_of(FILING)) == 1


def test_a_record_available_before_it_was_filed_is_rejected():
    with pytest.raises(DataError, match="precedes"):
        FundamentalRecord(
            ticker="X", period_end_date=dt.date(2021, 3, 31), fiscal_period="Q1",
            filing_date=dt.date(2021, 5, 1), data_available_date=dt.date(2021, 4, 1),
            metrics={"revenue": 1.0},
        )


def test_a_record_available_before_its_period_ended_is_rejected():
    with pytest.raises(DataError, match="precedes"):
        FundamentalRecord(
            ticker="X", period_end_date=dt.date(2021, 6, 30), fiscal_period="Q2",
            filing_date=dt.date(2021, 6, 1), data_available_date=dt.date(2021, 6, 1),
            metrics={"revenue": 1.0},
        )


# --- prices ----------------------------------------------------------------
def test_a_view_never_returns_a_bar_after_its_decision_date(market):
    view = market.view(DECISION, strict=True)
    for ticker in view.universe()[:25]:
        frame = view.history(ticker)
        assert frame.index.max() <= pd.Timestamp(DECISION), f"{ticker} leaked a future bar"


def test_future_prices_do_not_change_an_earlier_ranking(market, config):
    """Appending later bars must not move the decision-date ranking."""
    baseline = _rank(market, config, DECISION)
    # The full series already extends years past DECISION, so this is checking
    # the truncation itself: a view built on a shorter series must agree.
    truncated_prices = {}
    for ticker, history in market.prices.items():
        frame = history.frame.loc[: pd.Timestamp(DECISION)]
        truncated_prices[ticker] = PriceHistory.from_frame(
            ticker, frame[["open", "high", "low", "close", "volume", "dividend", "split_coef"]],
            history.provider,
        )
    truncated = MarketData(
        prices=truncated_prices, fundamentals=market.fundamentals, sectors=market.sectors,
        universe=market.universe, benchmark=market.benchmark,
        company_names=market.company_names, strict=True,
    )
    after = _rank(truncated, config, DECISION)
    pd.testing.assert_frame_equal(
        baseline, after,
        obj="ranking differed between the full series and one truncated at the decision date",
    )


def test_requesting_a_future_price_raises_in_strict_mode(market):
    view = market.view(DECISION, strict=True)
    ticker = view.universe()[0]
    with pytest.raises(LookAheadError, match="look-ahead"):
        view.price_on(ticker, DECISION + dt.timedelta(days=30))


def test_non_strict_mode_warns_instead_of_raising(market, caplog):
    view = market.view(DECISION, strict=False)
    ticker = view.universe()[0]
    future = market.history(ticker).index[market.history(ticker).index > pd.Timestamp(DECISION)][0].date()
    with caplog.at_level("WARNING"):
        view.price_on(ticker, future)
    assert any("look-ahead" in record.message for record in caplog.records)


# --- classification and membership -----------------------------------------
def test_a_future_sector_change_does_not_apply_retroactively():
    sectors = SectorMap(point_in_time=True)
    sectors.add(SectorAssignment("X", "Industrials", dt.date(2010, 1, 1), dt.date(2021, 9, 30)))
    sectors.add(SectorAssignment("X", "Information Technology", dt.date(2021, 10, 1), None))
    assert sectors.sector_of("X", DECISION) == "Industrials"


def test_a_stock_added_to_the_index_later_is_not_in_an_earlier_universe(market):
    from quant.data.store import UniverseMembership

    membership = UniverseMembership(name="t", survivorship_free=True)
    membership.add("LATER", dt.date(2023, 1, 1), None)
    membership.add("ALWAYS", dt.date(2010, 1, 1), None)
    assert membership.members_on(DECISION) == ["ALWAYS"]


# --- verification helpers --------------------------------------------------
def test_verify_point_in_time_passes_on_clean_data(market):
    view = market.view(DECISION, strict=True)
    assert verify_point_in_time(view, view.universe()[:20], strict=True) == []


def test_verify_point_in_time_catches_an_injected_future_filing(market):
    poisoned = _clone(market)
    ticker = sorted(poisoned.fundamentals)[0]
    # Bypass the constructor guard to simulate a provider that lies.
    bad = FundamentalRecord(
        ticker=ticker, period_end_date=dt.date(2021, 6, 30), fiscal_period="Q2",
        filing_date=FILING, data_available_date=FILING, metrics={"revenue": 1.0},
    )
    object.__setattr__(bad, "data_available_date", DECISION - dt.timedelta(days=1))
    poisoned.fundamentals[ticker].add(bad)

    view = poisoned.view(DECISION, strict=True)
    with pytest.raises(LookAheadError, match="was filed"):
        verify_point_in_time(view, [ticker], strict=True)


def test_verify_execution_order_catches_a_same_day_fill():
    class Event:
        def __init__(self, index, decision, execution):
            self.index, self.decision_date, self.execution_date = index, decision, execution

    problems = verify_execution_order([Event(0, DECISION, DECISION)])
    assert len(problems) == 1
    assert "not" in problems[0]


# --- the engine itself -----------------------------------------------------
def test_backtest_decisions_precede_their_fills(market, config):
    from quant.backtest.engine import BacktestEngine

    engine = BacktestEngine(config, market, run_id="LOOKAHEAD-TEST", progress=lambda m: None)
    result = engine.run(variants=["combined"])
    for event in result.schedule:
        assert event.execution_date > event.decision_date

    transactions = result.variants["combined"].portfolio.transactions_frame()
    trades = transactions[transactions["action"].isin(["BUY", "SELL"])]
    assert not trades.empty
    assert (
        pd.to_datetime(trades["trade_date"]) > pd.to_datetime(trades["decision_date"])
    ).all(), "a trade was filled on or before its own decision date"


def test_backtest_is_reproducible(market, config):
    """Same config, same data, same numbers (REQUIREMENTS 39)."""
    from quant.backtest.engine import BacktestEngine

    first = BacktestEngine(config, market, run_id="R1", progress=lambda m: None).run(
        variants=["combined"]
    )
    second = BacktestEngine(config, market, run_id="R2", progress=lambda m: None).run(
        variants=["combined"]
    )
    assert first.variants["combined"].metrics.ending_value == pytest.approx(
        second.variants["combined"].metrics.ending_value
    )
    pd.testing.assert_series_equal(
        first.variants["combined"].returns, second.variants["combined"].returns
    )


def test_data_snapshot_hash_is_stable(market):
    from quant.backtest.validation import snapshot_hash

    assert snapshot_hash(market) == snapshot_hash(market)
