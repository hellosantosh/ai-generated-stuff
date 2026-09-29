"""Drawdown analysis (REQUIREMENTS 25).

Drawdowns are measured on the **time-weighted return index**, not on the
portfolio's dollar value. In a DCA strategy the dollar value keeps climbing on
new contributions, so a value-based drawdown understates losses badly: during
March 2020 a weekly contributor's balance could look almost flat while the
underlying holdings fell by a third. The TWR index strips contributions out
and shows what actually happened to invested capital.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# Named historical stress windows the reports always cover.
SPECIAL_PERIODS: tuple[tuple[str, dt.date, dt.date], ...] = (
    ("2020 COVID crash", dt.date(2020, 2, 19), dt.date(2020, 3, 23)),
    ("2022 bear market", dt.date(2022, 1, 3), dt.date(2022, 10, 12)),
    ("2025 correction", dt.date(2025, 2, 19), dt.date(2025, 4, 8)),
)

# Minimum depth for an episode to be reported on its own line.
MATERIAL_DRAWDOWN = 0.10


@dataclass(frozen=True)
class DrawdownEpisode:
    peak_date: dt.date
    trough_date: dt.date
    recovery_date: dt.date | None
    depth: float                      # positive fraction, 0.314 = -31.4%
    days_to_trough: int
    days_to_recovery: int | None

    @property
    def recovered(self) -> bool:
        return self.recovery_date is not None

    def to_row(self) -> dict[str, object]:
        return {
            "peak_date": self.peak_date,
            "trough_date": self.trough_date,
            "recovery_date": self.recovery_date,
            "drawdown": -self.depth,
            "days_to_trough": self.days_to_trough,
            "days_to_recovery": self.days_to_recovery,
            "recovered": self.recovered,
        }

    def format_block(self) -> str:
        lines = [
            f"Peak:       {self.peak_date}",
            f"Trough:     {self.trough_date}",
            f"Drawdown:   {-self.depth:.1%}",
        ]
        if self.recovery_date is not None:
            lines.append(f"Recovery:   {self.recovery_date}")
            lines.append(f"Duration:   {self.days_to_recovery} days to recovery")
        else:
            lines.append("Recovery:   not yet recovered")
        lines.append(f"Decline:    {self.days_to_trough} days to trough")
        return "\n".join(lines)


def drawdown_series(values: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak at every point."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return pd.Series(dtype=float)
    peak = numeric.cummax()
    return numeric / peak - 1.0


def drawdown_table(values: pd.Series, min_depth: float = MATERIAL_DRAWDOWN) -> pd.DataFrame:
    """Every distinct drawdown episode deeper than ``min_depth``.

    An episode runs from the peak that preceded the decline to the point where
    the index regains that peak; an unrecovered final episode is included with
    a null recovery date rather than dropped.
    """
    episodes = find_episodes(values, min_depth)
    if not episodes:
        return pd.DataFrame(
            columns=[
                "peak_date", "trough_date", "recovery_date", "drawdown",
                "days_to_trough", "days_to_recovery", "recovered",
            ]
        )
    frame = pd.DataFrame([e.to_row() for e in episodes])
    return frame.sort_values("drawdown").reset_index(drop=True)


def find_episodes(values: pd.Series, min_depth: float = MATERIAL_DRAWDOWN) -> list[DrawdownEpisode]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 2:
        return []

    index = [_as_date(i) for i in numeric.index]
    series = numeric.to_numpy(dtype=float)

    episodes: list[DrawdownEpisode] = []
    peak_value = series[0]
    peak_position = 0
    trough_value = series[0]
    trough_position = 0
    in_drawdown = False

    for position in range(1, len(series)):
        value = series[position]
        if value >= peak_value:
            if in_drawdown:
                depth = 1.0 - trough_value / peak_value
                if depth >= min_depth:
                    episodes.append(
                        DrawdownEpisode(
                            peak_date=index[peak_position],
                            trough_date=index[trough_position],
                            recovery_date=index[position],
                            depth=depth,
                            days_to_trough=(index[trough_position] - index[peak_position]).days,
                            days_to_recovery=(index[position] - index[peak_position]).days,
                        )
                    )
                in_drawdown = False
            peak_value = value
            peak_position = position
            trough_value = value
            trough_position = position
        else:
            in_drawdown = True
            if value < trough_value:
                trough_value = value
                trough_position = position

    if in_drawdown:
        depth = 1.0 - trough_value / peak_value
        if depth >= min_depth:
            episodes.append(
                DrawdownEpisode(
                    peak_date=index[peak_position],
                    trough_date=index[trough_position],
                    recovery_date=None,
                    depth=depth,
                    days_to_trough=(index[trough_position] - index[peak_position]).days,
                    days_to_recovery=None,
                )
            )
    return episodes


def max_drawdown_detail(values: pd.Series) -> DrawdownEpisode | None:
    """The single worst episode, at any depth."""
    episodes = find_episodes(values, min_depth=0.0)
    if not episodes:
        return None
    return max(episodes, key=lambda e: e.depth)


def period_performance(
    values: pd.Series, periods: Iterable[tuple[str, dt.date, dt.date]] = SPECIAL_PERIODS
) -> pd.DataFrame:
    """Return and worst drawdown inside each named stress window."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    rows = []
    for name, start, end in periods:
        window = numeric.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        if len(window) < 2:
            rows.append(
                {
                    "period": name,
                    "start": start,
                    "end": end,
                    "return": np.nan,
                    "max_drawdown": np.nan,
                    "note": "outside the backtest window",
                }
            )
            continue
        total_return = float(window.iloc[-1] / window.iloc[0] - 1.0)
        worst = float(drawdown_series(window).min())
        rows.append(
            {
                "period": name,
                "start": _as_date(window.index[0]),
                "end": _as_date(window.index[-1]),
                "return": total_return,
                "max_drawdown": worst,
                "note": "",
            }
        )
    return pd.DataFrame(rows)


def underwater_duration(values: pd.Series) -> int:
    """Longest span from a peak until that peak is regained, in days.

    Measured peak-to-recovery rather than as a run of days below water, which
    is what "how long was I underwater?" actually asks: the clock starts the
    day the high was set and stops the day it is matched again. An episode
    that never recovers is measured to the end of the series.
    """
    episodes = find_episodes(values, min_depth=0.0)
    if not episodes:
        return 0
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    last_date = _as_date(numeric.index[-1])
    return max(
        ((episode.recovery_date or last_date) - episode.peak_date).days
        for episode in episodes
    )


def _as_date(value: object) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(value).date()
