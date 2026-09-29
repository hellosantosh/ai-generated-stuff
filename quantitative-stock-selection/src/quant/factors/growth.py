"""Growth factors (REQUIREMENTS 9).

Growth is computed on trailing-twelve-month figures rather than single
quarters, so seasonality does not dominate the ranking.

Where a growth rate is mathematically undefined - a zero or negative base,
a company that swung from a loss to a profit - the factor is ``None``. The
alternative, substituting zero or a capped number, would rank a
loss-to-smaller-loss company alongside genuine growers (REQUIREMENTS 16).
"""

from __future__ import annotations

from ..data.store import PointInTimeView
from .panel import FundamentalPanel

GROWTH_FACTOR_NAMES = (
    "revenue_growth_yoy",
    "revenue_cagr_3y",
    "revenue_cagr_5y",
    "eps_growth_yoy",
    "eps_cagr_3y",
    "eps_cagr_5y",
    "net_income_growth_yoy",
    "fcf_growth",
    "ocf_growth",
    "revenue_acceleration",
    "earnings_acceleration",
)


def growth_factors(
    view: PointInTimeView, ticker: str, panel: FundamentalPanel | None = None
) -> dict[str, float | None]:
    panel = panel if panel is not None else FundamentalPanel(view.fundamentals(ticker))
    factors: dict[str, float | None] = {name: None for name in GROWTH_FACTOR_NAMES}
    if not panel:
        return factors

    factors["revenue_growth_yoy"] = panel.growth("revenue", 1)
    factors["revenue_cagr_3y"] = panel.growth("revenue", 3)
    factors["revenue_cagr_5y"] = panel.growth("revenue", 5)
    factors["eps_growth_yoy"] = panel.growth("eps_diluted", 1)
    factors["eps_cagr_3y"] = panel.growth("eps_diluted", 3)
    factors["eps_cagr_5y"] = panel.growth("eps_diluted", 5)
    factors["net_income_growth_yoy"] = panel.growth("net_income", 1)
    factors["ocf_growth"] = panel.growth("operating_cash_flow", 1)
    factors["revenue_acceleration"] = panel.acceleration("revenue")
    factors["earnings_acceleration"] = panel.acceleration("eps_diluted")

    # Free cash flow is derived rather than tagged, so its growth is computed
    # from the derived series directly.
    current_fcf = panel.free_cash_flow()
    base_fcf = panel.ttm("free_cash_flow", 1)
    if base_fcf is None:
        ocf_base = panel.ttm("operating_cash_flow", 1)
        capex_base = panel.ttm("capital_expenditures", 1)
        base_fcf = None if ocf_base is None else ocf_base - abs(capex_base or 0.0)
    if current_fcf is not None and base_fcf is not None and base_fcf > 0:
        factors["fcf_growth"] = current_fcf / base_fcf - 1.0

    return factors
