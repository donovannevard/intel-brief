"""Stage: market_brief -- one LLM narrative per Markets tab, grounded in the
computed digest.

The division of labour is the whole design: `digest.py` does the arithmetic,
this asks the model to explain it. The model never sees a price series and is
never asked to calculate anything, because every figure it might want is
already in the digest it's handed. That is the direct answer to the failure
mode this project has hit twice on the news side -- a model given thin
context confidently producing numbers and names from its training data
instead of from the text.

Belt and braces, though: `_verify_figures()` checks every number in the
generated prose against the digest before storage, and anything unsupported
is recorded in `market_briefs.unverified` and shown as a warning rather than
quietly published. A grounding rule in a prompt is a request; this is a check.

Cost: one call per tab, four tabs, ~90s each -- about six minutes of GPU per
day on top of the news pipeline's hour.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from openai import OpenAI

from intel_brief import ai
from intel_brief.config import settings
from intel_brief.control import wait_while_paused
from intel_brief.db import get_connection
from intel_brief.markets import registry, store
from intel_brief.pipeline import digest as digest_mod
from intel_brief.pipeline.runs import track_run

log = logging.getLogger("intel_brief.market_brief")

PROMPT_VERSION = "v3"  # v2 fixed unit labelling and metric-restating; v3 adds day-over-day continuity

# How many instruments from each tab go into the prompt. The digest is ranked
# by notability, so this is "the dozen things worth talking about" rather than
# an arbitrary truncation -- and it keeps the prompt small enough that the
# model attends to all of it.
INSTRUMENTS_PER_PROMPT = 12

client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)


SYSTEM_PROMPT = """You are a markets analyst writing a short daily note for one person's private dashboard. You are given a digest of computed figures for a group of instruments. Your job is to explain what is happening, not to calculate.

Produce a JSON object with EXACTLY these fields:
{
  "summary": "2-4 sentences on the state of this group right now. Lead with whatever actually moved.",
  "whats_changing": array of 2-5 short strings, each naming a specific instrument and what it did,
  "watch": array of 1-4 short strings -- what could matter next, and what would confirm it,
  "regime_note": "1-2 sentences placing this in a longer arc, or null if nothing in the digest supports one"
}

RULES:

Numbers
- Every number you write MUST appear in the digest given to you. Do not compute new figures, do not convert units, do not estimate. If you want to say something the digest doesn't support with a number, say it qualitatively instead.
- Units: write "%" for changes. ONLY write "percentage points" or "pp" for an instrument whose digest says `change_is_percentage_points: true` (yields, inflation rates, spreads). Writing "percentage points" for a price or a count is wrong.

What to talk about
- The instruments are given in order of how unusual their move is, most unusual first. Lead with those. `move_vs_typical` is the measure: 4.5 means this week's move is four and a half times that instrument's normal week. A 16% move at 4.5x typical is a bigger story than a 148% move at 1.9x typical, because the second one is close to that series' ordinary behaviour.
- Say what a move means, not that it happened. "Active addresses fell 16.67%, the sharpest weekly drop in the group" is an observation. "BTC/USD had a 7-day move of 7.25%" is just reading the table back.
- `rising_is_bad: true` means a rise is an adverse development (yields, inflation, debt, credit spreads). Don't describe those rises as improvement.
- `cumulative_series: true` means the level only ever goes up (a running total). Never call its level a record or a high.

Continuity
- Where an instrument has a `previously` block, that is what the digest said on that earlier date. Use it to say whether a move is continuing, accelerating or reversing: "BTC/USD is up 7.5% over 7 days, against 7.25% in yesterday's note" is worth writing; repeating today's figure alone is not.
- Only compare against figures in `previously`. Do not describe a trend across days the digest doesn't cover.
- If nothing has meaningfully changed since the previous note, say that plainly. "Little has moved since yesterday" is a useful sentence.

The "watch" list
- Each entry names something that could happen next and what would show it. Good: "whether wheat follows corn higher, which would point at weather rather than a single-crop story". Bad: "7-day changes in mempool count" -- that is a metric, not something to watch.
- If nothing in the digest supports a forward-looking observation, return fewer entries. One good entry beats three empty ones.

General
- Do not predict prices. Do not give investment advice. Do not use the words "buy", "sell", "bullish" or "bearish".
- Write plainly, in full sentences. No hedging filler, no "in today's dynamic market" throat-clearing, no restating the instruction back.

Respond with ONLY the JSON object."""


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def call_llm_json(tab_label: str, digest_json: str) -> dict:
    user_msg = f"Market group: {tab_label}\n\nDigest:\n{digest_json}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    last_error = None
    for _ in range(2):
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=900,
            timeout=settings.llm_timeout_seconds,
        )
        raw = resp.choices[0].message.content
        try:
            return json.loads(_strip_code_fences(raw))
        except json.JSONDecodeError as e:
            last_error = f"JSON parse failed: {e}"
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "That was not valid JSON. Respond with ONLY the JSON object."})
    raise ValueError(last_error)


# ---------------------------------------------------------------------------
# Figure verification
# ---------------------------------------------------------------------------

# Bare integers up to this value are treated as prose ("over 7 days", "the
# past 30 days", "2 of the 5 indices") rather than as claimed data. Anything
# with a decimal point, a percent sign, or a larger magnitude has to be
# traceable to the digest.
SMALL_INTEGER_LIMIT = 100

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _allowed_values(entries: list[dict]) -> set[float]:
    """Every figure the model is permitted to state."""
    allowed: set[float] = set()

    def add(v):
        if isinstance(v, (int, float)):
            allowed.add(round(float(v), 4))
            allowed.add(round(float(v), 2))
            allowed.add(round(float(v), 1))
            allowed.add(float(round(v)))

    def add_entry(e: dict) -> None:
        for key in ("latest", "typical_7d_move", "move_vs_typical", "latest_move_zscore",
                    "week52_high", "week52_low", "pct_of_52w_range"):
            add(e.get(key))
        for v in e.get("change", {}).values():
            add(v)
            add(abs(v) if isinstance(v, (int, float)) else None)

    for e in entries:
        add_entry(e)
        # Yesterday's figures are quotable too -- that's the whole point of
        # giving them to the model -- so verification has to accept them.
        if e.get("previously"):
            add_entry(e["previously"])
    return allowed


def _verify_figures(narrative: dict, entries: list[dict]) -> list[str]:
    """Numbers in the prose that the digest doesn't support.

    Deliberately conservative about what counts as a claim: a bare small
    integer is usually a time window or a count of items, and flagging those
    would bury real problems in noise. The cost of that choice is that a
    fabricated small integer slips through -- acceptable, because the figures
    that would mislead are prices, percentages and multiples.
    """
    allowed = _allowed_values(entries)
    text_parts = [narrative.get("summary") or "", narrative.get("regime_note") or ""]
    text_parts += [str(x) for x in narrative.get("whats_changing", [])]
    text_parts += [str(x) for x in narrative.get("watch", [])]
    text = " ".join(text_parts)

    unverified = []
    for match in _NUMBER.finditer(text):
        token = match.group()
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if value == int(value) and abs(value) <= SMALL_INTEGER_LIMIT:
            continue
        candidates = {round(value, 4), round(value, 2), round(value, 1), float(round(value))}
        if candidates & allowed:
            continue
        # Tolerate the model rounding a digest figure sensibly (5,933 -> 5,930).
        if any(v and abs(value - v) / max(abs(v), 1e-9) < 0.01 for v in allowed):
            continue
        unverified.append(token)
    return unverified


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------

CORRECTION_PROMPT = """These figures in your response do not appear in the digest: {figures}

You must not state numbers that are not in the data you were given. Rewrite the JSON object using only figures from the digest. Where you wanted to make a point you have no figure for, make it qualitatively or drop it."""


def _generate_verified(tab_label: str, prompt_entries: list[dict], entries: list[dict]) -> tuple[dict, list[str]]:
    """Generate a narrative, and give the model one chance to fix fabricated
    figures before we give up on it.

    This exists because of what the first real run produced for the Monetary
    System tab: it reported the euro up 208% in a month, the dollar index down
    20.3% and the ruble down 87.6%, when those pairs had moved fractions of a
    percent. Every figure was invented. A grounding rule in the prompt did not
    prevent it -- the check did, and telling the model precisely which numbers
    were unsupported is the cheapest way to get a usable answer rather than
    none.
    """
    narrative = call_llm_json(tab_label, json.dumps(prompt_entries, indent=1))
    unverified = _verify_figures(narrative, entries)
    if not unverified:
        return narrative, []

    log.warning("unverified figures %s -- asking the model to correct them", sorted(set(unverified)))
    retry = call_llm_json(
        tab_label,
        json.dumps(prompt_entries, indent=1)
        + "\n\n"
        + CORRECTION_PROMPT.format(figures=", ".join(sorted(set(unverified)))),
    )
    retry_unverified = _verify_figures(retry, entries)
    if len(retry_unverified) < len(unverified):
        return retry, sorted(set(retry_unverified))
    return narrative, sorted(set(unverified))


def previous_digest(conn, tab_id: str, before: str) -> dict[str, dict]:
    """Yesterday's (or the most recent earlier) digest for this tab, keyed by
    instrument.

    This is what lets a note say "7.5% over 7 days now, against 7.25%
    yesterday" instead of restating today's number as though nothing came
    before it. Reads the stored digest rather than recomputing, so the
    comparison is against exactly what was published that day.
    """
    row = conn.execute(
        """SELECT date, movers FROM market_briefs
            WHERE tab = ? AND date < ? ORDER BY date DESC LIMIT 1""",
        (tab_id, before),
    ).fetchone()
    if not row:
        return {}
    try:
        entries = json.loads(row["movers"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return {}
    return {e["id"]: {**e, "_date": row["date"]} for e in entries if e.get("id")}


def with_previous(entries: list[dict], previous: dict[str, dict]) -> list[dict]:
    """Attach yesterday's figures to today's, where the instrument appeared in
    both. Only the fields worth comparing -- handing the model the whole prior
    digest would double the prompt for no gain."""
    out = []
    for entry in entries:
        prior = previous.get(entry["id"])
        if prior:
            entry = {**entry, "previously": {
                "as_of": prior.get("_date"),
                "latest": prior.get("latest"),
                "change": prior.get("change", {}),
                "move_vs_typical": prior.get("move_vs_typical"),
            }}
        out.append(entry)
    return out


def build_tab_brief(conn, mconn, tab: dict, stats: dict) -> dict | None:
    tab_digest = digest_mod.tab_digest(mconn, tab["id"])
    entries = tab_digest["instruments"][:INSTRUMENTS_PER_PROMPT]
    if not entries:
        log.info("no market data for tab %s -- skipping brief", tab["id"])
        stats["skipped_no_data"] = stats.get("skipped_no_data", 0) + 1
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    previous = previous_digest(conn, tab["id"], today)
    entries = with_previous(entries, previous)

    # Trim fields the model doesn't need to reason about, to keep the prompt
    # focused on the figures rather than on plumbing.
    prompt_entries = [
        {k: v for k, v in e.items() if k not in ("source", "dp", "group")}
        for e in entries
    ]
    narrative, unverified = _generate_verified(tab["label"], prompt_entries, entries)
    if unverified:
        log.warning("market brief for %s still contains unverifiable figures: %s", tab["id"], unverified)
        stats["unverified_figures"] = stats.get("unverified_figures", 0) + len(unverified)
        stats.setdefault("withheld_tabs", []).append(tab["id"])

    conn.execute(
        """INSERT OR REPLACE INTO market_briefs
           (date, tab, generated_at, movers, narrative, unverified, model, prompt_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            today, tab["id"], datetime.now(timezone.utc).isoformat(),
            json.dumps(entries), json.dumps(narrative), json.dumps(unverified),
            settings.llm_model, PROMPT_VERSION,
        ),
    )
    conn.commit()
    return narrative


def generate_market_briefs() -> dict:
    """One narrative per tab. Safe to re-run; replaces the day's rows."""
    with track_run("market_brief") as stats:
        # digest.py already ran and the computed cards render without us;
        # only the prose over them needs a model.
        if not ai.available("market_brief"):
            stats["skipped"] = "ai unavailable"
            return stats
        stats["tabs_written"] = 0
        stats["failed"] = 0
        stats.flush()

        budget = timedelta(seconds=settings.markets_llm_time_budget_seconds)
        elapsed = timedelta()
        conn = get_connection()
        mconn = store.get_connection()
        try:
            for tab in registry.TABS:
                # Same pause discipline as analyze(): this is GPU work, so it
                # waits rather than competing with whatever the user is doing.
                wait_while_paused()
                if elapsed > budget:
                    stats["skipped_time_budget"] = stats.get("skipped_time_budget", 0) + 1
                    continue

                started = time.monotonic()
                try:
                    if build_tab_brief(conn, mconn, tab, stats):
                        stats["tabs_written"] += 1
                except Exception as e:
                    log.warning("market brief failed for %s (%s): %s", tab["id"], type(e).__name__, e)
                    stats["failed"] += 1
                elapsed += timedelta(seconds=time.monotonic() - started)
                stats.flush()
        finally:
            conn.close()
            mconn.close()
        return stats


def recent_briefs(tab_id: str, days: int = 7) -> list[dict]:
    """The last `days` notes for a tab, newest first.

    Kept as an archive rather than overwritten because a market note is only
    useful next to the one before it -- "still climbing" means nothing without
    yesterday to compare against.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM market_briefs WHERE tab = ? ORDER BY date DESC LIMIT ?",
            (tab_id, days),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        try:
            narrative = json.loads(row["narrative"] or "{}")
            unverified = json.loads(row["unverified"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        regime = narrative.get("regime_note")
        if isinstance(regime, str) and regime.strip().lower() in ("", "null", "none", "n/a", "nil"):
            narrative["regime_note"] = None
        out.append({
            "date": row["date"],
            "generated_at": row["generated_at"],
            "model": row["model"],
            "unverified": unverified,
            **narrative,
        })
    return out

