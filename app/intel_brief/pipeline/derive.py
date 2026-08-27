"""Derive stage: assembles the morning brief from clustered, analyzed
articles. Writes one daily_briefs row; the dashboard only reads it (no
LLM calls on page load, per the spec's core principle)."""

import json
from datetime import datetime, timedelta, timezone

from intel_brief import ai
from intel_brief.db import get_connection
from intel_brief.pipeline.cluster import cluster_recent_articles
from intel_brief.pipeline.runs import track_run

TOP_STORIES_LIMIT = 12


def _build_top_stories(conn, today: str) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # Clusters with at least one article discovered in the last 24h.
    fresh_cluster_ids = [
        row["cluster_id"] for row in conn.execute(
            """SELECT DISTINCT ac.cluster_id FROM article_clusters ac
               JOIN articles a ON a.id = ac.article_id
               WHERE a.discovered_at >= ?""",
            (cutoff,),
        ).fetchall()
    ]
    if not fresh_cluster_ids:
        return []

    # Excludes sports/celebrity -- a standing editorial choice ("bread &
    # circus"), not an oversight. A whole cluster disappears if every member is sports/
    # celebrity (correct); a cluster with a mix keeps its non-sports members.
    placeholders = ",".join("?" * len(fresh_cluster_ids))
    rows = conn.execute(
        f"""SELECT ac.cluster_id, a.id as article_id, a.title, a.url, a.outlet, a.published_at,
                   an.summary, an.interpretation, an.narrative_critique, an.scope, an.importance_score
            FROM article_clusters ac
            JOIN articles a ON a.id = ac.article_id
            JOIN article_analysis an ON an.article_id = a.id
            WHERE ac.cluster_id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM json_each(an.categories) WHERE value IN ('sports', 'celebrity')
              )
            ORDER BY an.importance_score DESC""",
        fresh_cluster_ids,
    ).fetchall()

    by_cluster: dict[int, list[dict]] = {}
    for row in rows:
        by_cluster.setdefault(row["cluster_id"], []).append(dict(row))

    stories = []
    for cluster_id, articles in by_cluster.items():
        top = articles[0]  # highest importance_score in this cluster
        outlets = sorted({a["outlet"] for a in articles})
        stories.append({
            "cluster_id": cluster_id,
            "article_id": top["article_id"],
            "title": top["title"],
            "url": top["url"],
            "summary": top["summary"],
            "interpretation": top["interpretation"],
            "narrative_critique": top["narrative_critique"],
            "scope": top["scope"],
            "importance_score": top["importance_score"],
            "outlets": outlets,
            "outlet_count": len(outlets),
        })

    stories.sort(key=lambda s: s["importance_score"] or 0, reverse=True)
    return _with_outlet_coverage(stories)


def _with_outlet_coverage(stories: list[dict]) -> list[dict]:
    """Top stories by importance, plus one story from any outlet that ranking
    left out entirely.

    Ranking alone means a quiet day at one outlet drops it from the brief
    completely, which is the opposite of a morning round-up -- the point is to
    flick through what every paper led with, not only the loudest N stories.
    Analysis selection now spreads the budget across outlets (see
    pipeline/select.py), so the material exists; this makes sure the brief
    shows it.

    A story counts as covering an outlet if that outlet is among the sources
    for its cluster, so a widely-covered story represents several at once and
    the additions stay few.
    """
    selected = stories[:TOP_STORIES_LIMIT]
    covered = {outlet for story in selected for outlet in story["outlets"]}

    for story in stories[TOP_STORIES_LIMIT:]:
        missing = set(story["outlets"]) - covered
        if not missing:
            continue
        story = dict(story, included_for_coverage=sorted(missing))
        selected.append(story)
        covered |= set(story["outlets"])

    selected.sort(key=lambda s: s["importance_score"] or 0, reverse=True)
    return selected


def _build_learn_today(conn, today: str, exclude_article_ids: set[int]) -> dict | None:
    previously_featured = set(exclude_article_ids)
    for row in conn.execute("SELECT learn_today FROM daily_briefs WHERE learn_today IS NOT NULL"):
        try:
            prev = json.loads(row["learn_today"])
            if prev and prev.get("article_id"):
                previously_featured.add(prev["article_id"])
        except (json.JSONDecodeError, TypeError):
            continue

    # Prefer the two dedicated science feeds over other outlets' science-tagged
    # articles -- category classification is imperfect (e.g. "Apple regains top
    # spot" observed mis-tagged as science, see phase1/NOTES.md), and Ars
    # Technica/arXiv are specifically science content, not general news that
    # happened to get a science label. Falls back to the wider pool if neither
    # has anything usable.
    rows = conn.execute(
        """SELECT a.id, a.title, a.url, a.outlet, an.summary, an.importance_score
           FROM articles a
           JOIN article_analysis an ON an.article_id = a.id
           WHERE a.status = 'analyzed'
             AND EXISTS (SELECT 1 FROM json_each(an.categories) WHERE value LIKE 'science%')
           ORDER BY
             CASE WHEN a.outlet IN ('Ars Technica', 'arXiv') THEN 0 ELSE 1 END,
             an.importance_score DESC"""
    ).fetchall()

    for row in rows:
        if row["id"] not in previously_featured:
            return {
                "article_id": row["id"], "title": row["title"], "url": row["url"],
                "outlet": row["outlet"], "summary": row["summary"],
            }
    return None


def assemble_morning_brief() -> dict:
    with track_run("derive") as stats:
        # Clustering needs the embeddings analyze() stores, so without a
        # model there is no brief to assemble. The Today tab falls back to
        # the reader view, which is the correct no-AI experience anyway.
        if not ai.available("derive"):
            stats["skipped"] = "ai unavailable"
            return stats
        conn = get_connection()
        try:
            cluster_stats = cluster_recent_articles()
            stats["cluster_stats"] = cluster_stats

            today = datetime.now(timezone.utc).date().isoformat()
            top_stories = _build_top_stories(conn, today)
            top_story_article_ids = {s["article_id"] for s in top_stories}
            learn_today = _build_learn_today(conn, today, top_story_article_ids)
            stats["top_stories_count"] = len(top_stories)
            stats["learn_today_found"] = learn_today is not None

            conn.execute(
                """INSERT OR REPLACE INTO daily_briefs
                   (date, period, generated_at, top_stories, disproportionate, underreported,
                    learn_today, on_this_day, follow_ups)
                   VALUES (?, 'morning', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today, datetime.now(timezone.utc).isoformat(), json.dumps(top_stories),
                    json.dumps([]), json.dumps([]), json.dumps(learn_today), json.dumps(None), json.dumps([]),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return stats
