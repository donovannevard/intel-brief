"""markets.db -- the price archive, deliberately kept as a separate SQLite
file from intel.db.

Why two files rather than one:

* **Different exposure.** intel.db holds a detailed personal record -- what
  you read, which entities and claims were extracted, what the model said
  about them. markets.db holds public market prices. Other projects
  (sovereign-stack) need prices; giving them the news archive to get at a
  BTC quote would be the wrong trade. This file can be handed out
  read-only; that one can't.
* **Different value.** Prices are regenerable -- delete this file and the
  next ingest rebuilds it from the providers. The news archive is
  irreplaceable, because RSS feeds don't retain history. So: back up
  intel.db, and treat markets.db as a cache with a long memory.

Derived analysis (the LLM market briefs, and news<->market correlations)
does NOT live here -- it goes in intel.db, because it references articles
and SQLite has no cross-database foreign keys.

Provenance is the other reason this schema differs from the source
project's. Every price row records which provider it came from and when it
was fetched, `data_sources` says what each provider id actually is, and
`instrument_sources` keeps the history of which provider backed which
instrument over time. If a provider is discontinued and a series moves
elsewhere, the old rows keep saying where they really came from, and the
switch is visible rather than silent.

Schema changes to an existing deployment need the ALTER pattern from
intel_brief/db.py -- `CREATE TABLE IF NOT EXISTS` will not add a column to
a table that already exists.
"""

import sqlite3
from datetime import datetime, timezone

from intel_brief.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_prices (
    source     TEXT NOT NULL,   -- provenance: which provider this value came from
    series_id  TEXT NOT NULL,   -- provider-native identifier ('XMR-USD', 'DGS10')
    date       TEXT NOT NULL,   -- YYYY-MM-DD
    close      REAL NOT NULL,
    fetched_at TEXT NOT NULL,   -- when we retrieved it
    PRIMARY KEY (source, series_id, date)
);
CREATE INDEX IF NOT EXISTS idx_market_prices_series ON market_prices(series_id, date);

CREATE TABLE IF NOT EXISTS market_fetch_meta (
    source       TEXT NOT NULL,
    series_id    TEXT NOT NULL,
    last_fetched TEXT NOT NULL,
    last_status  TEXT,          -- 'ok' | 'empty' | 'error'
    last_error   TEXT,
    last_rows    INTEGER,
    PRIMARY KEY (source, series_id)
);

-- What each `source` value actually means. Rewritten from SOURCE_META on
-- every ingest so the file is self-describing to anything else reading it.
CREATE TABLE IF NOT EXISTS data_sources (
    id            TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    notes         TEXT,
    first_seen_at TEXT NOT NULL,
    last_used_at  TEXT NOT NULL
);

-- Which provider backed which instrument, and when. A row is closed
-- (active_to set) and a new one opened when the registry's binding changes,
-- so "we switched XMR from Yahoo to Kraken on this date" is a fact in the
-- database rather than something to reconstruct from git history.
CREATE TABLE IF NOT EXISTS instrument_sources (
    instrument_id TEXT NOT NULL,
    source        TEXT NOT NULL,
    series_id     TEXT NOT NULL,
    active_from   TEXT NOT NULL,
    active_to     TEXT,
    PRIMARY KEY (instrument_id, active_from)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.markets_db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_markets_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Provenance bookkeeping
# ---------------------------------------------------------------------------

def record_data_sources(conn: sqlite3.Connection, source_meta: dict[str, dict]) -> None:
    """Mirror the in-code source registry into the database."""
    now = _now()
    for source_id, meta in source_meta.items():
        conn.execute(
            """INSERT INTO data_sources (id, provider, endpoint, notes, first_seen_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   provider     = excluded.provider,
                   endpoint     = excluded.endpoint,
                   notes        = excluded.notes,
                   last_used_at = excluded.last_used_at""",
            (source_id, meta["provider"], meta["endpoint"], meta.get("notes"), now, now),
        )
    conn.commit()


def record_instrument_binding(
    conn: sqlite3.Connection, instrument_id: str, source: str, series_id: str
) -> bool:
    """Record which provider currently backs an instrument. Returns True if
    this was a *change* (i.e. a provider switch just got logged)."""
    current = conn.execute(
        """SELECT source, series_id FROM instrument_sources
           WHERE instrument_id = ? AND active_to IS NULL
           ORDER BY active_from DESC LIMIT 1""",
        (instrument_id,),
    ).fetchone()

    if current and current["source"] == source and current["series_id"] == series_id:
        return False

    now = _now()
    if current:
        conn.execute(
            "UPDATE instrument_sources SET active_to = ? WHERE instrument_id = ? AND active_to IS NULL",
            (now, instrument_id),
        )
    conn.execute(
        """INSERT OR REPLACE INTO instrument_sources
           (instrument_id, source, series_id, active_from, active_to)
           VALUES (?, ?, ?, ?, NULL)""",
        (instrument_id, source, series_id, now),
    )
    conn.commit()
    return current is not None  # first-ever binding isn't a "switch"


def source_history(conn: sqlite3.Connection, instrument_id: str) -> list[dict]:
    return [
        dict(r) for r in conn.execute(
            """SELECT source, series_id, active_from, active_to FROM instrument_sources
               WHERE instrument_id = ? ORDER BY active_from""",
            (instrument_id,),
        )
    ]


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def write_prices(conn: sqlite3.Connection, source: str, series_id: str, rows: list[dict]) -> int:
    """Upsert [{ts, close}] rows, stamping each with its provenance."""
    if not rows:
        return 0
    fetched_at = _now()
    conn.executemany(
        """INSERT INTO market_prices (source, series_id, date, close, fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source, series_id, date) DO UPDATE SET
               close      = excluded.close,
               fetched_at = excluded.fetched_at""",
        [(source, series_id, r["ts"], r["close"], fetched_at) for r in rows],
    )
    conn.commit()
    return len(rows)


def read_prices(
    conn: sqlite3.Connection, source: str, series_id: str, cutoff: str = ""
) -> list[dict]:
    if cutoff:
        cur = conn.execute(
            """SELECT date, close FROM market_prices
               WHERE source = ? AND series_id = ? AND date >= ? ORDER BY date""",
            (source, series_id, cutoff),
        )
    else:
        cur = conn.execute(
            """SELECT date, close FROM market_prices
               WHERE source = ? AND series_id = ? ORDER BY date""",
            (source, series_id),
        )
    return [{"ts": r["date"], "close": r["close"]} for r in cur]


def latest_price(conn: sqlite3.Connection, source: str, series_id: str) -> dict | None:
    row = conn.execute(
        """SELECT date, close, fetched_at FROM market_prices
           WHERE source = ? AND series_id = ? ORDER BY date DESC LIMIT 1""",
        (source, series_id),
    ).fetchone()
    return dict(row) if row else None


def latest_date(conn: sqlite3.Connection, source: str, series_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM market_prices WHERE source = ? AND series_id = ?",
        (source, series_id),
    ).fetchone()
    return row["d"] if row and row["d"] else None


# ---------------------------------------------------------------------------
# Fetch bookkeeping
# ---------------------------------------------------------------------------

def is_stale(conn: sqlite3.Connection, source: str, series_id: str, ttl_hours: float) -> bool:
    row = conn.execute(
        "SELECT last_fetched FROM market_fetch_meta WHERE source = ? AND series_id = ?",
        (source, series_id),
    ).fetchone()
    if not row:
        return True
    last = datetime.fromisoformat(row["last_fetched"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() > ttl_hours * 3600


def mark_fetched(
    conn: sqlite3.Connection, source: str, series_id: str,
    *, status: str = "ok", rows: int = 0, error: str | None = None,
) -> None:
    """Records the attempt, not just the success -- a provider that starts
    returning nothing should be visible on the Status page rather than
    looking identical to one that simply hasn't changed.

    Note the deliberate asymmetry: `last_fetched` advances on failures too,
    so a dead provider is retried on the normal TTL instead of being hammered
    every ingest run.
    """
    conn.execute(
        """INSERT INTO market_fetch_meta (source, series_id, last_fetched, last_status, last_error, last_rows)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(source, series_id) DO UPDATE SET
               last_fetched = excluded.last_fetched,
               last_status  = excluded.last_status,
               last_error   = excluded.last_error,
               last_rows    = excluded.last_rows""",
        (source, series_id, _now(), status, error, rows),
    )
    conn.commit()


# A fetch can succeed forever while the data behind it stops moving --
# exactly what happens when a provider discontinues a series instead of
# withdrawing the endpoint. `days_behind` measures the data, not the
# request, so a dead series is visible rather than looking healthy.
# Thresholds are generous because release cadence varies enormously here:
# daily prices vs quarterly wealth accounts vs annual debt-to-GDP.
STALE_DAYS_WARN = 120
STALE_DAYS_DEAD = 400


def fetch_health(conn: sqlite3.Connection) -> list[dict]:
    """Per-series ingest health, worst first -- for the Status page."""
    rows = [
        dict(r) for r in conn.execute(
            """SELECT m.source, m.series_id, m.last_fetched, m.last_status, m.last_error, m.last_rows,
                      (SELECT COUNT(*) FROM market_prices p
                        WHERE p.source = m.source AND p.series_id = m.series_id) AS stored_rows,
                      (SELECT MAX(date) FROM market_prices p
                        WHERE p.source = m.source AND p.series_id = m.series_id) AS latest_date,
                      CAST(julianday('now') - julianday(
                          (SELECT MAX(date) FROM market_prices p
                            WHERE p.source = m.source AND p.series_id = m.series_id)) AS INTEGER
                      ) AS days_behind
                 FROM market_fetch_meta m"""
        )
    ]
    for row in rows:
        days = row["days_behind"]
        if row["last_status"] == "error":
            row["health"] = "error"
        elif row["last_status"] == "empty" or days is None:
            row["health"] = "no data"
        elif days >= STALE_DAYS_DEAD:
            row["health"] = "discontinued?"
        elif days >= STALE_DAYS_WARN:
            row["health"] = "stale"
        else:
            row["health"] = "ok"

    order = {"error": 0, "no data": 1, "discontinued?": 2, "stale": 3, "ok": 4}
    rows.sort(key=lambda r: (order[r["health"]], -(r["days_behind"] or 0), r["series_id"]))
    return rows
