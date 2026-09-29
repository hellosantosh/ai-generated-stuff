"""Historical backtest report (REQUIREMENTS 27, 28, 64).

Structure follows the requirement list: executive summary, comparison table,
charts, rolling returns, year-by-year and monthly returns, attribution,
turnover, risk, drawdowns and stress periods, then the limitations section.

The limitations section is not boilerplate. It states the survivorship status
of the universe, whether sector classification was point-in-time, the
execution convention, the transaction-cost assumptions and which factors were
unavailable - the things that decide whether the headline number means
anything.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import AppConfig
from ..logging_config import get_logger
from .formatting import (
    DISCLAIMER,
    frame_to_markdown,
    integer,
    markdown_table,
    money,
    number,
    percent,
    signed_percent,
)
from .weekly import HTML_TEMPLATE, markdown_to_html

log = get_logger(__name__)

COMPARISON_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Weekly contribution", "Weekly contribution", "money"),
    ("Total contributions", "Total contributions", "money"),
    ("Ending balance", "Ending balance", "money"),
    ("Gain", "Gain", "money"),
    ("Return on contributions", "Return on contributions", "percent"),
    ("Time-weighted return", "Time-weighted return", "percent"),
    ("CAGR (time-weighted)", "CAGR (TWR)", "percent"),
    ("XIRR (money-weighted)", "XIRR", "percent"),
    ("Maximum drawdown", "Maximum drawdown", "percent"),
    ("Volatility (annualized)", "Volatility", "percent"),
    ("Sharpe", "Sharpe", "number"),
    ("Sortino", "Sortino", "number"),
    ("Beta vs IVV", "Beta", "number"),
    ("Tracking error", "Tracking error", "percent"),
    ("Turnover (annual)", "Turnover", "percent"),
    ("Trades", "Trades", "integer"),
    ("Holdings at end", "Holdings", "integer"),
    ("Weeks outperforming IVV", "Weeks outperforming IVV", "percent"),
    ("Months outperforming IVV", "Months outperforming IVV", "percent"),
)

_FORMATTERS = {
    "money": lambda v: money(v, 0),
    "percent": lambda v: percent(v, 2),
    "number": lambda v: number(v, 3),
    "integer": integer,
}

VARIANT_LABELS = {
    "ivv": "IVV only",
    "sector_leaders": "Sector leaders",
    "high_growth": "High growth",
    "combined": "Combined",
}


def _comparison_markdown(result) -> str:
    table = result.comparison_table()
    variants = [v for v in ("ivv", "sector_leaders", "high_growth", "combined") if v in table.columns]
    variants += [v for v in table.columns if v not in variants]
    headers = ["Metric"] + [VARIANT_LABELS.get(v, v) for v in variants]
    rows = []
    for label, key, kind in COMPARISON_ROWS:
        if key not in table.index:
            continue
        formatter = _FORMATTERS[kind]
        rows.append([label] + [formatter(table.loc[key, v]) for v in variants])
    return markdown_table(rows, headers, ["left"] + ["right"] * len(variants))


def _executive_summary(result, config: AppConfig) -> list[str]:
    lines: list[str] = []
    primary = "combined" if "combined" in result.variants else next(iter(result.variants))
    strategy = result.variants[primary]
    benchmark = result.variants.get("ivv")

    metrics = strategy.metrics
    lines.append(
        f"Over {metrics.num_contributions} weekly contributions from {metrics.start_date} to "
        f"{metrics.end_date} ({metrics.years:.1f} years), the **{VARIANT_LABELS.get(primary, primary)}** "
        f"portfolio received {money(metrics.total_contributions, 0)} and ended at "
        f"{money(metrics.ending_value, 0)}, a gain of {money(metrics.total_gain, 0)} "
        f"({percent(metrics.return_on_contributions)} on contributions)."
    )
    lines.append("")

    if benchmark is not None and primary != "ivv":
        benchmark_metrics = benchmark.metrics
        twr_gap = metrics.time_weighted_return - benchmark_metrics.time_weighted_return
        verdict = "outperformed" if twr_gap > 0 else "underperformed"
        lines.append(
            f"On a time-weighted basis the strategy returned {percent(metrics.time_weighted_return)} "
            f"against {percent(benchmark_metrics.time_weighted_return)} for a "
            f"{money(benchmark_metrics.weekly_contribution, 0)}/week IVV position — it **{verdict}** "
            f"the benchmark by {signed_percent(twr_gap)} cumulatively "
            f"({signed_percent(metrics.cagr_twr - benchmark_metrics.cagr_twr)} annualized)."
        )
        lines.append("")
        lines.append(
            f"It did so with {percent(metrics.volatility)} annualized volatility against "
            f"{percent(benchmark_metrics.volatility)}, a worst drawdown of "
            f"{percent(metrics.max_drawdown)} against {percent(benchmark_metrics.max_drawdown)}, "
            f"and a Sharpe ratio of {number(metrics.sharpe, 2)} against "
            f"{number(benchmark_metrics.sharpe, 2)}. It beat IVV in "
            f"{percent(metrics.weeks_outperforming, 1)} of weeks and "
            f"{percent(metrics.months_outperforming, 1)} of months."
        )
        lines.append("")
        lines.append(
            "Whether that margin is a real edge or the residue of one historical path cannot be "
            "settled by this backtest. The out-of-sample split below is the relevant check, and "
            "the limitations section lists the biases that remain."
        )
    lines.append("")
    return lines


def _out_of_sample(result, config: AppConfig) -> str:
    """Split the record into thirds and report each (REQUIREMENTS 50)."""
    primary = "combined" if "combined" in result.variants else next(iter(result.variants))
    returns = result.variants[primary].returns.dropna()
    benchmark = result.variants["ivv"].returns.dropna() if "ivv" in result.variants else pd.Series(dtype=float)
    if len(returns) < 30:
        return "_Not enough history to split into periods._"

    thirds = np.array_split(np.arange(len(returns)), 3)
    labels = ["In-sample (first third)", "Validation (second third)", "Out-of-sample (final third)"]
    rows = []
    for label, positions in zip(labels, thirds):
        window = returns.iloc[positions]
        growth = float((1.0 + window).prod() - 1.0)
        benchmark_growth = float("nan")
        if not benchmark.empty:
            aligned = benchmark.reindex(window.index).dropna()
            if not aligned.empty:
                benchmark_growth = float((1.0 + aligned).prod() - 1.0)
        rows.append([
            label,
            f"{window.index[0].date()} to {window.index[-1].date()}" if isinstance(window.index, pd.DatetimeIndex) else f"{len(window)} weeks",
            percent(growth),
            percent(benchmark_growth),
            signed_percent(growth - benchmark_growth) if np.isfinite(benchmark_growth) else "n/a",
        ])
    return markdown_table(
        rows, ["Period", "Dates", "Strategy", "IVV", "Excess"],
        ["left", "left", "right", "right", "right"],
    )


def render_backtest_report(
    result,
    config: AppConfig,
    provenance: Mapping[str, Any],
    charts: Mapping[str, str] | None = None,
    sector_attribution: pd.DataFrame | None = None,
    stock_attribution: pd.DataFrame | None = None,
    annual_returns: pd.DataFrame | None = None,
    monthly_returns: pd.DataFrame | None = None,
    data_quality_summary: str | None = None,
) -> str:
    charts = charts or {}
    lines: list[str] = []

    lines.append("# Historical backtest report")
    lines.append("")
    lines.append(f"**Run ID:** `{result.run_id}`  ")
    lines.append(f"**Strategy version:** {config.strategy.version}  ")
    lines.append(f"**Period:** {result.start_date} to {result.end_date} "
                 f"({len(result.schedule)} weekly contributions)  ")
    lines.append(f"**Data snapshot:** `{provenance.get('data_snapshot_hash', 'unknown')}`  ")
    lines.append(f"**Generated:** {dt.datetime.now():%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    lines.append("")

    lines.append("## 1. Executive summary")
    lines.append("")
    lines.extend(_executive_summary(result, config))

    lines.append("## 2. Portfolio comparison")
    lines.append("")
    lines.append(_comparison_markdown(result))
    lines.append("")
    lines.append(
        "_The IVV column invests the full weekly contribution in the benchmark. The sleeve "
        "columns run that sleeve alone on its own budget, and Combined splits the "
        "contribution across all three._"
    )
    lines.append("")

    for key, heading, caption in (
        ("portfolio_value", "3. Portfolio value over time", "Value of each variant against cumulative contributions."),
        ("portfolio_vs_ivv", "4. Strategy versus IVV", "Time-weighted index, contribution timing removed."),
        ("drawdown", "5. Drawdown", "Measured on the time-weighted index."),
        ("rolling_12m", "6. Rolling 12-month returns", ""),
        ("rolling_36m", "7. Rolling 36-month returns", ""),
    ):
        lines.append(f"## {heading}")
        lines.append("")
        if key in charts:
            lines.append(f"![{heading}]({charts[key]})")
            lines.append("")
        if caption:
            lines.append(f"_{caption}_")
            lines.append("")

    lines.append("## 8. Year-by-year performance")
    lines.append("")
    if annual_returns is not None and not annual_returns.empty:
        display = annual_returns.copy()
        for column in display.columns:
            if column != "year":
                display[column] = display[column].map(lambda v: percent(v) if pd.notna(v) else "")
        lines.append(frame_to_markdown(display))
    else:
        lines.append("_Not enough history for annual figures._")
    lines.append("")

    lines.append("## 9. Monthly returns")
    lines.append("")
    if monthly_returns is not None and not monthly_returns.empty:
        display = monthly_returns.copy()
        for column in display.columns:
            if column != "year":
                display[column] = display[column].map(lambda v: percent(v, 1) if pd.notna(v) else "")
        lines.append(frame_to_markdown(display))
        lines.append("")
        lines.append("_Combined portfolio, time-weighted._")
    else:
        lines.append("_Not enough history for monthly figures._")
    lines.append("")

    lines.append("## 10. Sector contribution")
    lines.append("")
    if "sector_contribution" in charts:
        lines.append(f"![Sector contribution]({charts['sector_contribution']})")
        lines.append("")
    if sector_attribution is not None and not sector_attribution.empty:
        rows = [
            [row.sector, money(row.invested, 0), money(row.current_value, 0), money(row.pnl, 0)]
            for row in sector_attribution.itertuples()
        ]
        lines.append(markdown_table(
            rows, ["Sector", "Invested", "Ending value", "Profit and loss"],
            ["left", "right", "right", "right"],
        ))
    lines.append("")

    lines.append("## 11. Individual stock contribution")
    lines.append("")
    for key, heading in (("top_winners", "Top contributors"), ("top_losers", "Largest detractors")):
        if key in charts:
            lines.append(f"![{heading}]({charts[key]})")
            lines.append("")
    if stock_attribution is not None and not stock_attribution.empty:
        top = stock_attribution.head(10)
        bottom = stock_attribution.tail(10).iloc[::-1]
        rows = [[r.ticker, r.sleeve, money(r.invested, 0), money(r.pnl, 0)] for r in top.itertuples()]
        lines.append("**Top 10 contributors**")
        lines.append("")
        lines.append(markdown_table(rows, ["Ticker", "Sleeve", "Invested", "Profit and loss"],
                                    ["left", "left", "right", "right"]))
        lines.append("")
        rows = [[r.ticker, r.sleeve, money(r.invested, 0), money(r.pnl, 0)] for r in bottom.itertuples()]
        lines.append("**Bottom 10 contributors**")
        lines.append("")
        lines.append(markdown_table(rows, ["Ticker", "Sleeve", "Invested", "Profit and loss"],
                                    ["left", "left", "right", "right"]))
    lines.append("")

    lines.append("## 12. Allocation and turnover")
    lines.append("")
    for key, heading in (("allocation", "Ending allocation"), ("turnover", "Turnover")):
        if key in charts:
            lines.append(f"![{heading}]({charts[key]})")
            lines.append("")

    lines.append("## 13. Risk metrics")
    lines.append("")
    rows = []
    for name, variant in result.variants.items():
        m = variant.metrics
        rows.append([
            VARIANT_LABELS.get(name, name), percent(m.volatility), percent(m.downside_volatility),
            number(m.sharpe, 2), number(m.sortino, 2), number(m.beta, 2),
            percent(m.tracking_error), percent(m.max_drawdown), integer(m.longest_underwater_days),
        ])
    lines.append(markdown_table(
        rows,
        ["Portfolio", "Volatility", "Downside vol", "Sharpe", "Sortino", "Beta", "Tracking error",
         "Max drawdown", "Longest underwater (days)"],
        ["left"] + ["right"] * 8,
    ))
    lines.append("")

    lines.append("## 14. Drawdowns and stress periods")
    lines.append("")
    primary = "combined" if "combined" in result.variants else next(iter(result.variants))
    for name in dict.fromkeys([primary, "ivv"]):
        if name not in result.variants:
            continue
        variant = result.variants[name]
        lines.append(f"### {VARIANT_LABELS.get(name, name)}")
        lines.append("")
        metrics = variant.metrics
        lines.append("```")
        lines.append("Maximum Drawdown")
        lines.append("----------------")
        lines.append(f"Peak:       {metrics.max_drawdown_peak}")
        lines.append(f"Trough:     {metrics.max_drawdown_trough}")
        lines.append(f"Drawdown:   {percent(metrics.max_drawdown, 1)}")
        lines.append(f"Recovery:   {metrics.max_drawdown_recovery or 'not recovered within the window'}")
        lines.append(f"Duration:   {metrics.max_drawdown_days} days to trough")
        lines.append("```")
        lines.append("")
        if not variant.drawdowns.empty:
            rows = [
                [str(r.peak_date), str(r.trough_date), str(r.recovery_date or "—"),
                 percent(r.drawdown, 1), integer(r.days_to_trough),
                 integer(r.days_to_recovery) if pd.notna(r.days_to_recovery) else "—"]
                for r in variant.drawdowns.head(6).itertuples()
            ]
            lines.append(markdown_table(
                rows, ["Peak", "Trough", "Recovery", "Depth", "Days to trough", "Days to recovery"],
                ["left", "left", "left", "right", "right", "right"],
            ))
            lines.append("")
        if not variant.special_periods.empty:
            rows = [
                [r.period, str(r.start), str(r.end),
                 percent(r._4) if pd.notna(r._4) else "—",
                 percent(r.max_drawdown) if pd.notna(r.max_drawdown) else "—",
                 r.note or ""]
                for r in variant.special_periods.itertuples()
            ]
            lines.append(markdown_table(
                rows, ["Stress period", "From", "To", "Return", "Worst drawdown", "Note"],
                ["left", "left", "left", "right", "right", "left"],
            ))
            lines.append("")

    lines.append("## 15. Best and worst periods")
    lines.append("")
    returns = result.variants[primary].returns.dropna()
    if len(returns) > 5:
        best = returns.nlargest(5)
        worst = returns.nsmallest(5)
        rows = [[str(idx.date() if hasattr(idx, "date") else idx), percent(value)] for idx, value in best.items()]
        lines.append("**Best weeks**")
        lines.append("")
        lines.append(markdown_table(rows, ["Week ending", "Return"], ["left", "right"]))
        lines.append("")
        rows = [[str(idx.date() if hasattr(idx, "date") else idx), percent(value)] for idx, value in worst.items()]
        lines.append("**Worst weeks**")
        lines.append("")
        lines.append(markdown_table(rows, ["Week ending", "Return"], ["left", "right"]))
        lines.append("")

    lines.append("## 16. In-sample, validation and out-of-sample")
    lines.append("")
    lines.append(
        "The scoring weights shipped in `config/scoring.yaml` were not fitted to this data, so "
        "the whole window is out-of-sample with respect to them. The split below is still the "
        "honest check to read if you change the weights and re-run (REQUIREMENTS 50)."
    )
    lines.append("")
    lines.append(_out_of_sample(result, config))
    lines.append("")

    lines.append("## 17. Limitations, assumptions and data quality")
    lines.append("")
    universe = provenance.get("universe", {})
    limitations = [
        f"**Survivorship bias:** {universe.get('bias_note') or 'not assessed'}",
        f"**Sector classification:** "
        + ("point-in-time." if universe.get("sector_classification_point_in_time")
           else "current classifications applied to all history. A company that changed GICS "
                "sector is ranked against today's peers throughout."),
        f"**Look-ahead control:** fundamentals are filtered on filing date, not fiscal period end. "
        f"Strict point-in-time verification was "
        + ("enabled" if config.backtest.strict_point_in_time else "disabled") + " for this run.",
        f"**Execution:** decide on the {config.backtest.contribution_day_name} close, fill at "
        + ("the next trading day's open" if config.backtest.execution == "next_open" else "that close")
        + ". The fill is always strictly after the signal.",
        f"**Transaction costs:** {number(config.transaction_cost.slippage_bps, 1)} bps slippage and "
        f"{money(config.transaction_cost.commission_per_trade)} commission per trade. Real spreads, "
        f"market impact and partial fills are not modeled.",
        f"**Dividends:** credited as cash on the ex-date and redeployed at the next weekly "
        f"contribution, rather than reinvested intraday. Return mode: "
        f"{config.backtest.return_mode.replace('_', ' ')}.",
        "**Taxes:** not modeled. All figures are pre-tax (REQUIREMENTS 53).",
        "**Forward-looking factors:** forward P/E, analyst estimates and revisions are not used. "
        "No point-in-time source was available, and using current estimates historically would "
        "leak future information.",
        "**Fractional shares** are assumed available at every broker, and cash is assumed to earn "
        "no interest while uninvested.",
        f"**Parameters:** scoring weights, sleeve sizes and rebalance frequencies come from "
        f"`config/`. Re-running with different weights and reporting the best result would be "
        f"overfitting; the strategy version ({config.strategy.version}) is recorded with this run "
        f"so results stay attributable to a specific model.",
    ]
    if config.risk_controls.any_active:
        limitations.append("**Risk controls are ACTIVE for this run** — see the configuration sheet.")
    else:
        limitations.append("**Risk controls:** disabled (the default).")
    if result.limit_breaches:
        limitations.append(
            f"**Concentration limits:** {len(result.limit_breaches)} distinct breach(es) were "
            f"detected and reported without altering the selection "
            f"(`limits.enforce` is {config.limits.enforce})."
        )
    for limitation in limitations:
        lines.append(f"- {limitation}")
    lines.append("")

    if result.warnings:
        lines.append("### Run warnings")
        lines.append("")
        for warning in result.warnings[:25]:
            lines.append(f"- {warning}")
        if len(result.warnings) > 25:
            lines.append(f"- _...and {len(result.warnings) - 25} more (see `logs/app.log`)._")
        lines.append("")

    if data_quality_summary:
        lines.append("### Data quality")
        lines.append("")
        lines.append("```")
        lines.append(data_quality_summary)
        lines.append("```")
        lines.append("")

    lines.append("## 18. Reproducing this run")
    lines.append("")
    lines.append("```bash")
    lines.append(f"python main.py backtest --run-id {result.run_id} \\")
    lines.append(f"    --start {result.start_date} --end {result.end_date}")
    lines.append("```")
    lines.append("")
    lines.append(markdown_table(
        [
            ["Software version", provenance.get("software_version", "?")],
            ["Strategy version", provenance.get("strategy_version", "?")],
            ["Python", provenance.get("python_version", "?")],
            ["Platform", provenance.get("platform", "?")],
            ["Git commit", provenance.get("git_commit") or "not a git checkout"],
            ["Data snapshot hash", provenance.get("data_snapshot_hash", "?")],
            ["Universe source", universe.get("source", "?")],
            ["Universe members (ever)", str(universe.get("ever_members", "?"))],
        ],
        ["Item", "Value"], ["left", "left"],
    ))
    lines.append("")
    lines.append("_The same configuration against the same data snapshot hash reproduces these "
                 "numbers exactly._")
    lines.append("")
    return "\n".join(lines)


def write_backtest_report(
    markdown: str, directory: Path, run_id: str, title: str | None = None
) -> dict[str, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    markdown_path = directory / "backtest_report.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    html_path = directory / "backtest_report.html"
    html_path.write_text(
        HTML_TEMPLATE.format(
            title=html.escape(title or f"Backtest report {run_id}"),
            body=markdown_to_html(markdown),
        ),
        encoding="utf-8",
    )
    log.info("wrote backtest report %s and %s", markdown_path, html_path)
    return {"markdown": markdown_path, "html": html_path}
