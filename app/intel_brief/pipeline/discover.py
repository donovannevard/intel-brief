"""Stage 1 -- Discover. Poll enabled feeds with conditional GET, insert new
articles by GUID (fallback: URL). A single feed failing never blocks the
others -- each feed's fetch is wrapped individually."""

import logging
from datetime import datetime, timezone

import feedparser

from intel_brief.db import get_connection
from intel_brief.pipeline.runs import track_run

log = logging.getLogger("intel_brief.discover")

# Per-feed cap per poll. Not all RSS feeds behave like "today's headlines" --
# arXiv's feed is a continuous firehose of every recent CS submission (938
# entries on first poll, observed directly), which would otherwise dominate
# Extract/Analyze with content the spec's "science" tab only wants one
# curated pick from. Applied per-poll, not a lifetime cap -- a feed that
# publishes 40 stories today can still all come in across several 30-min polls.
MAX_NEW_PER_FEED_PER_POLL = 25


def _guid_or_url(entry):
    return entry.get("id") or entry.get("guid") or entry.get("link")


def discover_feed(conn, feed_row) -> dict:
    result = {"new": 0, "error": None}
    kwargs = {}
    if feed_row["etag"]:
        kwargs["etag"] = feed_row["etag"]
    if feed_row["last_modified"]:
        kwargs["modified"] = feed_row["last_modified"]

    try:
        parsed = feedparser.parse(feed_row["url"], **kwargs)

        if getattr(parsed, "status", 200) == 304:
            conn.execute(
                "UPDATE feeds SET last_polled_at=?, consecutive_failures=0 WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), feed_row["id"]),
            )
            return result

        if parsed.bozo and not parsed.entries:
            raise RuntimeError(str(parsed.bozo_exception))

        for entry in parsed.entries:
            if result["new"] >= MAX_NEW_PER_FEED_PER_POLL:
                break
            guid = _guid_or_url(entry)
            url = entry.get("link", "")
            if not guid or not url:
                continue
            existing = conn.execute(
                "SELECT id FROM articles WHERE guid = ? OR url = ?", (guid, url)
            ).fetchone()
            if existing:
                continue

            published = entry.get("published") or entry.get("updated") or ""
            # feedparser hands back a parsed struct; keep an ISO copy so the
            # dashboard can order by date without re-parsing every row.
            parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            published_ts = (
                datetime(*parsed_struct[:6], tzinfo=timezone.utc).isoformat()
                if parsed_struct else None
            )
            conn.execute(
                """INSERT INTO articles (feed_id, guid, url, title, published_at, published_ts,
                   discovered_at, author, outlet, status, source_feed_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)""",
                (
                    feed_row["id"], guid, url, entry.get("title", "(no title)"),
                    published, published_ts, datetime.now(timezone.utc).isoformat(),
                    entry.get("author"), feed_row["outlet"],
                    # Snapshot, not a join: this records the feed this article
                    # actually came out of, even if that feed is later edited,
                    # repointed, or removed.
                    feed_row["url"],
                ),
            )
            result["new"] += 1

        conn.execute(
            """UPDATE feeds SET etag=?, last_modified=?, last_polled_at=?, consecutive_failures=0
               WHERE id=?""",
            (
                getattr(parsed, "etag", None), getattr(parsed, "modified", None),
                datetime.now(timezone.utc).isoformat(), feed_row["id"],
            ),
        )
    except Exception as e:
        result["error"] = str(e)
        conn.execute(
            "UPDATE feeds SET last_polled_at=?, consecutive_failures=consecutive_failures+1 WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), feed_row["id"]),
        )

    return result


def discover_hackernews(conn) -> dict:
    """Front-page Hacker News stories, as articles.

    Stored like any other article -- the linked page is what gets extracted and
    analysed, not the HN post -- so they flow through the normal pipeline and
    get the same treatment as anything else. The discussion is attached
    separately by pipeline/comments.py after analysis.
    """
    from intel_brief import hackernews

    result = {"new": 0, "error": None}
    try:
        stories = hackernews.top_stories(limit=25)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    for story in stories:
        existing = conn.execute(
            "SELECT id FROM articles WHERE url = ? OR guid = ?",
            (story["url"], f"hn:{story['hn_id']}"),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """INSERT INTO articles (feed_id, guid, url, title, published_at, published_ts,
               discovered_at, author, outlet, status, source_feed_url)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, NULL, 'Hacker News', 'discovered', ?)""",
            (
                f"hn:{story['hn_id']}", story["url"], story["title"],
                story["posted_at"], story["posted_at"],
                datetime.now(timezone.utc).isoformat(), story["permalink"],
            ),
        )
        result["new"] += 1
    conn.commit()
    return result


def discover() -> dict:
    with track_run("discover") as stats:
        conn = get_connection()
        try:
            feeds = conn.execute("SELECT * FROM feeds WHERE enabled = 1").fetchall()
            stats["feeds_polled"] = len(feeds)
            stats["new_articles"] = 0
            stats["errors"] = {}

            for feed_row in feeds:
                result = discover_feed(conn, feed_row)
                conn.commit()
                stats["new_articles"] += result["new"]
                if result["error"]:
                    stats["errors"][feed_row["name"]] = result["error"]

            hn = discover_hackernews(conn)
            stats["hackernews_new"] = hn["new"]
            stats["new_articles"] += hn["new"]
            if hn["error"]:
                stats["errors"]["Hacker News"] = hn["error"]

            failing = conn.execute(
                "SELECT name, consecutive_failures FROM feeds WHERE consecutive_failures >= 3"
            ).fetchall()
            if failing:
                stats["repeatedly_failing"] = [
                    f"{f['name']} ({f['consecutive_failures']} failures)" for f in failing
                ]
                log.warning("feeds failing repeatedly: %s", stats["repeatedly_failing"])
        finally:
            conn.close()
        return stats
