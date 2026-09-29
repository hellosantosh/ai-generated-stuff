"""Provider interfaces.

The architecture must support several market-data providers (REQUIREMENTS 6),
so selection logic never imports a provider directly - it receives a
``PriceProvider`` and a ``FundamentalProvider``.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, Sequence, runtime_checkable

from .types import FundamentalSeries, PriceHistory


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def fetch_prices(
        self, ticker: str, start: dt.date, end: dt.date, force: bool = False
    ) -> PriceHistory:
        """Daily bars for ``ticker`` covering [start, end].

        Raises ``ProviderError`` when the download fails and
        ``InsufficientDataError`` when the provider has no data for the ticker.
        """
        ...


@runtime_checkable
class FundamentalProvider(Protocol):
    name: str

    def fetch_fundamentals(self, ticker: str, force: bool = False) -> FundamentalSeries:
        """Point-in-time fundamental records, each carrying a filing date."""
        ...


@runtime_checkable
class UniverseProvider(Protocol):
    name: str

    def constituents(self, as_of: dt.date) -> Sequence[str]:
        ...

    def sector_of(self, ticker: str, as_of: dt.date) -> str | None:
        ...
