"""Factor normalization (REQUIREMENTS 14).

Raw financial metrics are never combined directly: a revenue CAGR of 0.35 and
a P/E of 28 do not live on a common scale. Every factor is mapped to a 0-100
sector-relative percentile first.

Three rules matter:

1. **Winsorize before ranking.** One company with a 4000% EPS growth rate off a
   near-zero base would otherwise compress everyone else.
2. **Rank within sector.** A software P/E compared against a utility P/E is
   noise; compared against other software it is information.
3. **Missing stays missing.** A stock with no value for a factor gets ``NaN``,
   not the median. The scoring layer then redistributes that factor's weight
   across the factors the stock does have.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import NormalizationConfig
from ..logging_config import get_logger

log = get_logger(__name__)

NEUTRAL_SCORE = 50.0


def winsorize(
    series: pd.Series, lower: float = 0.02, upper: float = 0.98
) -> pd.Series:
    """Clip a series to its own quantile bounds, ignoring NaNs."""
    values = pd.to_numeric(series, errors="coerce")
    finite = values[np.isfinite(values)]
    if finite.empty:
        return values
    low = float(finite.quantile(lower))
    high = float(finite.quantile(upper))
    if not np.isfinite(low) or not np.isfinite(high) or low > high:
        return values
    return values.clip(lower=low, upper=high)


def percentile_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Map values to 0-100 percentiles. Ties share the average rank.

    With a single observation the percentile is undefined, so the neutral 50
    is returned: ranking a lone stock as either 0 or 100 would be arbitrary.
    """
    values = pd.to_numeric(series, errors="coerce")
    usable = values[np.isfinite(values)]
    if usable.empty:
        return pd.Series(np.nan, index=series.index, dtype=float)
    if len(usable) == 1:
        out = pd.Series(np.nan, index=series.index, dtype=float)
        out.loc[usable.index] = NEUTRAL_SCORE
        return out
    ranks = usable.rank(ascending=ascending, method="average", pct=True) * 100.0
    out = pd.Series(np.nan, index=series.index, dtype=float)
    out.loc[ranks.index] = ranks
    return out


def zscore(series: pd.Series, clip: float = 3.0) -> pd.Series:
    """Standardize then map onto 0-100 so it composes with percentiles."""
    values = pd.to_numeric(series, errors="coerce")
    usable = values[np.isfinite(values)]
    if len(usable) < 2:
        out = pd.Series(np.nan, index=series.index, dtype=float)
        out.loc[usable.index] = NEUTRAL_SCORE
        return out
    mean = float(usable.mean())
    std = float(usable.std(ddof=1))
    if std <= 0:
        out = pd.Series(np.nan, index=series.index, dtype=float)
        out.loc[usable.index] = NEUTRAL_SCORE
        return out
    standardized = ((values - mean) / std).clip(-clip, clip)
    return (standardized + clip) / (2 * clip) * 100.0


def normalize_factor(
    series: pd.Series,
    method: str = "percentile",
    lower_is_better: bool = False,
    winsorize_lower: float = 0.02,
    winsorize_upper: float = 0.98,
) -> pd.Series:
    """Normalize one factor to 0-100 where higher always means more attractive."""
    values = pd.to_numeric(series, errors="coerce")
    if method in ("percentile", "winsorized_zscore", "zscore"):
        values = winsorize(values, winsorize_lower, winsorize_upper)

    if method == "percentile":
        return percentile_rank(values, ascending=not lower_is_better)
    if method in ("zscore", "winsorized_zscore"):
        scores = zscore(values)
        return 100.0 - scores if lower_is_better else scores
    raise ValueError(f"unknown normalization method: {method!r}")


def normalize_frame(
    frame: pd.DataFrame,
    factor_names: Sequence[str],
    config: NormalizationConfig,
    lower_is_better: Iterable[str] = (),
    group_column: str = "sector",
) -> pd.DataFrame:
    """Normalize every named factor, grouping as configured.

    Sectors smaller than ``min_group_size`` fall back to a universe-wide rank:
    a three-stock sector would otherwise hand out scores of 0, 50 and 100
    regardless of how those three compare with the market. Stocks with no
    sector are treated the same way.

    Winsorization is applied once per factor across the whole universe rather
    than inside each sector. Clipping is there to contain data errors and
    extreme outliers, and universe-wide bounds are far more stable than bounds
    estimated from the 30-odd names in a single sector. Ranking, which is the
    part that must be sector relative, still happens within the group.

    The grouped rank is one vectorized pandas call per factor. The obvious
    alternative - looping over sectors - costs roughly an order of magnitude
    more, which matters when this runs at each of 360 decision dates.
    """
    out = pd.DataFrame(index=frame.index)
    if frame.empty:
        return out

    lower_set = set(lower_is_better)
    groups, use_universe = _effective_groups(frame, config, group_column)
    everything = pd.Series("__universe__", index=frame.index)

    for name in factor_names:
        if name not in frame.columns:
            out[name] = np.nan
            continue
        raw = pd.to_numeric(frame[name], errors="coerce")
        clipped = winsorize(raw, config.winsorize_lower, config.winsorize_upper)
        inverted = name in lower_set

        if config.method == "percentile":
            scores = _grouped_percentile(clipped, groups, ascending=not inverted)
            if use_universe.any():
                # Members of an undersized sector are ranked against the ENTIRE
                # universe, not against the handful of peers that share their
                # label - otherwise a two-stock sector would hand out 50 and
                # 100 regardless of how those two compare with the market.
                universe_scores = _grouped_percentile(
                    clipped, everything, ascending=not inverted
                )
                scores = scores.where(~use_universe, universe_scores)
        else:
            scores = _grouped_zscore(clipped, groups)
            if use_universe.any():
                scores = scores.where(~use_universe, _grouped_zscore(clipped, everything))
            if inverted:
                scores = 100.0 - scores
        out[name] = scores.where(clipped.notna())

    return out


def _effective_groups(
    frame: pd.DataFrame, config: NormalizationConfig, group_column: str
) -> tuple[pd.Series, pd.Series]:
    """Group label per stock, plus a mask of rows to rank universe-wide.

    Returns ``(labels, use_universe)``. Rows flagged in ``use_universe`` sit in
    a sector too small to rank within, or have no sector at all.
    """
    if config.group_by != "sector" or group_column not in frame.columns:
        return (
            pd.Series("__universe__", index=frame.index),
            pd.Series(True, index=frame.index),
        )

    labels = frame[group_column].fillna("__unclassified__").astype(str)
    sizes = labels.value_counts()
    small = set(sizes[sizes < config.min_group_size].index) | {"__unclassified__"}
    named_small = small - {"__unclassified__"}
    if named_small:
        log.debug(
            "normalizing %d small sector(s) against the whole universe: %s",
            len(named_small),
            sorted(named_small),
        )
    return labels, labels.isin(small)


def _grouped_percentile(values: pd.Series, groups: pd.Series, ascending: bool) -> pd.Series:
    grouped = values.groupby(groups, observed=True)
    ranks = grouped.rank(ascending=ascending, method="average", pct=True) * 100.0
    # A group with a single usable observation has no meaningful percentile;
    # pct rank would score it 100 purely for being alone.
    counts = values.notna().groupby(groups, observed=True).transform("sum")
    return ranks.where(counts > 1, NEUTRAL_SCORE)


def _grouped_zscore(values: pd.Series, groups: pd.Series, clip: float = 3.0) -> pd.Series:
    grouped = values.groupby(groups, observed=True)
    mean = grouped.transform("mean")
    std = grouped.transform("std")
    standardized = ((values - mean) / std.where(std > 0)).clip(-clip, clip)
    scores = (standardized + clip) / (2 * clip) * 100.0
    return scores.where(std > 0, NEUTRAL_SCORE)
