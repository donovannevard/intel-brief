"""Stage: correlate -- possible links between the day's market moves and the
day's news.

The expensive way to do this is asking the model about every article against
every instrument. With ~40 analysed articles and ~78 instruments that's 3,000
calls, which on this hardware is a week of GPU time for a feature nobody
asked to be exhaustive.

So the shortlisting is done with arithmetic, not inference: the news pipeline
has already embedded every article, so a one-sentence description of each
market move gets embedded too and matched by cosine similarity. Only the
survivors -- a couple of dozen pairs -- go to the model, in a single call.
Roughly three minutes of GPU per day.

What this stage is careful about is honesty. Two things moving in the same
week is not causation, the prompt says so explicitly, and the model is told
to return an empty list when nothing lines up. A correlation feature that
always finds something is worse than useless: it manufactures a narrative
every single day and teaches you to trust it.
"""

import json
import logging
import struct
from datetime import datetime, timedelta, timezone

from openai import OpenAI

from intel_brief import ai
from intel_brief.config import settings
from intel_brief.control import wait_while_paused
from intel_brief.db import get_connection
from intel_brief.markets import store
from intel_brief.pipeline import digest as digest_mod
from intel_brief.pipeline.runs import track_run

log = logging.getLogger("intel_brief.correlate")

PROMPT_VERSION = "v1"

MOVERS_CONSIDERED = 8        # top movers by notability
ARTICLES_PER_MOVER = 3       # nearest articles kept per mover
ARTICLE_WINDOW_HOURS = 48    # only recent news can plausibly relate
# Below this cosine similarity the "nearest" article is just the least
# unrelated one. 0.55 is deliberately looser than the 0.80 used for story
# clustering -- these texts are a market sentence and a news summary, which
# are related by topic rather than by being the same story -- but tight
# enough to drop pure noise before it reaches the model.
MIN_SIMILARITY = 0.55

client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
embed_client = OpenAI(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key)


SYSTEM_PROMPT = """You are checking whether any of today's news plausibly relates to today's market moves, for one person's private dashboard.

You are given market moves, each with a few news articles that a similarity search found nearby. Similarity search matches on wording, so most of these pairings will be coincidental. Your job is to keep only the ones with a real, statable mechanism.

Return a JSON object: {"links": [ ... ]}, where each link is:
{
  "instrument_id": the id exactly as given,
  "article_id": the id exactly as given,
  "rationale": "one sentence naming the mechanism connecting them",
  "direction": "news_explains_move" | "move_reflects_news" | "shared_cause",
  "confidence": integer 1-5
}

RULES:
- Return {"links": []} if nothing has a real mechanism. That is the expected answer on a quiet day and is always better than a stretch.
- A mechanism is specific: "drought in the growing region reduces supply" is a mechanism. "Both relate to the economy" is not. "Sentiment" is not.
- These are associations, never proof of causation. Write the rationale so it reads as a possible connection, not an established fact.
- Never claim a link the article text doesn't support. Do not use outside knowledge about what happened; use only what you are given.
- Confidence 5 means the article explicitly concerns that instrument or its direct market. 1 means plausible but speculative. Do not inflate.
- At most one link per instrument -- the single best one.

Respond with ONLY the JSON object."""


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _embed(text: str) -> list[float]:
    resp = embed_client.embeddings.create(model=settings.embedding_model, input=text[:2000])
    return resp.data[0].embedding


def recent_article_embeddings(conn) -> list[dict]:
    """Analysed articles from the last two days, with their stored embeddings.

    Reusing these is what makes the stage cheap -- they were computed during
    analyze() and are sitting in the database already.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ARTICLE_WINDOW_HOURS)).isoformat()
    rows = conn.execute(
        """SELECT a.id, a.title, a.outlet, an.summary, e.embedding
             FROM articles a
             JOIN article_analysis an ON an.article_id = a.id
             JOIN article_embeddings e ON e.article_id = a.id
            WHERE a.discovered_at >= ? AND a.status = 'analyzed'""",
        (cutoff,),
    ).fetchall()
    out = []
    for r in rows:
        if not r["embedding"]:
            continue
        out.append({
            "id": r["id"], "title": r["title"], "outlet": r["outlet"],
            "summary": r["summary"], "vector": _unpack(r["embedding"]),
        })
    return out


def shortlist(conn, mconn, stats: dict) -> list[dict]:
    """Candidate (mover, articles) pairs, chosen by embedding similarity."""
    articles = recent_article_embeddings(conn)
    stats["articles_considered"] = len(articles)
    if not articles:
        return []

    movers = digest_mod.top_movers(mconn, MOVERS_CONSIDERED)
    stats["movers_considered"] = len(movers)

    candidates = []
    for mover in movers:
        sentence = digest_mod.describe_move(mover)
        vector = _embed(sentence)
        scored = sorted(
            ((_cosine(vector, a["vector"]), a) for a in articles),
            key=lambda p: p[0], reverse=True,
        )[:ARTICLES_PER_MOVER]
        matches = [
            {"article": a, "similarity": round(sim, 3)}
            for sim, a in scored if sim >= MIN_SIMILARITY
        ]
        if matches:
            candidates.append({"mover": mover, "sentence": sentence, "matches": matches})
    stats["pairs_shortlisted"] = sum(len(c["matches"]) for c in candidates)
    return candidates


def _prompt_payload(candidates: list[dict]) -> str:
    return json.dumps([
        {
            "instrument_id": c["mover"]["id"],
            "move": c["sentence"],
            "candidate_articles": [
                {
                    "article_id": m["article"]["id"],
                    "title": m["article"]["title"],
                    "outlet": m["article"]["outlet"],
                    "summary": (m["article"]["summary"] or "")[:600],
                }
                for m in c["matches"]
            ],
        }
        for c in candidates
    ], indent=1)


def call_llm_json(payload: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": payload},
    ]
    for _ in range(2):
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=900,
            timeout=settings.llm_timeout_seconds,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "That was not valid JSON. Respond with ONLY the JSON object."})
    return {"links": []}


def correlate() -> dict:
    """Link the day's market moves to the day's news, where a mechanism exists."""
    with track_run("correlate") as stats:
        if not ai.available("correlate"):
            stats["skipped"] = "ai unavailable"
            return stats
        stats["links_written"] = 0
        stats["rejected"] = 0
        stats.flush()

        conn = get_connection()
        mconn = store.get_connection()
        try:
            wait_while_paused()
            candidates = shortlist(conn, mconn, stats)
            stats.flush()
            if not candidates:
                log.info("no market/news pairs passed the similarity floor")
                return stats

            wait_while_paused()
            result = call_llm_json(_prompt_payload(candidates))
            links = result.get("links") or []

            # The model may only link pairs the shortlist actually offered --
            # otherwise a hallucinated article id would be stored as a real
            # association and rendered as though a human could click it.
            valid_pairs = {
                (c["mover"]["id"], m["article"]["id"])
                for c in candidates for m in c["matches"]
            }
            similarity_by_pair = {
                (c["mover"]["id"], m["article"]["id"]): m["similarity"]
                for c in candidates for m in c["matches"]
            }

            today = datetime.now(timezone.utc).date().isoformat()
            conn.execute("DELETE FROM market_news_links WHERE date = ?", (today,))
            for link in links:
                pair = (link.get("instrument_id"), link.get("article_id"))
                if pair not in valid_pairs:
                    log.warning("discarding link the shortlist never offered: %s", pair)
                    stats["rejected"] += 1
                    continue
                confidence = link.get("confidence")
                confidence = int(confidence) if isinstance(confidence, (int, float)) else None
                conn.execute(
                    """INSERT INTO market_news_links
                       (date, instrument_id, article_id, rationale, confidence, direction,
                        similarity, method, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'embedding+llm', ?)""",
                    (
                        today, pair[0], pair[1], link.get("rationale"), confidence,
                        link.get("direction"), similarity_by_pair.get(pair),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                stats["links_written"] += 1
            conn.commit()
        finally:
            conn.close()
            mconn.close()
        return stats


def group_links(links: list[dict]) -> list[dict]:
    """Collapse links that point at the same article.

    The model links each instrument separately, so an energy-costs story that
    explains both corn and soybeans arrives as two rows with near-identical
    rationales. Reading the same sentence twice with one word changed is
    worse than reading it once as "Corn & Soybeans", so they're merged here
    and the highest-confidence member's rationale is the one shown.
    """
    by_article: dict[int, list[dict]] = {}
    order: list[int] = []
    for link in links:
        key = link["article_id"]
        if key not in by_article:
            by_article[key] = []
            order.append(key)
        by_article[key].append(link)

    grouped = []
    for key in order:
        members = sorted(by_article[key], key=lambda x: x.get("confidence") or 0, reverse=True)
        best = members[0]
        labels = [m.get("instrument_label") or m["instrument_id"] for m in members]
        if len(labels) == 1:
            label = labels[0]
        elif len(labels) == 2:
            label = f"{labels[0]} & {labels[1]}"
        else:
            label = ", ".join(labels[:-1]) + f" & {labels[-1]}"
        grouped.append({**best, "instrument_label": label,
                        "instrument_ids": [m["instrument_id"] for m in members]})
    return grouped


def correlation_ran_today() -> bool:
    """Whether the stage actually ran today -- so the dashboard can tell
    "we looked and found nothing" apart from "we haven't looked"."""
    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM pipeline_runs WHERE stage = 'correlate' AND ok = 1 AND finished_at >= ? LIMIT 1",
            (today,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def links_for_date(date: str | None = None) -> list[dict]:
    """Stored links with their article titles, for the dashboard."""
    conn = get_connection()
    try:
        if date is None:
            row = conn.execute("SELECT MAX(date) AS d FROM market_news_links").fetchone()
            date = row["d"] if row else None
        if not date:
            return []
        rows = conn.execute(
            """SELECT l.*, a.title, a.url, a.outlet
                 FROM market_news_links l
                 LEFT JOIN articles a ON a.id = l.article_id
                WHERE l.date = ?
                ORDER BY l.confidence DESC""",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
