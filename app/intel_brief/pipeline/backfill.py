"""Catching up days the daily budget couldn't finish.

Every day since the archive started has articles that were collected and had
their text extracted, but were never analysed -- the analyse stage gets about
40 slots a day against a couple of hundred articles. As of 2026-08-19 that's
2,743 articles sitting in the database with their full text intact, waiting
for GPU time that the daily run will never have spare.

This is what turns those into archive. It re-runs the ordinary analysis over
old articles, bounded by its own time budget, triggered by hand from the
Status page when the machine isn't otherwise busy.

**What this cannot do** is recover a day where nothing was collected. RSS
feeds only serve what's currently live, so 2026-07-24 and 2026-07-27 through
07-30 -- when the service was up but the VPN kill-switch blocked DNS -- have
no articles at all, and no amount of processing brings them back. The Status
page shows those as gaps rather than as work to do, because offering a
"catch up" button that silently achieves nothing would be worse than showing
the hole.

Articles already marked `stale` are deliberately in scope. Staleness means
"too old to be worth the daily budget", which is exactly the population this
stage exists to work through -- it just doesn't get to jump the queue ahead
of today's news.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from intel_brief.config import settings
from intel_brief.control import is_paused, wait_while_paused
from intel_brief.db import get_connection
from intel_brief.gpu_guard import check_gpu_busy_with_other_work
from intel_brief.pipeline.analyze import PAUSE_ABORT, TRANSPORT_ABORT, analyze_one
from intel_brief.pipeline.runs import track_run
from intel_brief.pipeline.select import select_for_analysis

log = logging.getLogger("intel_brief.backfill")

# Statuses holding an article that was collected but never analysed. Both
# still have their text; the difference is only whether the daily run gave up
# on them for being old.
BACKFILLABLE = ("extracted", "stale", "failed_analysis")


def archive_start() -> str:
    return settings.archive_start_date


def day_coverage(days: int | None = None, start: str = "", end: str = "") -> list[dict]:
    """Per-day archive state, for the Status page chart.

    `pending` counts only what could actually be analysed -- articles whose
    text we still hold. A day with articles but no text is a day where
    extraction failed, which backfill can't fix either.
    """
    # The archive target starts at a fixed date rather than a rolling window:
    # "is my archive complete since 1 August" is the question, and a rolling
    # window would quietly drop older gaps out of view instead of answering it.
    since = archive_start()
    if days is not None:
        rolling = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        since = max(since, rolling)
    if start:
        # Never below the archive start: days before it were never a target,
        # so showing them as unfilled would invent gaps that aren't real.
        since = max(since, start)
    until = end or datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT date(discovered_at) AS day,
                      COUNT(*) AS collected,
                      SUM(status = 'analyzed') AS analysed,
                      SUM(status IN ('extracted','stale','failed_analysis')
                          AND full_text IS NOT NULL AND length(full_text) > 0) AS pending
                 FROM articles
                WHERE date(discovered_at) >= ? AND date(discovered_at) <= ?
                GROUP BY day
                ORDER BY day""",
            (since, until),
        ).fetchall()
    finally:
        conn.close()

    by_day = {r["day"]: dict(r) for r in rows}

    # Walk the calendar rather than the rows, so a day with no collection at
    # all appears as a gap instead of vanishing from the chart.
    out = []
    first = datetime.strptime(since, "%Y-%m-%d").date()
    last = min(datetime.strptime(until, "%Y-%m-%d").date(), datetime.now(timezone.utc).date())
    day = first
    while day <= last:
        key = day.isoformat()
        entry = by_day.get(key)
        out.append({
            "day": key,
            "collected": (entry or {}).get("collected", 0),
            "analysed": (entry or {}).get("analysed") or 0,
            "pending": (entry or {}).get("pending") or 0,
            # No articles at all: the feeds were unreachable that day and the
            # window has closed. Nothing to catch up.
            "unrecoverable": entry is None,
        })
        day += timedelta(days=1)
    return out


def day_detail(day: str) -> dict | None:
    """One day's state, for the panel behind a selected bar."""
    match = [d for d in day_coverage(start=day, end=day)]
    return match[0] if match else None


def estimated_seconds(pending: int, per_article: float) -> int:
    """Time to finish a day, with headroom -- a run that stops 3 articles short
    of completing a day is the most annoying possible outcome."""
    return int(pending * per_article * 1.25) + 60


def archive_totals(start: str = "", end: str = "") -> dict:
    coverage = day_coverage(start=start, end=end)
    return {
        "days": len(coverage),
        "days_with_gaps": sum(1 for d in coverage if d["unrecoverable"]),
        "analysed": sum(d["analysed"] for d in coverage),
        "pending": sum(d["pending"] for d in coverage),
        "first_day": coverage[0]["day"] if coverage else None,
        "last_day": coverage[-1]["day"] if coverage else None,
        "archive_start": archive_start(),
        "today": datetime.now(timezone.utc).date().isoformat(),
    }


def _candidates(conn, day: str | None) -> list:
    placeholders = ",".join("?" * len(BACKFILLABLE))
    sql = (
        f"SELECT * FROM articles WHERE status IN ({placeholders}) "
        "AND full_text IS NOT NULL AND length(full_text) > 0"
    )
    params: tuple = BACKFILLABLE
    if day:
        sql += " AND date(discovered_at) = ?"
        params = params + (day,)
    sql += " ORDER BY discovered_at DESC"
    return conn.execute(sql, params).fetchall()


def backfill(day: str | None = None, budget_seconds: int | None = None) -> dict:
    """Analyse stored-but-unanalysed articles, oldest day first if unspecified.

    Uses the same per-outlet round-robin as the daily run, so a backfill that
    only gets through half a day still leaves a balanced spread rather than
    one outlet's output.
    """
    budget = timedelta(seconds=budget_seconds or settings.backfill_time_budget_seconds)
    with track_run("backfill") as stats:
        conn = get_connection()
        try:
            if day is None:
                row = conn.execute(
                    f"""SELECT date(discovered_at) AS day FROM articles
                         WHERE status IN ({','.join('?' * len(BACKFILLABLE))})
                           AND full_text IS NOT NULL AND length(full_text) > 0
                           AND date(discovered_at) >= ?
                         ORDER BY discovered_at ASC LIMIT 1""",
                    BACKFILLABLE + (archive_start(),),
                ).fetchone()
                day = row["day"] if row else None
            stats["day"] = day
            if not day:
                stats["note"] = "nothing left to backfill"
                return stats

            hints = {
                r["outlet"]: r["category_hint"]
                for r in conn.execute(
                    "SELECT outlet, MIN(category_hint) AS category_hint FROM feeds GROUP BY outlet"
                )
            }
            rows, duplicates = select_for_analysis(_candidates(conn, day), hints)
            for dup_row, kept_row, reason in duplicates:
                conn.execute(
                    "UPDATE articles SET status='duplicate', fail_reason=? WHERE id=?",
                    (f"{reason}; see article {kept_row['id']}", dup_row["id"]),
                )
            conn.commit()

            stats["duplicates_skipped"] = len(duplicates)
            stats["candidates"] = len(rows)
            stats["analyzed"] = 0
            stats["failed"] = 0
            stats["remaining"] = len(rows)
            stats.flush()

            elapsed = timedelta()
            for row in rows:
                wait_while_paused()
                # Backfill is explicitly the low-priority job, so it yields to
                # interactive GPU use on exactly the same terms as the daily
                # run rather than pushing through.
                busy, reason = check_gpu_busy_with_other_work()
                if busy:
                    log.info("backfill stopping early: %s", reason)
                    stats["stopped_early"] = reason
                    break
                if elapsed > budget:
                    stats["stopped_early"] = "time budget reached"
                    break

                started = time.monotonic()
                ok, error = analyze_one(conn, row)
                elapsed += timedelta(seconds=time.monotonic() - started)

                if ok:
                    stats["analyzed"] += 1
                elif error and error.startswith(TRANSPORT_ABORT):
                    # Endpoint down: same reasoning as the daily run. A
                    # catch-up that burns the very backlog it exists to
                    # recover would be worse than not running at all.
                    conn.rollback()
                    stats["stopped_early"] = f"LLM endpoint unreachable ({error})"
                    log.error("backfill stopped: LLM endpoint unreachable (%s)", error)
                    stats.flush()
                    break
                elif error == PAUSE_ABORT or is_paused():
                    conn.rollback()
                    continue
                else:
                    conn.execute(
                        "UPDATE articles SET status='failed_analysis', fail_reason=? WHERE id=?",
                        (error, row["id"]),
                    )
                    stats["failed"] += 1
                conn.commit()
                stats["remaining"] = len(rows) - stats["analyzed"] - stats["failed"]
                stats.flush()
        finally:
            conn.close()
        return stats
