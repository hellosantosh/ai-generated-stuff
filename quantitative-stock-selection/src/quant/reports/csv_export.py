"""CSV exports (REQUIREMENTS 34)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Mapping

import pandas as pd

from ..logging_config import get_logger

log = get_logger(__name__)


def _write(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    log.debug("wrote %s (%d rows)", path, len(frame))
    return path


def export_csvs(
    result,
    directory: Path,
    rankings: pd.DataFrame,
    factor_scores: pd.DataFrame,
    stock_attribution: pd.DataFrame,
    sector_attribution: pd.DataFrame,
) -> dict[str, Path]:
    """Write every CSV the requirements name, plus attribution."""
    directory = Path(directory)
    written: dict[str, Path] = {}

    summary = result.comparison_table().reset_index().rename(columns={"index": "Metric"})
    written["backtest_summary"] = _write(summary, directory / "backtest_summary.csv")

    history_rows = []
    for name, variant in result.variants.items():
        frame = variant.values.copy()
        frame.insert(0, "variant", name)
        history_rows.append(frame.reset_index(drop=True))
    if history_rows:
        written["portfolio_history"] = _write(
            pd.concat(history_rows, ignore_index=True), directory / "portfolio_history.csv"
        )

    transaction_rows = []
    for name, variant in result.variants.items():
        frame = variant.portfolio.transactions_frame()
        if frame.empty:
            continue
        frame = frame.copy()
        frame.insert(0, "variant", name)
        transaction_rows.append(frame)
    if transaction_rows:
        written["transactions"] = _write(
            pd.concat(transaction_rows, ignore_index=True), directory / "transactions.csv"
        )

    if not rankings.empty:
        written["stock_rankings"] = _write(rankings, directory / "stock_rankings.csv")
    if not factor_scores.empty:
        written["factor_scores"] = _write(factor_scores, directory / "factor_scores.csv")

    drawdown_rows = []
    for name, variant in result.variants.items():
        frame = variant.drawdowns.copy()
        if frame.empty:
            continue
        frame.insert(0, "variant", name)
        drawdown_rows.append(frame)
    if drawdown_rows:
        written["drawdowns"] = _write(
            pd.concat(drawdown_rows, ignore_index=True), directory / "drawdowns.csv"
        )

    if not stock_attribution.empty:
        written["stock_contribution"] = _write(
            stock_attribution, directory / "stock_contribution.csv"
        )
    if not sector_attribution.empty:
        written["sector_contribution"] = _write(
            sector_attribution, directory / "sector_contribution.csv"
        )

    log.info("wrote %d CSV files to %s", len(written), directory)
    return written
