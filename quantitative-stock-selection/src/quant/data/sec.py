"""SEC EDGAR XBRL company-facts provider (REQUIREMENTS 6.2).

EDGAR is the preferred fundamental source precisely because every fact carries
a ``filed`` date. That date becomes ``data_available_date``, which is what
makes point-in-time backtesting possible at all: the June 30 decision cannot
see the July 15 filing (REQUIREMENTS 38).

The SEC requires a descriptive User-Agent with a contact address and asks for
no more than 10 requests per second. Both are enforced here.
"""

from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..errors import DataError, ProviderError
from ..logging_config import get_logger
from .cache import RawCache, safe_key
from .http import HttpClient, RateLimiter
from .types import FundamentalRecord, FundamentalSeries

log = get_logger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_MAX_REQUESTS_PER_SECOND = 8  # below the published limit of 10

# Each logical metric maps to the us-gaap tags companies actually use, in
# preference order. The first tag present for a period wins.
US_GAAP_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditures": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "interest_expense": ("InterestExpense", "InterestExpenseDebt"),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("ShortTermBorrowings", "DebtCurrent"),
    "shares_outstanding": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "CommonStockSharesOutstanding",
    ),
}

# Balance-sheet items are instants; income/cash-flow items are durations.
INSTANT_METRICS = frozenset(
    {
        "total_assets",
        "total_liabilities",
        "total_equity",
        "current_assets",
        "current_liabilities",
        "cash_and_equivalents",
        "long_term_debt",
        "short_term_debt",
    }
)


def default_user_agent() -> str:
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent:
        raise ProviderError(
            "SEC EDGAR requires a descriptive User-Agent with a contact address. "
            "Set SEC_USER_AGENT in your .env, for example "
            "'Jane Doe jane@example.com'."
        )
    if "@" not in agent:
        raise ProviderError(
            f"SEC_USER_AGENT must include a contact email address, got {agent!r}"
        )
    return agent


class SECProvider:
    name = "sec"

    def __init__(
        self,
        raw_cache: RawCache,
        user_agent: str | None = None,
        max_retries: int = 4,
        backoff_base: float = 1.5,
        timeout: float = 30.0,
        # Filings become public essentially on submission; a small buffer models
        # the delay before the data is usable downstream.
        availability_lag_days: int = 0,
    ) -> None:
        self.raw_cache = raw_cache
        self._user_agent = user_agent
        self.availability_lag = dt.timedelta(days=max(0, int(availability_lag_days)))
        self._http: HttpClient | None = None
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._timeout = timeout
        self._cik_map: dict[str, int] | None = None

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient(
                user_agent=self._user_agent or default_user_agent(),
                max_retries=self._max_retries,
                backoff_base=self._backoff_base,
                timeout=self._timeout,
                rate_limiter=RateLimiter(1.0 / SEC_MAX_REQUESTS_PER_SECOND),
            )
        return self._http

    # --- ticker -> CIK ----------------------------------------------------
    def cik_map(self, force: bool = False) -> dict[str, int]:
        if self._cik_map is not None and not force:
            return self._cik_map
        payload = None if force else self.raw_cache.read(self.name, "company_tickers")
        if payload is None:
            payload = self.http.get_json(TICKER_MAP_URL)
            self.raw_cache.write(self.name, "company_tickers", payload)
        mapping: dict[str, int] = {}
        entries = payload.values() if isinstance(payload, dict) else payload
        for entry in entries:
            try:
                mapping[str(entry["ticker"]).upper()] = int(entry["cik_str"])
            except (KeyError, TypeError, ValueError):
                continue
        if not mapping:
            raise ProviderError("SEC ticker map parsed to zero entries")
        self._cik_map = mapping
        return mapping

    def cik_for(self, ticker: str) -> int | None:
        # EDGAR writes class shares with a dash: BRK.B -> BRK-B.
        candidates = [ticker.upper(), ticker.upper().replace(".", "-")]
        mapping = self.cik_map()
        for candidate in candidates:
            if candidate in mapping:
                return mapping[candidate]
        return None

    # --- company facts ----------------------------------------------------
    def company_facts(self, ticker: str, force: bool = False) -> Mapping[str, Any] | None:
        cik = self.cik_for(ticker)
        if cik is None:
            log.warning("no SEC CIK for ticker %s; fundamentals unavailable", ticker)
            return None
        key = f"companyfacts_{safe_key(ticker)}"
        payload = None if force else self.raw_cache.read(self.name, key)
        if payload is None:
            try:
                payload = self.http.get_json(COMPANY_FACTS_URL.format(cik=cik))
            except ProviderError as exc:
                log.warning("SEC company facts failed for %s (CIK %s): %s", ticker, cik, exc)
                return None
            self.raw_cache.write(self.name, key, payload)
        return payload

    def fetch_fundamentals(self, ticker: str, force: bool = False) -> FundamentalSeries:
        ticker = ticker.upper()
        series = FundamentalSeries(ticker=ticker)
        payload = self.company_facts(ticker, force=force)
        if payload is None:
            return series

        facts = (payload.get("facts") or {}).get("us-gaap") or {}
        if not facts:
            log.warning("%s: SEC company facts contain no us-gaap taxonomy", ticker)
            return series

        # (period_end, fiscal_period) -> metric -> (filed, value, form)
        buckets: dict[tuple[dt.date, str], dict[str, tuple[dt.date, float, str]]] = defaultdict(dict)

        for metric, tags in US_GAAP_TAGS.items():
            for tag in tags:
                entry = facts.get(tag)
                if not entry:
                    continue
                for unit_name, observations in (entry.get("units") or {}).items():
                    if not _unit_is_usable(metric, unit_name):
                        continue
                    for observation in observations:
                        parsed = _parse_observation(metric, observation)
                        if parsed is None:
                            continue
                        period_end, fiscal_period, filed, value, form = parsed
                        bucket = buckets[(period_end, fiscal_period)]
                        existing = bucket.get(metric)
                        # Prefer the earliest filing that reported the period:
                        # that is what a decision maker saw first. A later
                        # restatement is a separate record.
                        if existing is None or filed < existing[0]:
                            bucket[metric] = (filed, value, form)
                # Stop at the first tag that produced anything for this metric.
                if any(metric in bucket for bucket in buckets.values()):
                    break

        skipped = 0
        for (period_end, fiscal_period), metrics in buckets.items():
            if not metrics:
                continue
            filing_date = max(filed for filed, _, _ in metrics.values())
            form = next((f for _, _, f in metrics.values() if f), "")
            values = {name: value for name, (_, value, _) in metrics.items()}
            values = _derive(values)
            try:
                series.add(
                    FundamentalRecord(
                        ticker=ticker,
                        period_end_date=period_end,
                        fiscal_period=fiscal_period,
                        filing_date=filing_date,
                        data_available_date=filing_date + self.availability_lag,
                        metrics=values,
                        provider=self.name,
                        form=form,
                    )
                )
            except DataError as exc:
                # Drop the offending period, keep the company. One malformed
                # XBRL context must not silently remove a whole index member
                # from the universe.
                skipped += 1
                log.debug("%s: skipping period %s - %s", ticker, period_end, exc)

        if skipped:
            log.warning(
                "%s: skipped %d malformed fiscal period(s) out of %d; the remaining "
                "history is used",
                ticker, skipped, len(series.records) + skipped,
            )
        log.debug("%s: parsed %d SEC fundamental periods", ticker, len(series.records))
        return series

    def close(self) -> None:
        if self._http is not None:
            self._http.close()


def _unit_is_usable(metric: str, unit_name: str) -> bool:
    if metric == "eps_diluted":
        return unit_name.startswith("USD/shares")
    if metric == "shares_outstanding":
        return unit_name == "shares"
    return unit_name == "USD"


def _parse_observation(
    metric: str, observation: Mapping[str, Any]
) -> tuple[dt.date, str, dt.date, float, str] | None:
    """Turn one XBRL observation into (period_end, period, filed, value, form).

    Duration facts are kept only when they span roughly one quarter or one
    year; anything else is a half-year or year-to-date figure that would
    corrupt a per-period series.
    """
    try:
        end = dt.date.fromisoformat(str(observation["end"]))
        filed = dt.date.fromisoformat(str(observation["filed"]))
        value = float(observation["val"])
    except (KeyError, TypeError, ValueError):
        return None

    if filed < end:
        # A report about a period cannot predate the end of that period. These
        # appear in EDGAR for companies with non-calendar fiscal years, where a
        # fact's context resolves to a period end later than the filing. Keeping
        # one would let a decision see a period that had not finished.
        return None

    form = str(observation.get("form", ""))
    if form not in {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"}:
        return None

    fiscal_period = str(observation.get("fp") or "")
    if metric in INSTANT_METRICS:
        if "start" in observation:
            return None
        period = fiscal_period or _quarter_label(end)
        return end, _normalize_period(period, form), filed, value, form

    start_raw = observation.get("start")
    if start_raw is None:
        return None
    try:
        start = dt.date.fromisoformat(str(start_raw))
    except (TypeError, ValueError):
        return None
    span = (end - start).days
    is_quarter = 80 <= span <= 100
    is_year = 350 <= span <= 380
    if not (is_quarter or is_year):
        return None
    period = "FY" if is_year else (fiscal_period if fiscal_period.startswith("Q") else _quarter_label(end))
    return end, _normalize_period(period, form), filed, value, form


def _normalize_period(period: str, form: str) -> str:
    period = period.upper()
    if period in {"FY", "Q1", "Q2", "Q3", "Q4"}:
        return period
    return "FY" if form.startswith("10-K") else "Q4"


def _quarter_label(end: dt.date) -> str:
    return f"Q{(end.month - 1) // 3 + 1}"


def _derive(values: dict[str, float]) -> dict[str, float]:
    """Add the composite metrics EDGAR does not tag directly."""
    out = dict(values)
    ocf = out.get("operating_cash_flow")
    capex = out.get("capital_expenditures")
    if ocf is not None and capex is not None:
        out["free_cash_flow"] = ocf - abs(capex)
        out["capital_expenditures"] = abs(capex)
    long_term = out.get("long_term_debt")
    short_term = out.get("short_term_debt")
    if long_term is not None or short_term is not None:
        out["total_debt"] = (long_term or 0.0) + (short_term or 0.0)
    operating_income = out.get("operating_income")
    depreciation = out.get("depreciation_amortization")
    if operating_income is not None and depreciation is not None:
        out["ebitda"] = operating_income + depreciation
    return out
