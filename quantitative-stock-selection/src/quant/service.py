"""Application service layer shared by the CLI and the HTTP API.

Holds the three things a long-running server needs that a one-shot CLI does
not: a background job runner, an in-process cache of loaded market data, and
JSON-shaped accessors over the domain objects.

Loading 500 price series takes tens of seconds even from cache, so the UI
would be unusable if every request rebuilt it. The cache is keyed on the
inputs that change the result and is invalidated whenever a data update runs.
"""

from __future__ import annotations

import datetime as dt
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from .analysis.indices import (
    COHORT_SIZES,
    MAJOR_INDICES,
    cohort_performance,
    index_performance,
    window,
)
from .config import AppConfig, load_config, load_env
from .errors import DataError, QuantError
from .factors import compute_universe_factors
from .factors.valuation import valuation_factors
from .logging_config import get_logger
from .pipeline import (
    LoadedData,
    build_price_provider,
    load_market_data,
    open_project_database,
)
from .portfolio.holdings import Holdings, default_path
from .portfolio.tradeplan import TradePlan, apply_plan, build_trade_plan, rebalance_due
from .ranking import rank_and_select
from .ranking.sector_ranker import GROWTH_SLEEVE, SECTOR_SLEEVE, RankingResult
from .reports.weekly import basket_weights

log = get_logger(__name__)

JOB_LINE_LIMIT = 2000

# Index proxies are fetched once over a window wide enough to contain any
# question the UI can ask, because the price cache keys on the requested dates:
# asking for a different window would download the series again rather than
# slicing the copy already on disk. 1993 is the oldest US equity ETF.
INDEX_HISTORY_START = dt.date(1993, 1, 1)


# --- jobs ------------------------------------------------------------------
@dataclass
class Job:
    """A background task, with its captured progress output."""

    id: str
    name: str
    status: str = "queued"          # queued | running | succeeded | failed
    created_at: dt.datetime = field(default_factory=dt.datetime.now)
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        return self.status in ("queued", "running")

    def to_dict(self, tail: int | None = 200) -> dict[str, Any]:
        lines = self.lines if tail is None else self.lines[-tail:]
        duration = None
        if self.started_at:
            end = self.finished_at or dt.datetime.now()
            duration = round((end - self.started_at).total_seconds(), 1)
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "finished_at": self.finished_at.isoformat(timespec="seconds") if self.finished_at else None,
            "duration_seconds": duration,
            "lines": lines,
            "line_count": len(self.lines),
            "error": self.error,
            "result": self.result,
        }


class JobRunner:
    """Runs one job at a time in a worker thread.

    Serialized deliberately: these jobs each load the whole universe, and two
    at once would double memory and thrash the provider's rate limits.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._active: str | None = None

    def submit(self, name: str, work: Callable[[Callable[[str], None]], dict[str, Any] | None]) -> Job:
        with self._lock:
            if self._active is not None and self._jobs[self._active].running:
                raise QuantError(
                    f"a job is already running ({self._jobs[self._active].name}); "
                    f"wait for it to finish"
                )
            job = Job(id=uuid.uuid4().hex[:12], name=name)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._active = job.id

        def emit(line: str) -> None:
            text = str(line).rstrip()
            if not text:
                return
            if len(job.lines) < JOB_LINE_LIMIT:
                job.lines.append(text)
            elif len(job.lines) == JOB_LINE_LIMIT:
                job.lines.append("... output truncated; see logs/app.log")

        def run() -> None:
            job.status = "running"
            job.started_at = dt.datetime.now()
            try:
                job.result = work(emit) or {}
                job.status = "succeeded"
            except Exception as exc:  # a failed job must not kill the server
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                emit(job.error)
                log.error("job %s (%s) failed: %s", job.id, job.name, exc)
                log.debug("%s", traceback.format_exc())
            finally:
                job.finished_at = dt.datetime.now()

        threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        return [self._jobs[i] for i in reversed(self._order[-limit:])]

    @property
    def busy(self) -> bool:
        return self._active is not None and self._jobs[self._active].running

    def active(self) -> Job | None:
        if self._active is None:
            return None
        job = self._jobs[self._active]
        return job if job.running else None


# --- service ---------------------------------------------------------------
@dataclass
class WeeklyView:
    """Everything the dashboard needs for one decision date."""

    as_of: dt.date
    execution_date: dt.date | None
    ranking: RankingResult
    basket: list[tuple[str, float, str, float]]
    sectors: dict[str, str]
    budgets: dict[str, float]
    universe_size: int
    eligible: int
    data: LoadedData


class AppService:
    """Stateful facade over the pipeline, used by the HTTP API."""

    def __init__(self, project_root: Path, config: AppConfig | None = None) -> None:
        self.project_root = Path(project_root)
        load_env(self.project_root)
        self._config = config or load_config(
            self.project_root / "config", project_root=self.project_root
        )
        self.jobs = JobRunner()
        self._market_cache: tuple[Any, LoadedData] | None = None
        self._weekly_cache: WeeklyView | None = None
        self._index_cache: dict[str, Any] = {}
        self._cohort_cache: dict[Any, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # --- config ----------------------------------------------------------
    @property
    def config(self) -> AppConfig:
        return self._config

    def reload_config(self) -> AppConfig:
        self._config = load_config(self.project_root / "config", project_root=self.project_root)
        self.invalidate()
        return self._config

    def invalidate(self) -> None:
        with self._lock:
            self._market_cache = None
            self._weekly_cache = None
            self._index_cache = {}
            self._cohort_cache = {}

    # --- data ------------------------------------------------------------
    def market(self, as_of: dt.date | None = None, force_reload: bool = False) -> LoadedData:
        """Loaded market data for a single decision date, cached in process."""
        key = (as_of or self._config.backtest.end_date, self._config.data.provider,
               self._config.data.fundamentals_provider)
        with self._lock:
            if not force_reload and self._market_cache is not None and self._market_cache[0] == key:
                return self._market_cache[1]
        data = load_market_data(
            self._config,
            members_as_of=as_of or self._config.backtest.end_date,
        )
        with self._lock:
            self._market_cache = (key, data)
        return data

    def weekly(self, as_of: dt.date | None = None, force_reload: bool = False) -> WeeklyView:
        with self._lock:
            cached = self._weekly_cache
        if cached is not None and not force_reload and (as_of is None or cached.as_of == as_of):
            return cached

        data = self.market(as_of, force_reload=force_reload)
        market = data.market
        resolved = market.previous_trading_day(
            as_of or market.calendar[-1].date(), inclusive=True
        )
        view = market.view(resolved, strict=self._config.backtest.strict_point_in_time)
        universe = view.universe()
        factors, rejections = compute_universe_factors(view, universe, self._config)
        ranking = rank_and_select(factors, self._config, resolved, rejections=rejections)
        basket, budgets = basket_weights(ranking, self._config)
        sectors = self.sectors_for([ticker for ticker, _, _, _ in basket], resolved, market)
        try:
            execution_date = market.next_trading_day(resolved)
        except QuantError:
            execution_date = None

        weekly = WeeklyView(
            as_of=resolved,
            execution_date=execution_date,
            ranking=ranking,
            basket=basket,
            sectors=sectors,
            budgets=budgets,
            universe_size=len(universe),
            eligible=ranking.universe_size,
            data=data,
        )
        with self._lock:
            self._weekly_cache = weekly
        return weekly

    # --- holdings --------------------------------------------------------
    @property
    def holdings_path(self) -> Path:
        return default_path(self.project_root)

    def holdings(self) -> Holdings:
        return Holdings.load(self.holdings_path)

    def save_holdings(self, holdings: Holdings) -> Path:
        return holdings.save(self.holdings_path)

    def sectors_for(
        self, tickers: Sequence[str], as_of: dt.date, market: Any | None = None
    ) -> dict[str, str]:
        """GICS sector per ticker, for the ones that have a classification.

        The benchmark ETF and anything held outside the index have none, and are
        left out rather than labeled "Unclassified" - a missing sector is a fact
        about the holding, not a category to put it in.
        """
        source = market if market is not None else self.market(as_of).market
        found: dict[str, str] = {}
        for ticker in {t.upper() for t in tickers}:
            sector = source.sectors.sector_of(ticker, as_of)
            if sector:
                found[ticker] = sector
        return found

    def prices_for(self, tickers: Sequence[str], as_of: dt.date) -> dict[str, float]:
        """Last close at or before ``as_of`` for each ticker that has one."""
        market = self.market(as_of).market
        prices: dict[str, float] = {}
        for ticker in {t.upper() for t in tickers}:
            if not market.has(ticker):
                continue
            try:
                prices[ticker] = market.mark_price(
                    ticker, as_of, "adj_close", self._config.eligibility.max_stale_days
                )
            except QuantError:
                continue
        return prices

    def trade_plan(
        self,
        contribution: float | None = None,
        mode: str | None = None,
        as_of: dt.date | None = None,
    ) -> tuple[TradePlan, dict[str, Any]]:
        weekly = self.weekly(as_of)
        holdings = self.holdings()
        contribution = (
            self._config.strategy.weekly_contribution if contribution is None else float(contribution)
        )

        due, reason = rebalance_due(self._config, weekly.as_of, holdings.last_rebalance)
        resolved_mode = mode or ("rebalance" if due else "contribute")

        targets = {ticker: weight for ticker, weight, _, _ in weekly.basket}
        sleeves = {ticker: sleeve for ticker, _, sleeve, _ in weekly.basket}
        scores = {ticker: score for ticker, _, _, score in weekly.basket}
        prices = self.prices_for(list(targets) + holdings.tickers(), weekly.as_of)
        sectors = {
            **weekly.sectors,
            **self.sectors_for(holdings.tickers(), weekly.as_of),
        }

        plan = build_trade_plan(
            holdings=holdings,
            target_weights=targets,
            prices=prices,
            contribution=contribution,
            as_of=weekly.as_of,
            mode=resolved_mode,  # type: ignore[arg-type]
            sleeves=sleeves,
            scores=scores,
            sectors=sectors,
        )
        meta = {
            "rebalance_due": due,
            "rebalance_reason": reason,
            "last_rebalance": holdings.last_rebalance.isoformat() if holdings.last_rebalance else None,
            "mode_source": "requested" if mode else "automatic",
        }
        return plan, meta

    def apply_trade_plan(self, plan: TradePlan) -> Holdings:
        holdings = apply_plan(self.holdings(), plan)
        self.save_holdings(holdings)
        return holdings

    # --- index and cohort analytics --------------------------------------
    def index_history(self, ticker: str, force: bool = False):
        """One index proxy's full price history, cached in process."""
        ticker = ticker.upper()
        with self._lock:
            cached = self._index_cache.get(ticker)
        if cached is not None and not force:
            return cached
        provider = build_price_provider(self._config)
        history = provider.fetch_prices(ticker, INDEX_HISTORY_START, dt.date.today(), force)
        with self._lock:
            self._index_cache[ticker] = history
        return history

    def index_performance(
        self,
        start: dt.date,
        end: dt.date,
        tickers: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        wanted = [t.upper() for t in tickers] if tickers else [p.ticker for p in MAJOR_INDICES]
        histories: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for ticker in wanted:
            try:
                histories[ticker] = self.index_history(ticker)
            except QuantError as exc:
                failures[ticker] = str(exc)
                log.warning("index proxy %s unavailable: %s", ticker, exc)
        if not histories:
            raise DataError(
                "none of the requested index proxies could be priced: "
                + "; ".join(f"{t} ({m})" for t, m in failures.items())
            )
        result = index_performance(histories, start, end)
        result["failures"] = failures
        return result

    def cohort_performance(
        self,
        start: dt.date,
        end: dt.date,
        sizes: Sequence[int] = COHORT_SIZES,
    ) -> dict[str, Any]:
        """Top-N cohorts of the index, formed on ``start`` and held to ``end``.

        The cohort can only be built from companies this installation has
        prices for. That set is today's index members, so companies that left
        the index between ``start`` and ``end`` are missing - and they left
        mostly by failing. The result says how many are missing and in which
        direction that bends the answer, rather than presenting the number as
        clean.
        """
        key = (start, end, tuple(sizes))
        with self._lock:
            cached = self._cohort_cache.get(key)
        if cached is not None:
            return cached

        market = self.market().market
        benchmark_ticker = self._config.benchmark.ticker
        if not market.has(benchmark_ticker):
            raise DataError(f"benchmark {benchmark_ticker} is not loaded")

        # The index ETFs go back to 1999; the company-level history here only
        # goes back as far as the configured backtest window plus its warmup.
        # Asking for more is a reasonable thing to do, so clamp and say so
        # rather than failing the whole panel.
        clamped: str | None = None
        earliest = market.calendar[0].date()
        if start < earliest:
            clamped = (
                f"Company-level history here starts {earliest}, so the cohorts begin there "
                f"rather than on {start}. The index lines above go back further because an "
                f"ETF is one series, while a cohort needs every member priced."
            )
            start = earliest

        members = [t for t in market.universe.members_on(start)]
        if not members:
            raise DataError(
                f"no index membership is recorded for {start}; the membership table here "
                f"starts at {earliest}"
            )
        priced = [t for t in members if market.has(t)]
        missing = sorted(set(members) - set(priced))

        view = market.view(start, strict=self._config.backtest.strict_point_in_time)
        caps: dict[str, float] = {}
        no_cap: list[str] = []
        for ticker in priced:
            try:
                value = valuation_factors(view, ticker).get("market_cap")
            except QuantError:
                value = None
            if value is None or not pd.notna(value) or value <= 0:
                no_cap.append(ticker)
                continue
            caps[ticker] = float(value)

        columns = {}
        for ticker in caps:
            series = window(market.history(ticker).frame["total_return_index"], start, end)
            if len(series) >= 2:
                columns[ticker] = series
        if not columns:
            raise DataError(f"no company in the index has prices between {start} and {end}")

        benchmark = window(
            market.history(benchmark_ticker).frame["total_return_index"], start, end
        )
        frame = pd.DataFrame(columns).reindex(benchmark.index)

        result = cohort_performance(
            total_returns=frame,
            market_caps=caps,
            benchmark=benchmark,
            start=start,
            end=end,
            sizes=sizes,
            benchmark_label=f"{self._config.universe.name.upper()} ({benchmark_ticker})",
        )
        result["coverage"] = {
            "members_on_start": len(members),
            "priced": len(priced),
            "with_market_cap": len(caps),
            "missing_members": len(missing),
            "missing_examples": missing[:10],
            "no_market_cap_examples": no_cap[:10],
            "point_in_time_membership": market.universe.survivorship_free,
        }
        result["warnings"] = self._cohort_warnings(members, priced, caps, start)
        if clamped:
            result["warnings"].insert(0, clamped)
        with self._lock:
            self._cohort_cache[key] = result
        return result

    @staticmethod
    def _cohort_warnings(
        members: Sequence[str], priced: Sequence[str], caps: Mapping[str, float], start: dt.date
    ) -> list[str]:
        warnings: list[str] = []
        missing = len(members) - len(priced)
        if missing > 0:
            warnings.append(
                f"{missing} of the {len(members)} index members on {start} have no price "
                f"history here, because this installation prices today's members. Companies "
                f"that left the index mostly left by failing, so excluding them flatters "
                f"every cohort - and the smaller cohorts least, since the largest companies "
                f"rarely drop out."
            )
        without_cap = len(priced) - len(caps)
        if without_cap > 0:
            warnings.append(
                f"{without_cap} priced member(s) had no share count on file as of {start}, "
                f"so no market cap could be computed and they were left out of the ranking."
            )
        return warnings

    # --- runs ------------------------------------------------------------
    def database(self):
        return open_project_database(self._config)

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        db = self.database()
        try:
            rows = db.query(
                "SELECT run_id, created_at, strategy_version, start_date, end_date, num_weeks "
                "FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def run_metrics(self, run_id: str) -> dict[str, Any]:
        db = self.database()
        try:
            rows = db.query(
                "SELECT variant, metric, value, text_value FROM backtest_metrics WHERE run_id = ?",
                (run_id,),
            )
            if not rows:
                raise QuantError(f"no metrics stored for run {run_id!r}")
            metrics: dict[str, dict[str, Any]] = {}
            for row in rows:
                metrics.setdefault(row["variant"], {})[row["metric"]] = (
                    row["value"] if row["value"] is not None else row["text_value"]
                )
            meta = db.query(
                "SELECT run_id, created_at, strategy_version, start_date, end_date, num_weeks, "
                "provenance_json FROM backtest_runs WHERE run_id = ?",
                (run_id,),
            )
            import json

            record = dict(meta[0]) if meta else {}
            provenance = json.loads(record.pop("provenance_json", "{}") or "{}")
            return {"run": record, "metrics": metrics, "provenance": provenance}
        finally:
            db.close()

    def run_series(self, run_id: str) -> dict[str, Any]:
        """Weekly value and TWR series per variant, for charting in the UI."""
        db = self.database()
        try:
            rows = db.query(
                "SELECT variant, date, total_value, cumulative_contributions, twr_index "
                "FROM portfolio_values WHERE run_id = ? ORDER BY variant, date",
                (run_id,),
            )
            series: dict[str, dict[str, list[Any]]] = {}
            for row in rows:
                bucket = series.setdefault(
                    row["variant"], {"date": [], "total_value": [], "contributions": [], "twr": []}
                )
                bucket["date"].append(row["date"])
                bucket["total_value"].append(row["total_value"])
                bucket["contributions"].append(row["cumulative_contributions"])
                bucket["twr"].append(row["twr_index"])
            return series
        finally:
            db.close()

    # --- status ----------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Cheap summary that never triggers a data load."""
        cache_dir = self._config.path(self._config.data.cache_dir)
        cached_files = list(cache_dir.rglob("*.csv")) if cache_dir.exists() else []
        newest = max((f.stat().st_mtime for f in cached_files), default=None)
        holdings = self.holdings()
        runs = self.runs(limit=1)

        loaded: dict[str, Any] | None = None
        with self._lock:
            cached_market = self._market_cache
            cached_weekly = self._weekly_cache
        if cached_market is not None:
            data = cached_market[1]
            loaded = {
                "price_series": len(data.market.prices),
                "fundamental_series": len(data.market.fundamentals),
                "universe": data.market.universe.name,
                "universe_status": data.market.universe.status_line(),
                "synthetic": data.synthetic,
                "failures": len(data.failures),
            }

        return {
            "strategy_version": self._config.strategy.version,
            "weekly_contribution": self._config.strategy.weekly_contribution,
            "allocations": self._config.strategy.allocations,
            "provider": self._config.data.provider,
            "fundamentals_provider": self._config.data.fundamentals_provider,
            "universe_source": self._config.universe.source,
            "synthetic_mode": self._config.data.provider == "synthetic",
            "cache_files": len(cached_files),
            "cache_updated": dt.datetime.fromtimestamp(newest).isoformat(timespec="seconds")
            if newest
            else None,
            "holdings": {
                "positions": len(holdings.positions),
                "updated_at": holdings.updated_at.isoformat() if holdings.updated_at else None,
                "last_rebalance": holdings.last_rebalance.isoformat()
                if holdings.last_rebalance
                else None,
            },
            "latest_run": runs[0] if runs else None,
            "loaded": loaded,
            "weekly_as_of": cached_weekly.as_of.isoformat() if cached_weekly else None,
            "job_running": self.jobs.busy,
        }


def ranking_rows(ranking: RankingResult, sleeve: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Ranking table as JSON rows."""
    table = ranking.sleeve_table(sleeve)
    if table.empty:
        return []
    frame = table.sort_values(["sector", "rank"] if sleeve == SECTOR_SLEEVE else ["rank"])
    if limit:
        frame = frame.groupby("sector").head(limit) if sleeve == SECTOR_SLEEVE else frame.head(limit)
    rows = []
    for ticker, row in frame.iterrows():
        rows.append({
            "ticker": str(ticker),
            "company_name": row.get("company_name"),
            "sector": row.get("sector"),
            "rank": int(row["rank"]),
            "total_score": _num(row.get("total_score")),
            "growth": _num(row.get("growth")),
            "momentum": _num(row.get("momentum")),
            "quality": _num(row.get("quality")),
            "valuation": _num(row.get("valuation")),
            "risk": _num(row.get("risk")),
            "relative_strength": _num(row.get("relative_strength")),
            "selected": bool(row.get("selected", False)),
        })
    return rows


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(numeric) else round(numeric, 2)
