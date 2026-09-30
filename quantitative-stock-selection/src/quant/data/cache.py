"""On-disk cache for downloaded data (REQUIREMENTS 30).

Two layers:

* ``RawCache``   - verbatim provider responses under ``data/raw/<provider>/``,
                   so a result can be re-derived without another download.
* ``FrameCache`` - parsed frames as CSV under ``data/cache/<provider>/`` with a
                   sidecar JSON recording provider, endpoint and retrieval
                   time. Staleness is decided by that timestamp, never guessed.

CSV rather than Parquet: it adds no binary dependency and the cached files stay
inspectable with ordinary tools, which matters when auditing a backtest.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ..logging_config import get_logger
from .types import ProviderInfo

log = get_logger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_key(value: str) -> str:
    """Filesystem-safe cache key. ``BRK.B`` and ``BF-B`` must not collide."""
    return _SAFE.sub("_", value.strip().upper())


class RawCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, provider: str, key: str, suffix: str = ".json") -> Path:
        return self.root / safe_key(provider).lower() / f"{safe_key(key)}{suffix}"

    def write(self, provider: str, key: str, payload: Any, suffix: str = ".json") -> Path:
        target = self.path(provider, key, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".json":
            target.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        else:
            target.write_text(str(payload), encoding="utf-8")
        return target

    def read(self, provider: str, key: str, suffix: str = ".json") -> Any | None:
        target = self.path(provider, key, suffix)
        if not target.exists():
            return None
        if suffix == ".json":
            return json.loads(target.read_text(encoding="utf-8"))
        return target.read_text(encoding="utf-8")


class FrameCache:
    """Cached DataFrames with provenance metadata."""

    def __init__(self, root: str | Path, ttl_days: int = 1) -> None:
        self.root = Path(root)
        self.ttl = dt.timedelta(days=max(0, int(ttl_days)))

    def _paths(self, provider: str, key: str) -> tuple[Path, Path]:
        folder = self.root / safe_key(provider).lower()
        stem = safe_key(key)
        return folder / f"{stem}.csv", folder / f"{stem}.meta.json"

    def load(
        self, provider: str, key: str, ignore_ttl: bool = False
    ) -> tuple[pd.DataFrame, ProviderInfo] | None:
        data_path, meta_path = self._paths(provider, key)
        if not data_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            retrieved_at = dt.datetime.fromisoformat(meta["retrieved_at"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning("cache metadata for %s/%s is unreadable (%s); ignoring cache", provider, key, exc)
            return None

        if not ignore_ttl and self.ttl.total_seconds() > 0:
            age = dt.datetime.now() - retrieved_at
            if age > self.ttl:
                log.debug("cache for %s/%s is %s old, beyond TTL %s", provider, key, age, self.ttl)
                return None

        frame = pd.read_csv(data_path, parse_dates=["date"]) if _has_date_header(data_path) else pd.read_csv(data_path)
        info = ProviderInfo(
            name=meta.get("provider", provider),
            retrieved_at=retrieved_at,
            endpoint=meta.get("endpoint"),
            notes=meta.get("notes"),
            split_adjusted=bool(meta.get("split_adjusted", False)),
        )
        return frame, info

    def store(
        self,
        provider: str,
        key: str,
        frame: pd.DataFrame,
        endpoint: str | None = None,
        notes: str | None = None,
        retrieved_at: dt.datetime | None = None,
        split_adjusted: bool = False,
    ) -> ProviderInfo:
        data_path, meta_path = self._paths(provider, key)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        out = frame.copy()
        if out.index.name == "date":
            out = out.reset_index()
        out.to_csv(data_path, index=False)
        info = ProviderInfo(
            name=provider,
            retrieved_at=retrieved_at or dt.datetime.now(),
            endpoint=endpoint,
            notes=notes,
            split_adjusted=split_adjusted,
        )
        meta_path.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")
        return info

    def stored_keys(self, provider: str) -> list[str]:
        folder = self.root / safe_key(provider).lower()
        if not folder.exists():
            return []
        return sorted(p.stem for p in folder.glob("*.csv"))

    def clear(self, provider: str | None = None) -> int:
        target = self.root if provider is None else self.root / safe_key(provider).lower()
        if not target.exists():
            return 0
        removed = 0
        for path in target.rglob("*"):
            if path.is_file():
                path.unlink()
                removed += 1
        return removed


def _has_date_header(path: Path) -> bool:
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline()
    return "date" in [c.strip().lower() for c in header.split(",")]


def detect_gaps(frame: pd.DataFrame, max_gap_days: int = 7) -> list[tuple[dt.date, dt.date]]:
    """Report suspicious holes in a daily series (REQUIREMENTS 30).

    Weekends and single holidays are normal; anything longer than
    ``max_gap_days`` calendar days is surfaced so the caller can decide.
    """
    if frame.empty:
        return []
    index = pd.DatetimeIndex(frame.index if frame.index.name == "date" else frame["date"])
    index = index.sort_values()
    deltas = index.to_series().diff().dt.days
    gaps: list[tuple[dt.date, dt.date]] = []
    for position, delta in enumerate(deltas):
        if position == 0 or pd.isna(delta):
            continue
        if delta > max_gap_days:
            gaps.append((index[position - 1].date(), index[position].date()))
    return gaps


def find_duplicates(frame: pd.DataFrame) -> list[dt.date]:
    if frame.empty:
        return []
    index = pd.DatetimeIndex(frame.index if frame.index.name == "date" else frame["date"])
    duplicated = index[index.duplicated()]
    return sorted({ts.date() for ts in duplicated})
