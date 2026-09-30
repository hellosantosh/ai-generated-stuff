"""Weekly decision report in Markdown and HTML (REQUIREMENTS 26, 57, 58).

The report states the market backdrop, every sector's ranking, the recommended
portfolio with a deterministic explanation per holding, the changes since last
week, and the data caveats that apply to the run.

The ACTION column is a description of how the model's selection changed. It is
not a trade instruction and the software places no trades (REQUIREMENTS 3, 26).
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import AppConfig
from ..data.store import MarketData, PointInTimeView
from ..logging_config import get_logger
from ..ranking.sector_ranker import GROWTH_SLEEVE, SECTOR_SLEEVE, RankingResult, Selection

# The benchmark core is not a ranked sleeve, so it needs its own budget key.
IVV_LABEL = "core"
from .formatting import (
    DISCLAIMER,
    frame_to_markdown,
    markdown_table,
    money,
    number,
    percent,
    rank_change_label,
    signed_percent,
)

log = get_logger(__name__)


def basket_weights(
    ranking: RankingResult, config: AppConfig
) -> tuple[list[tuple[str, float, str, float]], dict[str, float]]:
    """Consolidated portfolio weights as fractions of one contribution.

    Returns ``(rows, sleeve_totals)`` where each row is
    ``(ticker, weight, sleeves, score)``. Weights are expressed as a share of
    whatever is contributed, not as dollars, so the same basket can be traded
    at any contribution size.

    A ticker selected by both sleeves gets the **sum** of its two weights - it
    is one position in the basket, and listing it twice would understate it.
    An empty sleeve has its budget redistributed across the sleeves that did
    produce holdings, so the weights always total 100%.
    """
    strategy = config.strategy
    sector = ranking.sector_leaders
    growth = ranking.high_growth

    budgets = {
        IVV_LABEL: strategy.ivv_allocation,
        SECTOR_SLEEVE: strategy.sector_allocation if sector else 0.0,
        GROWTH_SLEEVE: strategy.growth_allocation if growth else 0.0,
    }
    total_budget = sum(budgets.values())
    if total_budget <= 0:
        return [], {}
    # Renormalize so an empty sleeve does not leave the basket short.
    budgets = {name: value / total_budget for name, value in budgets.items()}

    weights: dict[str, float] = {}
    sleeves: dict[str, list[str]] = {}
    scores: dict[str, float] = {}

    benchmark = config.benchmark.ticker
    if budgets[IVV_LABEL] > 0:
        weights[benchmark] = budgets[IVV_LABEL]
        sleeves[benchmark] = ["core"]
        scores[benchmark] = float("nan")

    for selections, sleeve_name, label in (
        (sector, SECTOR_SLEEVE, "sector"),
        (growth, GROWTH_SLEEVE, "growth"),
    ):
        if not selections or budgets[sleeve_name] <= 0:
            continue
        per_name = budgets[sleeve_name] / len(selections)
        for selection in selections:
            weights[selection.ticker] = weights.get(selection.ticker, 0.0) + per_name
            sleeves.setdefault(selection.ticker, []).append(label)
            scores[selection.ticker] = selection.total_score

    rows = [
        (ticker, weight, " + ".join(sleeves[ticker]), scores.get(ticker, float("nan")))
        for ticker, weight in weights.items()
    ]
    # Core first, then by weight: that is the order you would enter a basket.
    rows.sort(key=lambda row: (row[2] != "core", -row[1], row[0]))
    return rows, budgets


def _market_summary(view: PointInTimeView, config: AppConfig) -> list[tuple[str, float]]:
    """Benchmark returns, volatility and current drawdown at the decision date."""
    rows: list[tuple[str, str]] = []
    benchmark = config.benchmark.ticker
    if not view.has(benchmark):
        return [("Benchmark", f"{benchmark} price history unavailable")]

    total_return = view.total_return_series(benchmark)
    closes = view.close_series(benchmark)
    for label, lookback in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("12 months", 252)):
        if len(total_return) > lookback:
            change = float(total_return.iloc[-1] / total_return.iloc[-1 - lookback] - 1.0)
            rows.append((f"{benchmark} return, {label}", signed_percent(change)))

    if len(total_return) > 63:
        returns = total_return.iloc[-63:].pct_change().dropna()
        rows.append(("Market volatility (3m, annualized)", percent(float(returns.std(ddof=1) * np.sqrt(252)))))
    if len(closes) >= 252:
        high = float(closes.iloc[-252:].max())
        rows.append(("Current drawdown from 52-week high", signed_percent(float(closes.iloc[-1]) / high - 1.0)))
        ma200 = float(closes.iloc[-200:].mean())
        rows.append((
            "Market trend",
            "above the 200-day average" if float(closes.iloc[-1]) > ma200 else "below the 200-day average",
        ))
    return rows


def _selection_rows(
    selections: Sequence[Selection],
    weight_per_stock: Mapping[str, float],
    held: set[str],
) -> list[list[str]]:
    rows = []
    for selection in selections:
        if selection.previous_rank is None:
            action = "NEW"
        elif selection.ticker in held:
            action = "HOLD"
        else:
            action = "BUY"
        reason = "; ".join(selection.strengths[:2]) or "composite score rank"
        rows.append([
            selection.ticker,
            selection.sector,
            f"{selection.total_score:.1f}",
            percent(weight_per_stock.get(selection.ticker, 0.0), 3),
            reason,
            str(selection.previous_rank) if selection.previous_rank is not None else "-",
            action,
        ])
    return rows


def render_weekly_report(
    ranking: RankingResult,
    config: AppConfig,
    view: PointInTimeView,
    market: MarketData,
    previous: RankingResult | None = None,
    execution_date: dt.date | None = None,
    data_quality_summary: str | None = None,
) -> str:
    """Build the weekly report as Markdown."""
    decision_date = ranking.decision_date
    strategy = config.strategy
    lines: list[str] = []

    lines.append(f"# Quant weekly stock report")
    lines.append("")
    lines.append(f"**Decision date:** {decision_date} (using data through this session's close)  ")
    if execution_date:
        lines.append(f"**Modeled execution:** {execution_date} at the opening price  ")
    lines.append(f"**Strategy version:** {strategy.version}  ")
    lines.append(f"**Universe:** {market.universe.name} — {ranking.universe_size} eligible of "
                 f"{len(view.universe())} members")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    lines.append("")

    # --- market -------------------------------------------------------
    lines.append("## Market summary")
    lines.append("")
    summary_rows = _market_summary(view, config)
    lines.append(markdown_table([[k, v] for k, v in summary_rows], ["Measure", "Value"], ["left", "right"]))
    lines.append("")

    # --- allocation ---------------------------------------------------
    lines.append("## Recommended allocation")
    lines.append("")
    sector_selections = ranking.sector_leaders
    growth_selections = ranking.high_growth
    basket, budgets = basket_weights(ranking, config)

    per_sector_weight = (
        budgets.get(SECTOR_SLEEVE, 0.0) / len(sector_selections) if sector_selections else 0.0
    )
    per_growth_weight = (
        budgets.get(GROWTH_SLEEVE, 0.0) / len(growth_selections) if growth_selections else 0.0
    )
    allocation_per_stock = {s.ticker: per_sector_weight for s in sector_selections}
    growth_allocation = {s.ticker: per_growth_weight for s in growth_selections}

    lines.append(
        "Weights are shares of whatever you contribute this week, so the same basket "
        "works at any contribution size."
    )
    lines.append("")
    lines.append(markdown_table(
        [
            [f"{config.benchmark.ticker} core", percent(budgets.get(IVV_LABEL, 0.0)), "1 holding"],
            ["Sector leaders", percent(budgets.get(SECTOR_SLEEVE, 0.0)),
             f"{len(sector_selections)} holdings, {percent(per_sector_weight, 3)} each"],
            ["High growth", percent(budgets.get(GROWTH_SLEEVE, 0.0)),
             f"{len(growth_selections)} holdings, {percent(per_growth_weight, 3)} each"],
            ["**Total**", f"**{percent(sum(budgets.values()))}**", ""],
        ],
        ["Sleeve", "Share of contribution", "Detail"],
        ["left", "right", "left"],
    ))
    lines.append("")

    # --- the basket -----------------------------------------------------
    lines.append("## Basket")
    lines.append("")
    if not basket:
        lines.append("_No holdings could be selected this week._")
    else:
        lines.append(
            f"{len(basket)} positions. A ticker chosen by both sleeves appears once, "
            f"carrying the sum of its two weights."
        )
        lines.append("")
        rows = [
            [ticker, percent(weight, 3), sleeves, "" if pd.isna(score) else f"{score:.1f}"]
            for ticker, weight, sleeves, score in basket
        ]
        rows.append(["**Total**", f"**{percent(sum(r[1] for r in basket))}**", "", ""])
        lines.append(markdown_table(
            rows, ["Ticker", "Weight", "Sleeve", "Score"], ["left", "right", "left", "right"]
        ))
    lines.append("")

    # --- sector rankings ----------------------------------------------
    lines.append("## Sector rankings")
    lines.append("")
    table = ranking.sleeve_table(SECTOR_SLEEVE)
    previous_ranks = {}
    if previous is not None:
        previous_table = previous.sleeve_table(SECTOR_SLEEVE)
        if not previous_table.empty:
            previous_ranks = {str(t): int(r) for t, r in previous_table["rank"].items()}

    if table.empty:
        lines.append("_No stocks could be ranked at this decision date._")
    else:
        for sector in sorted(table["sector"].dropna().unique()):
            group = table[table["sector"] == sector].sort_values("rank").head(
                max(5, config.sector_strategy.holdings)
            )
            lines.append(f"### {sector}")
            lines.append("")
            rows = []
            for row in group.itertuples():
                previous_rank = previous_ranks.get(str(row.Index))
                change = None if previous_rank is None else previous_rank - int(row.rank)
                marker = " **✓**" if row.selected else ""
                rows.append([
                    f"{int(row.rank)}{marker}",
                    str(row.Index),
                    str(getattr(row, "company_name", "") or ""),
                    f"{row.total_score:.1f}",
                    str(previous_rank) if previous_rank is not None else "-",
                    rank_change_label(change),
                ])
            lines.append(markdown_table(
                rows, ["Rank", "Ticker", "Company", "Score", "Prev", "Change"],
                ["right", "left", "left", "right", "right", "right"],
            ))
            lines.append("")

    # --- recommended portfolio ----------------------------------------
    lines.append("## Recommended portfolio")
    lines.append("")
    lines.append("Actions describe how the model's selection changed since last week. "
                 "They are model output, not trade instructions.")
    lines.append("")
    held = set(previous.tickers(SECTOR_SLEEVE)) if previous else set()
    lines.append("### Sector leaders")
    lines.append("")
    rows = _selection_rows(sector_selections, allocation_per_stock, held)
    lines.append(markdown_table(
        rows, ["Ticker", "Sector", "Score", "Weight", "Reason for selection", "Prev rank", "Action"],
        ["left", "left", "right", "right", "left", "right", "left"],
    ) if rows else "_No sector leaders selected._")
    lines.append("")

    held_growth = set(previous.tickers(GROWTH_SLEEVE)) if previous else set()
    lines.append("### High-growth portfolio")
    lines.append("")
    rows = _selection_rows(growth_selections, growth_allocation, held_growth)
    lines.append(markdown_table(
        rows, ["Ticker", "Sector", "Score", "Weight", "Reason for selection", "Prev rank", "Action"],
        ["left", "left", "right", "right", "left", "right", "left"],
    ) if rows else "_No high-growth names selected._")
    lines.append("")

    # --- changes -------------------------------------------------------
    lines.append("## Changes from last week")
    lines.append("")
    if previous is None:
        lines.append("_No prior ranking available for comparison._")
    else:
        for sleeve, label in ((SECTOR_SLEEVE, "Sector leaders"), (GROWTH_SLEEVE, "High growth")):
            current_set = set(ranking.tickers(sleeve))
            previous_set = set(previous.tickers(sleeve))
            entered = sorted(current_set - previous_set)
            exited = sorted(previous_set - current_set)
            lines.append(f"**{label}**")
            lines.append("")
            lines.append(f"- NEW: {', '.join(entered) if entered else 'none'}")
            lines.append(f"- EXITED: {', '.join(exited) if exited else 'none'}")
            upgrades, downgrades = [], []
            for selection in ranking.selections(sleeve):
                change = selection.rank_change
                if change is None:
                    continue
                if change > 0:
                    upgrades.append(f"{selection.ticker} ({rank_change_label(change)})")
                elif change < 0:
                    downgrades.append(f"{selection.ticker} ({rank_change_label(change)})")
            lines.append(f"- UPGRADED: {', '.join(upgrades) if upgrades else 'none'}")
            lines.append(f"- DOWNGRADED: {', '.join(downgrades) if downgrades else 'none'}")
            lines.append("")

    # --- explanations ---------------------------------------------------
    lines.append("## Why these stocks")
    lines.append("")
    lines.append("Every line below is derived from a normalized factor percentile. "
                 "No qualitative reasoning is generated.")
    lines.append("")
    for selection in list(sector_selections)[:12] + list(growth_selections)[:5]:
        lines.append(f"<details><summary><strong>{selection.ticker}</strong> — "
                     f"{selection.company_name} ({selection.sector}) — score "
                     f"{selection.total_score:.1f}</summary>")
        lines.append("")
        lines.append("```")
        lines.append(selection.explanation())
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # --- risk ------------------------------------------------------------
    lines.append("## Risk")
    lines.append("")
    risk_rows = []
    if sector_selections:
        volatilities = [s.category_scores.get("risk") for s in sector_selections]
        usable = [v for v in volatilities if v is not None]
        if usable:
            risk_rows.append(["Average risk score of selections (higher is safer)", f"{np.mean(usable):.1f}"])
    if basket:
        heaviest = max(basket, key=lambda row: row[1])
        risk_rows.append([
            f"Largest single position ({heaviest[0]})", percent(heaviest[1])
        ])
        stock_only = [row for row in basket if row[2] != "core"]
        if stock_only:
            risk_rows.append([
                f"Largest single stock ({max(stock_only, key=lambda r: r[1])[0]})",
                percent(max(row[1] for row in stock_only)),
            ])
        risk_rows.append(["Positions in the basket", str(len(basket))])
    risk_rows.append(["Risk controls active", "yes" if config.risk_controls.any_active else "no (default)"])
    risk_rows.append(["Concentration limits enforced", "yes" if config.limits.enforce else "no (reported only)"])
    lines.append(markdown_table(risk_rows, ["Measure", "Value"], ["left", "right"]))
    lines.append("")

    # --- data quality and caveats ---------------------------------------
    lines.append("## Data and model caveats")
    lines.append("")
    caveats = [
        f"Universe membership: {market.universe.status_line()}",
        f"Sector classification is {'point-in-time' if market.sectors.point_in_time else 'current-only; historical sector changes are not modeled'}.",
        f"Execution convention: decide on the {config.backtest.contribution_day_name} close, "
        f"fill at {'the next trading day open' if config.backtest.execution == 'next_open' else 'that close'}.",
        f"Transaction costs: {number(config.transaction_cost.slippage_bps, 1)} bps slippage, "
        f"{money(config.transaction_cost.commission_per_trade)} commission per trade.",
        f"Return mode: {config.backtest.return_mode.replace('_', ' ')}.",
        "Forward P/E and analyst estimates are not used: no point-in-time source is wired up, "
        "and back-filling current estimates would leak future information.",
    ]
    if ranking.rejections:
        reasons = pd.Series(list(ranking.rejections.values())).str.split(":").str[0].value_counts()
        top = "; ".join(f"{reason} ({count})" for reason, count in reasons.head(4).items())
        caveats.append(f"{len(ranking.rejections)} stock(s) excluded — {top}.")
    for caveat in caveats:
        lines.append(f"- {caveat}")
    lines.append("")
    if ranking.warnings:
        lines.append("### Model warnings")
        lines.append("")
        for warning in ranking.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    if data_quality_summary:
        lines.append("```")
        lines.append(data_quality_summary)
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_Generated {dt.datetime.now():%Y-%m-%d %H:%M} by quant-stock-selector "
                 f"(strategy v{strategy.version}). No trades were placed._")
    return "\n".join(lines)


# --- HTML ------------------------------------------------------------------
HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light;
    --surface: #fcfcfb;
    --surface-2: #f4f3ef;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --rule: #e1e0d9;
    --accent: #2a78d6;
    --negative: #e34948;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --surface: #1a1a19;
      --surface-2: #232321;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --rule: #2c2c2a;
      --accent: #3987e5;
      --negative: #e66767;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface: #1a1a19;
    --surface-2: #232321;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --rule: #2c2c2a;
    --accent: #3987e5;
    --negative: #e66767;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--surface);
    color: var(--text-primary);
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  main {{ max-width: 62rem; margin: 0 auto; padding: 2.5rem 16px 5rem; }}
  h1 {{ font-size: 1.9rem; line-height: 1.2; margin: 0 0 .5rem; letter-spacing: -.01em; }}
  h2 {{ font-size: 1.3rem; margin: 2.6rem 0 .8rem; padding-top: 1.2rem; border-top: 1px solid var(--rule); }}
  h3 {{ font-size: 1.05rem; margin: 1.6rem 0 .6rem; color: var(--text-secondary); }}
  p, li {{ color: var(--text-secondary); }}
  blockquote {{
    margin: 1.2rem 0; padding: .9rem 1.1rem;
    background: var(--surface-2); border-left: 3px solid var(--accent);
    border-radius: 0 6px 6px 0; color: var(--text-secondary); font-size: .93rem;
  }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .9rem; }}
  th, td {{ padding: .5rem .7rem; border-bottom: 1px solid var(--rule); text-align: left; }}
  th {{ color: var(--text-primary); font-weight: 600; background: var(--surface-2); white-space: nowrap; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem; }}
  pre {{ background: var(--surface-2); padding: 1rem; border-radius: 6px; overflow-x: auto; color: var(--text-primary); }}
  details {{ margin: .4rem 0; padding: .5rem .8rem; background: var(--surface-2); border-radius: 6px; }}
  summary {{ cursor: pointer; color: var(--text-primary); }}
  img {{ max-width: 100%; height: auto; border-radius: 6px; margin: 1rem 0; }}
  .meta {{ color: var(--text-muted); font-size: .85rem; }}
  .table-scroll {{ overflow-x: auto; }}
  @media (max-width: 40rem) {{ main {{ padding: 1.5rem 16px 3rem; }} h1 {{ font-size: 1.5rem; }} }}
</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def markdown_to_html(markdown: str) -> str:
    """Minimal Markdown to HTML conversion for the subset these reports use.

    A dependency-free converter keeps the tool installable from the listed
    requirements alone; it handles headings, tables, lists, code fences,
    blockquotes, images, emphasis and the raw <details> blocks passed through.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    in_code = False
    in_table = False
    in_list = False

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            out.append("</tbody></table></div>")
            in_table = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    position = 0
    while position < len(lines):
        line = lines[position]
        stripped = line.strip()

        if stripped.startswith("```"):
            close_table(); close_list()
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            position += 1
            continue
        if in_code:
            out.append(html.escape(line))
            position += 1
            continue

        if stripped.startswith("<details") or stripped.startswith("</details") or stripped.startswith("<summary"):
            close_table(); close_list()
            out.append(stripped)
            position += 1
            continue

        if not stripped:
            close_table(); close_list()
            position += 1
            continue

        if stripped.startswith("|") and position + 1 < len(lines) and set(lines[position + 1].strip()) <= set("|-: "):
            close_list()
            headers = [c.strip() for c in stripped.strip("|").split("|")]
            alignments = [c.strip() for c in lines[position + 1].strip().strip("|").split("|")]
            classes = ["num" if a.endswith(":") and not a.startswith(":") else "" for a in alignments]
            out.append('<div class="table-scroll"><table><thead><tr>')
            for header, css in zip(headers, classes):
                out.append(f'<th class="{css}">{_inline(header)}</th>')
            out.append("</tr></thead><tbody>")
            in_table = True
            position += 2
            continue

        if in_table and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            position += 1
            continue
        close_table()

        if stripped.startswith("#"):
            close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append(f"<h{level}>{_inline(stripped[level:].strip())}</h{level}>")
            position += 1
            continue

        if stripped.startswith(">"):
            close_list()
            out.append(f"<blockquote>{_inline(stripped.lstrip('> ').strip())}</blockquote>")
            position += 1
            continue

        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            position += 1
            continue
        close_list()

        if stripped == "---":
            out.append("<hr>")
            position += 1
            continue

        out.append(f"<p>{_inline(stripped)}</p>")
        position += 1

    close_table(); close_list()
    return "\n".join(out)


def _inline(text: str) -> str:
    """Escape then re-apply the inline markup the reports use."""
    import re

    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1">', escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"(?<![*\w])_([^_]+)_(?![*\w])", r"<em>\1</em>", escaped)
    return escaped


def write_weekly_report(
    markdown: str,
    directory: Path,
    decision_date: dt.date,
    title: str | None = None,
    prefix: str = "",
) -> dict[str, Path]:
    """Write the Markdown and HTML forms of the weekly report.

    ``prefix`` marks generated-data output in the *filename*, so a synthetic
    report cannot be mistaken for a real one in a file listing, an email
    attachment or a chat thread - places where the in-document banner is not
    visible.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}weekly_report_{decision_date:%Y-%m-%d}"

    markdown_path = directory / f"{stem}.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    html_path = directory / f"{stem}.html"
    html_path.write_text(
        HTML_TEMPLATE.format(
            title=html.escape(title or f"Quant weekly report {decision_date}"),
            body=markdown_to_html(markdown),
        ),
        encoding="utf-8",
    )
    log.info("wrote weekly report %s and %s", markdown_path, html_path)
    return {"markdown": markdown_path, "html": html_path}
