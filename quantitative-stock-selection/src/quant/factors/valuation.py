"""Valuation factors (REQUIREMENTS 11).

Two deliberate choices:

* Yields (earnings yield, FCF yield) are preferred over their reciprocal
  multiples wherever possible. A yield stays well behaved when earnings cross
  zero, whereas a P/E explodes to infinity and then flips sign.
* Normalization happens **within sector** in the ranking layer, so a software
  company's multiple is ranked against other software companies rather than
  against utilities. Nothing here penalizes growth for being expensive; that
  is left to the 10% valuation weight in the composite.

Forward P/E needs consensus analyst estimates. No point-in-time source for
those is wired up, and back-filling today's estimates onto a 2020 decision
would be a look-ahead leak, so the factor is reported as ``None``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .panel import FundamentalPanel, safe_ratio
from ..data.store import PointInTimeView

VALUATION_FACTOR_NAMES = (
    "market_cap",
    "trailing_pe",
    "forward_pe",
    "earnings_yield",
    "fcf_yield",
    "price_to_sales",
    "price_to_fcf",
    "price_to_book",
    "ev_to_ebitda",
    "ev_to_sales",
    "peg",
    "dividend_yield",
)


def valuation_factors(
    view: PointInTimeView,
    ticker: str,
    panel: FundamentalPanel | None = None,
    eps_growth: float | None = None,
) -> dict[str, float | None]:
    panel = panel if panel is not None else FundamentalPanel(view.fundamentals(ticker))
    factors: dict[str, float | None] = {name: None for name in VALUATION_FACTOR_NAMES}

    frame = view.history(ticker)
    if frame.empty:
        return factors

    # Dividend yield is a ratio of two columns on the same adjusted scale, so
    # it is scale free. Market cap is not - see below.
    price = float(frame["adj_close"].iloc[-1])
    trailing_dividends = float(frame["dividend"].iloc[-252:].sum()) if len(frame) >= 60 else None
    if trailing_dividends is not None and price > 0:
        factors["dividend_yield"] = trailing_dividends / price

    if not panel:
        return factors

    market_cap = _market_cap(frame, panel)
    if market_cap is None:
        return factors
    factors["market_cap"] = market_cap

    net_income = panel.ttm("net_income")
    revenue = panel.ttm("revenue")
    free_cash_flow = panel.free_cash_flow()
    equity = panel.latest("total_equity")

    factors["earnings_yield"] = safe_ratio(net_income, market_cap)
    factors["fcf_yield"] = safe_ratio(free_cash_flow, market_cap)
    factors["price_to_sales"] = safe_ratio(market_cap, revenue)
    factors["price_to_book"] = safe_ratio(market_cap, equity)
    # Multiples are only defined against positive earnings/cash flow.
    factors["trailing_pe"] = safe_ratio(market_cap, net_income)
    factors["price_to_fcf"] = safe_ratio(market_cap, free_cash_flow)

    debt = panel.total_debt()
    cash = panel.latest("cash_and_equivalents")
    enterprise_value = market_cap + (debt or 0.0) - (cash or 0.0)
    if enterprise_value > 0:
        factors["ev_to_ebitda"] = safe_ratio(enterprise_value, panel.ebitda())
        factors["ev_to_sales"] = safe_ratio(enterprise_value, revenue)

    growth = eps_growth if eps_growth is not None else panel.growth("eps_diluted", 1)
    trailing_pe = factors["trailing_pe"]
    if trailing_pe is not None and growth is not None and growth > 0:
        factors["peg"] = trailing_pe / (growth * 100.0)

    return factors


def _market_cap(frame: pd.DataFrame, panel: FundamentalPanel) -> float | None:
    """Price times shares, with both sides on the same day's scale.

    A filing reports the share count as it stood then and is never restated
    for later splits, while a back-adjusted price series restates every bar
    before a split. Multiplying one by the other silently divides the company
    by every split since - or multiplies it, for a reverse split, which is how
    a $100bn industrial ends up ranked among the ten largest companies in the
    index.

    So: take the price as it was actually quoted on the valuation date, and
    carry the reported share count forward through the splits that happened
    between the filing's period end and that date. Both are then on the scale
    that was real on the day.
    """
    record = panel.latest_record("shares_outstanding")
    if record is None:
        return None
    shares = record.get("shares_outstanding")
    if shares is None or shares <= 0:
        return None

    column = "raw_close" if "raw_close" in frame.columns else "close"
    price = float(frame[column].iloc[-1])
    if not np.isfinite(price) or price <= 0:
        return None

    since = frame.loc[pd.Timestamp(record.period_end_date) + pd.Timedelta(days=1) :]
    split_factor = float(since["split_coef"].prod()) if not since.empty else 1.0
    if not np.isfinite(split_factor) or split_factor <= 0:
        split_factor = 1.0
    return price * float(shares) * split_factor
