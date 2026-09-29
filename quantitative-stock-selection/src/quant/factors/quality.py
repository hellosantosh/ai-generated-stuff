"""Profitability, balance-sheet and cash-flow quality factors (REQUIREMENTS 9).

Margins use TTM flows over TTM revenue. Returns on capital use TTM earnings
over the latest balance-sheet instant, which is the usual practical
compromise: averaging opening and closing equity needs a year-ago balance
sheet that is often missing for recent index entrants.
"""

from __future__ import annotations

from .panel import FundamentalPanel, safe_ratio
from ..data.store import PointInTimeView

# Statutory US federal corporate rate, used for NOPAT in the ROIC calculation.
# Effective rates vary by company; a single constant keeps the factor
# comparable across the universe rather than importing tax-line noise.
ASSUMED_TAX_RATE = 0.21

QUALITY_FACTOR_NAMES = (
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roe",
    "roa",
    "roic",
    "debt_to_equity",
    "net_debt_to_ebitda",
    "current_ratio",
    "interest_coverage",
    "cash_to_debt",
)


def quality_factors(
    view: PointInTimeView, ticker: str, panel: FundamentalPanel | None = None
) -> dict[str, float | None]:
    panel = panel if panel is not None else FundamentalPanel(view.fundamentals(ticker))
    factors: dict[str, float | None] = {name: None for name in QUALITY_FACTOR_NAMES}
    if not panel:
        return factors

    factors["gross_margin"] = panel.margin("gross_profit")
    factors["operating_margin"] = panel.margin("operating_income")
    factors["net_margin"] = panel.margin("net_income")

    revenue = panel.ttm("revenue")
    free_cash_flow = panel.free_cash_flow()
    factors["fcf_margin"] = safe_ratio(free_cash_flow, revenue)

    net_income = panel.ttm("net_income")
    equity = panel.latest("total_equity")
    assets = panel.latest("total_assets")
    # Negative equity makes ROE meaningless (a loss-making company with
    # negative book value would score as a spectacular return), so it is not
    # allowed as a denominator.
    factors["roe"] = safe_ratio(net_income, equity)
    factors["roa"] = safe_ratio(net_income, assets)

    operating_income = panel.ttm("operating_income")
    debt = panel.total_debt()
    cash = panel.latest("cash_and_equivalents")
    if operating_income is not None and equity is not None and equity > 0:
        invested_capital = equity + (debt or 0.0) - (cash or 0.0)
        if invested_capital > 0:
            factors["roic"] = operating_income * (1.0 - ASSUMED_TAX_RATE) / invested_capital

    factors["debt_to_equity"] = safe_ratio(debt, equity)

    ebitda = panel.ebitda()
    net_debt = panel.net_debt()
    if ebitda is not None and ebitda > 0 and net_debt is not None:
        factors["net_debt_to_ebitda"] = net_debt / ebitda

    factors["current_ratio"] = safe_ratio(
        panel.latest("current_assets"), panel.latest("current_liabilities")
    )

    interest = panel.ttm("interest_expense")
    if operating_income is not None and interest is not None and abs(interest) > 0:
        factors["interest_coverage"] = operating_income / abs(interest)

    if cash is not None and debt is not None and debt > 0:
        factors["cash_to_debt"] = cash / debt

    return factors
