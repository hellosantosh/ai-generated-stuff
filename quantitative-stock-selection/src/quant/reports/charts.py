"""Matplotlib charts for the backtest report (REQUIREMENTS 36).

Design rules applied throughout:

* **One y-axis per chart.** Two measures on different scales become two charts
  or an indexed common base, never a second axis.
* **Direct labels.** Every line series is labeled at its right end. Two of the
  categorical slots sit below 3:1 contrast on the light surface, so the label
  - not the color - carries identity.
* **Recessive chrome.** Hairline solid gridlines on the y-axis only, no top or
  right spines, axis text in the muted ink.
* **Color by job.** Distinct portfolios get categorical slots; magnitude
  rankings get the single-hue sequential ramp; gain-versus-loss gets the
  diverging pair with a neutral zero.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")  # headless: charts are files, never windows
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from ..logging_config import get_logger
from .theme import THEMES, Theme, color_for, label_for, sequential_steps

log = get_logger(__name__)

FIGSIZE = (10.0, 5.2)
DPI = 150
LINE_WIDTH = 2.0
BAR_GAP = 0.18          # leaves a visible surface gap between adjacent bars
ROUNDING = 0.02         # rounded data-end, in axis fraction


@dataclass
class ChartSet:
    """Paths of every chart written, keyed by name."""

    directory: Path
    charts: dict[str, Path] = field(default_factory=dict)

    def add(self, name: str, path: Path) -> None:
        self.charts[name] = path

    def __len__(self) -> int:
        return len(self.charts)

    def relative(self, base: Path) -> dict[str, str]:
        out = {}
        for name, path in self.charts.items():
            try:
                out[name] = str(path.relative_to(base))
            except ValueError:
                out[name] = str(path)
        return out


# --- helpers --------------------------------------------------------------
def _figure(theme: Theme, figsize: tuple[float, float] = FIGSIZE):
    figure, axes = plt.subplots(figsize=figsize, dpi=DPI)
    figure.patch.set_facecolor(theme.surface)
    axes.set_facecolor(theme.surface)
    return figure, axes


def _style_axes(axes, theme: Theme, ylabel: str = "", xlabel: str = "") -> None:
    axes.grid(axis="y", color=theme.gridline, linewidth=1.0, linestyle="-", zorder=0)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(theme.gridline)
        axes.spines[side].set_linewidth(1.0)
    axes.tick_params(colors=theme.text_muted, labelsize=9, length=0)
    if ylabel:
        axes.set_ylabel(ylabel, color=theme.text_secondary, fontsize=10)
    if xlabel:
        axes.set_xlabel(xlabel, color=theme.text_secondary, fontsize=10)


def _title(axes, theme: Theme, title: str, subtitle: str = "") -> None:
    axes.set_title(title, color=theme.text_primary, fontsize=13, fontweight="bold", loc="left", pad=18 if subtitle else 10)
    if subtitle:
        axes.text(
            0.0, 1.02, subtitle, transform=axes.transAxes,
            color=theme.text_secondary, fontsize=9.5, va="bottom", ha="left",
        )


def _currency(value: float, _position: int = 0) -> str:
    """Compact currency, keeping a decimal below 10k so nearby bars differ.

    Rounding 1.6k, 1.8k and 2.0k all to "$2k" would make three visibly
    different bars carry the same label.
    """
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if magnitude >= 10_000:
        return f"${value / 1_000:,.0f}k"
    if magnitude >= 1_000:
        return f"${value / 1_000:,.1f}k"
    return f"${value:,.0f}"


def _percent(value: float, _position: int = 0) -> str:
    return f"{value * 100:.0f}%"


def _direct_label(axes, theme: Theme, series: pd.Series, text: str, color: str) -> None:
    """Label a line at its right end so identity never rests on color."""
    clean = series.dropna()
    if clean.empty:
        return
    axes.annotate(
        text,
        xy=(clean.index[-1], float(clean.iloc[-1])),
        xytext=(6, 0),
        textcoords="offset points",
        color=color,
        fontsize=9.5,
        fontweight="bold",
        va="center",
        ha="left",
        annotation_clip=False,
    )


def _barh(axes, y: float, width: float, color: str, height: float) -> None:
    """One horizontal bar, anchored at zero.

    Square ends rather than the 4px rounded data-end the house style prefers:
    matplotlib's ``FancyBboxPatch`` measures its corner radius in data units,
    and with dollars on x and row indices on y a radius sized for the x-axis
    becomes an enormous vertical overhang. A correct square bar beats a
    decorative flourish that misreports the geometry. The 2px surface gap
    between adjacent bars is preserved through ``height``.
    """
    if not np.isfinite(width) or width == 0:
        return
    axes.barh(y, width, height=height, color=color, linewidth=0, zorder=2)


def _style_bar_axes(axes, theme: Theme, labels: Sequence[str], positions: np.ndarray) -> None:
    axes.set_yticks(positions)
    axes.set_yticklabels(labels, color=theme.text_secondary, fontsize=9.5)
    axes.axvline(0.0, color=theme.gridline, linewidth=1.0, zorder=1)
    axes.grid(axis="x", color=theme.gridline, linewidth=1.0, zorder=0)
    axes.grid(axis="y", visible=False)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(theme.gridline)
    axes.tick_params(colors=theme.text_muted, labelsize=9, length=0)
    axes.set_ylim(-0.7, len(positions) - 0.3)


def _bar_limits(axes, values: Sequence[float], label_room: float = 0.28) -> None:
    """Axis limits that fit the bars and leave room for the value labels."""
    finite = [float(v) for v in values if np.isfinite(v)]
    if not finite:
        axes.set_xlim(-1, 1)
        return
    low, high = min(finite), max(finite)
    span = max(abs(low), abs(high)) or 1.0
    pad = span * label_room
    axes.set_xlim(min(0.0, low) - (pad if low < 0 else span * 0.02), max(0.0, high) + pad)


def _save(figure, directory: Path, name: str, theme: Theme) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    figure.tight_layout()
    figure.savefig(path, facecolor=theme.surface, bbox_inches="tight")
    plt.close(figure)
    log.debug("wrote chart %s", path)
    return path


def _date_axis(axes, theme: Theme) -> None:
    """Adaptive date ticks.

    ConciseDateFormatter picks the unit that matches the span, so a two-year
    window gets months and a ten-year window gets years. A fixed "%Y" format
    prints the same year several times whenever the locator chooses a
    sub-annual interval.
    """
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


# --- individual charts ----------------------------------------------------
def chart_portfolio_value(result, theme: Theme, directory: Path) -> Path:
    """Value over time for every variant. Categorical: the series are the subject."""
    figure, axes = _figure(theme)
    for name, variant in result.variants.items():
        series = variant.value_series()
        color = color_for(theme, name)
        axes.plot(series.index, series.to_numpy(), color=color, linewidth=LINE_WIDTH, zorder=3)
        _direct_label(axes, theme, series, label_for(name), color)

    contributions = next(iter(result.variants.values())).values["cumulative_contributions"]
    axes.plot(
        contributions.index, contributions.to_numpy(),
        color=theme.deemphasis, linewidth=1.5, linestyle=(0, (4, 3)), zorder=2,
    )
    _direct_label(axes, theme, contributions, "Contributed", theme.deemphasis)

    _style_axes(axes, theme, ylabel="Portfolio value")
    axes.yaxis.set_major_formatter(FuncFormatter(_currency))
    _date_axis(axes, theme)
    _title(
        axes, theme, "Portfolio value over time",
        "Weekly contributions, dividends reinvested. Dashed line is cumulative cash contributed.",
    )
    axes.margins(x=0.12)
    return _save(figure, directory, "portfolio_value", theme)


def chart_vs_benchmark(result, theme: Theme, directory: Path) -> Path:
    """Growth of invested capital, strategy versus IVV. Emphasis form."""
    figure, axes = _figure(theme)
    ordered = [n for n in ("combined", "sector_leaders", "high_growth") if n in result.variants]
    for name in ordered:
        series = result.variants[name].twr
        color = color_for(theme, name)
        axes.plot(series.index, series.to_numpy(), color=color, linewidth=LINE_WIDTH, zorder=3)
        _direct_label(axes, theme, series, label_for(name), color)
    if "ivv" in result.variants:
        series = result.variants["ivv"].twr
        axes.plot(series.index, series.to_numpy(), color=theme.deemphasis, linewidth=LINE_WIDTH, zorder=2)
        _direct_label(axes, theme, series, "IVV", theme.deemphasis)

    axes.axhline(100.0, color=theme.gridline, linewidth=1.0, zorder=1)
    _style_axes(axes, theme, ylabel="Time-weighted index (start = 100)")
    _date_axis(axes, theme)
    _title(
        axes, theme, "Strategy versus IVV",
        "Time-weighted return: contribution timing removed, so this is a like-for-like comparison.",
    )
    axes.margins(x=0.14)
    return _save(figure, directory, "portfolio_vs_ivv", theme)


def chart_drawdown(result, theme: Theme, directory: Path) -> Path:
    from ..backtest.drawdown import drawdown_series

    figure, axes = _figure(theme)
    if "ivv" in result.variants:
        benchmark = drawdown_series(result.variants["ivv"].twr)
        axes.fill_between(
            benchmark.index, benchmark.to_numpy(), 0.0,
            color=theme.deemphasis, alpha=0.25, linewidth=0, zorder=2,
        )
        axes.plot(benchmark.index, benchmark.to_numpy(), color=theme.deemphasis, linewidth=1.5, zorder=3)
        _direct_label(axes, theme, benchmark, "IVV", theme.deemphasis)

    primary = "combined" if "combined" in result.variants else next(iter(result.variants))
    series = drawdown_series(result.variants[primary].twr)
    color = color_for(theme, primary)
    axes.fill_between(series.index, series.to_numpy(), 0.0, color=color, alpha=0.30, linewidth=0, zorder=4)
    axes.plot(series.index, series.to_numpy(), color=color, linewidth=LINE_WIDTH, zorder=5)
    _direct_label(axes, theme, series, label_for(primary), color)

    _style_axes(axes, theme, ylabel="Drawdown from peak")
    axes.yaxis.set_major_formatter(FuncFormatter(_percent))
    _date_axis(axes, theme)
    _title(
        axes, theme, "Drawdown over time",
        "Measured on the time-weighted index, so weekly contributions do not mask losses.",
    )
    axes.margins(x=0.12)
    return _save(figure, directory, "drawdown", theme)


def chart_rolling_return(result, theme: Theme, directory: Path, months: int) -> Path:
    window = int(round(months * 52 / 12))
    figure, axes = _figure(theme)
    plotted = False
    for name, variant in result.variants.items():
        returns = variant.returns.dropna()
        if len(returns) <= window:
            continue
        rolling = (1.0 + returns).rolling(window).apply(np.prod, raw=True) - 1.0
        rolling = rolling.dropna()
        if rolling.empty:
            continue
        color = color_for(theme, name) if name != "ivv" else theme.deemphasis
        axes.plot(rolling.index, rolling.to_numpy(), color=color, linewidth=LINE_WIDTH, zorder=3)
        _direct_label(axes, theme, rolling, label_for(name), color)
        plotted = True

    axes.axhline(0.0, color=theme.gridline, linewidth=1.0, zorder=1)
    _style_axes(axes, theme, ylabel=f"Rolling {months}-month return")
    axes.yaxis.set_major_formatter(FuncFormatter(_percent))
    _date_axis(axes, theme)
    _title(
        axes, theme, f"Rolling {months}-month return",
        "Time-weighted, annual periods overlapping week to week."
        if plotted else "Not enough history for this window.",
    )
    axes.margins(x=0.14)
    return _save(figure, directory, f"rolling_{months}m", theme)


def chart_sector_contribution(frame: pd.DataFrame, theme: Theme, directory: Path) -> Path:
    """Profit and loss by sector. Diverging: the sign is the point."""
    figure, axes = _figure(theme, (10.0, 5.6))
    if frame.empty:
        _title(axes, theme, "Sector contribution", "No sector attribution available.")
        _style_axes(axes, theme)
        return _save(figure, directory, "sector_contribution", theme)

    data = frame.sort_values("pnl")
    positions = np.arange(len(data))
    for position, (_, row) in zip(positions, data.iterrows()):
        value = float(row["pnl"])
        color = theme.positive if value >= 0 else theme.negative
        _barh(axes, position, value, color, 1.0 - BAR_GAP)
        axes.annotate(
            _currency(value),
            xy=(value, position), xytext=(6 if value >= 0 else -6, 0),
            textcoords="offset points", color=theme.text_secondary, fontsize=9,
            va="center", ha="left" if value >= 0 else "right",
        )

    _style_bar_axes(axes, theme, list(data["sector"]), positions)
    axes.xaxis.set_major_formatter(FuncFormatter(_currency))
    _bar_limits(axes, data["pnl"].tolist())
    _title(axes, theme, "Contribution by sector", "Realized plus unrealized profit and loss over the backtest.")
    return _save(figure, directory, "sector_contribution", theme)


def _stock_bar_chart(
    frame: pd.DataFrame, theme: Theme, directory: Path, name: str, title: str, subtitle: str, winners: bool
) -> Path:
    figure, axes = _figure(theme, (10.0, 5.2))
    if frame.empty:
        _title(axes, theme, title, "No positions to report.")
        _style_axes(axes, theme)
        return _save(figure, directory, name, theme)

    # Both charts are ranked by magnitude, so the biggest bar sits at the top
    # and the ramp darkens with size.
    data = frame.sort_values("pnl", ascending=winners).tail(12)
    positions = np.arange(len(data))
    colors = sequential_steps(theme, len(data)) if winners else [theme.negative] * len(data)
    for position, (_, row), color in zip(positions, data.iterrows(), colors):
        value = float(row["pnl"])
        _barh(axes, position, value, color, 1.0 - BAR_GAP)
        axes.annotate(
            _currency(value),
            xy=(value, position), xytext=(6 if value >= 0 else -6, 0),
            textcoords="offset points", color=theme.text_secondary, fontsize=9,
            va="center", ha="left" if value >= 0 else "right",
        )

    _style_bar_axes(axes, theme, list(data["ticker"]), positions)
    axes.xaxis.set_major_formatter(FuncFormatter(_currency))
    _bar_limits(axes, data["pnl"].tolist())
    _title(axes, theme, title, subtitle)
    return _save(figure, directory, name, theme)


def chart_top_winners(frame: pd.DataFrame, theme: Theme, directory: Path) -> Path:
    return _stock_bar_chart(
        frame, theme, directory, "top_winners", "Top contributors",
        "Largest positive profit and loss by holding.", winners=True,
    )


def chart_top_losers(frame: pd.DataFrame, theme: Theme, directory: Path) -> Path:
    losers = frame[frame["pnl"] < 0] if not frame.empty else frame
    return _stock_bar_chart(
        losers, theme, directory, "top_losers", "Largest detractors",
        "Largest negative profit and loss by holding.", winners=False,
    )


def chart_allocation(weights: Mapping[str, float], theme: Theme, directory: Path) -> Path:
    """Ending allocation by sector. A bar, not a pie: 11 classes need a scale."""
    figure, axes = _figure(theme, (10.0, 5.6))
    if not weights:
        _title(axes, theme, "Portfolio allocation", "No holdings at the end of the backtest.")
        _style_axes(axes, theme)
        return _save(figure, directory, "allocation", theme)

    items = sorted(weights.items(), key=lambda kv: kv[1])
    positions = np.arange(len(items))
    colors = sequential_steps(theme, len(items))
    for position, (label, weight), color in zip(positions, items, colors):
        _barh(axes, position, float(weight), color, 1.0 - BAR_GAP)
        axes.annotate(
            f"{weight:.1%}", xy=(float(weight), position), xytext=(6, 0),
            textcoords="offset points", color=theme.text_secondary, fontsize=9, va="center",
        )

    _style_bar_axes(axes, theme, [label for label, _ in items], positions)
    axes.xaxis.set_major_formatter(FuncFormatter(_percent))
    _bar_limits(axes, [w for _, w in items], label_room=0.18)
    _title(axes, theme, "Ending allocation by sector", "Share of the final portfolio value.")
    return _save(figure, directory, "allocation", theme)


def chart_turnover(result, theme: Theme, directory: Path) -> Path:
    """Rolling one-year sale volume as a share of portfolio value."""
    figure, axes = _figure(theme)
    plotted = False
    for name, variant in result.variants.items():
        transactions = variant.portfolio.transactions_frame()
        values = variant.values
        if transactions.empty or values.empty:
            continue
        sells = transactions[transactions["action"] == "SELL"].copy()
        if sells.empty:
            continue
        sells["trade_date"] = pd.to_datetime(sells["trade_date"])
        weekly = sells.groupby("trade_date")["gross_amount"].sum()
        weekly = weekly.reindex(values.index, fill_value=0.0)
        rolling = weekly.rolling(52, min_periods=8).sum() / values["total_value"]
        rolling = rolling.dropna()
        if rolling.empty:
            continue
        color = color_for(theme, name) if name != "ivv" else theme.deemphasis
        axes.plot(rolling.index, rolling.to_numpy(), color=color, linewidth=LINE_WIDTH, zorder=3)
        _direct_label(axes, theme, rolling, label_for(name), color)
        plotted = True

    _style_axes(axes, theme, ylabel="Trailing 12-month turnover")
    axes.yaxis.set_major_formatter(FuncFormatter(_percent))
    _date_axis(axes, theme)
    _title(
        axes, theme, "Portfolio turnover",
        "Sale proceeds over the prior year as a share of portfolio value."
        if plotted else "No sales were executed.",
    )
    axes.margins(x=0.14)
    return _save(figure, directory, "turnover", theme)


# --- entry point ----------------------------------------------------------
def generate_charts(
    result,
    directory: Path,
    sector_attribution: pd.DataFrame,
    stock_attribution: pd.DataFrame,
    ending_allocation: Mapping[str, float],
    theme_name: str = "light",
) -> ChartSet:
    """Render every chart required by REQUIREMENTS 36."""
    theme = THEMES.get(theme_name, THEMES["light"])
    directory = Path(directory)
    charts = ChartSet(directory=directory)

    charts.add("portfolio_value", chart_portfolio_value(result, theme, directory))
    charts.add("portfolio_vs_ivv", chart_vs_benchmark(result, theme, directory))
    charts.add("drawdown", chart_drawdown(result, theme, directory))
    charts.add("rolling_12m", chart_rolling_return(result, theme, directory, 12))
    charts.add("rolling_36m", chart_rolling_return(result, theme, directory, 36))
    charts.add("sector_contribution", chart_sector_contribution(sector_attribution, theme, directory))
    charts.add("top_winners", chart_top_winners(stock_attribution, theme, directory))
    charts.add("top_losers", chart_top_losers(stock_attribution, theme, directory))
    charts.add("allocation", chart_allocation(ending_allocation, theme, directory))
    charts.add("turnover", chart_turnover(result, theme, directory))

    log.info("wrote %d charts to %s", len(charts), directory)
    return charts
