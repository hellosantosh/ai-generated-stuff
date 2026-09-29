"""Chart design tokens.

A single validated palette drives every chart so the report reads as one
system. The categorical slots are used in fixed order and never cycled: a
fifth series folds into "Other" rather than inventing a hue.

The four-slot categorical set was checked with the palette validator against
the light surface - lightness band, chroma floor, adjacent-pair CVD separation
and normal-vision separation all pass. Aqua and yellow fall below 3:1 contrast
against the surface, so every chart that uses them ships **visible direct
labels**; identity is never carried by color alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text_primary: str
    text_secondary: str
    text_muted: str
    gridline: str
    # Categorical slots, in fixed assignment order.
    series: tuple[str, ...]
    # De-emphasis hue for "context" series in an emphasis chart.
    deemphasis: str
    # Diverging poles plus a neutral midpoint.
    positive: str
    negative: str
    neutral: str
    # Single-hue sequential ramp, light to dark.
    sequential: tuple[str, ...]


LIGHT = Theme(
    name="light",
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    text_muted="#898781",
    gridline="#e1e0d9",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),
    deemphasis="#898781",
    positive="#2a78d6",
    negative="#e34948",
    neutral="#f0efec",
    sequential=("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#256abf", "#184f95"),
)

DARK = Theme(
    name="dark",
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    text_muted="#898781",
    gridline="#2c2c2a",
    series=("#3987e5", "#d95926", "#199e70", "#c98500"),
    deemphasis="#898781",
    positive="#3987e5",
    negative="#e66767",
    neutral="#383835",
    sequential=("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#256abf", "#184f95"),
)

THEMES = {"light": LIGHT, "dark": DARK}

# Stable series assignment: a variant keeps its color whatever else is plotted,
# so filtering the chart never repaints the survivors.
VARIANT_SLOT = {
    "combined": 0,
    "sector_leaders": 1,
    "high_growth": 2,
    "ivv": 3,
}

VARIANT_LABEL = {
    "combined": "Combined",
    "sector_leaders": "Sector leaders",
    "high_growth": "High growth",
    "ivv": "IVV",
}


def color_for(theme: Theme, variant: str) -> str:
    """Color for a variant, keyed on identity rather than plot order."""
    slot = VARIANT_SLOT.get(variant)
    if slot is None:
        return theme.deemphasis
    return theme.series[slot % len(theme.series)]


def label_for(variant: str) -> str:
    return VARIANT_LABEL.get(variant, variant.replace("_", " ").title())


def sequential_steps(theme: Theme, count: int) -> list[str]:
    """``count`` evenly spaced steps from the sequential ramp, light to dark.

    Starts at step 250 rather than 100: the lightest steps recede into the
    surface, which is fine for a continuous heatmap but not for discrete bars.
    """
    ramp = theme.sequential[1:]
    if count <= 1:
        return [ramp[len(ramp) // 2]]
    step = (len(ramp) - 1) / (count - 1)
    return [ramp[min(len(ramp) - 1, int(round(i * step)))] for i in range(count)]
