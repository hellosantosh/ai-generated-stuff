"""Backtest integrity and reproducibility (REQUIREMENTS 22, 38, 39).

Two jobs:

* **Point-in-time verification** - confirm that a decision date really can
  only see data dated on or before it. ``verify_point_in_time`` is called at
  every decision date in strict mode, and the look-ahead test suite drives the
  same function with deliberately poisoned data.
* **Provenance** - capture everything needed to reproduce a run: config,
  data-provider versions, retrieval timestamps, universe, strategy version,
  software and Python versions, and a unique run ID.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .. import __version__
from ..config import AppConfig
from ..data.store import MarketData, PointInTimeView
from ..errors import LookAheadError
from ..logging_config import get_logger

log = get_logger(__name__)


def make_run_id(prefix: str = "BACKTEST", when: dt.datetime | None = None, sequence: int = 1) -> str:
    """Run identifier in the documented form ``BACKTEST-20260928-001``."""
    when = when or dt.datetime.now()
    return f"{prefix}-{when:%Y%m%d}-{sequence:03d}"


def next_run_id(existing: Sequence[str], prefix: str = "BACKTEST", when: dt.datetime | None = None) -> str:
    """First unused run ID for today."""
    when = when or dt.datetime.now()
    stamp = f"{prefix}-{when:%Y%m%d}-"
    used = {
        int(run_id[len(stamp) :])
        for run_id in existing
        if run_id.startswith(stamp) and run_id[len(stamp) :].isdigit()
    }
    sequence = 1
    while sequence in used:
        sequence += 1
    return make_run_id(prefix, when, sequence)


def git_commit(root: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root) if root else None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


@dataclass
class ProvenanceRecord:
    """Everything needed to reproduce a run (REQUIREMENTS 39)."""

    run_id: str
    created_at: dt.datetime
    software_version: str
    strategy_version: str
    python_version: str
    platform: str
    git_commit: str | None
    config: dict[str, Any]
    universe: dict[str, Any]
    data_providers: dict[str, Any]
    data_snapshot_hash: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "software_version": self.software_version,
            "strategy_version": self.strategy_version,
            "python_version": self.python_version,
            "platform": self.platform,
            "git_commit": self.git_commit,
            "config": self.config,
            "universe": self.universe,
            "data_providers": self.data_providers,
            "data_snapshot_hash": self.data_snapshot_hash,
            "warnings": self.warnings,
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path


def snapshot_hash(market: MarketData) -> str:
    """Stable fingerprint of the loaded data.

    Two runs producing the same hash saw the same bars, so a difference in
    results must come from the configuration rather than from a data refresh.
    """
    digest = hashlib.sha256()
    for ticker in sorted(market.prices):
        history = market.prices[ticker]
        frame = history.frame
        digest.update(ticker.encode("utf-8"))
        digest.update(str(len(frame)).encode("utf-8"))
        digest.update(str(history.first_date).encode("utf-8"))
        digest.update(str(history.last_date).encode("utf-8"))
        # Round to a cent: float noise across pandas versions must not change
        # the hash of identical data.
        digest.update(f"{frame['adj_close'].round(2).sum():.2f}".encode("utf-8"))
    for ticker in sorted(market.fundamentals):
        series = market.fundamentals[ticker]
        digest.update(f"{ticker}:{len(series.records)}".encode("utf-8"))
    return digest.hexdigest()[:16]


def build_provenance(
    run_id: str,
    config: AppConfig,
    market: MarketData,
    warnings: Sequence[str] = (),
) -> ProvenanceRecord:
    membership = market.universe
    return ProvenanceRecord(
        run_id=run_id,
        created_at=dt.datetime.now(),
        software_version=__version__,
        strategy_version=config.strategy.version,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        git_commit=git_commit(config.project_root),
        config=config.to_dict(),
        universe={
            "name": membership.name,
            "source": membership.source,
            "survivorship_free": membership.survivorship_free,
            "verified_through": membership.verified_through.isoformat()
            if membership.verified_through
            else None,
            "bias_note": membership.bias_note,
            "ever_members": len(membership),
            "sector_classification_point_in_time": market.sectors.point_in_time,
        },
        data_providers=market.provenance(),
        data_snapshot_hash=snapshot_hash(market),
        warnings=list(warnings),
    )


# --- point-in-time verification -------------------------------------------
def verify_point_in_time(
    view: PointInTimeView,
    tickers: Sequence[str],
    strict: bool = True,
) -> list[str]:
    """Confirm nothing visible through ``view`` postdates its decision date.

    Checks every price frame's last bar and every fundamental record's
    ``data_available_date``. Returns the list of violations; in strict mode the
    first one raises instead.
    """
    violations: list[str] = []
    as_of = view.as_of

    for ticker in tickers:
        if not view.has(ticker):
            continue
        frame = view.history(ticker)
        if frame.empty:
            continue
        last = frame.index[-1].date()
        if last > as_of:
            violations.append(
                f"{ticker}: price frame contains a bar dated {last}, after the "
                f"decision date {as_of}"
            )
        for record in view.fundamentals(ticker):
            if record.data_available_date > as_of:
                violations.append(
                    f"{ticker}: fundamental for period {record.period_end_date} became "
                    f"available {record.data_available_date}, after the decision date {as_of}"
                )
            if record.filing_date > as_of:
                violations.append(
                    f"{ticker}: fundamental for period {record.period_end_date} was filed "
                    f"{record.filing_date}, after the decision date {as_of}"
                )

    if violations and strict:
        head = "\n".join(f"  - {v}" for v in violations[:5])
        raise LookAheadError(
            f"point-in-time verification failed at {as_of} with "
            f"{len(violations)} violation(s):\n{head}"
        )
    return violations


def verify_execution_order(events: Sequence[Any]) -> list[str]:
    """Confirm every fill happens strictly after its own decision."""
    problems: list[str] = []
    for event in events:
        if event.execution_date <= event.decision_date:
            problems.append(
                f"contribution {event.index}: execution {event.execution_date} is not "
                f"after decision {event.decision_date}"
            )
    return problems


def check_universe_membership(
    market: MarketData, ticker: str, as_of: dt.date
) -> str | None:
    """Flag a selection that was not actually an index member on that date."""
    if not market.universe.intervals:
        return None
    if not market.universe.was_member(ticker, as_of):
        return f"{ticker} was not a member of {market.universe.name} on {as_of}"
    return None
