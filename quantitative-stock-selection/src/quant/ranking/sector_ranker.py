"""Sector ranking, selection and explanations (REQUIREMENTS 15, 17, 58).

Two sleeves are produced from the same factor matrix:

* **sector leaders** - the top N in each GICS sector by the default composite;
* **high growth**    - the top N across all sectors by the growth-tilted
  composite, which adds a relative-strength category.

Explanations are assembled from the normalized factor scores themselves. No
qualitative reasoning is invented: a "strong revenue growth" line appears only
because that factor's percentile cleared a threshold.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import AppConfig
from ..logging_config import get_logger
from .scoring import CategoryScores, normalize_for, score_universe

log = get_logger(__name__)

SECTOR_SLEEVE = "sector_leaders"
GROWTH_SLEEVE = "high_growth"

# Percentile thresholds for the plain-language strength/risk bullets.
STRENGTH_THRESHOLD = 75.0
WEAKNESS_THRESHOLD = 25.0

# If fewer than this share of the universe can be scored on a whole category,
# that category has effectively dropped out of the model and the composite is
# no longer the strategy that was configured. Worth saying out loud: with a
# fundamentals source that only carries a few quarters, Growth silently
# vanishes and a 5-factor model quietly becomes a 4-factor one.
MIN_CATEGORY_COVERAGE = 0.20

STRENGTH_LABELS = {
    "revenue_growth_yoy": "strong revenue growth",
    "revenue_cagr_3y": "sustained 3-year revenue growth",
    "eps_growth_yoy": "strong earnings growth",
    "eps_cagr_3y": "sustained 3-year earnings growth",
    "fcf_growth": "growing free cash flow",
    "return_12m": "strong 12-month momentum",
    "return_6m": "strong 6-month momentum",
    "return_3m": "strong 3-month momentum",
    "rs_vs_market_12m": "positive relative strength versus the market",
    "rs_vs_sector_12m": "positive relative strength versus its sector",
    "price_to_200dma": "trading above its long-term moving average",
    "ma50_to_ma200": "50-day average above the 200-day average",
    "gross_margin": "high gross margin",
    "operating_margin": "high operating margin",
    "net_margin": "high net margin",
    "roe": "high return on equity",
    "roic": "high return on invested capital",
    "fcf_margin": "high free-cash-flow margin",
    "current_ratio": "strong liquidity",
    "interest_coverage": "comfortable interest coverage",
    "earnings_yield": "attractive earnings yield",
    "fcf_yield": "attractive free-cash-flow yield",
    "price_to_sales": "modest price/sales for its sector",
    "ev_to_ebitda": "modest EV/EBITDA for its sector",
    "volatility_1y": "low realized volatility",
    "max_drawdown_1y": "shallow 12-month drawdown",
    "beta": "low market beta",
}

RISK_LABELS = {
    "revenue_growth_yoy": "weak revenue growth",
    "eps_growth_yoy": "weak earnings growth",
    "return_12m": "weak 12-month momentum",
    "return_3m": "weak recent momentum",
    "rs_vs_market_12m": "lagging the market",
    "price_to_200dma": "trading below its long-term moving average",
    "gross_margin": "low gross margin",
    "operating_margin": "low operating margin",
    "roe": "low return on equity",
    "roic": "low return on invested capital",
    "debt_to_equity": "elevated leverage",
    "net_debt_to_ebitda": "high net debt relative to EBITDA",
    "interest_coverage": "thin interest coverage",
    "earnings_yield": "high valuation on earnings",
    "fcf_yield": "high valuation on free cash flow",
    "price_to_sales": "high price/sales for its sector",
    "ev_to_ebitda": "high EV/EBITDA for its sector",
    "peg": "high PEG ratio",
    "volatility_1y": "high historical volatility",
    "max_drawdown_1y": "deep 12-month drawdown",
    "drawdown_52w": "trading well below its 52-week high",
    "beta": "high market beta",
}


@dataclass
class Selection:
    """One selected holding, with everything needed to explain the choice."""

    ticker: str
    sleeve: str
    sector: str
    company_name: str
    total_score: float
    rank: int
    previous_rank: int | None = None
    category_scores: dict[str, float] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def rank_change(self) -> int | None:
        if self.previous_rank is None:
            return None
        return self.previous_rank - self.rank

    def explanation(self) -> str:
        lines = [f"{self.ticker}", "", f"Overall Score: {self.total_score:.1f}", ""]
        for category, score in self.category_scores.items():
            lines.append(f"{category.replace('_', ' ').title():<18}{score:>6.0f}")
        if self.strengths:
            lines += ["", "Primary strengths:"] + [f"- {s}" for s in self.strengths]
        if self.risks:
            lines += ["", "Primary risks:"] + [f"- {r}" for r in self.risks]
        return "\n".join(lines)


@dataclass
class RankingResult:
    """Full ranking output for one decision date."""

    decision_date: dt.date
    table: pd.DataFrame                   # every scored stock, both sleeves
    sector_leaders: list[Selection]
    high_growth: list[Selection]
    rejections: dict[str, str] = field(default_factory=dict)
    universe_size: int = 0
    warnings: list[str] = field(default_factory=list)

    def selections(self, sleeve: str) -> list[Selection]:
        return self.sector_leaders if sleeve == SECTOR_SLEEVE else self.high_growth

    def tickers(self, sleeve: str) -> list[str]:
        return [s.ticker for s in self.selections(sleeve)]

    def sleeve_table(self, sleeve: str) -> pd.DataFrame:
        if self.table.empty:
            return self.table
        return self.table[self.table["sleeve"] == sleeve]


def explain_selection(
    ticker: str,
    normalized: pd.DataFrame,
    max_items: int = 4,
) -> tuple[list[str], list[str]]:
    """Deterministic strengths and risks from normalized factor percentiles."""
    if ticker not in normalized.index:
        return [], []
    row = normalized.loc[ticker].dropna()

    strengths = [
        (STRENGTH_LABELS[name], float(value))
        for name, value in row.items()
        if name in STRENGTH_LABELS and value >= STRENGTH_THRESHOLD
    ]
    risks = [
        (RISK_LABELS[name], float(value))
        for name, value in row.items()
        if name in RISK_LABELS and value <= WEAKNESS_THRESHOLD
    ]
    strengths.sort(key=lambda item: item[1], reverse=True)
    risks.sort(key=lambda item: item[1])

    # De-duplicate labels that several factors map onto.
    def unique(items: list[tuple[str, float]]) -> list[str]:
        seen: list[str] = []
        for label, _ in items:
            if label not in seen:
                seen.append(label)
            if len(seen) >= max_items:
                break
        return seen

    return unique(strengths), unique(risks)


def _build_selections(
    ranked: pd.DataFrame,
    sleeve: str,
    normalized: pd.DataFrame,
    category_columns: Sequence[str],
    previous_ranks: Mapping[str, int] | None,
) -> list[Selection]:
    selections: list[Selection] = []
    previous_ranks = previous_ranks or {}
    for row in ranked.itertuples():
        strengths, risks = explain_selection(row.Index, normalized)
        selections.append(
            Selection(
                ticker=str(row.Index),
                sleeve=sleeve,
                sector=str(getattr(row, "sector", "") or ""),
                company_name=str(getattr(row, "company_name", "") or row.Index),
                total_score=float(row.total_score),
                rank=int(row.rank),
                previous_rank=previous_ranks.get(str(row.Index)),
                category_scores={
                    column: float(getattr(row, column))
                    for column in category_columns
                    if not pd.isna(getattr(row, column, np.nan))
                },
                strengths=strengths,
                risks=risks,
            )
        )
    return selections


def rank_and_select(
    factors: pd.DataFrame,
    config: AppConfig,
    decision_date: dt.date,
    previous_ranks: Mapping[str, Mapping[str, int]] | None = None,
    rejections: Mapping[str, str] | None = None,
) -> RankingResult:
    """Score the universe and pick both sleeves.

    ``previous_ranks`` maps sleeve -> ticker -> last week's rank, which drives
    the rank-change column and the NEW/EXIT actions in the weekly report.
    """
    previous_ranks = previous_ranks or {}
    rejections = dict(rejections or {})

    if factors.empty:
        return RankingResult(
            decision_date=decision_date,
            table=pd.DataFrame(),
            sector_leaders=[],
            high_growth=[],
            rejections=rejections,
            universe_size=0,
        )

    sector_categories = list(config.scoring.weights.keys())
    growth_categories = list(config.scoring.growth_weights.keys())

    # Both sleeves rank the same universe on overlapping factors, so the
    # normalization - the expensive step - is done once for their union.
    normalized = normalize_for(
        factors, config.scoring, list(dict.fromkeys(sector_categories + growth_categories))
    )
    sector_scores = score_universe(
        factors, config.scoring, config.scoring.weights, sector_categories, normalized
    )
    growth_scores = score_universe(
        factors, config.scoring, config.scoring.growth_weights, growth_categories, normalized
    )

    meta = factors[["sector", "company_name"]].copy()
    rows: list[pd.DataFrame] = []

    # --- sector leaders: rank within each sector -------------------------
    sector_table = meta.join(sector_scores.scores)
    sector_table["total_score"] = sector_scores.composite
    sector_table = sector_table[sector_table["total_score"].notna()].copy()
    sector_table["rank"] = (
        sector_table.groupby("sector")["total_score"]
        .rank(ascending=False, method="first")
        .astype(int)
    )
    sector_table["sleeve"] = SECTOR_SLEEVE
    sector_table = sector_table.sort_values(["sector", "rank"])

    leaders_frame = sector_table[sector_table["rank"] <= config.sector_strategy.holdings]
    sector_leaders = _build_selections(
        leaders_frame,
        SECTOR_SLEEVE,
        sector_scores.normalized,
        sector_categories,
        previous_ranks.get(SECTOR_SLEEVE),
    )
    sector_table["selected"] = sector_table["rank"] <= config.sector_strategy.holdings
    rows.append(sector_table)

    # --- high growth: one cross-sector ranking ---------------------------
    growth_table = meta.join(growth_scores.scores)
    growth_table["total_score"] = growth_scores.composite
    growth_table = growth_table[growth_table["total_score"].notna()].copy()
    growth_table["rank"] = (
        growth_table["total_score"].rank(ascending=False, method="first").astype(int)
    )
    growth_table["sleeve"] = GROWTH_SLEEVE
    growth_table = growth_table.sort_values("rank")

    growth_frame = growth_table[growth_table["rank"] <= config.growth_strategy.holdings]
    high_growth = _build_selections(
        growth_frame,
        GROWTH_SLEEVE,
        growth_scores.normalized,
        growth_categories,
        previous_ranks.get(GROWTH_SLEEVE),
    )
    growth_table["selected"] = growth_table["rank"] <= config.growth_strategy.holdings
    rows.append(growth_table)

    table = pd.concat(rows, axis=0)
    table.index.name = "ticker"
    table["decision_date"] = decision_date

    warnings = _category_coverage_warnings(
        {**dict.fromkeys(sector_categories), **dict.fromkeys(growth_categories)},
        sector_scores,
        growth_scores,
        config.scoring.weights,
        config.scoring.growth_weights,
        decision_date,
    )
    for warning in warnings:
        log.warning("%s", warning)

    log.debug(
        "%s: ranked %d stocks; %d sector leaders across %d sectors, %d high-growth names",
        decision_date,
        len(factors),
        len(sector_leaders),
        sector_table["sector"].nunique(),
        len(high_growth),
    )

    return RankingResult(
        decision_date=decision_date,
        table=table,
        sector_leaders=sector_leaders,
        high_growth=high_growth,
        rejections=rejections,
        universe_size=len(factors),
        warnings=warnings,
    )


def _category_coverage_warnings(
    categories: Mapping[str, None],
    sector_scores: CategoryScores,
    growth_scores: CategoryScores,
    sector_weights: Mapping[str, float],
    growth_weights: Mapping[str, float],
    decision_date: dt.date,
) -> list[str]:
    """Flag scoring categories that almost no stock could be scored on.

    A category that is missing for nearly the whole universe does not just add
    noise: its weight is redistributed across the others, so the model that
    actually ran is not the model in ``scoring.yaml``.
    """
    warnings: list[str] = []
    seen: set[str] = set()
    for scores, weights in ((sector_scores, sector_weights), (growth_scores, growth_weights)):
        frame = scores.scores
        if frame.empty:
            continue
        for category in frame.columns:
            if category in seen:
                continue
            coverage = float(frame[category].notna().mean())
            if coverage >= MIN_CATEGORY_COVERAGE:
                continue
            seen.add(category)
            weight = weights.get(category, 0.0)
            warnings.append(
                f"{decision_date}: the {category!r} category could be scored for only "
                f"{coverage:.0%} of the universe, so its {weight:.0%} weight is being "
                f"redistributed across the other categories. The composite is not the "
                f"model configured in scoring.yaml. Check that the fundamentals source "
                f"carries enough history (SEC EDGAR provides years of filings; some "
                f"providers carry only a few quarters, which is not enough for "
                f"year-over-year growth)."
            )
    return warnings


def ranks_by_sleeve(result: RankingResult) -> dict[str, dict[str, int]]:
    """Extract this week's ranks, to be passed into next week's ranking."""
    if result.table.empty:
        return {}
    out: dict[str, dict[str, int]] = {}
    for sleeve, group in result.table.groupby("sleeve"):
        out[str(sleeve)] = {str(ticker): int(rank) for ticker, rank in group["rank"].items()}
    return out
