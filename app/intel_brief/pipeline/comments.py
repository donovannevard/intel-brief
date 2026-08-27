"""Stage: comments -- attach human discussion to articles that have one.

Runs after analysis, so the model has already had its say from the article
text alone, uninfluenced by what anyone else thought of it. That order is the
point: the AI reading and the human reading are produced independently, and
the dashboard shows them side by side. Agreement is corroboration; divergence
is the part worth reading.

Network-only and cheap -- a URL search plus a handful of small JSON fetches
per article, no GPU -- so it doesn't compete with anything and doesn't honour
the pause switch, which exists to free the GPU.

Only a minority of articles will have a thread: Hacker News covers technology,
science and business, not local planning disputes. That's expected, and the
lookup order deliberately doesn't chase the hit rate -- articles are checked in
the same order the analysis queue uses, so a second opinion lands wherever one
exists rather than only on the articles most likely to have one. An article
with no discussion simply doesn't get a comments block.
"""

import logging
from datetime import datetime, timedelta, timezone

from intel_brief import hackernews
from intel_brief.db import get_connection
from intel_brief.pipeline.runs import track_run
from intel_brief.pipeline.select import select_for_analysis

log = logging.getLogger("intel_brief.comments")

SOURCE = "hackernews"

# How far back to look for articles still missing a discussion. HN threads
# accumulate comments for a day or two after posting, so an article checked
# the moment it was analysed may have had nothing worth attaching yet.
LOOKBACK_HOURS = 72

# A ceiling on how many searches one run performs. Each is a single small
# request to a free public API; this is politeness, not a rate limit we've hit.
MAX_LOOKUPS = 60

# How long before an article with no thread is worth asking about again.
RECHECK_AFTER_HOURS = 24


def _candidates(conn) -> list:
    """Analysed articles worth searching for a discussion.

    Ordered exactly like the analysis queue -- round-robin across outlets,
    newest first within each (pipeline/select.py) -- rather than by how likely
    a thread is. An earlier version put HN-sourced and technical articles
    first, which found more threads per lookup but turned the feature into
    "commentary on the tech articles". Comments belong wherever they exist, on
    whatever the pipeline considers worth reading, so the ordering is the same
    ordering everything else uses. Most lookups return nothing; each is one
    small request to a free API, which is a fair price for not skewing what
    gets a second opinion.

    `comments_checked_at` stops a miss being retried every run. With the 72h
    window and a 24h re-check gap an article is searched about three times --
    enough for a thread that appears a day after publication, not enough to
    keep asking forever.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    recheck_before = (now - timedelta(hours=RECHECK_AFTER_HOURS)).isoformat()
    rows = conn.execute(
        """SELECT a.id, a.url, a.title, a.outlet, a.discovered_at, a.content_hash
             FROM articles a
             JOIN article_analysis an ON an.article_id = a.id
            WHERE a.status = 'analyzed'
              AND a.discovered_at >= ?
              AND (a.comments_checked_at IS NULL OR a.comments_checked_at < ?)
              AND NOT EXISTS (SELECT 1 FROM article_comments c WHERE c.article_id = a.id)
            ORDER BY a.discovered_at DESC""",
        (cutoff, recheck_before),
    ).fetchall()

    hints = {
        r["outlet"]: r["category_hint"]
        for r in conn.execute(
            "SELECT outlet, MIN(category_hint) AS category_hint FROM feeds GROUP BY outlet"
        )
    }
    ordered, _ = select_for_analysis(rows, hints)
    return ordered[:MAX_LOOKUPS]


def attach_comments(conn, article, discussion: dict, comments: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for comment in comments:
        conn.execute(
            """INSERT OR IGNORE INTO article_comments
               (article_id, source, external_id, thread_id, thread_url, thread_points,
                thread_comment_count, author, text, posted_at, permalink, rank, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article["id"], SOURCE, str(comment["hn_id"]), str(discussion["hn_id"]),
                discussion["permalink"], discussion["points"], discussion["comments"],
                comment["author"], comment["text"], comment["posted_at"],
                comment["permalink"], comment["rank"], now,
            ),
        )
        written += 1
    conn.commit()
    return written


def collect_comments() -> dict:
    """Find and store discussion for recently analysed articles."""
    with track_run("comments") as stats:
        conn = get_connection()
        try:
            candidates = _candidates(conn)
            stats["checked"] = 0
            stats["with_discussion"] = 0
            stats["comments_stored"] = 0
            stats.flush()

            now = datetime.now(timezone.utc).isoformat()
            for article in candidates:
                stats["checked"] += 1
                # Record the attempt whatever the outcome, so a miss isn't
                # repeated on the next run.
                conn.execute("UPDATE articles SET comments_checked_at = ? WHERE id = ?",
                             (now, article["id"]))
                conn.commit()

                discussion = hackernews.find_discussion(article["url"])
                if not discussion:
                    continue
                comments = hackernews.top_comments(discussion["hn_id"])
                if not comments:
                    continue
                stats["comments_stored"] += attach_comments(conn, article, discussion, comments)
                stats["with_discussion"] += 1
                log.info(
                    "hn thread for %r: %d points, %d comments",
                    article["title"][:60], discussion["points"], discussion["comments"],
                )
                stats.flush()
        finally:
            conn.close()
        return stats


def comments_for(article_ids: list[int]) -> dict[int, dict]:
    """Stored discussion for a set of articles, for the dashboard.

    Returns one entry per article: the thread's metadata plus its comments, so
    a card can show "23 comments on Hacker News" and link out even when only
    three are displayed.
    """
    if not article_ids:
        return {}
    placeholders = ",".join("?" * len(article_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""SELECT * FROM article_comments
                 WHERE article_id IN ({placeholders})
                 ORDER BY article_id, rank""",
            tuple(article_ids),
        ).fetchall()
    finally:
        conn.close()

    out: dict[int, dict] = {}
    for row in rows:
        entry = out.setdefault(row["article_id"], {
            "source": row["source"],
            "thread_url": row["thread_url"],
            "points": row["thread_points"],
            "comment_count": row["thread_comment_count"],
            "comments": [],
        })
        entry["comments"].append({
            "author": row["author"],
            "text": row["text"],
            "permalink": row["permalink"],
            "posted_at": row["posted_at"],
        })
    return out
