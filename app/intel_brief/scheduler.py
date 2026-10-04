"""APScheduler wiring. One daily job at PIPELINE_RUN_TIME (early morning by
default) runs discover -> extract -> analyze -> derive (brief assembly)
sequentially, all the way through, rather than continuously -- this is a real
GPU/energy resource on a machine shared with interactive use (gaming), and a
shared iGPU means LLM inference and game rendering genuinely compete for the
same hardware.

Brief assembly (derive) used to be a SEPARATE fixed-time trigger (07:00),
which broke badly the first time analyze() ran long: the brief got assembled
from whatever partial data existed at 07:00 regardless of whether analyze had
actually finished, and never refreshed again that day even once analyze
finally completed hours later. Now derive only ever runs immediately after
analyze finishes (or times out via ANALYZE_TIME_BUDGET_SECONDS), so the brief
always reflects a just-completed pass, whenever that actually happens.

Startup catch-up: the machine is currently turned off overnight, so a plain
CronTrigger would silently miss PIPELINE_RUN_TIME whenever the machine (or
just the service) isn't up at that moment -- confirmed happening for real
across 2026-07-24/25. On every startup, if today's date has no successful
`derive` run yet AND we're already past PIPELINE_RUN_TIME, run the chain
once immediately instead of waiting for tomorrow. Only fires once per day no
matter how many times the service restarts (guarded by the DB, not a flag),
and never fires *before* PIPELINE_RUN_TIME to avoid double-running against
the imminent real cron trigger. This does NOT reconstruct a separate brief
per day missed -- discover() only ever sees whatever's currently live in each
RSS feed, and most feeds don't retain a week-old backlog, so several missed
days collapse into a single catch-up run covering whatever news is still
visible in the feeds at the time it runs. Older rotated-out days are not
recoverable by this or any RSS-based design.

Network-readiness wait: a VPN with a kill-switch policy blocks ALL traffic
from the moment of boot until its tunnel connects -- 10-15s is typical, and
any always-on VPN, captive portal or slow DHCP lease produces the same
window. A catch-up run firing seconds after boot landed squarely inside it:
every feed failed DNS resolution, and because discover() catches each feed's
error individually rather than raising, the chain still completed and wrote
a "successful" empty brief. Five consecutive days of briefings were lost
that way before anyone noticed, because nothing in the run reported as
failed (root-caused by correlating boot times in the journal, not guessed). run_pipeline_chain() now waits (up to 2 min, polling every 5s) for
real DNS resolution before doing anything, for both the catch-up and the
normal cron-triggered run.

Disproportionate/underreported coverage, foreign-lens diff, and on-this-day
are deliberately deferred. No
evening brief -- user decided a morning roundup of the prior day is
sufficient."""

import logging
import socket
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from intel_brief.config import settings

log = logging.getLogger("intel_brief.scheduler")

NETWORK_WAIT_MAX_SECONDS = 120
NETWORK_WAIT_CHECK_INTERVAL_SECONDS = 5
NETWORK_CHECK_HOSTS = ["bbc.co.uk", "theguardian.com", "nytimes.com"]


def _wait_for_network() -> bool:
    deadline = time.monotonic() + NETWORK_WAIT_MAX_SECONDS
    while True:
        for host in NETWORK_CHECK_HOSTS:
            try:
                socket.getaddrinfo(host, 443)
                return True
            except OSError:
                continue
        if time.monotonic() >= deadline:
            return False
        time.sleep(NETWORK_WAIT_CHECK_INTERVAL_SECONDS)


def run_markets_ingest():
    """Price collection on its own schedule.

    Separate from the daily chain because prices shouldn't be a day stale
    just because the LLM stages run once a day. Network-only and never
    touches the GPU, so it deliberately ignores the manual pause switch --
    pausing means "release the GPU", and holding price collection would be
    an unrelated punishment.
    """
    from intel_brief.markets.ingest import ingest
    from intel_brief.outlets import refresh_outlet_logos

    if not _wait_for_network():
        log.warning("network unreachable -- skipping markets ingest, will retry next trigger")
        return
    try:
        log.info("markets ingest: %s", ingest())
    except Exception:
        log.exception("markets ingest failed")
    try:
        # Publisher logos: only fetches what's missing, so this is a no-op
        # after the first run. Riding along here keeps network work in one
        # scheduled place instead of on startup or a page load.
        log.info("outlet logos: %s", refresh_outlet_logos())
    except Exception:
        log.exception("outlet logo refresh failed")


# Each stage is isolated. The stages communicate only through the database --
# every one of them reads what it needs and writes what it produced -- so a
# failure upstream means a later stage has less to work with, not that it
# cannot run. Wrapping a whole chain in one try/except meant an unreachable
# LLM endpoint also took out markets_ingest, which is pure HTTP and needs no
# model at all.
def _run_stages(label: str, stages: list) -> list[str]:
    failures = []
    for name, fn in stages:
        try:
            log.info("%s: %s", name, fn())
        except Exception:
            log.exception("stage failed: %s", name)
            failures.append(name)
    if failures:
        log.warning("%s finished with %d failed stage(s): %s",
                    label, len(failures), ", ".join(failures))
    else:
        log.info("%s finished, all stages ok", label)
    return failures


def run_collection():
    """The daily job: gather the day's articles, and stop there.

    Collection and analysis are split because they have completely different
    costs. Discover and extract are network-bound, finish in minutes and must
    happen every day or the day is lost for good -- RSS serves only what is
    live now. Analysis is GPU-bound and took hours, which is a bad thing to
    start unattended on a machine someone also uses. So the articles are
    always there to read in the morning, unsorted, and analysis is asked for
    when there is capacity for it (see run_analysis).
    """
    from intel_brief.pipeline.discover import discover
    from intel_brief.pipeline.extract import extract

    if not _wait_for_network():
        log.warning(
            "network still unreachable after waiting %ss -- skipping this run, will retry next trigger",
            NETWORK_WAIT_MAX_SECONDS,
        )
        return

    log.info("collection: discover -> extract")
    _run_stages("collection", [("discover", discover), ("extract", extract)])


def run_analysis():
    """The on-demand job: read what has been collected and make sense of it.

    Triggered from the Status page, never on a timer. analyze() picks what is
    worth analysing and writes per-article summaries; derive folds the result
    into the morning brief; comments sets human discussion beside the model's
    reading, deliberately after it.
    """
    from intel_brief.pipeline.analyze import analyze
    from intel_brief.pipeline.comments import collect_comments
    from intel_brief.pipeline.derive import assemble_morning_brief

    log.info("analysis: analyze -> derive -> comments")
    _run_stages("analysis", [
        ("analyze", analyze),
        ("morning brief", assemble_morning_brief),
        ("comments", collect_comments),
    ])


def run_market_review():
    """The weekly job: refresh prices, then describe the week.

    Weekly rather than daily because a single day of price moves is mostly
    noise to read about, while a week has a shape worth a paragraph -- and
    the digest the model is given already reasons in 7-day terms. Prices
    themselves still refresh every few hours; this is only the narrative.
    """
    from intel_brief.markets.ingest import ingest as markets_ingest
    from intel_brief.pipeline.correlate import correlate
    from intel_brief.pipeline.market_brief import generate_market_briefs

    if not _wait_for_network():
        log.warning("network unreachable -- skipping the weekly market review")
        return

    log.info("market review: markets_ingest -> market_brief -> correlate")
    _run_stages("market review", [
        ("markets ingest", markets_ingest),
        ("market briefs", generate_market_briefs),
        ("news/market correlation", correlate),
    ])


def _needs_catchup(pipeline_hour: int, pipeline_minute: int) -> bool:
    from intel_brief.db import get_connection

    # Collection is what a missed day loses irrecoverably, so that -- not the
    # brief, which can be produced from stored articles at any time -- is what
    # catch-up checks for.
    now_local = datetime.now(ZoneInfo(settings.user_timezone))
    scheduled_today = now_local.replace(hour=pipeline_hour, minute=pipeline_minute, second=0, microsecond=0)
    if now_local < scheduled_today:
        return False  # today's normal cron trigger hasn't happened yet -- let it run as scheduled

    today_utc = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM pipeline_runs WHERE stage = 'extract' AND ok = 1 AND finished_at >= ?",
            (today_utc,),
        ).fetchone()
    finally:
        conn.close()
    return row is None


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.user_timezone)

    pipeline_hour, pipeline_minute = (int(x) for x in settings.pipeline_run_time.split(":"))

    scheduler.add_job(
        run_collection,
        CronTrigger(hour=pipeline_hour, minute=pipeline_minute),
        id="pipeline_chain",
    )

    review_hour, review_minute = (int(x) for x in settings.market_review_time.split(":"))
    scheduler.add_job(
        run_market_review,
        CronTrigger(day_of_week=settings.market_review_day,
                    hour=review_hour, minute=review_minute),
        id="market_review",
        coalesce=True,
        max_instances=1,
    )

    # Prices refresh independently of the GPU-bound chain. `next_run_time=now`
    # means a restart collects immediately rather than waiting out the
    # interval -- cheap (network only) and it's how a machine that's off
    # overnight catches up. Providers serve full history, so a missed window
    # repairs itself; only the snapshot-only series keep a real gap.
    scheduler.add_job(
        run_markets_ingest,
        IntervalTrigger(hours=settings.markets_ingest_interval_hours),
        id="markets_ingest",
        next_run_time=datetime.now(ZoneInfo(settings.user_timezone)),
        coalesce=True,
        max_instances=1,
    )

    if _needs_catchup(pipeline_hour, pipeline_minute):
        log.info(
            "nothing collected today yet and PIPELINE_RUN_TIME (%s) has passed -- "
            "catching up now instead of waiting for tomorrow", settings.pipeline_run_time,
        )
        scheduler.add_job(run_collection, id="pipeline_catchup")

    return scheduler
