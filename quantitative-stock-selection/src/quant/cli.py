"""Command-line interface (REQUIREMENTS 33).

    python main.py update-data       download and cache prices, fundamentals, universe
    python main.py validate-data     run the data-quality checks
    python main.py rank              rank the universe as of a date
    python main.py weekly-report     the weekly decision report
    python main.py backtest          the historical backtest and full report set
    python main.py export            re-export a stored run
    python main.py data-quality      the data-quality dashboard
    python main.py runs              list stored backtest runs

This program is research software. It does not place trades and it never will
without an explicit, separately implemented approval step (REQUIREMENTS 3, 47).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from . import __version__
from .backtest.engine import BacktestEngine, contribution_attribution, sector_attribution
from .backtest.validation import build_provenance, next_run_id
from .config import AppConfig, load_config, load_env
from .errors import QuantError
from .factors import compute_universe_factors
from .logging_config import configure_logging, get_logger
from .pipeline import (
    load_market_data,
    open_project_database,
    persist_backtest,
    persist_market_data,
)
from .portfolio.dca import build_schedule
from .ranking import rank_and_select
from .reports import (
    export_csvs,
    generate_charts,
    render_backtest_report,
    render_weekly_report,
    write_backtest_report,
    write_workbook,
    write_weekly_report,
)
from .reports.tables import (
    annual_returns_table,
    ending_positions_frame,
    ending_sector_allocation,
    factor_scores_frame,
    monthly_returns_table,
    rankings_frame,
)

# The project root, used to resolve `config/`, `data/` and `reports/` when the
# CLI is run from anywhere. src/quant/cli.py -> src/quant -> src -> root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

log = get_logger("quant.cli")

BANNER = "quant-stock-selector — research and backtesting only; no trades are placed."


# --- helpers ---------------------------------------------------------------
def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date (YYYY-MM-DD)") from exc


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Turn CLI flags into a config override tree."""
    backtest: dict[str, Any] = {}
    data: dict[str, Any] = {}
    universe: dict[str, Any] = {}
    reporting: dict[str, Any] = {}

    if getattr(args, "start", None):
        backtest["start_date"] = args.start
    if getattr(args, "end", None):
        backtest["end_date"] = args.end
    if getattr(args, "weeks", None):
        backtest["num_weeks"] = args.weeks
    if getattr(args, "variants", None):
        backtest["variants"] = args.variants
    if getattr(args, "no_strict", False):
        backtest["strict_point_in_time"] = False
    if getattr(args, "provider", None):
        data["provider"] = args.provider
        if args.provider == "synthetic":
            universe["source"] = "synthetic"
            data["fundamentals_provider"] = "synthetic"
    if getattr(args, "fundamentals", None):
        data["fundamentals_provider"] = args.fundamentals
    if getattr(args, "universe_source", None):
        universe["source"] = args.universe_source
    if getattr(args, "no_charts", False):
        reporting["generate_charts"] = False
    if getattr(args, "no_excel", False):
        reporting["generate_excel"] = False

    overrides: dict[str, Any] = {}
    if backtest:
        overrides["backtest"] = backtest
    if data:
        overrides["data"] = data
    if universe:
        overrides["universe"] = universe
    if reporting:
        overrides["reporting"] = reporting

    scoring: dict[str, Any] = {}
    if getattr(args, "weights", None):
        scoring["weights"] = _parse_weights(args.weights)
    result: dict[str, Any] = {"settings": overrides} if overrides else {}
    if scoring:
        result["scoring"] = scoring
    return result


def _parse_weights(text: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for part in text.split(","):
        if "=" not in part:
            raise argparse.ArgumentTypeError(
                f"--weights expects name=value pairs, got {part!r}"
            )
        name, value = part.split("=", 1)
        weights[name.strip()] = float(value)
    return weights


def _load(args: argparse.Namespace) -> AppConfig:
    load_env(PROJECT_ROOT)
    # Passing --provider synthetic (or --fundamentals synthetic) on the command
    # line is the deliberate act that unlocks generated data. Configuration
    # alone cannot.
    wants_synthetic = "synthetic" in {
        getattr(args, "provider", None), getattr(args, "fundamentals", None)
    }
    config = load_config(
        config_dir=PROJECT_ROOT / (args.config or "config"),
        overrides=_overrides(args) or None,
        project_root=PROJECT_ROOT,
        allow_synthetic=wants_synthetic,
    )
    configure_logging(
        level=args.log_level or config.logging.level,
        log_file=config.path(config.logging.file) if config.logging.file else None,
        max_bytes=config.logging.max_bytes,
        backup_count=config.logging.backup_count,
        force=True,
    )
    return config


def _print(message: str = "") -> None:
    print(message, flush=True)


def _warn_if_synthetic(data) -> None:
    if data.banner:
        _print("")
        _print("!" * 78)
        _print(data.banner)
        _print("!" * 78)
        _print("")


# --- commands --------------------------------------------------------------
def cmd_update_data(args: argparse.Namespace) -> int:
    config = _load(args)
    _print(BANNER)
    _print("")
    data = load_market_data(
        config, force=args.force, max_tickers=args.max_tickers,
        with_fundamentals=not args.skip_fundamentals,
    )
    _warn_if_synthetic(data)

    db = open_project_database(config)
    try:
        if not args.no_database:
            _print("writing to the local database (this can take a minute for a full universe)...")
            counts = persist_market_data(db, data, config)
            for table, count in counts.items():
                _print(f"  {table:<24}{count:>10,} rows")
    finally:
        db.close()

    market = data.market
    _print("")
    _print(f"Universe:          {market.universe.name}")
    _print(f"  {market.universe.status_line()}")
    _print(f"Price series:      {len(market.prices):,}")
    _print(f"Fundamental series:{len(market.fundamentals):>9,}")
    _print(f"Failures:          {len(data.failures):,}")
    _print("")
    _print(data.quality.summary())
    if data.failures:
        _print("")
        _print("First failures:")
        for ticker, message in list(data.failures.items())[:10]:
            _print(f"  {ticker:<8}{message[:90]}")
    return 0


def cmd_validate_data(args: argparse.Namespace) -> int:
    config = _load(args)
    _print(BANNER)
    data = load_market_data(config, max_tickers=args.max_tickers)
    _warn_if_synthetic(data)
    report = data.quality
    _print("")
    _print(report.summary())
    _print("")
    for severity in ("critical", "warning"):
        issues = report.by_severity(severity)
        if not issues:
            continue
        _print(f"{severity.upper()} ({len(issues)}):")
        for issue in issues[: args.limit]:
            _print(f"  {issue}")
        if len(issues) > args.limit:
            _print(f"  ... and {len(issues) - args.limit} more")
        _print("")

    if args.output:
        path = config.path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        report.to_frame().to_csv(path, index=False)
        _print(f"wrote {path}")

    if args.strict:
        report.raise_if_critical()
    return 1 if report.critical else 0


def cmd_data_quality(args: argparse.Namespace) -> int:
    args.limit = 0
    args.output = args.output or "reports/data_quality.csv"
    args.strict = False
    return cmd_validate_data(args)


def _rank_as_of(config: AppConfig, data, as_of: dt.date):
    view = data.market.view(as_of, strict=config.backtest.strict_point_in_time)
    universe = view.universe()
    factors, rejections = compute_universe_factors(view, universe, config)
    ranking = rank_and_select(factors, config, as_of, rejections=rejections)
    return view, factors, ranking


def cmd_rank(args: argparse.Namespace) -> int:
    config = _load(args)
    _print(BANNER)
    data = load_market_data(
        config, max_tickers=args.max_tickers,
        members_as_of=args.as_of or config.backtest.end_date,
    )
    _warn_if_synthetic(data)

    as_of = args.as_of or data.market.calendar[-1].date()
    as_of = data.market.previous_trading_day(as_of, inclusive=True)
    view, factors, ranking = _rank_as_of(config, data, as_of)

    _print("")
    _print(f"Rankings as of {as_of} — {ranking.universe_size} eligible of {len(view.universe())} members")
    _print("")
    table = ranking.sleeve_table("sector_leaders")
    if table.empty:
        _print("No stocks could be ranked.")
        return 1
    for sector in sorted(table["sector"].dropna().unique()):
        group = table[table["sector"] == sector].sort_values("rank").head(args.top)
        _print(sector)
        for row in group.itertuples():
            marker = "*" if row.selected else " "
            _print(
                f" {marker}{int(row.rank):>2}. {str(row.Index):<7}{row.total_score:>6.1f}  "
                f"G{row.growth:>4.0f} M{row.momentum:>4.0f} Q{row.quality:>4.0f} "
                f"V{row.valuation:>4.0f} R{row.risk:>4.0f}  {str(getattr(row,'company_name',''))[:34]}"
            )
        _print("")

    if ranking.warnings:
        _print("MODEL WARNINGS")
        for warning in ranking.warnings:
            _print(f"  ! {warning}")
        _print("")

    _print(f"HIGH-GROWTH TOP {config.growth_strategy.holdings}")
    for selection in ranking.high_growth:
        _print(f"  {selection.rank:>2}. {selection.ticker:<7}{selection.total_score:>6.1f}  {selection.sector}")
    _print("")

    if args.output:
        path = config.path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        ranking.table.reset_index().to_csv(path, index=False)
        _print(f"wrote {path}")
    if args.explain:
        for selection in ranking.high_growth[:5]:
            _print("")
            _print(selection.explanation())
    return 0


def cmd_weekly_report(args: argparse.Namespace) -> int:
    config = _load(args)
    _print(BANNER)
    data = load_market_data(
        config, max_tickers=args.max_tickers,
        members_as_of=args.as_of or config.backtest.end_date,
    )
    _warn_if_synthetic(data)
    market = data.market

    as_of = args.as_of or market.calendar[-1].date()
    as_of = market.previous_trading_day(as_of, inclusive=True)
    view, factors, ranking = _rank_as_of(config, data, as_of)

    previous = None
    try:
        previous_date = market.previous_trading_day(as_of - dt.timedelta(days=6), inclusive=True)
        _, _, previous = _rank_as_of(config, data, previous_date)
    except QuantError as exc:
        log.warning("could not rank the prior week for comparison: %s", exc)

    try:
        execution_date = market.next_trading_day(as_of)
    except QuantError:
        execution_date = None

    _print("")
    _print(f"decision date: {as_of}  (latest session in the loaded data)")
    if execution_date:
        _print(f"modeled fill:  {execution_date} at the open")
    staleness = (dt.date.today() - as_of).days
    if staleness > 7:
        _print(
            f"WARNING: that session is {staleness} days old. Check backtest.end_date "
            f"in config/settings.yaml is null, and re-run update-data."
        )
    elif as_of == dt.date.today() and as_of.weekday() < 5:
        # Providers serve a live, partial bar while the session is open. Every
        # factor would then be computed from an intraday price that is not the
        # close the model assumes.
        _print(
            "WARNING: the decision date is today and the market may still be open, so "
            "the last bar could be a partial session. Run after the close, or pass "
            "--as-of with the previous trading day."
        )

    markdown = render_weekly_report(
        ranking=ranking,
        config=config,
        view=view,
        market=market,
        previous=previous,
        execution_date=execution_date,
        data_quality_summary=data.quality.summary(),
    )
    if data.banner:
        markdown = f"> **{data.banner}**\n\n" + markdown

    paths = write_weekly_report(
        markdown, config.path(config.reporting.output_dir), as_of,
        prefix="SYNTHETIC_" if data.synthetic else "",
    )
    _print("")
    for kind, path in paths.items():
        _print(f"wrote {kind:<9}{path}")

    factor_csv = config.path(config.reporting.output_dir) / f"factor_scores_{as_of:%Y-%m-%d}.csv"
    factor_scores_frame(factors, as_of).to_csv(factor_csv, index=False)
    ranking_csv = config.path(config.reporting.output_dir) / f"stock_rankings_{as_of:%Y-%m-%d}.csv"
    ranking.table.reset_index().to_csv(ranking_csv, index=False)
    _print(f"wrote csv      {factor_csv}")
    _print(f"wrote csv      {ranking_csv}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    config = _load(args)
    _print(BANNER)
    _print("")
    data = load_market_data(config, force=args.force, max_tickers=args.max_tickers)
    _warn_if_synthetic(data)
    market = data.market

    db = open_project_database(config)
    try:
        existing = [row["run_id"] for row in db.query("SELECT run_id FROM backtest_runs")]
        run_id = args.run_id or next_run_id(existing)

        engine = BacktestEngine(config, market, run_id=run_id, progress=_print)
        result = engine.run()

        provenance = build_provenance(run_id, config, market, warnings=result.warnings)
        output_dir = config.path(config.reporting.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        provenance.write(output_dir / f"provenance_{run_id}.json")

        if not args.no_database:
            persist_backtest(db, result, provenance.to_dict(), config)
    finally:
        db.close()

    _print("")
    _print(result.comparison_table().to_string(float_format=lambda v: f"{v:,.4f}"))
    _print("")

    final_date = result.end_date
    primary = "combined" if "combined" in result.variants else next(iter(result.variants))
    sectors = {t: market.sector_of(t, final_date) for t in market.loaded_tickers()}

    stock_attr = contribution_attribution(result.variants[primary], final_date)
    sector_attr = sector_attribution(
        result.variants[primary], sectors, final_date, config.benchmark.ticker
    )
    allocation = ending_sector_allocation(result, sectors, final_date, primary)

    charts_map: dict[str, str] = {}
    if config.reporting.generate_charts:
        chart_dir = config.path(config.reporting.chart_dir)
        chart_set = generate_charts(result, chart_dir, sector_attr, stock_attr, allocation, args.theme)
        charts_map = chart_set.relative(output_dir)
        _print(f"wrote {len(chart_set)} charts to {chart_dir}")

    rankings = rankings_frame(result.rankings, limit_dates=args.ranking_history)
    last_date = max(result.rankings) if result.rankings else final_date
    last_view = market.view(last_date, strict=False)
    last_factors, _ = compute_universe_factors(last_view, last_view.universe(), config)
    factor_scores = factor_scores_frame(last_factors, last_date)
    sector_rankings = rankings[rankings["sleeve"] == "sector_leaders"] if not rankings.empty else pd.DataFrame()

    monthly = monthly_returns_table(result.variants[primary].returns)
    annual = annual_returns_table(result.variants)
    positions = ending_positions_frame(result, final_date)

    written = export_csvs(result, output_dir, rankings, factor_scores, stock_attr, sector_attr)
    _print(f"wrote {len(written)} CSV files to {output_dir}")

    if config.reporting.generate_excel:
        workbook = write_workbook(
            output_dir / "backtest_summary.xlsx", result, rankings, factor_scores,
            sector_rankings, monthly, annual, positions, provenance.to_dict(),
        )
        _print(f"wrote {workbook}")

    markdown = render_backtest_report(
        result=result, config=config, provenance=provenance.to_dict(), charts=charts_map,
        sector_attribution=sector_attr, stock_attribution=stock_attr,
        annual_returns=annual, monthly_returns=monthly,
        data_quality_summary=data.quality.summary(),
    )
    if data.banner:
        markdown = f"> **{data.banner}**\n\n" + markdown
    paths = write_backtest_report(markdown, output_dir, run_id)
    for kind, path in paths.items():
        _print(f"wrote {kind:<9}{path}")

    _print("")
    _print(f"run id: {run_id}")
    if result.warnings:
        _print(f"{len(result.warnings)} warning(s) recorded — see the report and logs/app.log")
    if result.limit_breaches:
        _print(f"{len(result.limit_breaches)} concentration-limit breach(es) reported "
               f"(enforce={config.limits.enforce})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    config = _load(args)
    db = open_project_database(config)
    try:
        run_id = args.run_id or db.latest_run_id()
        if run_id is None:
            _print("no stored backtest runs; run `python main.py backtest` first")
            return 1
        output_dir = config.path(args.output or config.reporting.output_dir) / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        exports = {
            "backtest_metrics": "SELECT * FROM backtest_metrics WHERE run_id = ?",
            "portfolio_values": "SELECT * FROM portfolio_values WHERE run_id = ? ORDER BY variant, date",
            "portfolio_transactions": "SELECT * FROM portfolio_transactions WHERE run_id = ? ORDER BY variant, sequence",
            "rankings": "SELECT * FROM rankings WHERE run_id = ? ORDER BY decision_date, sleeve, rank",
        }
        for name, sql in exports.items():
            rows = [dict(row) for row in db.query(sql, (run_id,))]
            frame = pd.DataFrame(rows)
            path = output_dir / f"{name}.csv"
            frame.to_csv(path, index=False)
            _print(f"wrote {path} ({len(frame):,} rows)")

        config_path = output_dir / "config.json"
        config_path.write_text(json.dumps(db.load_run_config(run_id), indent=2), encoding="utf-8")
        _print(f"wrote {config_path}")
    finally:
        db.close()
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    config = _load(args)
    db = open_project_database(config)
    try:
        rows = db.query(
            "SELECT run_id, created_at, strategy_version, start_date, end_date, num_weeks "
            "FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
            (args.limit,),
        )
        if not rows:
            _print("no stored backtest runs")
            return 0
        _print(f"{'RUN ID':<24}{'CREATED':<21}{'STRAT':<7}{'FROM':<12}{'TO':<12}{'WEEKS':>6}")
        for row in rows:
            _print(
                f"{row['run_id']:<24}{str(row['created_at'])[:19]:<21}"
                f"{row['strategy_version']:<7}{row['start_date']:<12}{row['end_date']:<12}"
                f"{row['num_weeks']:>6}"
            )
    finally:
        db.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the local web UI and JSON API."""
    config = _load(args)
    _print(BANNER)
    _print("")
    _print(f"  dashboard  http://{args.host}:{args.port}/")
    _print(f"  API docs   http://{args.host}:{args.port}/docs")
    _print("")
    if args.host not in ("127.0.0.1", "localhost"):
        _print(
            "WARNING: binding to a non-loopback address. This server has no "
            "authentication and exposes your research data. Put a proxy with "
            "auth in front of it, or bind to 127.0.0.1."
        )
        _print("")
    from quant.api import run_server

    run_server(host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_holdings(args: argparse.Namespace) -> int:
    """Show, set or import the live position file."""
    config = _load(args)
    from quant.portfolio.holdings import Holdings, default_path

    path = default_path(PROJECT_ROOT)
    if args.action == "import":
        if not args.file:
            _print("ERROR: --file is required for `holdings import`")
            return 2
        holdings = Holdings.from_csv(config.path(args.file))
        existing = Holdings.load(path)
        holdings.last_rebalance = existing.last_rebalance
        holdings.save(path)
        _print(f"imported {len(holdings.positions)} position(s) into {path}")
        return 0

    if args.action == "set":
        if not args.ticker or args.shares is None:
            _print("ERROR: --ticker and --shares are required for `holdings set`")
            return 2
        holdings = Holdings.load(path)
        holdings.set_position(args.ticker, args.shares, args.cost_basis)
        holdings.updated_at = dt.date.today()
        holdings.save(path)
        _print(f"{args.ticker.upper()}: {args.shares} shares")
        return 0

    if args.action == "clear":
        Holdings(updated_at=dt.date.today()).save(path)
        _print(f"cleared {path}")
        return 0

    holdings = Holdings.load(path)
    if not holdings.positions:
        _print(f"no positions recorded in {path}")
        return 0
    _print(f"{len(holdings.positions)} position(s), updated {holdings.updated_at}, "
           f"last rebalance {holdings.last_rebalance or 'never'}")
    _print(f"{'TICKER':<10}{'SHARES':>14}{'COST BASIS':>14}")
    for lot in sorted(holdings.positions.values(), key=lambda l: l.ticker):
        _print(f"{lot.ticker:<10}{lot.shares:>14,.4f}{lot.cost_basis:>14,.2f}")
    return 0


def cmd_trade_plan(args: argparse.Namespace) -> int:
    """Diff current holdings against this week's target basket."""
    config = _load(args)
    _print(BANNER)
    from quant.service import AppService

    service = AppService(PROJECT_ROOT, config)
    plan, meta = service.trade_plan(args.contribution, args.mode, args.as_of)

    _print("")
    _print(f"decision date {plan.as_of} | mode {plan.mode} ({meta['rebalance_reason']})")
    _print(f"portfolio {plan.portfolio_value_before:,.0f} -> {plan.portfolio_value_after:,.0f} "
           f"with a {plan.contribution:,.0f} contribution")
    _print("")
    if not plan.trades:
        _print("no trades proposed")
        return 0
    _print(f"{'ACTION':<8}{'TICKER':<8}{'AMOUNT':>12}{'SHARES':>12}{'NOW':>8}{'TARGET':>8}  REASON")
    for trade in plan.trades:
        _print(
            f"{trade.action:<8}{trade.ticker:<8}{trade.amount:>12,.2f}{trade.shares:>12,.4f}"
            f"{trade.current_weight * 100:>7.2f}%{trade.target_weight * 100:>7.2f}%  {trade.reason}"
        )
    _print("")
    _print(f"buys {plan.total_buys:,.2f} | sells {plan.total_sells:,.2f}")
    for note in plan.notes:
        _print(f"note: {note}")
    _print("")
    _print("These are proposals. Nothing has been ordered.")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    """Print the contribution schedule, to audit the decision/execution split."""
    config = _load(args)
    data = load_market_data(config, max_tickers=5, with_fundamentals=False)
    schedule = build_schedule(data.market, config)
    _print(f"{len(schedule)} contributions")
    _print(f"{'#':>4}  {'DECISION':<12}{'EXECUTION':<12}{'PRICE FIELD':<12}{'AMOUNT':>10}")
    # Head and tail would overlap and print duplicates on a short schedule.
    shown = schedule if args.all or len(schedule) <= 20 else schedule[:10] + schedule[-10:]
    for event in shown:
        _print(
            f"{event.index:>4}  {str(event.decision_date):<12}{str(event.execution_date):<12}"
            f"{event.price_field:<12}{event.amount:>10,.2f}"
        )
    return 0


# --- argument parsing ------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"quant-stock-selector {__version__}")
    parser.add_argument("--config", default="config", help="configuration directory (default: config)")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser, data_flags: bool = True) -> None:
        if data_flags:
            subparser.add_argument("--provider", choices=["yfinance", "alphavantage", "synthetic"],
                                   help="override data.provider")
            subparser.add_argument("--universe-source", choices=["wikipedia", "static", "synthetic", "custom"],
                                   help="override universe.source")
            subparser.add_argument("--max-tickers", type=int, default=None,
                                   help="limit the universe size (useful for a quick trial run)")
            subparser.add_argument("--fundamentals", choices=["sec", "yfinance", "synthetic", "none"],
                                   help="override data.fundamentals_provider")

    update = subparsers.add_parser("update-data", help="download and cache market data")
    add_common(update)
    update.add_argument("--force", action="store_true", help="ignore the cache and re-download")
    update.add_argument("--skip-fundamentals", action="store_true")
    update.add_argument("--no-database", action="store_true", help="do not write to SQLite")
    update.set_defaults(func=cmd_update_data)

    validate = subparsers.add_parser("validate-data", help="run data-quality checks")
    add_common(validate)
    validate.add_argument("--limit", type=int, default=20, help="issues to print per severity")
    validate.add_argument("--output", default=None, help="write all issues to this CSV")
    validate.add_argument("--strict", action="store_true", help="exit non-zero on critical issues")
    validate.set_defaults(func=cmd_validate_data)

    quality = subparsers.add_parser("data-quality", help="the data-quality dashboard")
    add_common(quality)
    quality.add_argument("--output", default=None)
    quality.set_defaults(func=cmd_data_quality)

    rank = subparsers.add_parser("rank", help="rank the universe as of a date")
    add_common(rank)
    rank.add_argument("--as-of", type=_parse_date, default=None, help="decision date (default: latest)")
    rank.add_argument("--top", type=int, default=5, help="rows to show per sector")
    rank.add_argument("--output", default=None, help="write the full ranking to this CSV")
    rank.add_argument("--explain", action="store_true", help="print factor explanations")
    rank.add_argument("--no-strict", action="store_true")
    rank.set_defaults(func=cmd_rank)

    weekly = subparsers.add_parser("weekly-report", help="generate the weekly report")
    add_common(weekly)
    weekly.add_argument("--as-of", type=_parse_date, default=None)
    weekly.add_argument("--no-strict", action="store_true")
    weekly.set_defaults(func=cmd_weekly_report)

    backtest = subparsers.add_parser("backtest", help="run the historical backtest")
    add_common(backtest)
    backtest.add_argument("--start", type=_parse_date, default=None)
    backtest.add_argument("--end", type=_parse_date, default=None)
    backtest.add_argument("--weeks", type=int, default=None, help="number of weekly contributions")
    backtest.add_argument("--variants", nargs="+",
                          choices=["ivv", "sector_leaders", "high_growth", "combined"])
    backtest.add_argument("--weights", default=None,
                          help="override scoring weights, e.g. growth=0.4,momentum=0.3,quality=0.2,valuation=0.05,risk=0.05")
    backtest.add_argument("--run-id", default=None, help="reuse a specific run ID")
    backtest.add_argument("--force", action="store_true", help="re-download data")
    backtest.add_argument("--no-strict", action="store_true", help="disable point-in-time verification")
    backtest.add_argument("--no-charts", action="store_true")
    backtest.add_argument("--no-excel", action="store_true")
    backtest.add_argument("--no-database", action="store_true")
    backtest.add_argument("--theme", choices=["light", "dark"], default="light")
    backtest.add_argument("--ranking-history", type=int, default=52,
                          help="weeks of ranking history to export (default: 52)")
    backtest.set_defaults(func=cmd_backtest)

    export = subparsers.add_parser("export", help="export a stored run to CSV")
    export.add_argument("--run-id", default=None, help="default: the most recent run")
    export.add_argument("--output", default=None)
    export.set_defaults(func=cmd_export)

    runs = subparsers.add_parser("runs", help="list stored backtest runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.set_defaults(func=cmd_runs)

    serve = subparsers.add_parser("serve", help="start the web UI and JSON API")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.set_defaults(func=cmd_serve)

    holdings = subparsers.add_parser("holdings", help="show or edit your live positions")
    holdings.add_argument("action", nargs="?", default="show",
                          choices=["show", "set", "import", "clear"])
    holdings.add_argument("--ticker")
    holdings.add_argument("--shares", type=float)
    holdings.add_argument("--cost-basis", type=float, default=None)
    holdings.add_argument("--file", help="broker CSV with ticker and shares columns")
    holdings.set_defaults(func=cmd_holdings)

    plan = subparsers.add_parser("trade-plan", help="diff your holdings against this week's target")
    add_common(plan)
    plan.add_argument("--contribution", type=float, default=None)
    plan.add_argument("--mode", choices=["contribute", "rebalance"], default=None)
    plan.add_argument("--as-of", type=_parse_date, default=None)
    plan.set_defaults(func=cmd_trade_plan)

    schedule = subparsers.add_parser("schedule", help="print the contribution schedule")
    add_common(schedule)
    schedule.add_argument("--start", type=_parse_date, default=None)
    schedule.add_argument("--end", type=_parse_date, default=None)
    schedule.add_argument("--weeks", type=int, default=None)
    schedule.add_argument("--all", action="store_true", help="print every contribution")
    schedule.set_defaults(func=cmd_schedule)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except QuantError as exc:
        configure_logging()
        log.error("%s: %s", type(exc).__name__, exc)
        _print("")
        _print(f"ERROR ({type(exc).__name__}): {exc}")
        return 2
    except KeyboardInterrupt:
        _print("\ninterrupted")
        return 130
