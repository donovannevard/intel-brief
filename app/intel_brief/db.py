"""SQLite schema + connection helpers for the news archive. WAL mode,
single-writer discipline (pipeline stages run sequentially by default).
Schema anticipates the full roadmap (spec's own principle) -- later-phase
tables exist from day one even before they have UI or agent logic writing
to them.

**Retention policy: none, deliberately.** This database IS the archive --
building a personal, permanent record of what was published and how it was
analysed is the point of the project, not a side effect. There was an
`ARTICLE_RETENTION_DAYS` setting that was read from the environment but
never used by any code (so the archive was permanent by accident); it was
removed rather than implemented. If pruning is ever wanted it should be a
deliberate, separate decision -- and note that `intel.db` is the file worth
backing up, while `markets.db` is a regenerable cache.

Provenance: every row records where it came from, so the archive can be
audited and cited later. Articles keep the feed URL they were discovered
from (snapshotted at discovery -- `feeds.url` can be edited afterwards) and
the tool/version that extracted their text; analysis rows already keep the
model and prompt version. Market prices carry the same discipline in
`intel_brief/markets/store.py`.
"""

import logging
import sqlite3
from datetime import timezone

from intel_brief.config import settings

log = logging.getLogger("intel_brief.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    outlet TEXT,
    outlet_type TEXT,
    category_hint TEXT,
    country TEXT,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    etag TEXT,
    last_modified TEXT,
    last_polled_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER REFERENCES feeds(id),
    guid TEXT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    author TEXT,
    outlet TEXT,
    raw_html_path TEXT,
    full_text TEXT,
    word_count INTEGER,
    status TEXT NOT NULL DEFAULT 'discovered',
    content_hash TEXT,
    fail_reason TEXT,
    -- Provenance (see module docstring). source_feed_url is snapshotted at
    -- discovery rather than joined from feeds(url) at read time, so editing
    -- or repointing a feed doesn't silently rewrite the recorded origin of
    -- articles already collected.
    source_feed_url TEXT,
    fetched_at TEXT,
    extractor TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);

CREATE TABLE IF NOT EXISTS article_analysis (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    summary TEXT,
    interpretation TEXT,
    narrative_critique TEXT,
    scope TEXT,
    categories TEXT,
    sentiment_framing TEXT,
    importance_score INTEGER,
    analyzed_at TEXT,
    model_used TEXT,
    prompt_version TEXT
);

CREATE TABLE IF NOT EXISTS article_embeddings (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    embedding BLOB,
    model TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    type TEXT,
    aliases TEXT,
    first_seen_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS article_entities (
    article_id INTEGER REFERENCES articles(id),
    entity_id INTEGER REFERENCES entities(id),
    role TEXT,
    PRIMARY KEY (article_id, entity_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER REFERENCES entities(id),
    article_id INTEGER REFERENCES articles(id),
    claim_text TEXT NOT NULL,
    claim_type TEXT,
    claim_date TEXT,
    resolves_by TEXT,
    embedding BLOB,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_entity_id ON claims(entity_id);

CREATE TABLE IF NOT EXISTS contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_a_id INTEGER REFERENCES claims(id),
    claim_b_id INTEGER REFERENCES claims(id),
    explanation TEXT,
    confidence REAL,
    detected_at TEXT,
    dismissed BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER REFERENCES claims(id),
    predictor_entity_id INTEGER REFERENCES entities(id),
    prediction_text TEXT,
    due_date TEXT,
    outcome TEXT,
    outcome_note TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS story_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    created_at TEXT,
    peak_date TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS article_clusters (
    article_id INTEGER REFERENCES articles(id),
    cluster_id INTEGER REFERENCES story_clusters(id),
    similarity REAL,
    PRIMARY KEY (article_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_article_clusters_cluster_id ON article_clusters(cluster_id);

CREATE TABLE IF NOT EXISTS coverage_stats (
    date TEXT,
    cluster_id INTEGER REFERENCES story_clusters(id),
    article_count INTEGER,
    outlet_count INTEGER,
    PRIMARY KEY (date, cluster_id)
);

CREATE TABLE IF NOT EXISTS daily_briefs (
    date TEXT NOT NULL,
    period TEXT NOT NULL DEFAULT 'morning',
    generated_at TEXT,
    top_stories TEXT,
    disproportionate TEXT,
    underreported TEXT,
    learn_today TEXT,
    on_this_day TEXT,
    follow_ups TEXT,
    PRIMARY KEY (date, period)
);

CREATE TABLE IF NOT EXISTS historical_parallels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER REFERENCES articles(id),
    parallel_description TEXT,
    era TEXT,
    similarity_basis TEXT,
    confidence REAL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER REFERENCES story_clusters(id),
    created_at TEXT,
    revisit_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    resolution_note TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats TEXT,
    ok BOOLEAN
);

-- Derived market analysis. Lives here rather than in markets.db because
-- market_news_links references articles(id) and SQLite has no cross-database
-- foreign keys -- and because generated interpretation belongs with the
-- archive, not in a regenerable price cache.
CREATE TABLE IF NOT EXISTS market_briefs (
    date TEXT NOT NULL,
    tab TEXT NOT NULL,
    generated_at TEXT,
    movers TEXT,          -- JSON: the computed digest the narrative was grounded in
    narrative TEXT,       -- JSON: {summary, whats_changing[], watch[], regime_note}
    unverified TEXT,      -- JSON: figures the model stated that the digest doesn't support
    model TEXT,
    prompt_version TEXT,
    PRIMARY KEY (date, tab)
);

CREATE TABLE IF NOT EXISTS market_news_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    article_id INTEGER REFERENCES articles(id),
    cluster_id INTEGER REFERENCES story_clusters(id),
    rationale TEXT,
    confidence INTEGER,   -- 1-5, the model's own confidence in the association
    direction TEXT,
    similarity REAL,      -- cosine score that shortlisted the pair
    method TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_news_links_date ON market_news_links(date);

-- Human commentary attached to an article -- currently Hacker News threads.
-- Kept separate from article_analysis because it is not analysis: it is what
-- other readers said, stored verbatim so it can be set against the model's
-- reading rather than blended into it.
CREATE TABLE IF NOT EXISTS article_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL REFERENCES articles(id),
    source       TEXT NOT NULL,          -- 'hackernews'
    external_id  TEXT NOT NULL,          -- the comment id at the source
    thread_id    TEXT,                   -- the discussion it belongs to
    thread_url   TEXT,
    thread_points INTEGER,
    thread_comment_count INTEGER,
    author       TEXT,
    text         TEXT NOT NULL,
    posted_at    TEXT,
    permalink    TEXT,
    rank         INTEGER,                -- position in the source's own ordering
    fetched_at   TEXT NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_article_comments_article ON article_comments(article_id);

-- Publisher logos, fetched once and cached (see outlets.py). Kept in the
-- main schema rather than owned by that module so init_db() alone produces a
-- complete database -- a split schema meant a fresh DB was missing this and
-- every news page 500'd on it.
CREATE TABLE IF NOT EXISTS outlet_logos (
    outlet       TEXT PRIMARY KEY,
    image        BLOB,
    content_type TEXT,
    source_url   TEXT,
    fetched_at   TEXT NOT NULL,
    error        TEXT
);

-- Single-row control table (id is CHECKed to 1) holding the manual pause
-- switch. In the DB rather than a process global so the pause survives a
-- service restart -- see control.py for why that matters.
CREATE TABLE IF NOT EXISTS pipeline_control (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    paused BOOLEAN NOT NULL DEFAULT 0,
    paused_at TEXT,
    resumed_at TEXT
);
INSERT OR IGNORE INTO pipeline_control (id, paused) VALUES (1, 0);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, full_text, outlet, content='articles', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS articles_fts_insert AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, full_text, outlet)
    VALUES (new.id, new.title, new.full_text, new.outlet);
END;

-- External-content FTS5 tables cannot be UPDATEd/DELETEd directly like a normal
-- table -- that corrupts the index (hit this for real: "database disk image is
-- malformed" on the very first Extract run). The documented pattern is a special
-- 'delete' command with the OLD row's values, then a fresh insert for updates.
CREATE TRIGGER IF NOT EXISTS articles_fts_update AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, full_text, outlet)
    VALUES ('delete', old.id, old.title, old.full_text, old.outlet);
    INSERT INTO articles_fts(rowid, title, full_text, outlet)
    VALUES (new.id, new.title, new.full_text, new.outlet);
END;

CREATE TRIGGER IF NOT EXISTS articles_fts_delete AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, full_text, outlet)
    VALUES ('delete', old.id, old.title, old.full_text, old.outlet);
END;
"""


def get_connection() -> sqlite3.Connection:
    # busy_timeout matters once more than one writer can be active at a time
    # (e.g. the scheduler's pipeline chain and a manual CLI run overlapping) --
    # without it, a second writer gets an immediate "database is locked"
    # instead of waiting for the first to finish its transaction.
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added to tables that already exist in deployed databases.
# `CREATE TABLE IF NOT EXISTS` silently does nothing when the table is
# already there, so new columns need an explicit ALTER -- without this, a
# schema addition works on a fresh DB and breaks on the live one.
# (table, column, column definition)
MIGRATIONS: list[tuple[str, str, str]] = [
    ("articles", "source_feed_url", "TEXT"),
    # When we last looked for a discussion of this article. Without it, an
    # article with no thread is searched again on every run, forever.
    ("articles", "comments_checked_at", "TEXT"),
    # published_at holds whatever the feed said, which is RFC-822 ("Wed, 8 Jul
    # 2026 15:56:00 GMT"). Sorting that as text sorts by weekday name, so
    # "Wed" outranked every other day and a three-week-old article sat at the
    # top of every tab. This is the same instant in ISO 8601, which sorts.
    ("articles", "published_ts", "TEXT"),
    ("articles", "fetched_at", "TEXT"),
    ("articles", "extractor", "TEXT"),
]


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Idempotent ADD COLUMN migrations. Returns what it actually changed."""
    applied = []
    for table, column, ddl in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table isn't there yet; SCHEMA will have created it above
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")
    return applied


def backfill_published_ts(conn) -> int:
    """Parse existing RFC-822 published_at values into sortable ISO.

    Only touches rows where published_ts is still NULL, so it costs one scan
    after the migration and nothing thereafter.
    """
    from email.utils import parsedate_to_datetime

    rows = conn.execute(
        "SELECT id, published_at FROM articles "
        "WHERE published_ts IS NULL AND published_at IS NOT NULL AND published_at != ''"
    ).fetchall()
    filled = 0
    for row in rows:
        try:
            parsed = parsedate_to_datetime(row["published_at"])
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        conn.execute(
            "UPDATE articles SET published_ts = ? WHERE id = ?",
            (parsed.astimezone(timezone.utc).isoformat(), row["id"]),
        )
        filled += 1
    if filled:
        conn.commit()
    return filled


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        applied = apply_migrations(conn)
        conn.commit()
        if applied:
            log.info("db migrations applied: %s", ", ".join(applied))
        filled = backfill_published_ts(conn)
        if filled:
            log.info("parsed %d publication dates into sortable form", filled)
    finally:
        conn.close()
