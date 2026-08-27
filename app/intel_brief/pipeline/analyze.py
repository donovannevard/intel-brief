"""Stage 3 -- Analyze. Per extracted article: one structured LLM call
producing the article_analysis row plus entity/claim extraction, then a
separate embedding call. Uses qwen2.5-7b (not a bigger/reasoning model --
measured on this workload, bigger models were both slower and LESS reliable
at staying grounded in the text they were given) via an OpenAI-compatible
endpoint.

JSON-mode via response_format (grammar-constrained by llama-server), plus
a lenient fallback parse (strip code fences) and one retry on failure --
never let one bad article kill the whole run."""

import logging
import json
import time
from datetime import datetime, timedelta, timezone

from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from intel_brief import ai
from intel_brief.config import settings
from intel_brief.control import is_paused, wait_while_paused
from intel_brief.db import get_connection
from intel_brief.gpu_guard import check_gpu_busy_with_other_work
from intel_brief.pipeline.runs import track_run
from intel_brief.pipeline.select import coverage_summary, select_for_analysis

log = logging.getLogger("intel_brief.analyze")

PROMPT_VERSION = "v3"  # v2 labelled scope by the story's own geography (Florida primaries came back "local")

# Sentinel returned by analyze_one() when a pause cut the article short. Not
# a failure: the article keeps status='extracted' and is retried on resume.
PAUSE_ABORT = "aborted: paused mid-article"

# Sentinel prefix for "the endpoint is unreachable/misconfigured", which says
# nothing about the article. Marking these failed_analysis would be wrong twice
# over: the daily run only ever picks up status='extracted', so a burnt article
# is never retried, and with no endpoint at all *every* article burns -- one
# unreachable model turns the whole queue into permanent dead weight that only
# a manual per-day backfill can recover. Leave the article alone and stop the
# stage instead; there is nothing to be gained by asking 300 more times.
TRANSPORT_ABORT = "transport"

# Failures of the endpoint rather than of the article. Auth/404 are included
# deliberately: a bad key or a model name the server does not serve is a
# configuration problem, and no article will ever succeed under it.
TRANSPORT_ERRORS = (
    APIConnectionError,      # refused, DNS, TLS, and APITimeoutError subclasses this
    InternalServerError,     # 5xx
    RateLimitError,          # 429
    AuthenticationError,     # 401
    PermissionDeniedError,   # 403
    NotFoundError,           # 404 -- wrong base_url or unknown model
)

VALID_SCOPES = {"local", "country", "global"}
VALID_ENTITY_TYPES = {"person", "corporation", "agency", "country", "party", "other"}
VALID_CLAIM_TYPES = {"statement", "promise", "prediction", "denial"}

client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
embed_client = OpenAI(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key)


CATEGORY_GUIDE = """- politics: government, elections, legislation, political parties, heads of state/government.
- economics: MACRO/MARKET-MOVING content specifically -- commodity prices (oil, gold, silver, wheat, soy, etc.) rising/falling, currency and Bitcoin/crypto PRICE movements, central bank rate decisions, inflation/GDP/employment figures, trade data. Do NOT use this tag just because a story is loosely "about the economy" or mentions money in passing -- it must be reporting an actual economic indicator, price movement, or monetary policy action.
- corporate: news specifically ABOUT a major corporation (mostly US-based) as the subject -- earnings, executive appointments/departures, mergers/acquisitions, major product or strategic announcements, corporate scandals. Do NOT use this tag just because a company is mentioned in passing within a story that's really about something else (e.g. a political or regulatory story that happens to name a company is politics/economics, not corporate, unless the company's own actions are the actual subject).
- bitcoin: Bitcoin and major cryptocurrency adoption, regulatory developments, institutional/national adoption, and ecosystem news. Bitcoin PRICE movements alone (with no adoption/regulatory angle) should be tagged economics instead, not bitcoin -- use bitcoin for adoption/ecosystem substance, economics for pure price/market moves.
- science.math: mathematics research and breakthroughs.
- science.cryptography: cryptography and cryptographic algorithm research (the mathematical/computer-science discipline -- NOT Bitcoin/cryptocurrency, use "bitcoin" for that).
- science.physics, science.computing, science.ai, science.robotics: as named.
- health, conflict, sports, celebrity, other: as named."""


def build_system_prompt() -> str:
    return f"""You are a news analysis engine. The reader is located in {settings.user_locality}, {settings.user_region}, {settings.user_country}.

For the article given, produce a JSON object with EXACTLY these fields:
{{
  "summary": "3-5 sentence neutral summary of what happened, stripped of loaded language",
  "interpretation": "1-2 sentences on what this means or why it matters",
  "narrative_critique": "1-3 sentences on the framing, assumptions, or what's not said -- grounded in specifics from the article, not vague suspicion",
  "scope": one of "local", "country", "global" -- RELATIVE TO THE READER, not to the story.
    "local"   = about {settings.user_locality} specifically.
    "country" = about {settings.user_country}, but not specifically {settings.user_locality}.
    "global"  = everywhere else, INCLUDING stories that are local or national somewhere else.
    A US state primary, a Hungarian media law and an Indian protest are all "global" to this
    reader, however local they are to their own audience. Do not label by the story's own
    geography.
  "categories": array of zero or more from: politics, economics, corporate, bitcoin, science.math, science.cryptography, science.physics, science.computing, science.ai, science.robotics, health, conflict, sports, celebrity, other.

  Category guide -- be precise, not liberal, especially for economics/corporate/bitcoin (they are commonly over-tagged; only apply them when the article's actual subject matches, not just a passing mention):
{CATEGORY_GUIDE}

  "sentiment_framing": {{"tone": short description, "loaded_language": array of specific loaded words/phrases actually found in the text (empty array if none), "passive_voice_notable": true or false}},
  "importance_score": integer 1-10,
  "entities": array of {{"name": string, "type": one of "person", "corporation", "agency", "country", "party", "other", "role": one of "subject", "mentioned", "source"}},
  "claims": array of {{"entity_name": string, "claim_text": string (verbatim or tight paraphrase), "claim_type": one of "statement", "promise", "prediction", "denial", "claim_date": ISO date string or null, "resolves_by": ISO date string or null (only for predictions with a stated deadline)}}
}}

IMPORTANT: Use ONLY the names, titles, facts, and dates given in the article text below. Do not substitute any other name or fact, even if a different one feels more familiar to you. If something isn't stated, leave it out or use null -- do not guess or fill in from outside knowledge.

Respond with ONLY the JSON object. No markdown code fences, no commentary before or after.
"""


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def call_llm_json(article_title: str, article_text: str) -> dict:
    user_msg = f"Title: {article_title}\n\nArticle text:\n{article_text[:6000]}"
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": user_msg},
    ]

    last_error = None
    for attempt in range(2):
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=1200,
            timeout=settings.llm_timeout_seconds,
        )
        raw = resp.choices[0].message.content
        try:
            return json.loads(_strip_code_fences(raw))
        except json.JSONDecodeError as e:
            last_error = f"JSON parse failed: {e}"
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "That was not valid JSON. Respond with ONLY the JSON object, no other text."})
    raise ValueError(last_error)


def get_embedding(text: str) -> bytes:
    resp = embed_client.embeddings.create(model=settings.embedding_model, input=text[:2000])
    vec = resp.data[0].embedding
    import struct
    return struct.pack(f"{len(vec)}f", *vec)


def resolve_entity(conn, name: str, entity_type: str) -> int:
    """Exact + case-insensitive + substring match against existing entities.
    Full embedding-similarity fuzzy matching (per the spec) is deferred --
    this heuristic covers the common "Andy Burnham" vs "Burnham" case without
    needing per-entity embedding storage/comparison infra not yet built."""
    name_lower = name.strip().lower()
    rows = conn.execute("SELECT id, canonical_name, aliases FROM entities").fetchall()
    for row in rows:
        canon_lower = row["canonical_name"].lower()
        aliases = json.loads(row["aliases"] or "[]")
        alias_lowers = [a.lower() for a in aliases]
        if name_lower == canon_lower or name_lower in alias_lowers:
            return row["id"]
        if name_lower in canon_lower or canon_lower in name_lower:
            if name_lower not in alias_lowers:
                aliases.append(name)
                conn.execute("UPDATE entities SET aliases=? WHERE id=?", (json.dumps(aliases), row["id"]))
            return row["id"]

    cur = conn.execute(
        "INSERT INTO entities (canonical_name, type, aliases, first_seen_at) VALUES (?, ?, ?, ?)",
        (name, entity_type, json.dumps([]), datetime.now(timezone.utc).isoformat()),
    )
    return cur.lastrowid


def analyze_one(conn, article_row) -> tuple[bool, str | None]:
    try:
        result = call_llm_json(article_row["title"], article_row["full_text"])

        scope = result.get("scope") if result.get("scope") in VALID_SCOPES else None
        categories = [c for c in result.get("categories", []) if isinstance(c, str)]
        importance = result.get("importance_score")
        importance = int(importance) if isinstance(importance, (int, float)) else None

        conn.execute(
            """INSERT OR REPLACE INTO article_analysis
               (article_id, summary, interpretation, narrative_critique, scope, categories,
                sentiment_framing, importance_score, analyzed_at, model_used, prompt_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_row["id"], result.get("summary"), result.get("interpretation"),
                result.get("narrative_critique"), scope, json.dumps(categories),
                json.dumps(result.get("sentiment_framing", {})), importance,
                datetime.now(timezone.utc).isoformat(), settings.llm_model, PROMPT_VERSION,
            ),
        )

        for ent in result.get("entities", []):
            name = ent.get("name")
            etype = ent.get("type")
            role = ent.get("role")
            if not name or etype not in VALID_ENTITY_TYPES:
                continue
            entity_id = resolve_entity(conn, name, etype)
            conn.execute(
                "INSERT OR IGNORE INTO article_entities (article_id, entity_id, role) VALUES (?, ?, ?)",
                (article_row["id"], entity_id, role),
            )

        for claim in result.get("claims", []):
            entity_name = claim.get("entity_name")
            claim_text = claim.get("claim_text")
            claim_type = claim.get("claim_type")
            if not claim_text or claim_type not in VALID_CLAIM_TYPES:
                continue
            entity_id = resolve_entity(conn, entity_name, "other") if entity_name else None
            conn.execute(
                """INSERT INTO claims (entity_id, article_id, claim_text, claim_type, claim_date,
                   resolves_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, article_row["id"], claim_text, claim_type,
                    claim.get("claim_date"), claim.get("resolves_by"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        # The embedding is a second GPU call, so a pause landing between the
        # two would otherwise immediately reload the model that pausing just
        # unloaded. Abandon the article instead and roll back its writes --
        # nothing here is committed until the caller commits.
        if is_paused():
            conn.rollback()
            return False, PAUSE_ABORT

        embedding_input = f"{article_row['title']}\n{result.get('summary', '')}"
        embedding_blob = get_embedding(embedding_input)
        conn.execute(
            "INSERT OR REPLACE INTO article_embeddings (article_id, embedding, model, created_at) VALUES (?, ?, ?, ?)",
            (article_row["id"], embedding_blob, settings.embedding_model, datetime.now(timezone.utc).isoformat()),
        )

        conn.execute("UPDATE articles SET status='analyzed' WHERE id=?", (article_row["id"],))
        return True, None
    except TRANSPORT_ERRORS as e:
        # Nothing wrong with this article -- roll back and let the caller stop.
        conn.rollback()
        return False, f"{TRANSPORT_ABORT}: {type(e).__name__}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def mark_stale_articles(conn) -> int:
    """Articles that have sat in 'extracted' past STALE_ARTICLE_HOURS are no
    longer timely enough to matter for a daily brief, and would otherwise
    permanently lose the newest-first ordering race and just accumulate
    forever. Marking them 'stale' removes them from consideration rather
    than leaving unbounded dead weight in the queue."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=settings.stale_article_hours)).isoformat()
    cur = conn.execute(
        "UPDATE articles SET status='stale' WHERE status='extracted' AND discovered_at < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


def analyze() -> dict:
    with track_run("analyze") as stats:
        if not ai.available("analyze"):
            stats["skipped"] = "ai unavailable"
            return stats
        conn = get_connection()
        try:
            stale_count = mark_stale_articles(conn)
            stats["marked_stale"] = stale_count

            # Round-robin across outlets and topics -- see pipeline/select.py.
            # Plain newest-first handed the whole budget to whichever feed
            # polled last: one real run analysed 38 arXiv preprints and nothing
            # else, with 85 BBC and 88 Guardian articles queued.
            queued = conn.execute(
                "SELECT * FROM articles WHERE status = 'extracted' ORDER BY discovered_at DESC"
            ).fetchall()
            hints = {
                r["outlet"]: r["category_hint"]
                for r in conn.execute(
                    "SELECT outlet, MIN(category_hint) AS category_hint FROM feeds GROUP BY outlet"
                )
            }
            rows, duplicates = select_for_analysis(queued, hints)

            for dup_row, kept_row, reason in duplicates:
                conn.execute(
                    "UPDATE articles SET status='duplicate', fail_reason=? WHERE id=?",
                    (f"{reason}; see article {kept_row['id']}", dup_row["id"]),
                )
            if duplicates:
                conn.commit()
            stats["duplicates_skipped"] = len(duplicates)
            stats["candidates"] = len(rows)
            stats["analyzed"] = 0
            stats["failed"] = 0
            stats["skipped_time_budget"] = 0
            stats["gpu_paused"] = False
            stats["manual_paused"] = False
            stats["manual_pause_seconds"] = 0.0
            stats["requeued_on_pause"] = 0
            # Recorded so a lopsided day is visible in the run stats rather
            # than needing a query to discover.
            budget_articles = max(1, int(settings.analyze_time_budget_seconds / 95))
            stats["planned_coverage"] = coverage_summary(rows, budget_articles)
            stats.flush()  # visible immediately as "0 / N", not just after the first (~90s) article

            budget = timedelta(seconds=settings.analyze_time_budget_seconds)
            max_pause = timedelta(seconds=settings.gpu_pause_max_total_seconds)
            active_elapsed = timedelta()
            total_paused = timedelta()
            stats["gpu_pause_events"] = 0

            def note_manual_pause(paused: bool) -> None:
                stats["manual_paused"] = paused
                stats.flush()  # so the dashboard banner reflects it right away

            for row in rows:
                # Explicit user pause (dashboard button). Held before the GPU
                # check so a deliberate pause doesn't keep re-running
                # intel_gpu_top, and unlike the automatic guard below it has
                # no time cap -- it waits as long as the user wants. Also not
                # counted against the analyze time budget.
                stats["manual_pause_seconds"] = round(
                    stats["manual_pause_seconds"] + wait_while_paused(note_manual_pause), 1
                )

                # Pause (not counted against the time budget) if something
                # else -- a game, etc. -- is actively using the GPU. This is
                # the direct fix for two real incidents where this competed
                # with the user's own interactive GPU use.
                while True:
                    busy, reason = check_gpu_busy_with_other_work()
                    if not busy or total_paused >= max_pause:
                        if busy:
                            log_msg = f"GPU pause limit ({max_pause}) reached, proceeding anyway: {reason}"
                            log.warning("%s", log_msg)
                        if stats["gpu_paused"]:
                            stats["gpu_paused"] = False
                            stats.flush()
                        break
                    stats["gpu_pause_events"] += 1
                    stats["gpu_paused"] = True
                    stats.flush()
                    log.info("analyze paused, %s", reason)
                    pause_start = datetime.now(timezone.utc)
                    time.sleep(settings.gpu_pause_check_interval_seconds)
                    total_paused += datetime.now(timezone.utc) - pause_start

                if active_elapsed > budget:
                    stats["skipped_time_budget"] = len(rows) - stats["analyzed"] - stats["failed"]
                    break

                article_start = datetime.now(timezone.utc)
                ok, error = analyze_one(conn, row)
                active_elapsed += datetime.now(timezone.utc) - article_start
                if ok:
                    stats["analyzed"] += 1
                elif error and error.startswith(TRANSPORT_ABORT):
                    # The endpoint is down or misconfigured. Every remaining
                    # article would fail identically, so stop rather than
                    # marking the queue failed. Status stays 'extracted', so
                    # the next run picks them up with nothing lost.
                    stats["aborted_transport"] = error
                    stats["skipped_endpoint_down"] = len(rows) - stats["analyzed"] - stats["failed"]
                    log.error("analyze stopped: LLM endpoint unreachable (%s)", error)
                    stats.flush()
                    break
                elif error == PAUSE_ABORT or is_paused():
                    # Pausing frees the GPU by unloading the model out from
                    # under whatever request is in flight, so the resulting
                    # error (typically a 502) is our own doing, not a bad
                    # article. Leave it 'extracted' to retry after resume
                    # rather than burning it as failed_analysis.
                    conn.rollback()
                    stats["requeued_on_pause"] += 1
                    stats.flush()
                    continue
                else:
                    conn.execute(
                        "UPDATE articles SET status='failed_analysis', fail_reason=? WHERE id=?",
                        (error, row["id"]),
                    )
                    stats["failed"] += 1
                conn.commit()
                stats.flush()
            stats["paused_seconds"] = total_paused.total_seconds()
        finally:
            conn.close()
        return stats
