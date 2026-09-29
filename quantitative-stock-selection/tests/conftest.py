"""Shared fixtures.

The tests run entirely on the synthetic provider: no network, no API keys and
a fixed seed, so a failure always means a code change rather than a market
move or a provider outage.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from quant.config import AppConfig, load_config  # noqa: E402
from quant.data.store import MarketData  # noqa: E402
from quant.data.synthetic import SyntheticProvider  # noqa: E402
from quant.data.universe import SyntheticUniverse  # noqa: E402

HISTORY_START = dt.date(2014, 1, 2)
HISTORY_END = dt.date(2024, 12, 31)
DECISION_DATE = dt.date(2022, 6, 24)     # a Friday
SMALL_UNIVERSE = 44                      # four per GICS sector


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def config(project_root: Path) -> AppConfig:
    return load_config(
        config_dir=project_root / "config",
        overrides={
            "settings": {
                "backtest": {"end_date": HISTORY_END, "num_weeks": 60, "start_date": None},
                "data": {"provider": "synthetic", "fundamentals_provider": "synthetic"},
                "universe": {"source": "synthetic"},
                "reporting": {"generate_charts": False, "generate_excel": False},
            }
        },
        project_root=project_root,
    )


@pytest.fixture(scope="session")
def provider() -> SyntheticProvider:
    return SyntheticProvider(seed=424242, num_stocks=SMALL_UNIVERSE, history_start=HISTORY_START)


@pytest.fixture(scope="session")
def market(provider: SyntheticProvider) -> MarketData:
    tickers = provider.tickers + ["IVV"]
    prices = {t: provider.fetch_prices(t, HISTORY_START, HISTORY_END) for t in tickers}
    fundamentals = {t: provider.fetch_fundamentals(t) for t in provider.tickers}
    universe = SyntheticUniverse(provider)
    return MarketData(
        prices=prices,
        fundamentals=fundamentals,
        sectors=universe.sector_map(),
        universe=universe.membership(HISTORY_START, HISTORY_END),
        benchmark="IVV",
        company_names=universe.company_names(),
        strict=True,
    )


@pytest.fixture
def view(market: MarketData):
    return market.view(DECISION_DATE, strict=True)


@pytest.fixture(scope="session")
def decision_date() -> dt.date:
    return DECISION_DATE
