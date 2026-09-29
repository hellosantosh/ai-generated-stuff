"""Normalization, scoring and ranking tests (REQUIREMENTS 37, scoring tests)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.config import NormalizationConfig, ScoringConfig, load_config
from quant.errors import ConfigError
from quant.factors import compute_universe_factors
from quant.ranking.normalization import (
    NEUTRAL_SCORE,
    normalize_factor,
    normalize_frame,
    percentile_rank,
    winsorize,
    zscore,
)
from quant.ranking.scoring import (
    MIN_WEIGHT_COVERAGE,
    composite_score,
    score_universe,
    weighted_mean_with_renormalization,
)
from quant.ranking.sector_ranker import explain_selection, rank_and_select, ranks_by_sleeve


def _norm_config(**kwargs) -> NormalizationConfig:
    defaults = dict(method="percentile", group_by="sector", winsorize_lower=0.02,
                    winsorize_upper=0.98, min_group_size=5)
    defaults.update(kwargs)
    return NormalizationConfig(**defaults)


# --- normalization ---------------------------------------------------------
def test_percentile_rank_spans_the_range():
    series = pd.Series([1.0, 2.0, 3.0, 4.0], index=list("abcd"))
    ranks = percentile_rank(series)
    assert ranks["a"] == pytest.approx(25.0)
    assert ranks["d"] == pytest.approx(100.0)


def test_percentile_rank_can_be_inverted_for_lower_is_better():
    series = pd.Series([10.0, 20.0, 30.0, 40.0])
    ascending = normalize_factor(series, lower_is_better=False)
    descending = normalize_factor(series, lower_is_better=True)
    assert ascending.iloc[-1] > ascending.iloc[0]
    assert descending.iloc[-1] < descending.iloc[0]


def test_winsorize_clips_extremes_without_dropping_them():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 1000.0])
    clipped = winsorize(series, 0.0, 0.80)
    assert clipped.max() < 1000.0
    assert len(clipped) == len(series)
    assert clipped.notna().all()


def test_winsorize_keeps_nan_as_nan():
    series = pd.Series([1.0, np.nan, 3.0])
    assert winsorize(series).isna().sum() == 1


def test_missing_values_stay_missing_after_normalization():
    """A gap must not be filled with the median; scoring redistributes weight."""
    frame = pd.DataFrame({"sector": ["A"] * 6, "f": [1.0, 2.0, np.nan, 4.0, 5.0, 6.0]})
    out = normalize_frame(frame, ["f"], _norm_config(min_group_size=3))
    assert pd.isna(out["f"].iloc[2])
    assert out["f"].notna().sum() == 5


def test_a_single_observation_scores_neutral_not_100():
    frame = pd.DataFrame({"sector": ["A"], "f": [42.0]})
    out = normalize_frame(frame, ["f"], _norm_config(min_group_size=1))
    assert out["f"].iloc[0] == pytest.approx(NEUTRAL_SCORE)


def test_ranking_is_within_sector():
    """A weak tech name must not outrank a strong utility on a raw comparison."""
    frame = pd.DataFrame(
        {
            "sector": ["Tech"] * 5 + ["Utilities"] * 5,
            "f": [50.0, 60.0, 70.0, 80.0, 90.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        },
        index=[f"T{i}" for i in range(5)] + [f"U{i}" for i in range(5)],
    )
    out = normalize_frame(frame, ["f"], _norm_config(min_group_size=5))
    # The best utility (raw 5.0) beats the worst tech (raw 50.0) on rank.
    assert out.loc["U4", "f"] > out.loc["T0", "f"]
    assert out.loc["T4", "f"] == pytest.approx(out.loc["U4", "f"])


def test_small_sectors_fall_back_to_universe_ranking():
    frame = pd.DataFrame(
        {
            "sector": ["Big"] * 6 + ["Tiny"] * 2,
            "f": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0, 200.0],
        },
        index=[f"B{i}" for i in range(6)] + ["S0", "S1"],
    )
    out = normalize_frame(frame, ["f"], _norm_config(min_group_size=5))
    # Ranked against all 8 rows, S0 is 7th and S1 is 8th: 87.5 and 100.
    # Ranked inside their own two-stock sector they would score 50 and 100,
    # which says nothing about how they compare with the market.
    assert out.loc["S1", "f"] == pytest.approx(100.0)
    assert out.loc["S0", "f"] == pytest.approx(87.5)
    # The large sector is still ranked within itself.
    assert out.loc["B5", "f"] == pytest.approx(100.0)
    assert out.loc["B0", "f"] == pytest.approx(100.0 / 6)


def test_unclassified_stocks_are_ranked_against_the_universe():
    frame = pd.DataFrame({"sector": ["A"] * 6 + [None], "f": list(range(7))})
    out = normalize_frame(frame, ["f"], _norm_config(min_group_size=3))
    assert out["f"].notna().all()


def test_zscore_method_maps_onto_the_same_scale():
    frame = pd.DataFrame({"sector": ["A"] * 20, "f": np.linspace(0, 1, 20)})
    out = normalize_frame(frame, ["f"], _norm_config(method="zscore", min_group_size=3))
    assert out["f"].min() >= 0.0
    assert out["f"].max() <= 100.0


# --- weights ---------------------------------------------------------------
def test_weights_must_sum_to_one(project_root):
    with pytest.raises(ConfigError, match="must sum to 1.0"):
        ScoringConfig.from_dict({
            "weights": {"growth": 0.5, "momentum": 0.3, "quality": 0.1,
                        "valuation": 0.05, "risk": 0.02},
            "growth_weights": {"growth": 1.0},
            "factors": {},
        })


def test_config_rejects_unknown_score_categories():
    with pytest.raises(ConfigError, match="must contain exactly"):
        ScoringConfig.from_dict({
            "weights": {"growth": 0.5, "vibes": 0.5},
            "growth_weights": {"growth": 1.0},
            "factors": {},
        })


def test_shipped_config_weights_are_valid(config):
    assert sum(config.scoring.weights.values()) == pytest.approx(1.0)
    assert sum(config.scoring.growth_weights.values()) == pytest.approx(1.0)
    for category, weights in config.scoring.factor_weights.items():
        assert sum(weights.values()) == pytest.approx(1.0), f"{category} weights do not sum to 1"


# --- composite score -------------------------------------------------------
def test_composite_score_is_the_weighted_mean():
    scores = {"growth": 100.0, "momentum": 0.0, "quality": 50.0, "valuation": 50.0, "risk": 50.0}
    weights = {"growth": 0.3, "momentum": 0.3, "quality": 0.2, "valuation": 0.1, "risk": 0.1}
    assert composite_score(scores, weights) == pytest.approx(30 + 0 + 10 + 5 + 5)


def test_missing_categories_renormalize_rather_than_default_to_50():
    """Filling gaps with a neutral 50 would drag every incomplete stock inward."""
    scores = {"growth": 100.0, "momentum": 100.0, "quality": None, "valuation": None, "risk": None}
    weights = {"growth": 0.3, "momentum": 0.3, "quality": 0.2, "valuation": 0.1, "risk": 0.1}
    assert composite_score(scores, weights) == pytest.approx(100.0)


def test_a_stock_below_the_coverage_floor_is_not_scored():
    scores = {"growth": 100.0, "momentum": None, "quality": None, "valuation": None, "risk": None}
    weights = {"growth": 0.3, "momentum": 0.3, "quality": 0.2, "valuation": 0.1, "risk": 0.1}
    # Only 30% of the weight is backed by data, below the 50% floor.
    assert composite_score(scores, weights) is None


def test_coverage_is_reported_alongside_the_score():
    frame = pd.DataFrame([{"a": 100.0, "b": np.nan}])
    score, coverage = weighted_mean_with_renormalization(frame, {"a": 0.5, "b": 0.5})
    assert coverage.iloc[0] == pytest.approx(0.5)
    assert score.iloc[0] == pytest.approx(100.0)


# --- end-to-end ranking ----------------------------------------------------
def test_rank_and_select_picks_the_configured_number_per_sector(view, config):
    factors, _ = compute_universe_factors(view, view.universe(), config)
    result = rank_and_select(factors, config, view.as_of)
    by_sector: dict[str, int] = {}
    for selection in result.sector_leaders:
        by_sector[selection.sector] = by_sector.get(selection.sector, 0) + 1
    for sector, count in by_sector.items():
        assert count <= config.sector_strategy.holdings, f"{sector} has too many picks"
    assert len(result.high_growth) <= config.growth_strategy.holdings


def test_ranks_are_dense_and_start_at_one(view, config):
    factors, _ = compute_universe_factors(view, view.universe(), config)
    result = rank_and_select(factors, config, view.as_of)
    table = result.sleeve_table("sector_leaders")
    for sector, group in table.groupby("sector"):
        ranks = sorted(group["rank"].tolist())
        assert ranks == list(range(1, len(ranks) + 1)), f"{sector} ranks are not contiguous"


def test_higher_score_means_better_rank(view, config):
    factors, _ = compute_universe_factors(view, view.universe(), config)
    result = rank_and_select(factors, config, view.as_of)
    table = result.sleeve_table("high_growth").sort_values("rank")
    scores = table["total_score"].tolist()
    assert scores == sorted(scores, reverse=True)


def test_previous_ranks_produce_rank_changes(view, config, market):
    earlier = market.view(view.as_of - dt.timedelta(days=28), strict=True)
    earlier_factors, _ = compute_universe_factors(earlier, earlier.universe(), config)
    earlier_result = rank_and_select(earlier_factors, config, earlier.as_of)

    factors, _ = compute_universe_factors(view, view.universe(), config)
    result = rank_and_select(
        factors, config, view.as_of, previous_ranks=ranks_by_sleeve(earlier_result)
    )
    changes = [s.rank_change for s in result.sector_leaders if s.rank_change is not None]
    assert changes, "no rank changes were computed"


def test_explanations_come_only_from_factor_percentiles():
    normalized = pd.DataFrame(
        {"revenue_growth_yoy": [95.0], "volatility_1y": [5.0], "roe": [50.0]},
        index=["X"],
    )
    strengths, risks = explain_selection("X", normalized)
    assert "strong revenue growth" in strengths
    assert "high historical volatility" in risks
    # A mid-range factor produces neither a strength nor a risk.
    assert not any("return on equity" in s for s in strengths + risks)


def test_scoring_is_deterministic(view, config):
    factors, _ = compute_universe_factors(view, view.universe(), config)
    first = rank_and_select(factors, config, view.as_of)
    second = rank_and_select(factors, config, view.as_of)
    pd.testing.assert_series_equal(
        first.table["total_score"], second.table["total_score"], check_names=False
    )
