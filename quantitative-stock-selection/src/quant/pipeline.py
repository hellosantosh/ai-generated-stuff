"""Orchestration: assemble providers, load data, run the weekly and backtest flows.

This is the layer the CLI calls. It keeps provider selection, caching and
report wiring in one place so ``main.py`` stays a thin argument parser.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .config import AppConfig
from .data.cache import FrameCache, RawCache
from .data.store import MarketData, SectorMap, UniverseMembership
from .data.synthetic import SyntheticProvider
from .data.types import FundamentalSeries, PriceHistory
from .data.universe import StaticUniverse, SyntheticUniverse, WikipediaSP500
from .data.validator import DataQualityReport, build_report
from .database import Database, open_database
from .errors import DataError, ProviderError
from .logging_config import get_logger

log = get_logger(__name__)

# Extra history loaded before the backtest start so that 5-year fundamental
# growth and 200-day moving averages are computable on the very first
# decision date instead of being reported as missing.
WARMUP_YEARS = 6

# Share of historical index members that may be unpriceable before the
# survivorship-free claim is withdrawn. Reconstructing membership correctly
# is only half the job: if the price provider cannot serve the companies that
# left the index, those names still cannot be held, and the backtest is
# survivorship biased whatever the membership table says.
MAX_MISSING_MEMBER_FRACTION = 0.02


@dataclass
class LoadedData:
    market: MarketData
    quality: DataQualityReport
    failures: dict[str, str] = field(default_factory=dict)
    synthetic: bool = False

    @property
    def banner(self) -> str | None:
        if self.synthetic:
            return (
                "SYNTHETIC DATA: prices and fundamentals were generated, not observed. "
                "Results are a plumbing check, not investment research."
            )
        return None


# --- provider wiring -------------------------------------------------------
def build_price_provider(config: AppConfig, synthetic: SyntheticProvider | None = None):
    cache = FrameCache(config.path(config.data.cache_dir), config.data.cache_ttl_days)
    raw = RawCache(config.path(config.data.raw_dir))

    if config.data.provider == "synthetic":
        return synthetic or SyntheticProvider()
    if config.data.provider == "alphavantage":
        from .data.alphavantage import AlphaVantageProvider

        return AlphaVantageProvider(
            cache=cache,
            raw_cache=raw,
            max_retries=config.data.max_retries,
            backoff_base=config.data.backoff_base_seconds,
            timeout=config.data.request_timeout_seconds,
        )
    from .data.yfinance_provider import YFinanceProvider

    return YFinanceProvider(cache=cache, raw_dir=config.path(config.data.raw_dir))


def build_fundamental_provider(config: AppConfig, synthetic: SyntheticProvider | None = None):
    raw = RawCache(config.path(config.data.raw_dir))
    name = config.data.fundamentals_provider
    if name == "none":
        return None
    if name == "synthetic":
        return synthetic or SyntheticProvider()
    if name == "yfinance":
        from .data.yfinance_provider import YFinanceProvider

        return YFinanceProvider(
            cache=FrameCache(config.path(config.data.cache_dir), config.data.cache_ttl_days),
            raw_dir=config.path(config.data.raw_dir),
        )
    from .data.sec import SECProvider

    return SECProvider(
        raw_cache=raw,
        max_retries=config.data.max_retries,
        backoff_base=config.data.backoff_base_seconds,
        timeout=config.data.request_timeout_seconds,
    )


def build_universe_provider(config: AppConfig, synthetic: SyntheticProvider | None = None):
    raw = RawCache(config.path(config.data.raw_dir))
    source = config.universe.source
    if source == "synthetic" or config.data.provider == "synthetic":
        return SyntheticUniverse(synthetic or SyntheticProvider())
    if source == "static":
        return StaticUniverse.from_csv(
            config.path("config/universe_static.csv"), config.universe.name
        )
    if source == "custom":
        if not config.universe.custom_tickers:
            raise DataError("universe.source is 'custom' but universe.custom_tickers is empty")
        return StaticUniverse(config.universe.custom_tickers, config.universe.name)
    return WikipediaSP500(raw_cache=raw, universe_name=config.universe.name)


# --- loading ---------------------------------------------------------------
def load_market_data(
    config: AppConfig,
    start: dt.date | None = None,
    end: dt.date | None = None,
    force: bool = False,
    max_tickers: int | None = None,
    with_fundamentals: bool = True,
) -> LoadedData:
    """Build a ``MarketData`` for the configured universe and window."""
    end = end or config.backtest.end_date
    window_start = start or config.backtest.start_date
    if window_start is None:
        window_start = end - dt.timedelta(weeks=config.backtest.num_weeks)
    load_start = window_start - dt.timedelta(days=int(365.25 * WARMUP_YEARS))

    synthetic = SyntheticProvider() if config.data.provider == "synthetic" else None
    price_provider = build_price_provider(config, synthetic)
    universe_provider = build_universe_provider(config, synthetic)
    fundamental_provider = build_fundamental_provider(config, synthetic) if with_fundamentals else None

    membership = universe_provider.membership(load_start, end)
    sector_map = universe_provider.sector_map()
    company_names = universe_provider.company_names()

    tickers = membership.all_tickers()
    if max_tickers:
        tickers = tickers[:max_tickers]
    benchmarks = list(config.benchmark.all_tickers)
    to_load = list(dict.fromkeys(benchmarks + tickers))
    log.info(
        "loading prices for %d tickers (%d universe members + %d benchmarks) "
        "from %s to %s",
        len(to_load), len(tickers), len(benchmarks), load_start, end,
    )

    prices: dict[str, PriceHistory] = {}
    failures: dict[str, str] = {}

    bulk = getattr(price_provider, "fetch_prices_bulk", None)
    if callable(bulk):
        prices, failures = bulk(to_load, load_start, end, force)
    else:
        for ticker in to_load:
            try:
                prices[ticker] = price_provider.fetch_prices(ticker, load_start, end, force)
            except (ProviderError, DataError) as exc:
                failures[ticker] = str(exc)
                log.warning("price download failed for %s: %s", ticker, exc)

    benchmark = config.benchmark.ticker
    if benchmark not in prices:
        raise DataError(
            f"benchmark {benchmark} has no price history ({failures.get(benchmark, 'unknown reason')}). "
            f"The backtest cannot proceed without it: it supplies the trading calendar and "
            f"every comparison metric."
        )

    _assess_survivorship_coverage(membership, tickers, prices, benchmarks, price_provider.name)

    fundamentals: dict[str, FundamentalSeries] = {}
    if fundamental_provider is not None:
        loadable = [t for t in prices if t not in benchmarks]
        log.info("loading fundamentals for %d tickers from %s", len(loadable), fundamental_provider.name)
        for position, ticker in enumerate(loadable, start=1):
            try:
                series = fundamental_provider.fetch_fundamentals(ticker, force)
            except (ProviderError, DataError) as exc:
                failures.setdefault(ticker, f"fundamentals: {exc}")
                log.warning("fundamental download failed for %s: %s", ticker, exc)
                continue
            if series.records:
                fundamentals[ticker] = series
            if position % 100 == 0:
                log.info("  fundamentals: %d/%d", position, len(loadable))

    # Fill any sector gaps from the price provider where it can supply them.
    missing_sectors = [
        t for t in prices
        if t not in benchmarks and sector_map.sector_of(t, end) is None
    ]
    if missing_sectors and hasattr(price_provider, "sector_of"):
        log.info("looking up sectors for %d unclassified ticker(s)", len(missing_sectors))
        for ticker in missing_sectors:
            try:
                sector = price_provider.sector_of(ticker, end)
            except Exception as exc:  # provider-specific failures are non-fatal
                log.debug("sector lookup failed for %s: %s", ticker, exc)
                continue
            if sector:
                sector_map.add_current(ticker, sector, source=price_provider.name)

    market = MarketData(
        prices=prices,
        fundamentals=fundamentals,
        sectors=sector_map,
        universe=membership,
        benchmark=benchmark,
        company_names=company_names,
        strict=config.backtest.strict_point_in_time,
    )

    quality = build_report(
        prices=prices,
        fundamentals=fundamentals,
        expected_tickers=to_load,
        as_of=end,
        min_bars=config.eligibility.min_price_history_weeks * 5,
        max_stale_days=config.eligibility.max_stale_days,
        failures=failures,
    )
    log.info(
        "loaded %d price series, %d fundamental series, %d failure(s)",
        len(prices), len(fundamentals), len(failures),
    )
    return LoadedData(
        market=market,
        quality=quality,
        failures=failures,
        synthetic=config.data.provider == "synthetic",
    )


# --- persistence -----------------------------------------------------------
def persist_market_data(db: Database, data: LoadedData, config: AppConfig) -> dict[str, int]:
    """Write prices, fundamentals, universe and sectors into SQLite."""
    market = data.market
    counts: dict[str, int] = {}
    now = dt.datetime.now(dt.timezone.utc)

    stock_rows = []
    for ticker in sorted(market.prices):
        history = market.prices[ticker]
        stock_rows.append({
            "ticker": ticker,
            "company_name": market.company_name(ticker),
            "cik": None,
            "sector": market.sector_of(ticker, config.backtest.end_date),
            "industry": None,
            "first_seen": history.first_date,
            "last_seen": history.last_date,
            "delisted_on": None,
            "updated_at": now,
        })
    counts["stocks"] = db.upsert_many("stocks", stock_rows)

    price_rows = []
    for ticker in sorted(market.prices):
        history = market.prices[ticker]
        frame = history.frame
        for timestamp, row in frame.iterrows():
            price_rows.append({
                "ticker": ticker,
                "date": timestamp.date(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "adj_close": float(row["adj_close"]),
                "volume": None if pd.isna(row.get("volume")) else float(row["volume"]),
                "dividend": float(row["dividend"]),
                "split_coef": float(row["split_coef"]),
                "provider": history.provider.name,
                "retrieved_at": history.provider.retrieved_at,
            })
        if len(price_rows) >= 200_000:
            counts["historical_prices"] = counts.get("historical_prices", 0) + db.upsert_many(
                "historical_prices", price_rows
            )
            price_rows = []
    if price_rows:
        counts["historical_prices"] = counts.get("historical_prices", 0) + db.upsert_many(
            "historical_prices", price_rows
        )

    import json

    fundamental_rows = []
    for ticker, series in market.fundamentals.items():
        for record in series.records:
            fundamental_rows.append({
                "ticker": ticker,
                "period_end_date": record.period_end_date,
                "fiscal_period": record.fiscal_period,
                "filing_date": record.filing_date,
                "data_available_date": record.data_available_date,
                "payload": json.dumps(dict(record.metrics), default=float),
                "provider": record.provider,
                "retrieved_at": now,
            })
    if fundamental_rows:
        counts["fundamentals"] = db.upsert_many("fundamentals", fundamental_rows)

    membership_rows = []
    for ticker, spans in market.universe.intervals.items():
        for span_start, span_end in spans:
            membership_rows.append({
                "universe": market.universe.name,
                "ticker": ticker,
                "start_date": span_start,
                "end_date": span_end,
                "source": market.universe.source,
            })
    if membership_rows:
        counts["universe_membership"] = db.upsert_many("universe_membership", membership_rows)

    sector_rows = []
    for ticker in market.sectors.tickers():
        sector = market.sector_of(ticker, config.backtest.end_date)
        if sector:
            sector_rows.append({
                "ticker": ticker,
                "sector": sector,
                "effective_date": dt.date(1990, 1, 1),
                "end_date": None,
                "source": "provider",
            })
    if sector_rows:
        counts["sector_history"] = db.upsert_many("sector_history", sector_rows)

    log.info("persisted to database: %s", counts)
    return counts


def persist_backtest(db: Database, result, provenance: Mapping[str, Any], config: AppConfig) -> None:
    """Store run metadata, metrics, values and transactions."""
    db.record_run(
        run_id=result.run_id,
        strategy_version=config.strategy.version,
        software_version=str(provenance.get("software_version", "")),
        python_version=str(provenance.get("python_version", "")),
        start_date=result.start_date,
        end_date=result.end_date,
        num_weeks=len(result.schedule),
        config=config.to_dict(),
        provenance=dict(provenance),
        git_commit=provenance.get("git_commit"),
    )

    metric_rows = []
    for name, variant in result.variants.items():
        for key, value in variant.metrics.to_dict().items():
            if key == "variant":
                continue
            numeric = None
            text = None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
            else:
                text = None if value is None else str(value)
            metric_rows.append({
                "run_id": result.run_id, "variant": name, "metric": key,
                "value": numeric, "text_value": text,
            })
    db.upsert_many("backtest_metrics", metric_rows)

    value_rows = []
    transaction_rows = []
    for name, variant in result.variants.items():
        for row in variant.values.to_dict("records"):
            value_rows.append({
                "run_id": result.run_id, "variant": name, "date": row["date"],
                "contribution": float(row["contribution"]), "cash": float(row["cash"]),
                "holdings_value": float(row["holdings_value"]),
                "total_value": float(row["total_value"]),
                "cumulative_contributions": float(row["cumulative_contributions"]),
                "twr_index": None if pd.isna(row.get("twr_index")) else float(row["twr_index"]),
            })
        frame = variant.portfolio.transactions_frame()
        for row in frame.to_dict("records"):
            transaction_rows.append({
                "run_id": result.run_id, "variant": name, "sequence": int(row["sequence"]),
                "decision_date": row["decision_date"], "trade_date": row["trade_date"],
                "ticker": row["ticker"], "sleeve": row["sleeve"], "action": row["action"],
                "shares": float(row["shares"]), "price": float(row["price"]),
                "gross_amount": float(row["gross_amount"]), "commission": float(row["commission"]),
                "slippage": float(row["slippage"]), "net_amount": float(row["net_amount"]),
                "reason": row.get("reason", ""),
            })
    if value_rows:
        db.upsert_many("portfolio_values", value_rows)
    if transaction_rows:
        db.upsert_many("portfolio_transactions", transaction_rows)

    ranking_rows = []
    for decision_date, ranking in result.rankings.items():
        if ranking.table.empty:
            continue
        for ticker, row in ranking.table.iterrows():
            ranking_rows.append({
                "run_id": result.run_id, "decision_date": decision_date,
                "sleeve": row["sleeve"], "ticker": ticker,
                "sector": row.get("sector"), "company_name": row.get("company_name"),
                "total_score": _float_or_none(row.get("total_score")),
                "growth": _float_or_none(row.get("growth")),
                "momentum": _float_or_none(row.get("momentum")),
                "quality": _float_or_none(row.get("quality")),
                "valuation": _float_or_none(row.get("valuation")),
                "risk": _float_or_none(row.get("risk")),
                "relative_strength": _float_or_none(row.get("relative_strength")),
                "rank": int(row["rank"]), "previous_rank": None, "rank_change": None,
                "selected": int(bool(row.get("selected", False))),
            })
        if len(ranking_rows) >= 100_000:
            db.upsert_many("rankings", ranking_rows)
            ranking_rows = []
    if ranking_rows:
        db.upsert_many("rankings", ranking_rows)


def _assess_survivorship_coverage(
    membership: UniverseMembership,
    members: Sequence[str],
    prices: Mapping[str, Any],
    benchmarks: Sequence[str],
    provider_name: str,
) -> None:
    """Check that the price provider can actually serve delisted members.

    Point-in-time membership only removes survivorship bias if the companies
    that left the index can still be priced. Most free providers drop delisted
    and acquired tickers entirely, so the names that did badly quietly vanish
    from the backtest even though the membership table lists them. When that
    happens the ``survivorship_free`` claim is withdrawn and the report says
    exactly how many names were lost and why.
    """
    universe_members = [t for t in members if t not in set(benchmarks)]
    if not universe_members:
        return
    missing = sorted(t for t in universe_members if t not in prices)
    if not missing:
        if membership.survivorship_free:
            membership.bias_note += (
                f" Every one of the {len(universe_members)} historical members has price "
                f"data, so delisted companies are genuinely represented."
            )
        return

    fraction = len(missing) / len(universe_members)
    sample = ", ".join(missing[:8]) + (", ..." if len(missing) > 8 else "")
    note = (
        f" However, {len(missing)} of {len(universe_members)} historical members "
        f"({fraction:.0%}) have no price history from {provider_name} - typically "
        f"companies that were acquired or delisted ({sample}). Those names cannot be "
        f"held in the backtest."
    )
    if membership.survivorship_free and fraction > MAX_MISSING_MEMBER_FRACTION:
        membership.survivorship_free = False
        membership.bias_summary = (
            f"index membership is reconstructed point-in-time, but {fraction:.0%} of "
            f"historical members have no price history from {provider_name}, so delisted "
            f"and acquired companies drop out of the backtest"
        )
        note += (
            f" Because the missing share exceeds "
            f"{MAX_MISSING_MEMBER_FRACTION:.0%}, this run is reported as SURVIVORSHIP "
            f"BIASED despite the point-in-time membership: the reconstruction is "
            f"sound, but the price provider cannot serve the companies that left."
        )
        log.warning(
            "survivorship-free claim withdrawn: %d/%d historical members (%.0f%%) "
            "have no price data from %s",
            len(missing), len(universe_members), fraction * 100, provider_name,
        )
    membership.bias_note += note


def _float_or_none(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(numeric) else numeric


def open_project_database(config: AppConfig) -> Database:
    return open_database(config.path(config.data.database_path))
