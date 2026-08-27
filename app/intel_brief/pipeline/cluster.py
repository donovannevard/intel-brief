"""Clustering: groups same-story articles across outlets so the brief shows
one story with multiple sources, not N near-duplicate cards. Greedy
cosine-similarity clustering over the last 72h of analyzed articles.

Threshold (0.80 default) is a starting point, not a validated number --
tune based on real observed clustering quality (an embeddings
proof-of-concept measured ~0.73 for same-topic-different-wording sentences, ~0.39 for
unrelated ones, so 0.80 is deliberately conservative -- errs toward
under-merging rather than incorrectly collapsing distinct stories)."""

import struct
from datetime import datetime, timedelta, timezone

from intel_brief.db import get_connection

CLUSTER_WINDOW_HOURS = 72
SIMILARITY_THRESHOLD = 0.80


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def cluster_recent_articles() -> dict:
    stats = {"candidates": 0, "joined_existing": 0, "new_clusters": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CLUSTER_WINDOW_HOURS)).isoformat()
    conn = get_connection()
    try:
        # Existing clusters in the window: represented by their first (oldest) member's embedding.
        cluster_reps = []  # list of (cluster_id, embedding)
        cluster_rows = conn.execute(
            """SELECT sc.id, ae.embedding
               FROM story_clusters sc
               JOIN (
                   SELECT cluster_id, MIN(article_id) as rep_article_id
                   FROM article_clusters GROUP BY cluster_id
               ) rep ON rep.cluster_id = sc.id
               JOIN article_embeddings ae ON ae.article_id = rep.rep_article_id
               WHERE sc.created_at >= ?"""
            ,
            (cutoff,),
        ).fetchall()
        for row in cluster_rows:
            cluster_reps.append([row["id"], _unpack(row["embedding"])])

        # Candidate articles: analyzed, in window, with an embedding, not yet clustered.
        candidates = conn.execute(
            """SELECT a.id, a.title, a.outlet, a.published_at, ae.embedding
               FROM articles a
               JOIN article_embeddings ae ON ae.article_id = a.id
               WHERE a.status = 'analyzed' AND a.discovered_at >= ?
                 AND a.id NOT IN (SELECT article_id FROM article_clusters)
               ORDER BY a.id ASC""",
            (cutoff,),
        ).fetchall()
        stats["candidates"] = len(candidates)

        now = datetime.now(timezone.utc).isoformat()
        for article in candidates:
            emb = _unpack(article["embedding"])
            best_cluster_id, best_sim = None, 0.0
            for cluster_id, rep_emb in cluster_reps:
                sim = _cosine(emb, rep_emb)
                if sim > best_sim:
                    best_sim, best_cluster_id = sim, cluster_id

            if best_cluster_id is not None and best_sim >= SIMILARITY_THRESHOLD:
                conn.execute(
                    "INSERT INTO article_clusters (article_id, cluster_id, similarity) VALUES (?, ?, ?)",
                    (article["id"], best_cluster_id, best_sim),
                )
                stats["joined_existing"] += 1
            else:
                cur = conn.execute(
                    "INSERT INTO story_clusters (label, created_at, peak_date, status) VALUES (?, ?, ?, 'active')",
                    (article["title"], now, now[:10]),
                )
                new_cluster_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO article_clusters (article_id, cluster_id, similarity) VALUES (?, ?, 1.0)",
                    (article["id"], new_cluster_id),
                )
                cluster_reps.append([new_cluster_id, emb])
                stats["new_clusters"] += 1
            conn.commit()

        # Refresh today's coverage_stats for every cluster touched in this window.
        today = now[:10]
        conn.execute("DELETE FROM coverage_stats WHERE date = ?", (today,))
        rows = conn.execute(
            """SELECT ac.cluster_id, COUNT(DISTINCT ac.article_id) as article_count,
                      COUNT(DISTINCT a.outlet) as outlet_count
               FROM article_clusters ac
               JOIN articles a ON a.id = ac.article_id
               JOIN story_clusters sc ON sc.id = ac.cluster_id
               WHERE sc.created_at >= ?
               GROUP BY ac.cluster_id"""
            ,
            (cutoff,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO coverage_stats (date, cluster_id, article_count, outlet_count) VALUES (?, ?, ?, ?)",
                (today, row["cluster_id"], row["article_count"], row["outlet_count"]),
            )
        conn.commit()
    finally:
        conn.close()
    return stats
