"""Manual pause switch for the GPU-heavy part of the pipeline.

gpu_guard.py already pauses analyze() *automatically* when it sees Render/3D
engine load, but that has two limits: the 30% threshold is an unvalidated
guess, and it deliberately gives up after GPU_PAUSE_MAX_TOTAL_SECONDS (4h)
and proceeds anyway rather than stalling the brief forever. This module is
the explicit override for "I'm about to game, stop touching the GPU until I
say so" -- no threshold to second-guess and no time cap.

State lives in the DB, not a module global, so a pause survives a service
restart (uvicorn restarting must not silently un-pause the thing that's
keeping the GPU free). The counterweight to that persistence is the
dashboard banner, which shows the paused state on every page load with the
Resume button right there -- so a forgotten pause is visible rather than a
silent cause of missing briefs.

Only analyze() honours this, because analyze() is the only stage that
touches the GPU: discover/extract are network-bound, and derive/cluster is
pure CPU cosine similarity over already-stored embeddings.

Pausing also frees the GPU rather than merely stopping new work. Just
setting the flag would leave the loaded model resident in memory until
llama-swap's globalTTL (1800s) expired -- half an hour of the GPU still
being occupied by a paused pipeline, which defeats the point. So a pause
calls llama-swap's /unload, which stops the llama-server process outright.
Measured on this machine: an /unload issued during an in-flight generation
returns in ~10s, the in-flight request dies with a 502, and `/running` goes
empty. That 502 is expected, not a real analysis failure -- analyze() puts
the article back on the queue instead of marking it failed (see
PAUSE_ABORT there).

Caveat worth knowing: if the endpoint is shared with anything else on the
machine, /unload is server-wide. Pausing here will also kill whatever other
generation happens to be in flight, and nothing stops that other service
from loading a model again while you're still gaming. Making the pause
cover every consumer would need the switch to live in the inference server
itself, or somewhere all of them read.

Paused time is excluded from ANALYZE_TIME_BUDGET_SECONDS, so resuming picks
up with as much analysis budget left as when you paused.
"""

import logging
import time
from datetime import datetime, timezone

import httpx

from intel_brief.config import settings
from intel_brief.db import get_connection

log = logging.getLogger("intel_brief.control")

# Generous relative to the ~10s measured above: this is a best-effort call
# whose failure is logged and surfaced, never raised, so waiting a little
# longer beats giving up while the GPU is still occupied.
UNLOAD_TIMEOUT_SECONDS = 30

# A DB read is essentially free (unlike gpu_guard's 1.5s of intel_gpu_top
# sampling, which is why that polls every 120s), so poll often enough that
# pressing Resume feels immediate.
PAUSE_POLL_SECONDS = 5


def get_state() -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT paused, paused_at, resumed_at FROM pipeline_control WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:  # pre-migration DB; treated as not paused
        return {"paused": False, "paused_at": None, "resumed_at": None}
    return {
        "paused": bool(row["paused"]),
        "paused_at": row["paused_at"],
        "resumed_at": row["resumed_at"],
    }


def is_paused() -> bool:
    return get_state()["paused"]


def set_paused(paused: bool) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    # Column name comes from this literal pair, never from a request.
    column = "paused_at" if paused else "resumed_at"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE pipeline_control SET paused = ?, {column} = ? WHERE id = 1",
            (1 if paused else 0, now),
        )
        conn.commit()
    finally:
        conn.close()
    log.info("pipeline %s by user", "paused" if paused else "resumed")
    return get_state()


def _unload_url() -> str:
    """Where to ask the server to release the GPU, or "" if that is not a
    thing it can do.

    `/unload` is a llama-swap extension, not part of the OpenAI API. Against
    ollama or a hosted endpoint the request would 404 -- and unloading someone
    else's model is not ours to ask for anyway. So it is explicit:
    LLM_UNLOAD_URL empty means pausing simply stops us making calls, which is
    the whole of what pause means when the GPU is not ours.

    Empty defaults to llama-swap's own path only when LLM_UNLOAD_URL is unset
    *and* the operator opted in with LLM_UNLOAD_URL=auto.
    """
    configured = settings.llm_unload_url.strip()
    if not configured:
        return ""
    if configured != "auto":
        return configured
    base = settings.llm_base_url.rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/unload"


def unload_models() -> tuple[bool, str]:
    """Asks llama-swap to stop all model processes, freeing the GPU now
    instead of when globalTTL expires. Best-effort: returns (ok, detail) and
    never raises -- failing to free the GPU must not leave the pause switch
    in an inconsistent state, and the detail is logged either way."""
    url = _unload_url()
    if not url:
        return True, "no unload endpoint configured (pause stops new calls only)"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=UNLOAD_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            log.warning("unload endpoint returned HTTP %s", resp.status_code)
            return False, f"HTTP {resp.status_code}"
        log.info("llama-swap /unload ok -- GPU released")
        return True, "ok"
    except Exception as e:
        log.warning("llama-swap /unload failed (%s: %s)", type(e).__name__, e)
        return False, f"{type(e).__name__}: {e}"


def pause(free_gpu: bool = True) -> dict:
    """Pauses and releases the GPU. Sets the flag BEFORE unloading, so
    analyze() can't slip a request in between the two and reload a model we
    just freed."""
    state = set_paused(True)
    if free_gpu:
        ok, detail = unload_models()
        state["gpu_freed"] = ok
        state["gpu_freed_detail"] = detail
    return state


def resume() -> dict:
    """No reload needed -- llama-swap loads the model on the next request."""
    return set_paused(False)


def wait_while_paused(on_state_change=None) -> float:
    """Blocks while the manual pause is on. Returns seconds spent waiting
    (0.0 if it was never paused), so the caller can report that time without
    counting it as work.

    on_state_change(paused: bool) is called once when the wait begins and
    once when it ends -- analyze() uses it to push the paused state into the
    live progress banner.
    """
    if not is_paused():
        return 0.0

    log.info("manual pause is on -- holding before next article")
    if on_state_change:
        on_state_change(True)

    started = time.monotonic()
    while is_paused():
        time.sleep(PAUSE_POLL_SECONDS)
    waited = time.monotonic() - started

    log.info("manual pause released after %.0fs -- resuming", waited)
    if on_state_change:
        on_state_change(False)
    return waited
