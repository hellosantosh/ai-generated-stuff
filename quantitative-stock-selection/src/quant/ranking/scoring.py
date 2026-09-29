"""Composite scoring (REQUIREMENTS 13).

    Total Score = 30% Growth + 30% Momentum + 20% Quality
                + 10% Valuation + 10% Risk

Weights come from ``config/scoring.yaml`` and are validated to sum to 1.0 at
load time.

Handling of missing factors is the substantive design decision here. When a
stock is missing some inputs the remaining weights are **renormalized** rather
than the gaps being filled with a neutral 50. Filling with 50 quietly drags
every incomplete stock toward the middle of the pack, which both flatters weak
names and penalizes strong ones. Renormalizing instead scores each stock on
what is genuinely known about it - but only if enough is known, hence
``MIN_WEIGHT_COVERAGE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import ScoringConfig
from ..errors import ConfigError
from ..logging_config import get_logger
from .normalization import normalize_frame

log = get_logger(__name__)

# A category is scored only when factors carrying at least this share of its
# weight have values. Below it the category is NaN and its own weight is
# redistributed at the composite level.
MIN_WEIGHT_COVERAGE = 0.5

CATEGORIES = ("growth", "momentum", "quality", "valuation", "risk", "relative_strength")


@dataclass
class CategoryScores:
    """Per-category 0-100 scores plus the composite, for a whole universe."""

    scores: pd.DataFrame          # index=ticker, columns=categories
    composite: pd.Series          # index=ticker
    normalized: pd.DataFrame      # index=ticker, columns=individual factors
    coverage: pd.DataFrame        # index=ticker, weight coverage per category

    def join(self) -> pd.DataFrame:
        out = self.scores.copy()
        out["total_score"] = self.composite
        return out


def weighted_mean_with_renormalization(
    values: pd.DataFrame, weights: Mapping[str, float], min_coverage: float = MIN_WEIGHT_COVERAGE
) -> tuple[pd.Series, pd.Series]:
    """Weighted mean per row over the available columns.

    Returns ``(score, coverage)`` where coverage is the share of total weight
    backed by an actual value. Rows below ``min_coverage`` yield NaN.
    """
    if values.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    columns = [c for c in weights if c in values.columns]
    if not columns:
        nan = pd.Series(np.nan, index=values.index, dtype=float)
        return nan, pd.Series(0.0, index=values.index, dtype=float)

    numeric = values[columns].apply(pd.to_numeric, errors="coerce")
    weight_row = pd.Series({c: float(weights[c]) for c in columns})
    total_weight = float(weight_row.sum())
    if total_weight <= 0:
        raise ConfigError(f"weights sum to {total_weight}; expected a positive total")

    present = numeric.notna()
    available_weight = present.mul(weight_row, axis=1).sum(axis=1)
    coverage = available_weight / total_weight

    weighted_sum = numeric.fillna(0.0).mul(weight_row, axis=1).sum(axis=1)
    score = weighted_sum.divide(available_weight.where(available_weight > 0))
    score = score.where(coverage >= min_coverage)
    return score, coverage


def normalize_for(
    factors: pd.DataFrame, config: ScoringConfig, categories: Sequence[str]
) -> pd.DataFrame:
    """Normalize exactly the factors the given categories need."""
    factor_names: list[str] = []
    for category in categories:
        factor_names.extend(config.factor_weights.get(category, {}).keys())
    factor_names = list(dict.fromkeys(factor_names))
    return normalize_frame(
        factors,
        factor_names,
        config.normalization,
        lower_is_better=config.lower_is_better,
    )


def score_universe(
    factors: pd.DataFrame,
    config: ScoringConfig,
    weights: Mapping[str, float] | None = None,
    categories: Sequence[str] | None = None,
    normalized: pd.DataFrame | None = None,
) -> CategoryScores:
    """Normalize factors, roll them into categories, then into a composite.

    ``weights`` defaults to the sector-leader model; the high-growth sleeve
    passes ``config.growth_weights`` and the ``relative_strength`` category.

    ``normalized`` lets a caller that scores the same universe under two
    weight schemes normalize once and reuse the result, which is what the
    ranker does for the two sleeves.
    """
    weights = dict(weights or config.weights)
    categories = list(categories or weights.keys())

    if factors.empty:
        empty = pd.DataFrame(columns=categories, dtype=float)
        return CategoryScores(empty, pd.Series(dtype=float), pd.DataFrame(), empty)

    if normalized is None:
        normalized = normalize_for(factors, config, categories)

    category_scores = pd.DataFrame(index=factors.index, dtype=float)
    category_coverage = pd.DataFrame(index=factors.index, dtype=float)
    for category in categories:
        category_weights = config.factor_weights.get(category)
        if not category_weights:
            raise ConfigError(f"scoring.factors has no definition for category {category!r}")
        score, coverage = weighted_mean_with_renormalization(normalized, category_weights)
        category_scores[category] = score
        category_coverage[category] = coverage

    composite, composite_coverage = weighted_mean_with_renormalization(
        category_scores, weights
    )
    category_coverage["composite"] = composite_coverage

    scored = int(composite.notna().sum())
    if scored < len(factors):
        log.debug(
            "%d of %d stocks could not be scored (insufficient factor coverage)",
            len(factors) - scored,
            len(factors),
        )

    return CategoryScores(
        scores=category_scores,
        composite=composite,
        normalized=normalized,
        coverage=category_coverage,
    )


def composite_score(
    category_scores: Mapping[str, float | None], weights: Mapping[str, float]
) -> float | None:
    """Single-stock composite, used by tests and the explainability report."""
    frame = pd.DataFrame([{k: v for k, v in category_scores.items()}])
    score, _ = weighted_mean_with_renormalization(frame, weights)
    value = score.iloc[0]
    return None if pd.isna(value) else float(value)
