"""Market data ingest -- the only place that talks to price providers.

In the source project, fetching happened lazily inside the dashboard's
request handler whenever a 6-hour cache expired, so an unlucky page load sat
waiting on Yahoo and FRED. Here it's a scheduled stage instead, which makes
page loads pure database reads -- the same principle intel-brief already
holds for news ("page loads never trigger LLM calls").

Runs on its own schedule (MARKETS_INGEST_INTERVAL_HOURS, default 6) as well
as once inside the daily pipeline chain before the LLM stages. It's
network-only and never touches the GPU, so it deliberately does *not* honour
the manual pause switch: pausing means "release the GPU for something else",
and holding price collection would be an unrelated punishment.

Catch-up after downtime is inherent rather than engineered: yfinance, FRED
and blockchain.com all serve full history on request, so a missed window is
repaired by the next run. The two snapshot-only series (Bitnodes node count,
mempool.space LN latest) can't be backfilled by anyone -- no free historical
API exists -- so they gain a permanent gap for the downtime. That's a
property of the upstream data, not of this code.

Every write records its provenance (see store.py). A registry change that
repoints an instrument at a different provider is logged as a source switch
the first time this runs after the change.
"""

import logging
import time

from intel_brief.markets import registry, sources, store
from intel_brief.pipeline.runs import track_run

log = logging.getLogger("intel_brief.markets.ingest")

# Per-source refresh intervals.
#
# Bitnodes' is a hard constraint: ~10 requests/day/IP across all its
# endpoints, so 6h (≈4/day) leaves headroom.
#
# FRED's 24h is the result of getting this wrong. Three full ingests inside
# an hour (a live run plus two test runs, ~100 requests from one IP) got the
# IP refused by FRED's edge -- connections failing in ~0.1s where they had
# been succeeding in 0.4s. Nothing here publishes intraday: FRED series are
# daily at their fastest and quarterly or annual at their slowest, so
# refetching every 6h was buying nothing and spending goodwill. One fetch a
# day per series, spaced by FRED_REQUEST_DELAY_SECONDS, is well within what
# a free public endpoint should be asked for.
TTL_HOURS: dict[str, float] = {
    sources.SOURCE_YFINANCE: 6,
    sources.SOURCE_FRED: 24,
    sources.SOURCE_BLOCKCHAIN: 12,
    sources.SOURCE_BITNODES: 6,
    sources.SOURCE_MEMPOOL_LN: 12,
    sources.SOURCE_MEMPOOL_LN_LATEST: 6,
}

# Space out sequential requests to the same host. ~35 FRED series at 1.5s
# adds under a minute to a background job that runs a few times a day --
# an irrelevant cost here, and the difference between a burst and a trickle
# from the provider's side. yfinance isn't listed because it's fetched in
# one bulk call, not per-instrument.
REQUEST_DELAY_SECONDS: dict[str, float] = {
    sources.SOURCE_FRED: 1.5,
    sources.SOURCE_BLOCKCHAIN: 1.5,
    sources.SOURCE_MEMPOOL_LN: 1.0,
    sources.SOURCE_MEMPOOL_LN_LATEST: 1.0,
}

# yfinance is fetched in bulk (one download for many tickers) rather than
# per-instrument -- that's how the source project avoided rate-limiting, and
# it stays that way here.
YF_INITIAL_PERIOD = "10y"


def _instrument_binding(inst: dict) -> tuple[str, str] | None:
    """(source, storage key) for an instrument, or None if it has no upstream.

    Instruments without a source are the documented gaps -- CoinJoin order
    books, exchange netflows -- that need a paid provider or your own node.

    The `#pp` / `#yoy` suffixes matter: four FRED CPI series each back two
    different instruments, one showing a purchasing-power index and one
    showing year-over-year change (GBRCPIALLMINMEI is both `pp_gbp` and
    `inf_uk`, and likewise for Germany, Japan and China). Both are *derived*
    locally from the same raw series, so keying storage on the raw id alone
    made them overwrite each other -- whichever was ingested second won, and
    one of the two charts then rendered the wrong transform entirely. The
    suffix keeps derived series distinct, and marks them as locally computed
    rather than as something FRED publishes under that name.
    """
    if "yf" in inst:
        return sources.SOURCE_YFINANCE, inst["yf"]
    if "fred" in inst:
        # A spliced series is our construction, not something FRED publishes
        # under either id, so the key names both halves. That also keeps it
        # from colliding with an unspliced read of the same live series.
        key = inst["fred"]
        if inst.get("fred_history"):
            key = f"{key}+{inst['fred_history']}"
        if inst.get("purchasing_power"):
            return sources.SOURCE_FRED, f"{key}#pp"
        if inst.get("yoy"):
            return sources.SOURCE_FRED, f"{key}#yoy"
        return sources.SOURCE_FRED, key
    if "blockchain" in inst:
        return sources.SOURCE_BLOCKCHAIN, inst["blockchain"]
    if inst.get("bitnodes"):
        return sources.SOURCE_BITNODES, "node_count"
    if "mempool_ln" in inst:
        return sources.SOURCE_MEMPOOL_LN, inst["mempool_ln"]
    if "mempool_ln_latest" in inst:
        return sources.SOURCE_MEMPOOL_LN_LATEST, inst["mempool_ln_latest"]
    return None


def _record_bindings(conn, stats: dict) -> None:
    """Write the current instrument->provider bindings, logging any switch."""
    switches = []
    for inst in registry.fetchable_instruments():
        binding = _instrument_binding(inst)
        if not binding:
            continue
        source, series_id = binding
        if store.record_instrument_binding(conn, inst["id"], source, series_id):
            switches.append(f"{inst['id']} -> {source}:{series_id}")
            log.warning("data source changed for %s: now %s:%s", inst["id"], source, series_id)
    stats["source_switches"] = switches


def _ingest_yfinance(conn, insts: list[dict], stats: dict) -> None:
    """Bulk fetch. Incremental once a ticker has history, full on first sight."""
    tickers = [i["yf"] for i in insts]
    stale = [t for t in tickers if store.is_stale(conn, sources.SOURCE_YFINANCE, t, TTL_HOURS[sources.SOURCE_YFINANCE])]
    if not stale:
        stats["yf_skipped_fresh"] = len(tickers)
        return

    need_full, need_incremental = [], []
    for ticker in stale:
        if store.latest_date(conn, sources.SOURCE_YFINANCE, ticker):
            need_incremental.append(ticker)
        else:
            need_full.append(ticker)

    for group, kwargs in (
        (need_full, {"period": YF_INITIAL_PERIOD}),
        (need_incremental, {}),
    ):
        if not group:
            continue
        if not kwargs:
            # Start one day after the *earliest* latest-date so every ticker in
            # the batch stays in sync in a single download.
            earliest = min(
                store.latest_date(conn, sources.SOURCE_YFINANCE, t) or "1970-01-01" for t in group
            )
            from datetime import datetime, timedelta
            kwargs = {
                "start": (datetime.strptime(earliest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            }

        try:
            result = sources._yf_fetch(group, **kwargs)
        except Exception as e:  # a whole-batch failure must not kill the run
            log.warning("yfinance batch failed (%s): %s", type(e).__name__, e)
            for ticker in group:
                store.mark_fetched(conn, sources.SOURCE_YFINANCE, ticker,
                                   status="error", error=f"{type(e).__name__}: {e}")
            stats["errors"] += len(group)
            continue

        for ticker in group:
            rows = result.get(ticker, [])
            if rows:
                written = store.write_prices(conn, sources.SOURCE_YFINANCE, ticker, rows)
                store.mark_fetched(conn, sources.SOURCE_YFINANCE, ticker, status="ok", rows=written)
                stats["rows_written"] += written
                stats["series_updated"] += 1
            else:
                # An empty result from an incremental fetch is normal (no new
                # closes since last time); on a first fetch it means the ticker
                # is wrong or Yahoo dropped it, which is worth surfacing.
                first_fetch = ticker in need_full
                store.mark_fetched(
                    conn, sources.SOURCE_YFINANCE, ticker,
                    status="empty" if first_fetch else "ok", rows=0,
                    error="no data returned on first fetch -- check the symbol" if first_fetch else None,
                )
                if first_fetch:
                    stats["empty"] += 1


# Stop hitting a provider that has clearly stopped answering. Learned the
# hard way: while FRED was refusing this IP, a run worked through ~35 series
# one at a time and each one *hung* rather than failing fast -- urllib's
# timeout applies per socket read, so a connection that dribbles bytes resets
# it indefinitely. A run that should take two minutes was still going after
# thirty. Three consecutive failures from one source is enough to conclude
# the provider is down and leave the rest for the next run; the skipped
# series are deliberately not marked as fetched, so they retry then.
MAX_CONSECUTIVE_FAILURES = 3


def _ingest_single(conn, source: str, series_id: str, fetch, stats: dict, failures: dict) -> None:
    """One series from a one-at-a-time provider."""
    if failures.get(source, 0) >= MAX_CONSECUTIVE_FAILURES:
        stats["skipped_provider_down"] += 1
        return

    if not store.is_stale(conn, source, series_id, TTL_HOURS[source]):
        return

    delay = REQUEST_DELAY_SECONDS.get(source)
    if delay:
        time.sleep(delay)

    try:
        rows = fetch()
    except Exception as e:
        log.warning("%s:%s fetch failed (%s): %s", source, series_id, type(e).__name__, e)
        store.mark_fetched(conn, source, series_id, status="error", error=f"{type(e).__name__}: {e}")
        stats["errors"] += 1
        failures[source] = failures.get(source, 0) + 1
        if failures[source] == MAX_CONSECUTIVE_FAILURES:
            log.warning(
                "%s failed %d times in a row -- skipping its remaining series this run",
                source, MAX_CONSECUTIVE_FAILURES,
            )
            stats.setdefault("providers_down", []).append(source)
        return

    failures[source] = 0

    if rows:
        written = store.write_prices(conn, source, series_id, rows)
        store.mark_fetched(conn, source, series_id, status="ok", rows=written)
        stats["rows_written"] += written
        stats["series_updated"] += 1
    else:
        store.mark_fetched(conn, source, series_id, status="empty", rows=0,
                           error="provider returned no rows")
        stats["empty"] += 1


def ingest() -> dict:
    """Refresh every instrument whose data is stale. Safe to run any time."""
    with track_run("markets_ingest") as stats:
        store.init_markets_db()
        conn = store.get_connection()
        try:
            stats["rows_written"] = 0
            stats["series_updated"] = 0
            stats["errors"] = 0
            stats["empty"] = 0
            stats["skipped_provider_down"] = 0
            failures: dict[str, int] = {}
            stats.flush()

            store.record_data_sources(conn, sources.SOURCE_META)
            _record_bindings(conn, stats)

            instruments = registry.fetchable_instruments()
            stats["instruments"] = len(instruments)
            stats.flush()

            yf_insts = [i for i in instruments if "yf" in i]
            if yf_insts:
                _ingest_yfinance(conn, yf_insts, stats)
                stats.flush()

            for inst in instruments:
                if "fred" in inst:
                    # Fetch by the raw FRED id, store under the binding's key
                    # (which carries the #pp / #yoy transform marker).
                    raw = inst["fred"]
                    hist = inst.get("fred_history", "")
                    _, storage_key = _instrument_binding(inst)
                    if inst.get("purchasing_power"):
                        fetch = lambda s=raw, h=hist: sources._fred_pp(s, h)
                    elif inst.get("yoy"):
                        fetch = lambda s=raw, h=hist: sources._fred_yoy(s, h, cutoff="")
                    else:
                        fetch = lambda s=raw, h=hist: sources._fred_series(s, h, cutoff="")
                    _ingest_single(conn, sources.SOURCE_FRED, storage_key, fetch, stats, failures)

                elif "blockchain" in inst:
                    chart = inst["blockchain"]
                    _ingest_single(conn, sources.SOURCE_BLOCKCHAIN, chart,
                                   lambda c=chart: sources._blockchain_fetch(c), stats, failures)

                elif inst.get("bitnodes"):
                    _ingest_single(conn, sources.SOURCE_BITNODES, "node_count",
                                   sources._bitnodes_fetch, stats, failures)

                elif "mempool_ln" in inst:
                    field = inst["mempool_ln"]
                    _ingest_single(conn, sources.SOURCE_MEMPOOL_LN, field,
                                   lambda f=field: sources._mempool_ln_fetch(f), stats, failures)

                elif "mempool_ln_latest" in inst:
                    field = inst["mempool_ln_latest"]
                    _ingest_single(conn, sources.SOURCE_MEMPOOL_LN_LATEST, field,
                                   lambda f=field: sources._mempool_ln_latest_fetch(f), stats, failures)

                stats.flush()
        finally:
            conn.close()
        return stats
