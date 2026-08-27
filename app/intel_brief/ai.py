"""Whether an AI endpoint is available, and what to do when it isn't.

intel-brief works without a model. Discovery, extraction, the archive and its
full-text search, the markets price collection and every computed figure on the
markets tabs are all model-free -- that is the product without AI: a reader and
an archive. The model adds interpretation on top: the per-article summary,
why-it-matters and framing note, the clustering that assembles the morning
brief, the market narratives, and the news/market correlation.

Three states rather than a boolean, because "I turned it off" and "it is
broken" should never look the same on the Status page:

    disabled  LLM_BASE_URL is empty. Nothing is attempted, nothing warns.
    degraded  Configured, but the endpoint did not answer. Stages skip and
              say so loudly; no article is marked failed (see analyze.py's
              TRANSPORT_ABORT for why that distinction matters).
    enabled   The endpoint answered.

The probe is cached with a TTL so a daily chain costs one request rather than
one per stage, while a machine whose endpoint comes back later still recovers
without a restart.
"""

import logging
import time
import urllib.error
import urllib.request

from intel_brief.config import settings

log = logging.getLogger("intel_brief.ai")

MODE_DISABLED = "disabled"
MODE_DEGRADED = "degraded"
MODE_ENABLED = "enabled"

# Long enough that one pipeline chain probes once; short enough that an
# endpoint coming back is noticed within the same day.
PROBE_TTL_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 10

_cache: dict = {"at": 0.0, "mode": None, "detail": ""}


def configured() -> bool:
    """Has an endpoint been set at all?"""
    return bool(settings.llm_base_url.strip())


def _probe() -> tuple[str, str]:
    """GET {base}/models -- the cheapest call every OpenAI-compatible server
    implements. We only care that something answers; the model list itself is
    not validated here, because a wrong model name surfaces as a 404 on first
    use and is reported as a transport failure rather than a bad article."""
    url = settings.llm_base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {settings.llm_api_key}",
        "User-Agent": settings.fetch_user_agent,
    })
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:
            if 200 <= resp.status < 300:
                return MODE_ENABLED, f"{settings.llm_base_url} answered {resp.status}"
            return MODE_DEGRADED, f"{settings.llm_base_url} answered {resp.status}"
    except urllib.error.HTTPError as e:
        # A 401/403 means something is listening but we are not welcome --
        # still unusable, and worth saying precisely rather than "unreachable".
        return MODE_DEGRADED, f"{settings.llm_base_url} refused us: HTTP {e.code}"
    except Exception as e:
        return MODE_DEGRADED, f"{settings.llm_base_url} unreachable: {type(e).__name__}: {e}"


def mode(force: bool = False) -> str:
    return status(force)[0]


def status(force: bool = False) -> tuple[str, str]:
    """(mode, human-readable detail). Cached for PROBE_TTL_SECONDS."""
    if not configured():
        return MODE_DISABLED, "LLM_BASE_URL is not set -- running as a reader, without interpretation"

    now = time.monotonic()
    if not force and _cache["mode"] and (now - _cache["at"]) < PROBE_TTL_SECONDS:
        return _cache["mode"], _cache["detail"]

    m, detail = _probe()
    if m != _cache["mode"]:
        # Only log on a transition, so a working setup stays quiet.
        (log.info if m == MODE_ENABLED else log.warning)("AI %s: %s", m, detail)
    _cache.update(at=now, mode=m, detail=detail)
    return m, detail


def available(stage: str) -> bool:
    """Gate for a stage that needs a model. Logs the reason once per call and
    returns False rather than raising, so callers stay linear."""
    m, detail = status()
    if m == MODE_ENABLED:
        return True
    if m == MODE_DISABLED:
        log.info("%s skipped: AI disabled (%s)", stage, detail)
    else:
        log.warning("%s skipped: AI unavailable -- %s", stage, detail)
    return False


def invalidate() -> None:
    """Force the next status() to re-probe (used after a manual resume)."""
    _cache.update(at=0.0, mode=None, detail="")
