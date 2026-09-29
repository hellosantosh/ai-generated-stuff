"""SQLite persistence layer (REQUIREMENTS 29).

The schema separates three dates for every fundamental fact:

    period_end_date      the fiscal period the number describes
    filing_date          the day the filing reached the SEC
    data_available_date  the first day a backtest may legally use it

Only ``data_available_date`` is consulted by the point-in-time store. Keeping
all three lets us audit why a fact was or was not visible on a decision date.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .errors import DataError
from .logging_config import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sectors (
    name        TEXT PRIMARY KEY,
    gics_code   TEXT
);

CREATE TABLE IF NOT EXISTS stocks (
    ticker       TEXT PRIMARY KEY,
    company_name TEXT,
    cik          TEXT,
    sector       TEXT,
    industry     TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    delisted_on  TEXT,
    updated_at   TEXT NOT NULL
);

-- Date-aware sector classification. `effective_date` is the first date the
-- classification applies to; `end_date` NULL means "still current".
CREATE TABLE IF NOT EXISTS sector_history (
    ticker         TEXT NOT NULL,
    sector         TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    end_date       TEXT,
    source         TEXT NOT NULL,
    PRIMARY KEY (ticker, effective_date)
);

CREATE TABLE IF NOT EXISTS historical_prices (
    ticker      TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL    NOT NULL,
    adj_close   REAL,
    volume      REAL,
    dividend    REAL    NOT NULL DEFAULT 0.0,
    split_coef  REAL    NOT NULL DEFAULT 1.0,
    provider    TEXT    NOT NULL,
    retrieved_at TEXT   NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON historical_prices(date);

CREATE TABLE IF NOT EXISTS corporate_actions (
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,
    action     TEXT NOT NULL,          -- dividend | split
    value      REAL NOT NULL,
    provider   TEXT NOT NULL,
    PRIMARY KEY (ticker, date, action)
);

-- One row per (ticker, metric, fiscal period) XBRL fact.
CREATE TABLE IF NOT EXISTS financial_statements (
    ticker              TEXT NOT NULL,
    metric              TEXT NOT NULL,
    period_end_date     TEXT NOT NULL,
    period_start_date   TEXT,
    fiscal_year         INTEGER,
    fiscal_period       TEXT,
    form                TEXT,
    filing_date         TEXT NOT NULL,
    data_available_date TEXT NOT NULL,
    value               REAL,
    unit                TEXT,
    provider            TEXT NOT NULL,
    retrieved_at        TEXT NOT NULL,
    PRIMARY KEY (ticker, metric, period_end_date, filing_date, form)
);
CREATE INDEX IF NOT EXISTS idx_fs_avail ON financial_statements(ticker, data_available_date);

-- Derived per-period fundamentals (revenue, EPS, FCF, ...) assembled from the
-- statement facts. Same point-in-time keys.
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker              TEXT NOT NULL,
    period_end_date     TEXT NOT NULL,
    fiscal_period       TEXT NOT NULL,
    filing_date         TEXT NOT NULL,
    data_available_date TEXT NOT NULL,
    payload             TEXT NOT NULL,   -- JSON metric -> value
    provider            TEXT NOT NULL,
    retrieved_at        TEXT NOT NULL,
    PRIMARY KEY (ticker, period_end_date, fiscal_period, filing_date)
);
CREATE INDEX IF NOT EXISTS idx_fund_avail ON fundamentals(ticker, data_available_date);

CREATE TABLE IF NOT EXISTS universe_membership (
    universe   TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT,                     -- NULL = still a member
    source     TEXT NOT NULL,
    PRIMARY KEY (universe, ticker, start_date)
);
CREATE INDEX IF NOT EXISTS idx_universe_dates
    ON universe_membership(universe, start_date, end_date);

CREATE TABLE IF NOT EXISTS factor_scores (
    run_id        TEXT NOT NULL,
    decision_date TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    sector        TEXT,
    category      TEXT NOT NULL,        -- growth | momentum | ... | raw
    factor        TEXT NOT NULL,
    raw_value     REAL,
    normalized    REAL,
    PRIMARY KEY (run_id, decision_date, ticker, category, factor)
);

CREATE TABLE IF NOT EXISTS rankings (
    run_id        TEXT NOT NULL,
    decision_date TEXT NOT NULL,
    sleeve        TEXT NOT NULL,        -- sector_leaders | high_growth
    ticker        TEXT NOT NULL,
    sector        TEXT,
    company_name  TEXT,
    total_score   REAL,
    growth        REAL,
    momentum      REAL,
    quality       REAL,
    valuation     REAL,
    risk          REAL,
    relative_strength REAL,
    rank          INTEGER,
    previous_rank INTEGER,
    rank_change   INTEGER,
    selected      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, decision_date, sleeve, ticker)
);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    run_id   TEXT NOT NULL,
    variant  TEXT NOT NULL,
    date     TEXT NOT NULL,
    ticker   TEXT NOT NULL,
    sleeve   TEXT NOT NULL,
    shares   REAL NOT NULL,
    price    REAL NOT NULL,
    value    REAL NOT NULL,
    cost_basis REAL NOT NULL,
    PRIMARY KEY (run_id, variant, date, ticker, sleeve)
);

CREATE TABLE IF NOT EXISTS portfolio_transactions (
    run_id        TEXT NOT NULL,
    variant       TEXT NOT NULL,
    sequence      INTEGER NOT NULL,
    decision_date TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    sleeve        TEXT NOT NULL,
    action        TEXT NOT NULL,        -- BUY | SELL | DIVIDEND
    shares        REAL NOT NULL,
    price         REAL NOT NULL,
    gross_amount  REAL NOT NULL,
    commission    REAL NOT NULL,
    slippage      REAL NOT NULL,
    net_amount    REAL NOT NULL,
    reason        TEXT,
    PRIMARY KEY (run_id, variant, sequence)
);

CREATE TABLE IF NOT EXISTS portfolio_values (
    run_id        TEXT NOT NULL,
    variant       TEXT NOT NULL,
    date          TEXT NOT NULL,
    contribution  REAL NOT NULL,
    cash          REAL NOT NULL,
    holdings_value REAL NOT NULL,
    total_value   REAL NOT NULL,
    cumulative_contributions REAL NOT NULL,
    twr_index     REAL,
    PRIMARY KEY (run_id, variant, date)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id           TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    software_version TEXT NOT NULL,
    python_version   TEXT NOT NULL,
    git_commit       TEXT,
    start_date       TEXT NOT NULL,
    end_date         TEXT NOT NULL,
    num_weeks        INTEGER NOT NULL,
    config_json      TEXT NOT NULL,
    provenance_json  TEXT NOT NULL,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    run_id  TEXT NOT NULL,
    variant TEXT NOT NULL,
    metric  TEXT NOT NULL,
    value   REAL,
    text_value TEXT,
    PRIMARY KEY (run_id, variant, metric)
);

CREATE TABLE IF NOT EXISTS data_quality (
    run_id     TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    scope      TEXT NOT NULL,
    check_name TEXT NOT NULL,
    severity   TEXT NOT NULL,           -- info | warning | critical
    subject    TEXT,
    detail     TEXT,
    PRIMARY KEY (run_id, scope, check_name, subject)
);
"""


def _iso(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


class Database:
    """Thin wrapper over sqlite3 with upsert helpers and a transaction scope.

    sqlite3 is used directly rather than through the SQLAlchemy ORM: the access
    pattern here is bulk insert plus a handful of range scans, and raw SQL keeps
    the point-in-time predicates visible at the call site.
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    # --- lifecycle -------------------------------------------------------
    def create_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        log.debug("schema ready at %s (version %s)", self.path, SCHEMA_VERSION)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # --- generic helpers -------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, tuple(_iso(p) for p in params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.execute(sql, params).fetchall())

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def upsert_many(
        self, table: str, rows: Iterable[Mapping[str, Any]], replace: bool = True
    ) -> int:
        rows = list(rows)
        if not rows:
            return 0
        columns = list(rows[0].keys())
        for row in rows:
            if list(row.keys()) != columns:
                raise DataError(
                    f"upsert_many({table}): every row must share the same columns; "
                    f"expected {columns}, got {list(row.keys())}"
                )
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        placeholders = ", ".join("?" for _ in columns)
        sql = f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        payload = [tuple(_iso(row[c]) for c in columns) for row in rows]
        with self.transaction() as conn:
            conn.executemany(sql, payload)
        return len(payload)

    def table_count(self, table: str) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {table}") or 0)

    # --- domain helpers --------------------------------------------------
    def record_run(
        self,
        run_id: str,
        strategy_version: str,
        software_version: str,
        python_version: str,
        start_date: dt.date,
        end_date: dt.date,
        num_weeks: int,
        config: Mapping[str, Any],
        provenance: Mapping[str, Any],
        git_commit: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.upsert_many(
            "backtest_runs",
            [
                {
                    "run_id": run_id,
                    "created_at": dt.datetime.now(dt.timezone.utc),
                    "strategy_version": strategy_version,
                    "software_version": software_version,
                    "python_version": python_version,
                    "git_commit": git_commit,
                    "start_date": start_date,
                    "end_date": end_date,
                    "num_weeks": num_weeks,
                    "config_json": json.dumps(config, indent=2, sort_keys=True, default=str),
                    "provenance_json": json.dumps(provenance, indent=2, sort_keys=True, default=str),
                    "notes": notes,
                }
            ],
        )

    def load_run_config(self, run_id: str) -> dict[str, Any]:
        raw = self.scalar("SELECT config_json FROM backtest_runs WHERE run_id = ?", (run_id,))
        if raw is None:
            raise DataError(f"no backtest run with id {run_id!r}")
        return json.loads(raw)

    def latest_run_id(self) -> str | None:
        return self.scalar("SELECT run_id FROM backtest_runs ORDER BY created_at DESC LIMIT 1")


def open_database(path: str | Path, create: bool = True) -> Database:
    db = Database(path)
    if create:
        db.create_schema()
    return db
