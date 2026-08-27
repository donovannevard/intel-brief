"""FastAPI dashboard. Page loads never trigger LLM calls -- this only reads
what the pipeline stages have already written (spec's core principle)."""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from intel_brief import control, geo, outlets
from intel_brief import ai
from intel_brief.config import settings
from intel_brief.db import get_connection
from intel_brief.markets import registry as markets_registry
from intel_brief.markets import service as markets_service
from intel_brief.markets import store as markets_store
from intel_brief.pipeline import backfill as backfill_mod
from intel_brief.pipeline import comments as comments_mod
from intel_brief.pipeline import correlate, market_brief

log = logging.getLogger("intel_brief.web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Intel Brief")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# A fresh brief is assembled once daily (~05:30 local); "warn" allows for one
# missed day (e.g. machine was off), "stale" means two or more were missed.
FRESHNESS_WARN_HOURS = 30
FRESHNESS_STALE_HOURS = 54


def get_dashboard_freshness() -> dict:
    """Age of the last successful full pipeline run (derive stage), for the
    header indicator -- so a missed/skipped run (e.g. machine off at 05:30)
    is obvious from the dashboard itself rather than only discoverable by
    noticing stale articles."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT finished_at FROM pipeline_runs WHERE stage = 'derive' AND ok = 1 "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"relative": "no successful run yet", "display": "", "level": "stale"}

    finished_at = datetime.fromisoformat(row["finished_at"])
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - finished_at).total_seconds() / 3600

    if age_hours < FRESHNESS_WARN_HOURS:
        level = "fresh"
    elif age_hours < FRESHNESS_STALE_HOURS:
        level = "warn"
    else:
        level = "stale"

    if age_hours < 1:
        relative = "just now"
    elif age_hours < 48:
        relative = f"{int(age_hours)}h ago"
    else:
        relative = f"{int(age_hours // 24)}d ago"

    local_dt = finished_at.astimezone(ZoneInfo(settings.user_timezone))
    return {"relative": relative, "display": local_dt.strftime("%a %d %b, %H:%M"), "level": level}


templates.env.globals["dashboard_freshness"] = get_dashboard_freshness

STAGE_LABELS = {
    "discover": "Checking for new articles",
    "extract": "Fetching full article text",
    "analyze": "Analyzing articles",
    "derive": "Assembling today's brief",
    "markets_ingest": "Collecting market data",
    "market_brief": "Reading the markets",
    "correlate": "Looking for news/market connections",
    "comments": "Finding reader discussion",
}

# Worst-case legitimate run: up to ANALYZE_TIME_BUDGET_SECONDS (1h) active
# plus up to GPU_PAUSE_MAX_TOTAL_SECONDS (4h) paused, plus extract/discover.
# A row still "unfinished" past this is not a real in-progress run -- it's
# orphaned (e.g. the process was killed with SIGKILL, which skips
# track_run's finally block that would otherwise set finished_at) -- ignore
# it rather than showing a progress bar stuck forever.
PROGRESS_STALE_HOURS = 6


def get_pipeline_progress() -> dict:
    """A stage is "currently running" iff its pipeline_runs row hasn't been
    finished yet (see runs.py). Drives the live status banner -- polled via
    htmx so it updates without a page reload.

    Always returns a dict: the banner also carries the manual pause control
    (control.py), which must be reachable and visible whether or not a run
    is currently in flight. `running` says whether there's live progress to
    show; `paused` is the state of the user's pause switch."""
    manually_paused = control.is_paused()

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT stage, started_at, stats FROM pipeline_runs WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    idle = {
        "running": False,
        "paused": manually_paused,
        "next_run": settings.pipeline_run_time,
    }
    if not row:
        return idle

    try:
        stats = json.loads(row["stats"] or "{}")
    except (json.JSONDecodeError, TypeError):
        stats = {}

    started_at = datetime.fromisoformat(row["started_at"])
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    too_old = (datetime.now(timezone.utc) - started_at).total_seconds() > PROGRESS_STALE_HOURS * 3600
    # A manual pause has no time cap, so a legitimately-held run can outlive
    # PROGRESS_STALE_HOURS -- don't mistake it for an orphan while it's
    # actually parked on the pause switch. (If the process was killed mid-
    # pause, resuming clears the exemption and the orphan check applies again.)
    if too_old and not (manually_paused and stats.get("manual_paused")):
        return idle

    stage = row["stage"]
    done = stats.get("extracted") if stage == "extract" else stats.get("analyzed")
    total = stats.get("candidates")
    percent = None
    if isinstance(done, int) and isinstance(total, int) and total > 0:
        percent = round(min(done, total) / total * 100)

    counts = f"{done} / {total}" if isinstance(done, int) and isinstance(total, int) else None
    if stage == "analyze" and stats.get("gpu_paused") and not manually_paused:
        detail = "paused -- GPU is busy with something else"
    else:
        detail = counts

    return {
        "running": True,
        "paused": manually_paused,
        "stage": stage,
        "label": STAGE_LABELS.get(stage, stage),
        "detail": detail,
        "detail_counts": counts,  # the paused banner phrases this itself
        "percent": percent,
        "next_run": settings.pipeline_run_time,
    }


templates.env.globals["pipeline_progress"] = get_pipeline_progress
templates.env.globals["markets_tabs"] = lambda: markets_registry.TABS
templates.env.globals["ai_enabled"] = lambda: ai.mode() == ai.MODE_ENABLED


_has_analysis_cache: dict = {"at": 0.0, "value": None}


def has_analysis() -> bool:
    """Whether any analysis exists at all.

    Decides whether the category tabs are worth showing. Turning AI off does
    not delete what it already produced, so an archive that was analysed and
    then unplugged should keep its category tabs -- they still hold real
    content. Only an installation that has never had a model gets them hidden.
    """
    import time
    now = time.monotonic()
    if _has_analysis_cache["value"] is not None and (now - _has_analysis_cache["at"]) < 300:
        return _has_analysis_cache["value"]
    conn = get_connection()
    try:
        value = conn.execute("SELECT 1 FROM article_analysis LIMIT 1").fetchone() is not None
    except Exception:
        value = False
    finally:
        conn.close()
    _has_analysis_cache.update(at=now, value=value)
    return value


templates.env.globals["has_analysis"] = has_analysis
templates.env.globals["ai_status"] = ai.status


def pretty_date(value: str) -> str:
    """2026-08-01 -> 1st August 2026. Dates the reader chose deserve reading
    like prose; ISO stays where machines compare them."""
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        return value
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suffix} {d.strftime('%B %Y')}"


templates.env.filters["pretty_date"] = pretty_date
templates.env.globals["markets_periods"] = lambda: list(markets_registry.PERIOD_DAYS)

# Newest first, everywhere articles are listed. published_ts is the feed's own
# timestamp normalised to ISO; discovered_at covers anything that arrived
# without one (Hacker News items, feeds with no date).
NEWEST_FIRST = " ORDER BY COALESCE(a.published_ts, a.discovered_at) DESC"

# LEFT JOIN, not JOIN: without a model there are no article_analysis rows, and
# an inner join left every news tab completely empty rather than degraded.
# _article_card.html is already fully conditional -- importance, scope,
# categories and the whole analysis block sit behind {% if %} -- so a row with
# nothing joined renders as outlet mark, headline and date, linking out to the
# publisher. That is the reader view, and it needs no template change.
_ARTICLE_SELECT = """
    SELECT a.id, a.title, a.url, a.outlet, a.published_at,
           an.summary, an.interpretation, an.narrative_critique, an.scope,
           an.categories, an.importance_score{extra}
    FROM articles a
    LEFT JOIN article_analysis an ON an.article_id = a.id
"""

# With a model, the tabs deliberately show the curated subset -- roughly 40
# analysed articles a day out of ~400 collected. Dropping the status filter
# outright would bury those 40 in 400 bare headlines, so the filter follows the
# mode rather than being removed.
_WHERE_ANALYZED = " WHERE a.status = 'analyzed'"
_WHERE_READABLE = " WHERE a.status IN ('analyzed', 'extracted', 'stale')"

# Bounded slice of the article body, used for place-name scope matching.
GEO_BODY_SQL = "COALESCE(substr(a.full_text, 1, 4000), an.summary, '')"


def article_query(extra_cols: str = "") -> str:
    """The base article query for the current AI mode.

    `extra_cols` is appended to the SELECT list (leading comma included by the
    caller) so a query that genuinely needs the article body can ask for it
    without every list view paying to carry full text it never renders.
    """
    return _ARTICLE_SELECT.format(extra=extra_cols) + (
        _WHERE_ANALYZED if ai.mode() == ai.MODE_ENABLED else _WHERE_READABLE
    )


def with_comments(articles: list[dict]) -> dict:
    """Discussion for a page of articles, fetched in one query."""
    return comments_mod.comments_for([a["id"] for a in articles])


def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["categories_list"] = json.loads(d.get("categories") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["categories_list"] = []
    return d


def parse_outlets(raw: str) -> list[str]:
    """Selected outlets from the query string, validated against outlets that
    actually exist so a hand-edited URL can't inject anything."""
    if not raw:
        return []
    known = {o["outlet"] for o in outlets.outlets_with_logos()}
    return [o for o in (x.strip() for x in raw.split(",")) if o in known]


def outlet_clause(selected: list[str]) -> tuple[str, tuple]:
    if not selected:
        return "", ()
    placeholders = ",".join("?" * len(selected))
    return f" AND a.outlet IN ({placeholders})", tuple(selected)


def fetch_by_scope(scope: str, selected_outlets: list[str], limit: int = 40) -> list[dict]:
    """Articles for the Local / Country / Global tabs.

    Scope comes from place names in the text (see geo.py), not from the
    model's `scope` field, which labels stories relative to their own
    geography and put Florida primaries in the Local tab.
    """
    # Match against the article's own text, not the model's summary: this has
    # to work with no analysis at all, and the body is strictly more text to
    # match on than a summary even when analysis is present. Bounded because a
    # place name that decides where a story belongs appears early, and the
    # classifier caps body hits anyway -- no reason to drag whole articles
    # through a list view.
    clause, params = geo.sql_prefilter(scope, ("a.title", GEO_BODY_SQL))
    outlet_clause_sql, outlet_params = outlet_clause(selected_outlets)
    conn = get_connection()
    try:
        # Over-fetch: the SQL clause is a loose prefilter and the precise
        # check happens below, so ask for more rows than we intend to show.
        rows = conn.execute(
            article_query(f", {GEO_BODY_SQL} AS geo_body")
            + clause + outlet_clause_sql + NEWEST_FIRST + " LIMIT ?",
            params + outlet_params + (limit * 6,),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        article = _row_to_dict(row)
        if geo.classify(article["title"], article.get("geo_body") or "") != scope:
            continue
        article.pop("geo_body", None)
        out.append(article)
        if len(out) >= limit:
            break
    return out


def fetch_articles(where_extra: str = "", params: tuple = (), limit: int = 40) -> list[dict]:
    conn = get_connection()
    try:
        query = article_query() + where_extra + NEWEST_FIRST + " LIMIT ?"
        rows = conn.execute(query, params + (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def today(request: Request, outlets_param: str = Query("", alias="outlets")):
    conn = get_connection()
    try:
        brief_row = conn.execute(
            "SELECT * FROM daily_briefs WHERE period = 'morning' ORDER BY date DESC LIMIT 1"
        ).fetchone()

        # While a pipeline is actively running, surface articles as they're
        # analyzed rather than making the user wait for derive() to fold
        # them into a freshly reassembled brief -- excludes whatever's
        # already shown in the current brief so nothing appears twice.
        just_analyzed = []
        if get_pipeline_progress()["running"]:
            existing_ids = set()
            if brief_row:
                try:
                    existing_ids = {
                        s["article_id"] for s in json.loads(brief_row["top_stories"] or "[]") if s.get("article_id")
                    }
                except (json.JSONDecodeError, TypeError):
                    pass
            today_utc = datetime.now(timezone.utc).date().isoformat()
            rows = conn.execute(
                article_query() + " AND an.analyzed_at >= ? ORDER BY an.analyzed_at DESC LIMIT 10",
                (today_utc,),
            ).fetchall()
            just_analyzed = [_row_to_dict(r) for r in rows if r["id"] not in existing_ids]
    finally:
        conn.close()

    selected = parse_outlets(outlets_param)

    if brief_row:
        stories = json.loads(brief_row["top_stories"] or "[]")
        learn_today = json.loads(brief_row["learn_today"] or "null")

        # A brief story is a cluster covering several outlets, so the filter
        # means "stories this outlet covered" rather than "stories from this
        # outlet" -- keep a story if any selected outlet is among its sources.
        if selected:
            chosen = set(selected)
            stories = [s for s in stories if chosen & set(s.get("outlets") or [])]
            just_analyzed = [a for a in just_analyzed if a.get("outlet") in chosen]
            learn_today = learn_today if (learn_today or {}).get("outlet") in chosen else None

        return templates.TemplateResponse(
            request, "brief.html",
            {
                "section": "news", "active": "today", "brief_date": brief_row["date"], "stories": stories,
                "learn_today": learn_today, "just_analyzed": just_analyzed,
                "comments": with_comments(just_analyzed),
                "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected,
            },
        )

    # Fallback: no brief assembled yet (e.g. before the first 07:00 run) --
    # raw top-N by importance, same as before the Derive stage existed. This
    # already reflects live analyze() progress, since fetch_articles() reads
    # whatever's currently status='analyzed', committed per-article.
    clause, oparams = outlet_clause(selected)
    articles = fetch_articles(clause, oparams, limit=20)
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "active": "today",
         "page_title": "Today (top stories, all scopes) -- brief not yet assembled",
         "articles": articles, "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/partials/progress", response_class=HTMLResponse)
def progress_partial(request: Request):
    return templates.TemplateResponse(request, "_progress_banner.html", {"progress": get_pipeline_progress()})


# The pause switch. These are POSTs with no auth or CSRF token, matching the
# rest of this dashboard (DASHBOARD_HOST is 0.0.0.0, so anything on the LAN
# can reach them) -- the blast radius is "someone stops/starts local news
# analysis", but if this ever gets exposed beyond the LAN it needs real auth.
#
# Pause is deliberately synchronous even though releasing the GPU takes up to
# ~10s: the response is then proof the GPU is actually free, rather than a
# promise the user has no way to check. The button shows a "Freeing GPU..."
# state for the duration (see _progress_banner.html).
@app.post("/control/pause", response_class=HTMLResponse)
def pause_pipeline(request: Request):
    state = control.pause()
    progress = get_pipeline_progress()
    progress["gpu_freed"] = state.get("gpu_freed", False)
    return templates.TemplateResponse(request, "_progress_banner.html", {"progress": progress})


@app.post("/control/resume", response_class=HTMLResponse)
def resume_pipeline(request: Request):
    control.resume()
    return templates.TemplateResponse(request, "_progress_banner.html", {"progress": get_pipeline_progress()})


@app.get("/local", response_class=HTMLResponse)
def local(request: Request, outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    articles = fetch_by_scope("local", selected)
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "active": "local", "page_title": "Local", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/country", response_class=HTMLResponse)
def country(request: Request, outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    articles = fetch_by_scope("country", selected)
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "active": "country", "page_title": "Country", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/global", response_class=HTMLResponse)
def global_(request: Request, outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    articles = fetch_by_scope("global", selected)
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "active": "global", "page_title": "Global", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/economics", response_class=HTMLResponse)
def economics(request: Request, outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    clause, oparams = outlet_clause(selected)
    articles = fetch_articles(
        " AND EXISTS (SELECT 1 FROM json_each(an.categories) WHERE value = ?)" + clause,
        ("economics",) + oparams,
    )
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "needs_ai": True, "active": "economics", "page_title": "Economics", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/corporate", response_class=HTMLResponse)
def corporate(request: Request, outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    clause, oparams = outlet_clause(selected)
    articles = fetch_articles(
        " AND EXISTS (SELECT 1 FROM json_each(an.categories) WHERE value = ?)" + clause,
        ("corporate",) + oparams,
    )
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "needs_ai": True, "active": "corporate", "page_title": "Corporate", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/crypto", response_class=HTMLResponse)
def crypto(request: Request, outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    clause, oparams = outlet_clause(selected)
    articles = fetch_articles(
        " AND EXISTS (SELECT 1 FROM json_each(an.categories) WHERE value = ?)" + clause,
        ("bitcoin",) + oparams,
    )
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "needs_ai": True, "active": "crypto", "page_title": "Crypto / Bitcoin", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/science", response_class=HTMLResponse)
def science(request: Request, outlets_param: str = Query("", alias="outlets")):
    conn = get_connection()
    try:
        selected = parse_outlets(outlets_param)
        clause, oparams = outlet_clause(selected)
        query = (article_query()
                 + " AND EXISTS (SELECT 1 FROM json_each(an.categories) WHERE value LIKE 'science%')"
                 + clause
                 + NEWEST_FIRST + " LIMIT ?")
        rows = conn.execute(query, oparams + (40,)).fetchall()
        articles = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "article_list.html",
        {"section": "news", "needs_ai": True, "active": "science", "page_title": "Science & Maths", "articles": articles,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected},
    )


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request, q: str = "", outlets_param: str = Query("", alias="outlets")):
    selected = parse_outlets(outlets_param)
    clause, oparams = outlet_clause(selected)
    conn = get_connection()
    try:
        if q:
            rows = conn.execute(
                """SELECT a.id, a.title, a.url, a.outlet, a.published_at,
                          an.summary, an.interpretation, an.narrative_critique, an.scope,
                          an.categories, an.importance_score
                   FROM articles_fts f
                   JOIN articles a ON a.id = f.rowid
                   LEFT JOIN article_analysis an ON an.article_id = a.id
                   WHERE articles_fts MATCH ?""" + clause + NEWEST_FIRST + " LIMIT 40",
                (q,) + oparams,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT a.id, a.title, a.url, a.outlet, a.published_at,
                          an.summary, an.interpretation, an.narrative_critique, an.scope,
                          an.categories, an.importance_score
                   FROM articles a
                   LEFT JOIN article_analysis an ON an.article_id = a.id
                   WHERE 1=1""" + clause + NEWEST_FIRST + " LIMIT 40",
                oparams,
            ).fetchall()
        articles = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "archive.html", {"section": "news", "active": "archive", "articles": articles, "query": q,
         "comments": with_comments(articles),
         "outlet_options": outlets.outlets_with_logos(), "selected_outlets": selected}
    )


# ---------------------------------------------------------------------------
# Markets pages
#
# The server renders a shell; the instrument grid is drawn client-side from
# /api/markets/{tab}. That split is inherited from the source project and
# kept deliberately -- the charts are a real JS application (Plotly, a detail
# modal, date and moving-average controls), while the news half stays
# server-rendered with htmx. They don't interact.
# ---------------------------------------------------------------------------

@app.get("/markets", response_class=HTMLResponse)
def markets_home(request: Request, period: str = Query("")):
    return markets_page(request, markets_registry.TABS[0]["id"], period)


@app.get("/markets/{tab_id}", response_class=HTMLResponse)
def markets_page(request: Request, tab_id: str, period: str = Query("")):
    tab = markets_registry.tab_by_id(tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail=f"unknown markets tab '{tab_id}'")
    if period not in markets_registry.PERIOD_DAYS:
        period = markets_registry.DEFAULT_PERIOD
    # Both of these are plain database reads of what the pipeline already
    # wrote -- the page never triggers an LLM call, same rule as the news side.
    # Last week of notes, newest first; today's is open and the rest are
    # collapsed, so the archive doesn't push the instrument grid off-screen.
    history = market_brief.recent_briefs(tab_id, days=7)
    brief = history[0] if history else None
    links_by_date = {
        entry["date"]: correlate.group_links([
            dict(link, instrument_label=(
                (markets_registry.instrument_by_id(link["instrument_id"]) or {}).get("label")
                or link["instrument_id"]))
            for link in correlate.links_for_date(entry["date"])
            if markets_registry.GROUP_TABS.get(
                (markets_registry.instrument_by_id(link["instrument_id"]) or {}).get("group")
            ) == tab_id
        ])
        for entry in history
    }
    links = correlate.group_links([
        dict(link, instrument_label=(
            (markets_registry.instrument_by_id(link["instrument_id"]) or {}).get("label")
            or link["instrument_id"]))
        for link in correlate.links_for_date()
        # Only show a connection on the tab whose instrument it concerns.
        if markets_registry.GROUP_TABS.get(
            (markets_registry.instrument_by_id(link["instrument_id"]) or {}).get("group")
        ) == tab_id
    ])
    return templates.TemplateResponse(
        request, "markets.html",
        {
            "section": "markets", "active": tab_id, "tab": tab,
            "periods": list(markets_registry.PERIOD_DAYS),
            "period": period,
            "brief": brief, "links": links,
            "history": history, "links_by_date": links_by_date,
            "correlation_ran": correlate.correlation_ran_today(),
        },
    )


# ---------------------------------------------------------------------------
# Markets API
#
# Every one of these is a pure database read -- no provider is contacted on a
# request. Collection happens in the scheduled markets_ingest stage, so a page
# load can't sit waiting on Yahoo the way it did in the source project.
# ---------------------------------------------------------------------------

# The cross-project contract (sovereign-stack and anything else that wants a
# price). Responses carry provenance -- source, provider, endpoint, and when
# the value was collected -- so a consumer can cite where a number came from
# without reading this codebase.
@app.get("/api/markets/price/{instrument_id}")
def market_price(instrument_id: str):
    price = markets_service.get_price(instrument_id)
    if not price:
        raise HTTPException(
            status_code=404,
            detail=f"unknown instrument '{instrument_id}', or no data collected for it yet",
        )
    return JSONResponse(price)


@app.get("/api/markets/price/{instrument_id}/history")
def market_price_history(instrument_id: str, start: str = "", end: str = ""):
    history = markets_service.get_price_history(instrument_id, start, end)
    if not history:
        raise HTTPException(status_code=404, detail=f"unknown instrument '{instrument_id}'")
    return JSONResponse(history)


@app.get("/api/markets")
def markets_index():
    """What's available: tabs, instruments, and their current data sources.
    The discovery endpoint for a consumer that doesn't know the ids yet."""
    return JSONResponse({
        "tabs": markets_registry.TABS,
        "instruments": [
            {
                "id": i["id"], "label": i["label"], "group": i["group"],
                "tab": markets_registry.GROUP_TABS.get(i["group"]),
                "unit": i["unit"],
            }
            for i in markets_registry.fetchable_instruments()
        ],
    })


# Declared last on purpose: this is the catch-all of the /api/markets/* space,
# so the literal routes above must be registered before it.
@app.get("/api/markets/{tab_id}")
def markets_tab(tab_id: str, period: str = Query(markets_registry.DEFAULT_PERIOD)):
    try:
        return JSONResponse(markets_service.get_tab_data(tab_id, period))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/control/run-now", response_class=HTMLResponse)
def run_pipeline_now(request: Request):
    """Run today's chain on demand.

    The startup catch-up already covers a missed 05:30 on the same day, so
    this is for the cases it can't: the run failed, or it was paused through,
    or you want today's brief refreshed after adding a feed.
    """
    global _backfill_thread
    if _backfill_running() or get_pipeline_progress()["running"]:
        return RedirectResponse("/status", status_code=303)

    from intel_brief.scheduler import run_pipeline_chain

    _backfill_thread = threading.Thread(target=run_pipeline_chain, daemon=True, name="run-now")
    _backfill_thread.start()
    return RedirectResponse("/status", status_code=303)


@app.get("/outlet-logo/{outlet}")
def outlet_logo(outlet: str):
    """Publisher logos, served from our own cache -- the dashboard never
    hotlinks a publisher's servers."""
    logo = outlets.get_logo(outlet)
    if logo:
        data, mime = logo
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "public, max-age=86400"})
    # No usable icon: a generated square tile rather than a 404, so the row of
    # marks stays a row of marks. CoinDesk only publishes a wide wordmark, and
    # a sliver of one at icon size is worse than a clean monogram.
    return Response(content=outlets.monogram_svg(outlet), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/status", response_class=HTMLResponse)
def status(request: Request, start: str = Query(""), end: str = Query(""), day: str = Query("")):
    conn = get_connection()
    try:
        run_rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 30"
        ).fetchall()
        runs = []
        for r in run_rows:
            d = dict(r)
            try:
                d["stats_dict"] = json.loads(d.get("stats") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["stats_dict"] = {}
            runs.append(d)

        feeds = [dict(r) for r in conn.execute("SELECT * FROM feeds ORDER BY name").fetchall()]
        status_counts = [dict(r) for r in conn.execute(
            "SELECT status, COUNT(*) as n FROM articles GROUP BY status"
        ).fetchall()]
    finally:
        conn.close()

    # Market ingest health lives in the other database. Worst-first ordering
    # means a provider that started returning nothing (as bitnodes.io did when
    # it began rejecting the default urllib user-agent) surfaces at the top
    # rather than hiding among ~80 healthy series.
    try:
        mconn = markets_store.get_connection()
        try:
            market_health = markets_store.fetch_health(mconn)
        finally:
            mconn.close()
    except Exception as e:
        market_health = []
        log.warning("markets health unavailable (%s): %s", type(e).__name__, e)

    coverage = backfill_mod.day_coverage(start=start, end=end)
    rate = _seconds_per_article()
    selected = backfill_mod.day_detail(day) if day else None
    if selected:
        selected["estimated_minutes"] = round(
            backfill_mod.estimated_seconds(selected["pending"], rate) / 60)
        # A day bigger than one run can finish: say so rather than promising a
        # complete day and stopping short.
        selected["exceeds_one_run"] = selected["estimated_minutes"] > MAX_BACKFILL_MINUTES
        selected["max_run_minutes"] = MAX_BACKFILL_MINUTES
    return templates.TemplateResponse(
        request, "status.html",
        {
            "section": "status", "active": "status", "runs": runs, "feeds": feeds,
            "status_counts": status_counts, "market_health": market_health,
            "coverage": coverage, "coverage_max": max((d["collected"] for d in coverage), default=1),
            "archive": backfill_mod.archive_totals(start=start, end=end),
            "seconds_per_article": rate,
            "range_start": start or backfill_mod.archive_start(),
            "range_end": end or datetime.now(timezone.utc).date().isoformat(),
            "selected_day": selected,
            "todays_run": todays_run(),
            "scheduled_time": settings.pipeline_run_time,
            "backfill_running": _backfill_running(),
            "pipeline_running": get_pipeline_progress()["running"],
        },
    )


def todays_run() -> dict:
    """Whether today's chain has completed, for the Status panel.

    Catch-up only ever covers *today* -- a missed previous day is gone,
    because discover() reads what the feeds serve now and RSS keeps no
    history. So this reports one day, not a backlog of runs to make up.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT finished_at FROM pipeline_runs
                WHERE stage = 'derive' AND ok = 1 AND finished_at >= ?
                ORDER BY finished_at DESC LIMIT 1""",
            (today,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"done": False}
    finished = datetime.fromisoformat(row["finished_at"])
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return {
        "done": True,
        "at": finished.astimezone(ZoneInfo(settings.user_timezone)).strftime("%H:%M"),
    }


def _seconds_per_article() -> float:
    """Observed analysis rate, so the page can say what a given run buys
    instead of quoting a number from a comment."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT stats FROM pipeline_runs
                WHERE stage IN ('analyze','backfill') AND ok = 1 AND stats IS NOT NULL
                ORDER BY id DESC LIMIT 8"""
        ).fetchall()
    finally:
        conn.close()
    total_seconds = total_articles = 0.0
    for row in rows:
        try:
            st = json.loads(row["stats"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        done = (st.get("analyzed") or 0)
        if done and st.get("duration_seconds"):
            total_seconds += st["duration_seconds"] - (st.get("paused_seconds") or 0)
            total_articles += done
    return round(total_seconds / total_articles, 1) if total_articles else 95.0


# Hard ceiling on a single run. Eight hours is already an unattended
# overnight job; anything longer should be a decision made twice.
MAX_BACKFILL_MINUTES = 480


# Backfill runs in a thread so the click returns immediately -- it's a
# half-hour job. Progress goes through pipeline_runs like every other stage,
# so the existing banner reports it with no extra plumbing.
_backfill_thread: threading.Thread | None = None


def _backfill_running() -> bool:
    return _backfill_thread is not None and _backfill_thread.is_alive()


@app.post("/control/backfill", response_class=HTMLResponse)
def start_backfill(request: Request, day: str = Query(""), minutes: int = Query(0),
                   whole_day: int = Query(0)):
    global _backfill_thread
    if _backfill_running():
        # One at a time: two passes over the same day would race on status
        # updates and double-spend the GPU they're trying to conserve.
        return RedirectResponse("/status", status_code=303)
    if get_pipeline_progress()["running"]:
        # The daily chain is mid-run. Catch-up is the low-priority job by
        # definition, so it waits rather than competing with today's news for
        # the same GPU.
        return RedirectResponse("/status", status_code=303)

    # `whole_day` sizes the budget to what that day actually needs, so a
    # single-day burst finishes the day instead of stopping partway through it.
    if whole_day and day:
        detail = backfill_mod.day_detail(day)
        pending = (detail or {}).get("pending", 0)
        budget = backfill_mod.estimated_seconds(pending, _seconds_per_article())
    else:
        budget = (minutes or 30) * 60
    # Clamped either way: this is the control that spends hours of GPU, so a
    # hand-edited URL shouldn't be able to ask for a week of it.
    budget = max(5 * 60, min(budget, MAX_BACKFILL_MINUTES * 60))

    def run():
        try:
            result = backfill_mod.backfill(day or None, budget_seconds=budget)
            log.info("backfill finished: %s", dict(result))
        except Exception:
            log.exception("backfill failed")

    _backfill_thread = threading.Thread(target=run, daemon=True, name="backfill")
    _backfill_thread.start()
    # Back to the day that was just started, so the page shows its progress.
    return RedirectResponse(f"/status?day={day}" if day else "/status", status_code=303)
