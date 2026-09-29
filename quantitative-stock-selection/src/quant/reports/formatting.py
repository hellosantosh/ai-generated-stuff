"""Shared formatting helpers and the standing research disclaimer."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

# REQUIREMENTS 64: every report must state what this is and is not.
DISCLAIMER = (
    "This is a research and backtesting system. Historical results do not establish "
    "that the strategy will outperform in future. Nothing here is investment advice, "
    "no trade has been or will be placed by this software, and every figure below is "
    "a model output subject to the data limitations listed in this report."
)

ACTION_ORDER = ["NEW", "BUY", "HOLD", "NO CHANGE", "SELL", "EXIT"]


def money(value: float | None, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"${value:,.{decimals}f}"


def percent(value: float | None, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value * 100:.{decimals}f}%"


def number(value: float | None, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value:,.{decimals}f}"


def integer(value: float | None) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{int(value):,}"


def signed_percent(value: float | None, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value * 100:+.{decimals}f}%"


def rank_change_label(change: int | None) -> str:
    if change is None:
        return "new"
    if change > 0:
        return f"+{change}"
    if change < 0:
        return str(change)
    return "="


def markdown_table(rows: Sequence[Sequence[Any]], headers: Sequence[str], align: Sequence[str] | None = None) -> str:
    """Render a GitHub-flavored Markdown table."""
    align = align or ["left"] * len(headers)
    separators = []
    for alignment in align:
        if alignment == "right":
            separators.append("---:")
        elif alignment == "center":
            separators.append(":---:")
        else:
            separators.append("---")
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "| " + " | ".join(separators) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(lines)


def frame_to_markdown(frame: pd.DataFrame, float_format: str = "{:,.2f}") -> str:
    if frame is None or frame.empty:
        return "_No data._"
    display = frame.copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            display[column] = display[column].map(
                lambda v: "" if pd.isna(v) else float_format.format(v)
            )
    headers = list(display.columns)
    rows = display.astype(str).values.tolist()
    align = ["right" if pd.api.types.is_numeric_dtype(frame[c]) else "left" for c in frame.columns]
    return markdown_table(rows, headers, align)


def classify_action(ticker: str, previous_rank: int | None, held: bool, selected: bool) -> str:
    """Model output describing how the selection changed (REQUIREMENTS 26).

    This is a description of the model's state, never a trade instruction.
    """
    if selected and not held and previous_rank is None:
        return "NEW"
    if selected and not held:
        return "BUY"
    if selected and held:
        return "HOLD"
    if not selected and held:
        return "EXIT"
    return "NO CHANGE"
