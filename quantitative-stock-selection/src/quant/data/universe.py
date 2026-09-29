"""Stock universe construction (REQUIREMENTS 7, 8).

Survivorship bias is the reason this module exists. Running a 2019 backtest
against today's S&P 500 members quietly deletes every company that was dropped
in between - exactly the companies that did badly. So:

* ``WikipediaSP500`` takes the current membership table *and* the published
  change log, then walks the changes backwards to reconstruct who was actually
  in the index on any past date. The resulting ``UniverseMembership`` is
  flagged ``survivorship_free=True``.
* ``StaticUniverse`` reads a plain ticker list and is flagged
  ``survivorship_free=False``. Every report built on it says so in print.

Sector classification from Wikipedia is current-only, so ``SectorMap`` built
here is marked non-point-in-time regardless of the membership quality.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from ..errors import DataError, ProviderError
from ..logging_config import get_logger
from .cache import RawCache
from .http import HttpClient, RateLimiter
from .store import SectorAssignment, SectorMap, UniverseMembership

log = get_logger(__name__)

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
EARLIEST_SUPPORTED = dt.date(1990, 1, 1)


def normalize_ticker(value: object) -> str | None:
    """Normalize an index ticker to the dotted convention (BRK.B)."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text or text in {"NAN", "NONE", "-", "—"}:
        return None
    text = re.sub(r"\[.*?\]", "", text)          # strip Wikipedia footnotes
    text = text.replace("​", "").strip()
    text = text.replace("-", ".") if re.fullmatch(r"[A-Z]+-[A-Z]", text) else text
    return text or None


class StaticUniverse:
    """A fixed ticker list. Explicitly survivorship biased."""

    name = "static"

    def __init__(self, tickers: Sequence[str], universe_name: str = "sp500") -> None:
        self.tickers = [t for t in (normalize_ticker(t) for t in tickers) if t]
        self.universe_name = universe_name
        if not self.tickers:
            raise DataError("static universe is empty")

    @classmethod
    def from_csv(cls, path: str | Path, universe_name: str = "sp500") -> "StaticUniverse":
        path = Path(path)
        if not path.exists():
            raise DataError(
                f"static universe file not found: {path}. Run `python main.py update-data` "
                f"with universe.source=wikipedia to create one."
            )
        frame = pd.read_csv(path)
        column = next(
            (c for c in frame.columns if c.strip().lower() in {"ticker", "symbol"}), None
        )
        if column is None:
            raise DataError(f"{path}: expected a `ticker` or `symbol` column")
        universe = cls(frame[column].tolist(), universe_name)
        universe._frame = frame  # type: ignore[attr-defined]
        return universe

    def membership(self, start: dt.date, end: dt.date) -> UniverseMembership:
        membership = UniverseMembership(
            name=self.universe_name, survivorship_free=False, source="static-list"
        )
        for ticker in self.tickers:
            membership.add(ticker, EARLIEST_SUPPORTED, None)
        log.warning(
            "static universe: %d tickers held constant over the whole backtest. "
            "Results are SURVIVORSHIP BIASED.",
            len(self.tickers),
        )
        return membership

    def sector_map(self) -> SectorMap:
        sector_map = SectorMap(point_in_time=False)
        frame = getattr(self, "_frame", None)
        if frame is None:
            return sector_map
        ticker_col = next((c for c in frame.columns if c.strip().lower() in {"ticker", "symbol"}), None)
        sector_col = next((c for c in frame.columns if "sector" in c.strip().lower()), None)
        if ticker_col is None or sector_col is None:
            return sector_map
        for _, row in frame.iterrows():
            ticker = normalize_ticker(row[ticker_col])
            sector = str(row[sector_col]).strip()
            if ticker and sector and sector.lower() != "nan":
                sector_map.add_current(ticker, sector, source="static-list")
        return sector_map

    def company_names(self) -> dict[str, str]:
        frame = getattr(self, "_frame", None)
        if frame is None:
            return {}
        ticker_col = next((c for c in frame.columns if c.strip().lower() in {"ticker", "symbol"}), None)
        name_col = next(
            (c for c in frame.columns if c.strip().lower() in {"name", "security", "company", "company_name"}),
            None,
        )
        if ticker_col is None or name_col is None:
            return {}
        out = {}
        for _, row in frame.iterrows():
            ticker = normalize_ticker(row[ticker_col])
            if ticker:
                out[ticker] = str(row[name_col]).strip()
        return out


class WikipediaSP500:
    """S&P 500 membership, reconstructed as far back as the sources allow.

    The article carries the *current* constituents. Until mid-2026 it also
    carried a "Selected changes" table listing every addition and removal with
    a date; that section has since been deleted. Rather than give up on
    point-in-time membership, this provider walks the article's revision
    history for the newest revision that still contains the change log, then:

    1. reconstructs membership backwards from that revision's own constituent
       list using the change rows, and
    2. reconciles that revision forward to today against the live table, using
       the ``Date added`` column for entrants.

    Removals that happened after the change log disappeared cannot be dated, so
    ``verified_through`` records the revision's date and the affected tickers
    are closed at it rather than carried forward as survivors.
    """

    name = "wikipedia"
    API_URL = "https://en.wikipedia.org/w/api.php"
    OLDID_URL = "https://en.wikipedia.org/w/index.php"
    # How far back to probe for a revision still containing the change log.
    PROBE_OFFSETS_DAYS = (0, 30, 60, 120, 240, 480, 900)

    def __init__(
        self,
        raw_cache: RawCache,
        http: HttpClient | None = None,
        universe_name: str = "sp500",
    ) -> None:
        self.raw_cache = raw_cache
        self.universe_name = universe_name
        self.http = http or HttpClient(
            user_agent="quant-stock-selector/1.0 (research; contact via SEC_USER_AGENT)",
            rate_limiter=RateLimiter(1.0),
        )
        self._current: pd.DataFrame | None = None
        self._changes_snapshot: _ChangesSnapshot | None = None

    # --- download ---------------------------------------------------------
    def _page_html(self, force: bool = False) -> str:
        cached = None if force else self.raw_cache.read(self.name, "sp500_page", suffix=".html")
        if cached:
            return cached
        response = self.http.get(WIKIPEDIA_SP500_URL)
        self.raw_cache.write(self.name, "sp500_page", response.text, suffix=".html")
        return response.text

    def _revision_html(self, revid: int, force: bool = False) -> str:
        key = f"sp500_rev_{revid}"
        cached = None if force else self.raw_cache.read(self.name, key, suffix=".html")
        if cached:
            return cached
        response = self.http.get(self.OLDID_URL, params={"oldid": revid})
        self.raw_cache.write(self.name, key, response.text, suffix=".html")
        return response.text

    def _revision_before(self, timestamp: str) -> dict[str, object] | None:
        payload = self.http.get_json(
            self.API_URL,
            params={
                "action": "query",
                "prop": "revisions",
                "titles": "List of S&P 500 companies",
                "rvlimit": "1",
                "rvprop": "ids|timestamp",
                "rvstart": timestamp,
                "rvdir": "older",
                "format": "json",
            },
        )
        pages = (payload.get("query") or {}).get("pages") or {}
        for page in pages.values():
            revisions = page.get("revisions") or []
            if revisions:
                return revisions[0]
        return None

    @staticmethod
    def _parse_tables(html: str) -> list[pd.DataFrame]:
        try:
            return pd.read_html(io.StringIO(html))
        except (ValueError, ImportError) as exc:
            raise ProviderError(f"could not parse the S&P 500 page HTML: {exc}") from exc

    # --- current table ----------------------------------------------------
    def current_constituents(self, force: bool = False) -> pd.DataFrame:
        if self._current is not None and not force:
            return self._current
        tables = self._parse_tables(self._page_html(force=force))
        table = next((t for t in tables if _has_columns(t, {"symbol", "gics sector"})), None)
        if table is None:
            raise ProviderError(
                "the S&P 500 page no longer contains a constituents table with Symbol "
                "and GICS Sector columns (provider schema change)"
            )
        self._current = _constituent_frame(table)
        return self._current

    # --- change log -------------------------------------------------------
    def _changes(self, force: bool = False) -> "_ChangesSnapshot":
        """Newest revision that still carries the change log, with its tables."""
        if self._changes_snapshot is not None and not force:
            return self._changes_snapshot

        marker = None if force else self.raw_cache.read(self.name, "changes_revision")
        candidates: list[int] = []
        if marker and isinstance(marker, dict) and marker.get("revid"):
            candidates.append(int(marker["revid"]))

        today = dt.date.today()
        for offset in self.PROBE_OFFSETS_DAYS:
            probe = today - dt.timedelta(days=offset)
            revision = self._revision_before(f"{probe.isoformat()}T23:59:59Z")
            if revision is None:
                continue
            revid = int(revision["revid"])
            if revid not in candidates:
                candidates.append(revid)

        for revid in candidates:
            html = self._revision_html(revid)
            tables = self._parse_tables(html)
            changes = next((t for t in tables if _is_changes_table(t)), None)
            constituents = next(
                (t for t in tables if _has_columns(t, {"symbol", "gics sector"})), None
            )
            if changes is None or constituents is None:
                continue
            rows = _parse_changes(_flatten_columns(changes))
            if not rows:
                continue
            as_of = _revision_date(html) or max(date for date, _, _ in rows)
            snapshot = _ChangesSnapshot(
                revid=revid,
                as_of=as_of,
                changes=rows,
                constituents=_constituent_frame(constituents),
            )
            self.raw_cache.write(
                self.name,
                "changes_revision",
                {"revid": revid, "as_of": as_of.isoformat(), "rows": len(rows)},
            )
            self._changes_snapshot = snapshot
            log.info(
                "S&P 500 change log found in revision %s (%s): %d change rows",
                revid,
                as_of,
                len(rows),
            )
            return snapshot

        log.warning(
            "no revision of the S&P 500 page with a usable change log was found; "
            "historical membership will be SURVIVORSHIP BIASED"
        )
        self._changes_snapshot = _ChangesSnapshot(revid=0, as_of=None, changes=[], constituents=None)
        return self._changes_snapshot

    # --- membership -------------------------------------------------------
    def membership(
        self, start: dt.date, end: dt.date, force: bool = False
    ) -> UniverseMembership:
        current = self.current_constituents(force=force)
        snapshot = self._changes(force=force)

        membership = UniverseMembership(
            name=self.universe_name, survivorship_free=False, source="wikipedia"
        )

        if not snapshot.changes or snapshot.constituents is None or snapshot.as_of is None:
            # Degraded mode: today's list only, dated by `Date added` so at
            # least we never claim a stock was a member before it joined.
            for row in current.itertuples():
                added = row.date_added.date() if pd.notna(row.date_added) else EARLIEST_SUPPORTED
                membership.add(row.ticker, added, None)
            membership.bias_note = (
                "No S&P 500 change log was available. Membership is today's list only: "
                "additions are dated, but companies removed from the index before today "
                "are missing entirely. Results are SURVIVORSHIP BIASED."
            )
            membership.bias_summary = (
                "no S&P 500 change log was available, so membership is today's list only "
                "and companies removed from the index are missing entirely"
            )
            log.warning(membership.bias_note)
            return membership

        anchor = snapshot.as_of
        # Walking the log backwards from the anchor revision's own list.
        open_end: dict[str, dt.date | None] = {t: None for t in snapshot.constituents["ticker"]}
        seeded_start: dict[str, dt.date] = {
            row.ticker: row.date_added.date()
            for row in snapshot.constituents.itertuples()
            if pd.notna(row.date_added)
        }

        for change_date, added, removed in snapshot.changes:
            if change_date > anchor:
                continue
            if added and added in open_end:
                membership.add(added, change_date, open_end.pop(added))
                seeded_start.pop(added, None)
            if removed and removed not in open_end:
                open_end[removed] = change_date - dt.timedelta(days=1)

        for ticker, spell_end in open_end.items():
            # A spell with no "added" row began before the log starts. Its
            # `Date added` is the best evidence; otherwise it predates support.
            membership.add(ticker, seeded_start.get(ticker, EARLIEST_SUPPORTED), spell_end)

        # Reconcile the anchor revision forward to today.
        anchor_members = set(snapshot.constituents["ticker"])
        today_members = set(current["ticker"])
        today_added = {
            row.ticker: row.date_added.date()
            for row in current.itertuples()
            if pd.notna(row.date_added)
        }
        entrants = today_members - anchor_members
        for ticker in entrants:
            membership.add(ticker, today_added.get(ticker, anchor), None)
        departures = anchor_members - today_members
        for ticker in departures:
            # Close the open spell at the anchor: we can verify membership up
            # to that date and no further, and inventing a later exit date
            # would be guessing.
            spans = membership.intervals.get(ticker, [])
            membership.intervals[ticker] = [
                (span_start, anchor if span_end is None else span_end)
                for span_start, span_end in spans
            ]

        membership.survivorship_free = True
        membership.verified_through = anchor
        membership.bias_note = (
            f"Additions and removals reconstructed from the Wikipedia change log, "
            f"verified through {anchor.isoformat()} (revision {snapshot.revid}). "
            f"After that date {len(departures)} ticker(s) known to have left the index "
            f"are closed at the verification date, and {len(entrants)} entrant(s) are "
            f"dated from the current table."
        )
        log.info(
            "universe %s: %d tickers ever a member; %s",
            self.universe_name,
            len(membership),
            membership.status_line(),
        )
        return membership

    # --- classification ---------------------------------------------------
    def sector_map(self, force: bool = False) -> SectorMap:
        """Current GICS sectors. Wikipedia publishes no sector history.

        Historical members that have since left the index are not in the
        current table, so they are classified from the change-log revision
        where they were still listed.
        """
        sector_map = SectorMap(point_in_time=False)
        snapshot = self._changes(force=force)
        if snapshot.constituents is not None:
            for row in snapshot.constituents.itertuples():
                if row.sector and row.sector.lower() != "nan":
                    sector_map.add_current(row.ticker, row.sector, source="wikipedia-archive")
        for row in self.current_constituents(force=force).itertuples():
            if row.sector and row.sector.lower() != "nan":
                sector_map.add_current(row.ticker, row.sector, source="wikipedia")
        return sector_map

    def company_names(self, force: bool = False) -> dict[str, str]:
        names: dict[str, str] = {}
        snapshot = self._changes(force=force)
        if snapshot.constituents is not None:
            names.update(dict(zip(snapshot.constituents["ticker"], snapshot.constituents["company_name"])))
        current = self.current_constituents(force=force)
        names.update(dict(zip(current["ticker"], current["company_name"])))
        return names

    def save_static_snapshot(self, path: str | Path, force: bool = False) -> Path:
        """Write today's membership as the offline fallback list."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = self.current_constituents(force=force)
        out = frame[["ticker", "company_name", "sector", "cik"]].copy()
        out.insert(0, "snapshot_date", dt.date.today().isoformat())
        out.to_csv(path, index=False)
        log.info("wrote %d-ticker static universe snapshot to %s", len(out), path)
        return path


@dataclass(frozen=True)
class _ChangesSnapshot:
    """The archived revision that supplied the change log."""

    revid: int
    as_of: dt.date | None
    changes: list[tuple[dt.date, str | None, str | None]]
    constituents: pd.DataFrame | None


def _constituent_frame(table: pd.DataFrame) -> pd.DataFrame:
    frame = _flatten_columns(table)
    out = pd.DataFrame()
    out["ticker"] = frame[_col(frame, "symbol")].map(normalize_ticker)
    security_col = _col(frame, "security", required=False)
    out["company_name"] = (
        frame[security_col].astype(str).str.strip() if security_col else out["ticker"]
    )
    out["sector"] = frame[_col(frame, "gics sector")].astype(str).str.strip()
    date_col = _col(frame, "date added", required=False)
    out["date_added"] = pd.to_datetime(frame[date_col], errors="coerce") if date_col else pd.NaT
    cik_col = _col(frame, "cik", required=False)
    out["cik"] = frame[cik_col] if cik_col else None
    return out.dropna(subset=["ticker"]).drop_duplicates(subset=["ticker"], keep="first")


def _revision_date(html: str) -> dt.date | None:
    """Read the 'as of <timestamp>' banner Wikipedia renders on old revisions."""
    match = re.search(
        r"revision as of\s*<a[^>]*>([^<]+)</a>", html, re.IGNORECASE
    ) or re.search(r'"dateModified"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})', html)
    if not match:
        return None
    text = match.group(1)
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


# --- table helpers --------------------------------------------------------
def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [
            " ".join(str(part) for part in col if "Unnamed" not in str(part)).strip()
            for col in out.columns
        ]
    out.columns = [str(c).strip() for c in out.columns]
    return out


def _has_columns(frame: pd.DataFrame, needed: set[str]) -> bool:
    flat = _flatten_columns(frame)
    lowered = {str(c).lower() for c in flat.columns}
    return all(any(n in c for c in lowered) for n in needed)


def _is_changes_table(frame: pd.DataFrame) -> bool:
    flat = _flatten_columns(frame)
    lowered = " ".join(str(c).lower() for c in flat.columns)
    return "added" in lowered and "removed" in lowered and "date" in lowered


def _col(frame: pd.DataFrame, needle: str, required: bool = True) -> str | None:
    for column in frame.columns:
        if needle in str(column).strip().lower():
            return column
    if required:
        raise ProviderError(
            f"expected a column containing {needle!r}; available columns: {list(frame.columns)}"
        )
    return None


def _parse_changes(changes: pd.DataFrame) -> list[tuple[dt.date, str | None, str | None]]:
    """Normalize the change log to (date, added_ticker, removed_ticker), newest first."""
    if changes is None or changes.empty:
        return []
    date_col = _col(changes, "date", required=False)
    added_col = next(
        (c for c in changes.columns if "added" in str(c).lower() and "ticker" in str(c).lower()),
        None,
    )
    removed_col = next(
        (c for c in changes.columns if "removed" in str(c).lower() and "ticker" in str(c).lower()),
        None,
    )
    if date_col is None or (added_col is None and removed_col is None):
        return []

    rows: list[tuple[dt.date, str | None, str | None]] = []
    for _, row in changes.iterrows():
        parsed_date = pd.to_datetime(row[date_col], errors="coerce")
        if pd.isna(parsed_date):
            continue
        added = normalize_ticker(row[added_col]) if added_col else None
        removed = normalize_ticker(row[removed_col]) if removed_col else None
        if added is None and removed is None:
            continue
        rows.append((parsed_date.date(), added, removed))

    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


class SyntheticUniverse:
    """Universe backed by the synthetic provider, for offline runs and tests."""

    name = "synthetic"

    def __init__(self, provider, universe_name: str = "sp500-synthetic") -> None:
        self.provider = provider
        self.universe_name = universe_name

    def membership(self, start: dt.date, end: dt.date) -> UniverseMembership:
        membership = UniverseMembership(
            name=self.universe_name, survivorship_free=True, source="synthetic"
        )
        # Reproduce the provider's slow churn as explicit dated intervals.
        step = dt.timedelta(days=91)
        seen: dict[str, dt.date] = {}
        cursor = start
        while cursor <= end:
            for ticker in self.provider.constituents(cursor):
                seen.setdefault(ticker, cursor)
            cursor += step
        for ticker, first in seen.items():
            membership.add(ticker, first, None)
        return membership

    def sector_map(self) -> SectorMap:
        sector_map = SectorMap(point_in_time=True)
        for ticker in self.provider.tickers:
            sector = self.provider.sector_of(ticker)
            if sector:
                sector_map.add(
                    SectorAssignment(ticker, sector, EARLIEST_SUPPORTED, None, "synthetic")
                )
        return sector_map

    def company_names(self) -> dict[str, str]:
        return {t: self.provider.company_name(t) for t in self.provider.tickers}
