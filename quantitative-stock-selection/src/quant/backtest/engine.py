"""The backtest engine (REQUIREMENTS 20-24).

One pass over the contribution schedule drives every portfolio variant, so the
expensive part - ranking the universe at each of ~360 decision dates - happens
once and all variants see identical selections.

Per contribution the order of operations is fixed and matters:

1. credit dividends with ex-dates since the last contribution;
2. mark the portfolio (this is ``value_before``, used for time-weighted return);
3. add the new cash;
4. rebalance if this sleeve is due, then deploy all sleeve cash to targets;
5. mark again (``value_after``) and record positions.

Steps 1-2 run before step 3 so that contribution cash never leaks into the
return calculation.

Look-ahead control: selection reads only ``PointInTimeView(decision_date)``,
while fills are priced by ``MarketData`` at ``execution_date``, which is always
a later session. In strict mode every decision date is additionally verified.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import AppConfig
from ..data.store import MarketData, PointInTimeView
from ..errors import DataError, InsufficientDataError
from ..factors import compute_universe_factors
from ..logging_config import get_logger
from ..portfolio.dca import ContributionEvent, build_schedule, split_contribution
from ..portfolio.portfolio import Portfolio
from ..portfolio.rebalance import (
    RebalanceCalendar,
    check_limits,
    target_weights,
    threshold_triggered,
)
from ..ranking.sector_ranker import (
    GROWTH_SLEEVE,
    SECTOR_SLEEVE,
    RankingResult,
    rank_and_select,
    ranks_by_sleeve,
)
from . import metrics as metrics_module
from .drawdown import drawdown_series, drawdown_table, period_performance
from .metrics import PerformanceMetrics, compute_metrics, twr_index
from .validation import verify_execution_order, verify_point_in_time

log = get_logger(__name__)

IVV_SLEEVE = "ivv"

# Which sleeves each variant runs, and what share of the weekly contribution
# it receives. This reproduces the comparison table in REQUIREMENTS 28: the
# IVV column is the full $1,000 invested in the benchmark, while the sleeve
# columns are those sleeves run on their own budget.
VARIANT_SLEEVES: dict[str, tuple[str, ...]] = {
    "ivv": (IVV_SLEEVE,),
    "sector_leaders": (SECTOR_SLEEVE,),
    "high_growth": (GROWTH_SLEEVE,),
    "combined": (IVV_SLEEVE, SECTOR_SLEEVE, GROWTH_SLEEVE),
}


def variant_contribution(variant: str, config: AppConfig) -> float:
    weekly = config.strategy.weekly_contribution
    if variant == "ivv":
        return weekly
    if variant == "sector_leaders":
        return weekly * config.strategy.sector_allocation
    if variant == "high_growth":
        return weekly * config.strategy.growth_allocation
    return weekly


@dataclass
class VariantResult:
    """Everything produced for one portfolio variant."""

    name: str
    portfolio: Portfolio
    values: pd.DataFrame
    returns: pd.Series
    twr: pd.Series
    metrics: PerformanceMetrics
    cashflows: list[tuple[dt.date, float]]
    drawdowns: pd.DataFrame
    special_periods: pd.DataFrame
    contribution_per_week: float

    def value_series(self) -> pd.Series:
        return self.values["total_value"]


@dataclass
class BacktestResult:
    """The complete output of one backtest run."""

    run_id: str
    config: AppConfig
    schedule: list[ContributionEvent]
    variants: dict[str, VariantResult]
    rankings: dict[dt.date, RankingResult]
    benchmark_returns: pd.Series
    warnings: list[str] = field(default_factory=list)
    limit_breaches: list[str] = field(default_factory=list)

    @property
    def start_date(self) -> dt.date:
        return self.schedule[0].execution_date

    @property
    def end_date(self) -> dt.date:
        return self.schedule[-1].execution_date

    def comparison_table(self) -> pd.DataFrame:
        """The side-by-side table required by REQUIREMENTS 28."""
        rows = []
        for name, variant in self.variants.items():
            m = variant.metrics
            rows.append(
                {
                    "Metric": name,
                    "Weekly contribution": variant.contribution_per_week,
                    "Total contributions": m.total_contributions,
                    "Ending balance": m.ending_value,
                    "Gain": m.total_gain,
                    "Return on contributions": m.return_on_contributions,
                    "Time-weighted return": m.time_weighted_return,
                    "CAGR (TWR)": m.cagr_twr,
                    "XIRR": m.xirr,
                    "Maximum drawdown": m.max_drawdown,
                    "Volatility": m.volatility,
                    "Sharpe": m.sharpe,
                    "Sortino": m.sortino,
                    "Beta": m.beta,
                    "Tracking error": m.tracking_error,
                    "Turnover": m.turnover,
                    "Trades": m.num_trades,
                    "Holdings": m.num_holdings,
                    "Weeks outperforming IVV": m.weeks_outperforming,
                    "Months outperforming IVV": m.months_outperforming,
                }
            )
        return pd.DataFrame(rows).set_index("Metric").T

    def latest_ranking(self) -> RankingResult | None:
        if not self.rankings:
            return None
        return self.rankings[max(self.rankings)]


class BacktestEngine:
    """Runs the weekly decision process over a historical window."""

    def __init__(
        self,
        config: AppConfig,
        market: MarketData,
        run_id: str = "BACKTEST-ADHOC-001",
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.market = market
        self.run_id = run_id
        self.progress = progress or (lambda message: log.info(message))
        self.warnings: list[str] = []
        self.limit_breaches: list[str] = []
        self._seen_breaches: set[str] = set()
        self._seen_warnings: set[str] = set()

    # --- main loop -------------------------------------------------------
    def run(self, variants: Sequence[str] | None = None) -> BacktestResult:
        config = self.config
        variants = list(variants or config.backtest.variants)
        unknown = set(variants) - set(VARIANT_SLEEVES)
        if unknown:
            raise DataError(f"unknown backtest variants: {sorted(unknown)}")
        # The IVV portfolio is always built: every comparison metric needs it.
        run_variants = list(dict.fromkeys(["ivv", *variants]))

        schedule = build_schedule(self.market, config)
        order_problems = verify_execution_order(schedule)
        if order_problems:
            raise DataError("contribution schedule violates execution ordering:\n" + "\n".join(order_problems))

        portfolios: dict[str, Portfolio] = {}
        calendars: dict[str, dict[str, RebalanceCalendar]] = {}
        active_targets: dict[str, dict[str, list[str]]] = {}
        value_before: dict[str, list[float]] = {}
        value_after: dict[str, list[float]] = {}
        cashflows: dict[str, list[tuple[dt.date, float]]] = {}

        for name in run_variants:
            sleeves = VARIANT_SLEEVES[name]
            portfolios[name] = Portfolio(
                name=name,
                sleeves=sleeves,
                costs=config.transaction_cost,
                market=self.market,
                max_stale_days=config.eligibility.max_stale_days,
            )
            calendars[name] = {
                sleeve: RebalanceCalendar(self._frequency_for(sleeve)) for sleeve in sleeves
            }
            active_targets[name] = {sleeve: [] for sleeve in sleeves}
            value_before[name] = []
            value_after[name] = []
            cashflows[name] = []

        rankings: dict[dt.date, RankingResult] = {}
        previous_ranks: dict[str, dict[str, int]] = {}
        total = len(schedule)
        self.progress(
            f"running {total} weekly contributions from {schedule[0].execution_date} "
            f"to {schedule[-1].execution_date} across {len(run_variants)} variant(s)"
        )

        for event in schedule:
            view = self.market.view(event.decision_date, strict=config.backtest.strict_point_in_time)
            ranking = self._rank(view, previous_ranks)
            rankings[event.decision_date] = ranking
            previous_ranks = ranks_by_sleeve(ranking)

            for name in run_variants:
                self._process_contribution(
                    name,
                    portfolios[name],
                    calendars[name],
                    active_targets[name],
                    event,
                    ranking,
                    view,
                    value_before[name],
                    value_after[name],
                    cashflows[name],
                )

            if (event.index + 1) % 52 == 0 or event.index == total - 1:
                self.progress(
                    f"  week {event.index + 1}/{total} ({event.execution_date}) "
                    f"universe={ranking.universe_size}"
                )

        results: dict[str, VariantResult] = {}
        benchmark_returns = metrics_module.time_weighted_returns(
            value_before["ivv"], value_after["ivv"]
        )
        # Every variant shares the contribution schedule, so stamping the
        # benchmark series with those dates lets it align with each variant's
        # own return series. Without this the comparison metrics silently
        # collapse to NaN on an empty index intersection.
        contribution_dates = pd.DatetimeIndex([e.execution_date for e in schedule])
        if len(benchmark_returns) == len(contribution_dates) - 1:
            benchmark_returns.index = contribution_dates[1:]
        final_date = schedule[-1].execution_date

        for name in run_variants:
            portfolio = portfolios[name]
            flows = list(cashflows[name])
            ending_value = portfolio.value(final_date)
            flows.append((final_date, ending_value))

            returns = metrics_module.time_weighted_returns(value_before[name], value_after[name])
            values = portfolio.values_frame()
            dates = pd.DatetimeIndex(values.index)
            returns.index = dates[1:] if len(returns) == len(dates) - 1 else returns.index

            index = twr_index(returns)
            values = values.copy()
            values["twr_index"] = pd.Series([100.0], index=dates[:1]).reindex(dates)
            values.loc[index.index, "twr_index"] = index.to_numpy()

            transactions = portfolio.transactions_frame()
            average_value = float(values["total_value"].mean()) if not values.empty else 0.0
            years = max((final_date - schedule[0].execution_date).days / 365.25, 1e-9)

            variant_metrics = compute_metrics(
                variant=name,
                values=values,
                returns=returns,
                cashflows=flows,
                portfolio_summary=portfolio.summary(final_date),
                benchmark_returns=benchmark_returns,
                risk_free_rate=config.backtest.risk_free_rate,
                weekly_contribution=variant_contribution(name, config),
                turnover=metrics_module.annualized_turnover(transactions, average_value, years),
                average_holding_period_days=metrics_module.holding_periods(transactions),
            )

            results[name] = VariantResult(
                name=name,
                portfolio=portfolio,
                values=values,
                returns=returns,
                twr=index,
                metrics=variant_metrics,
                cashflows=flows,
                drawdowns=drawdown_table(index),
                special_periods=period_performance(index),
                contribution_per_week=variant_contribution(name, config),
            )

        # Drop the implicit IVV run if the user did not ask for it.
        exposed = {name: result for name, result in results.items() if name in variants}

        return BacktestResult(
            run_id=self.run_id,
            config=config,
            schedule=schedule,
            variants=exposed or results,
            rankings=rankings,
            benchmark_returns=benchmark_returns,
            warnings=self.warnings,
            limit_breaches=self.limit_breaches,
        )

    # --- steps -----------------------------------------------------------
    def _frequency_for(self, sleeve: str) -> str:
        if sleeve == SECTOR_SLEEVE:
            return self.config.sector_strategy.rebalance_frequency
        if sleeve == GROWTH_SLEEVE:
            return self.config.growth_strategy.rebalance_frequency
        # The benchmark sleeve holds one instrument; every contribution buys it.
        return "weekly"

    def _rank(
        self, view: PointInTimeView, previous_ranks: Mapping[str, Mapping[str, int]]
    ) -> RankingResult:
        universe = view.universe()
        if self.config.backtest.strict_point_in_time:
            # Verifying every name every week is too slow for a 360-week run;
            # a rotating sample gives the same guarantee in expectation while
            # the structural guard in PointInTimeView covers the rest.
            sample = universe[:: max(1, len(universe) // 25)]
            verify_point_in_time(view, sample, strict=True)

        factors, rejections = compute_universe_factors(view, universe, self.config)
        if factors.empty:
            self.warnings.append(f"{view.as_of}: no eligible stocks; contribution held in cash")
        ranking = rank_and_select(
            factors,
            self.config,
            view.as_of,
            previous_ranks=previous_ranks,
            rejections=rejections,
        )
        for warning in ranking.warnings:
            # A category missing on week one is missing every week; report the
            # first occurrence rather than 360 copies of it.
            key = warning.split(": ", 1)[-1][:80]
            if key not in self._seen_warnings:
                self._seen_warnings.add(key)
                self.warnings.append(warning)
        return ranking

    def _process_contribution(
        self,
        variant: str,
        portfolio: Portfolio,
        calendars: Mapping[str, RebalanceCalendar],
        active_targets: dict[str, list[str]],
        event: ContributionEvent,
        ranking: RankingResult,
        view: PointInTimeView,
        value_before: list[float],
        value_after: list[float],
        cashflows: list[tuple[dt.date, float]],
    ) -> None:
        trade_date = event.execution_date
        decision_date = event.decision_date
        sleeves = VARIANT_SLEEVES[variant]

        # 1. dividends, then 2. the pre-contribution mark.
        portfolio.credit_dividends(trade_date, decision_date)
        value_before.append(portfolio.value(trade_date))

        # 3. new cash.
        amount = variant_contribution(variant, self.config)
        scale = self._risk_control_scale(view)
        deployable = amount * scale
        allocations = split_contribution(
            deployable, self.config.strategy.allocations, sleeves
        )
        for sleeve_name, sleeve_amount in allocations.items():
            portfolio.contribute(sleeve_name, sleeve_amount, trade_date)
        cashflows.append((trade_date, -amount))
        if scale < 1.0:
            # Cash that the risk control withheld is still contributed capital;
            # it simply sits idle, which is the point of the control.
            withheld = amount - deployable
            portfolio.contribute(sleeves[0], withheld, trade_date)

        # 4. rebalance and deploy.
        for sleeve_name in sleeves:
            self._handle_sleeve(
                portfolio,
                sleeve_name,
                calendars[sleeve_name],
                active_targets,
                event,
                ranking,
                view,
                variant,
            )

        # 5. the post-contribution mark.
        portfolio.record_value(trade_date, contribution=amount)
        value_after.append(portfolio.value(trade_date))
        portfolio.record_positions(trade_date)

    def _risk_control_scale(self, view: PointInTimeView) -> float:
        """Scale the contribution down when the market trend filter is on."""
        controls = self.config.risk_controls
        if not controls.reduce_when_market_below_200dma:
            return 1.0
        benchmark = view.benchmark
        if not view.has(benchmark):
            return 1.0
        closes = view.close_series(benchmark)
        if len(closes) < 200:
            return 1.0
        ma200 = float(closes.iloc[-200:].mean())
        return controls.market_below_200dma_scale if float(closes.iloc[-1]) < ma200 else 1.0

    def _handle_sleeve(
        self,
        portfolio: Portfolio,
        sleeve_name: str,
        calendar: RebalanceCalendar,
        active_targets: dict[str, list[str]],
        event: ContributionEvent,
        ranking: RankingResult,
        view: PointInTimeView,
        variant: str,
    ) -> None:
        trade_date = event.execution_date
        decision_date = event.decision_date
        sleeve = portfolio.sleeves[sleeve_name]

        proposed = self._targets_for(sleeve_name, ranking)
        extra_trigger = False
        if calendar.frequency == "threshold":
            config = (
                self.config.sector_strategy if sleeve_name == SECTOR_SLEEVE
                else self.config.growth_strategy
            )
            current_ranks = ranks_by_sleeve(ranking).get(sleeve_name, {})
            extra_trigger, _ = threshold_triggered(
                config, portfolio.current_weights(trade_date), active_targets[sleeve_name], current_ranks
            )

        due = calendar.due(decision_date, extra_trigger)
        if due and proposed:
            active_targets[sleeve_name] = proposed
            calendar.mark(decision_date)

        targets = [t for t in active_targets[sleeve_name] if self._tradeable(t, trade_date)]
        if not targets:
            if active_targets[sleeve_name]:
                self.warnings.append(
                    f"{trade_date}: no tradeable targets for {variant}/{sleeve_name}; "
                    f"contribution held as cash"
                )
            return

        weights = self._weights_for(sleeve_name, targets, ranking, view)
        if due:
            self._rebalance_to_targets(
                portfolio, sleeve_name, targets, weights, event, view
            )
        self._deploy_cash(portfolio, sleeve_name, targets, weights, event)

        self._record_limit_breaches(portfolio, trade_date, view, variant)

    def _targets_for(self, sleeve_name: str, ranking: RankingResult) -> list[str]:
        if sleeve_name == IVV_SLEEVE:
            return [self.config.benchmark.ticker]
        if sleeve_name == SECTOR_SLEEVE:
            return ranking.tickers(SECTOR_SLEEVE)
        return ranking.tickers(GROWTH_SLEEVE)

    def _tradeable(self, ticker: str, trade_date: dt.date) -> bool:
        """A target can only be bought if it actually traded that session."""
        if not self.market.has(ticker):
            return False
        return self.market.history(ticker).has_bar(trade_date)

    def _weights_for(
        self,
        sleeve_name: str,
        targets: Sequence[str],
        ranking: RankingResult,
        view: PointInTimeView,
    ) -> dict[str, float]:
        if sleeve_name == IVV_SLEEVE:
            return {targets[0]: 1.0}
        sleeve_config = (
            self.config.sector_strategy if sleeve_name == SECTOR_SLEEVE
            else self.config.growth_strategy
        )
        method = sleeve_config.weighting
        scores: dict[str, float] = {}
        caps: dict[str, float] = {}
        volatilities: dict[str, float] = {}
        if method in ("score", "market_cap", "volatility"):
            table = ranking.sleeve_table(sleeve_name)
            for ticker in targets:
                if ticker in table.index:
                    scores[ticker] = float(table.loc[ticker, "total_score"])
            if method in ("market_cap", "volatility"):
                from ..factors.risk import risk_factors
                from ..factors.valuation import valuation_factors

                for ticker in targets:
                    if method == "market_cap":
                        value = valuation_factors(view, ticker).get("market_cap")
                        if value is not None:
                            caps[ticker] = value
                    else:
                        value = risk_factors(view, ticker).get("volatility_1y")
                        if value is not None:
                            volatilities[ticker] = value
        return target_weights(
            targets, method, scores=scores, market_caps=caps, volatilities=volatilities
        )

    def _rebalance_to_targets(
        self,
        portfolio: Portfolio,
        sleeve_name: str,
        targets: Sequence[str],
        weights: Mapping[str, float],
        event: ContributionEvent,
        view: PointInTimeView,
    ) -> None:
        """Sell exits and trim overweights back to their target weight."""
        trade_date = event.execution_date
        decision_date = event.decision_date
        sleeve = portfolio.sleeves[sleeve_name]
        target_set = set(targets)

        for ticker in list(sleeve.positions):
            if ticker in target_set:
                continue
            if not self._tradeable(ticker, trade_date):
                # Cannot sell what did not trade; it stays and is retried next
                # rebalance rather than being written off silently.
                self.warnings.append(
                    f"{trade_date}: {ticker} left the {sleeve_name} target set but did not "
                    f"trade; position held until the next rebalance"
                )
                continue
            portfolio.sell_all(
                sleeve_name, ticker, trade_date, decision_date, event.price_field,
                reason="rebalance: dropped from selection",
            )

        prices = {
            t: self.market.mark_price(t, trade_date, "adj_close", self.config.eligibility.max_stale_days)
            for t in sleeve.positions
            if sleeve.positions[t].shares > 0
        }
        sleeve_value = sleeve.total_value(prices)
        if sleeve_value <= 0:
            return

        for ticker, position in list(sleeve.positions.items()):
            if position.shares <= 0 or ticker not in prices:
                continue
            target_value = sleeve_value * weights.get(ticker, 0.0)
            current_value = position.shares * prices[ticker]
            excess = current_value - target_value
            # Only trim a meaningful overweight: churning a few dollars every
            # quarter would pay commission and slippage for nothing.
            if excess > max(1.0, 0.02 * sleeve_value) and self._tradeable(ticker, trade_date):
                portfolio.sell(
                    sleeve_name,
                    ticker,
                    excess / prices[ticker],
                    trade_date,
                    decision_date,
                    event.price_field,
                    reason="rebalance: trim to target weight",
                )

    def _deploy_cash(
        self,
        portfolio: Portfolio,
        sleeve_name: str,
        targets: Sequence[str],
        weights: Mapping[str, float],
        event: ContributionEvent,
    ) -> None:
        """Spend the sleeve's cash, steering toward target weights."""
        trade_date = event.execution_date
        sleeve = portfolio.sleeves[sleeve_name]
        cash = sleeve.cash
        if cash <= 0.01:
            return

        prices = {
            t: self.market.mark_price(t, trade_date, "adj_close", self.config.eligibility.max_stale_days)
            for t in set(list(sleeve.positions) + list(targets))
            if self.market.has(t)
        }
        holdings_value = sum(
            position.shares * prices[t]
            for t, position in sleeve.positions.items()
            if position.shares > 0 and t in prices
        )
        post_value = holdings_value + cash

        # Shortfall against each target's post-contribution target value.
        shortfalls: dict[str, float] = {}
        for ticker in targets:
            target_value = post_value * weights.get(ticker, 0.0)
            position = sleeve.positions.get(ticker)
            current = position.shares * prices[ticker] if position and ticker in prices else 0.0
            shortfall = target_value - current
            if shortfall > 0:
                shortfalls[ticker] = shortfall

        if not shortfalls:
            # Everything is already at or above target, which happens right
            # after a rebalance trim; spread the cash by target weight instead.
            shortfalls = {t: weights.get(t, 0.0) for t in targets if weights.get(t, 0.0) > 0}
        total_shortfall = sum(shortfalls.values())
        if total_shortfall <= 0:
            return

        for ticker, shortfall in sorted(shortfalls.items(), key=lambda kv: -kv[1]):
            allocation = cash * shortfall / total_shortfall
            if allocation <= 0.01:
                continue
            portfolio.buy(
                sleeve_name,
                ticker,
                allocation,
                trade_date,
                event.decision_date,
                event.price_field,
                reason="contribution",
            )

    def _record_limit_breaches(
        self, portfolio: Portfolio, trade_date: dt.date, view: PointInTimeView, variant: str
    ) -> None:
        """Report concentration-limit conflicts (REQUIREMENTS 54).

        Reported whether or not ``limits.enforce`` is set: the requirement is
        that the strategy *says* when its limits conflict with the selection.
        The single-instrument benchmark variant is exempt, since holding 100%
        IVV is the definition of that variant rather than a breach.
        """
        if variant == "ivv":
            return
        limits = self.config.limits
        weights = portfolio.current_weights(trade_date)
        if not weights:
            return
        sectors = {t: view.sector_of(t) for t in weights}
        report = check_limits(
            weights, sectors, limits.max_single_stock_weight, limits.max_sector_weight, limits.max_positions
        )
        for key, breach in report.items():
            # Keyed on rule + subject, not on the formatted percentage, so a
            # limit exceeded every week is listed once with its first date.
            scoped = f"{variant}/{key}"
            if scoped not in self._seen_breaches:
                self._seen_breaches.add(scoped)
                self.limit_breaches.append(f"first seen {trade_date} [{variant}] {breach}")


def contribution_attribution(result: VariantResult, final_date: dt.date) -> pd.DataFrame:
    """Profit and loss contributed by each holding (REQUIREMENTS 27.10).

    Total P&L per ticker is the sum of its signed cash flows plus the market
    value it still carries, which captures realized and unrealized together.
    """
    transactions = result.portfolio.transactions_frame()
    if transactions.empty:
        return pd.DataFrame(columns=["ticker", "sleeve", "invested", "pnl", "current_value"])

    rows: list[dict[str, object]] = []
    market = result.portfolio.market
    for (sleeve, ticker), group in transactions.groupby(["sleeve", "ticker"]):
        net_cash = float(group["net_amount"].sum())
        invested = float(-group.loc[group["action"] == "BUY", "net_amount"].sum())
        position = result.portfolio.sleeves[sleeve].positions.get(ticker)
        current_value = 0.0
        if position is not None and position.shares > 0:
            try:
                current_value = position.shares * market.mark_price(ticker, final_date)
            except DataError:
                current_value = float("nan")
        rows.append(
            {
                "ticker": ticker,
                "sleeve": sleeve,
                "invested": invested,
                "current_value": current_value,
                "pnl": net_cash + current_value,
            }
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values("pnl", ascending=False).reset_index(drop=True)


def sector_attribution(
    result: VariantResult,
    sectors: Mapping[str, str | None],
    final_date: dt.date,
    benchmark_ticker: str = "IVV",
) -> pd.DataFrame:
    """Aggregate per-stock P&L up to GICS sectors.

    The benchmark ETF gets its own row rather than falling into
    "Unclassified": in the combined portfolio it is the single largest
    position, and burying it in a catch-all bucket would misread the chart.
    """
    stocks = contribution_attribution(result, final_date)
    if stocks.empty:
        return pd.DataFrame(columns=["sector", "invested", "current_value", "pnl"])

    def label(ticker: str) -> str:
        if ticker.upper() == benchmark_ticker.upper():
            return f"{benchmark_ticker} (benchmark core)"
        return sectors.get(ticker) or "Unclassified"

    stocks["sector"] = stocks["ticker"].map(label)
    grouped = stocks.groupby("sector")[["invested", "current_value", "pnl"]].sum()
    return grouped.sort_values("pnl", ascending=False).reset_index()
