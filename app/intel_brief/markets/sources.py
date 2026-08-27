"""Raw market-data fetchers -- one per upstream provider, no database, no
caching. Each returns a plain [{ts, close}] list.

**Transport failures raise; only genuinely empty payloads return [].** The
inherited code swallowed every exception into a debug log and returned [],
which made a hard failure indistinguishable from "no new data" -- when FRED
started refusing connections, the Status page said "provider returned no
rows" and gave no hint that anything was actually wrong. ingest.py catches
these and records the real reason, so a dead provider is diagnosable
without reading the journal.

These endpoints were chosen because they had already been proven to run
unattended for months without a key. The database-caching wrappers that
used to live alongside them are now intel_brief/markets/ingest.py, so these
functions do exactly one thing: talk to the provider.

Every provider here is free and needs no API key. The refresh intervals
below are per-provider rather than global because the constraints differ --
Bitnodes in particular rate-limits to ~10 requests/day/IP across all its
endpoints, which is why 6h (≈4 calls/day) is the ceiling, not a preference.

`SOURCE_*` names are the provenance identifiers written into every price
row (see store.py). They are deliberately provider names rather than
protocol names: if a series ever has to move to a different provider, the
old rows keep saying who they actually came from.
"""

import csv
import json
import logging
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO
from urllib.request import Request, urlopen

from intel_brief.config import settings

log = logging.getLogger("intel_brief.markets.sources")


def _open(url: str, timeout: int):
    """HTTP GET with an identifying User-Agent.

    Not cosmetic: bitnodes.io returns 403 to Python's default
    `Python-urllib/3.11` agent (confirmed 2026-08-19 -- the same request with
    a real UA returns 200). The inherited code used bare urlopen, so that
    series had silently stopped collecting. Identifying ourselves honestly is
    also just the right thing to do against free, unauthenticated APIs.
    """
    return urlopen(Request(url, headers={"User-Agent": settings.fetch_user_agent}), timeout=timeout)

SOURCE_YFINANCE = "yfinance"
SOURCE_FRED = "fred"
SOURCE_BLOCKCHAIN = "blockchain.com"
SOURCE_BITNODES = "bitnodes.io"
SOURCE_MEMPOOL_LN = "mempool.space/ln"
SOURCE_MEMPOOL_LN_LATEST = "mempool.space/ln-latest"

# Registry of what each provenance id actually means, mirrored into the
# markets.db `data_sources` table on every ingest so the database is
# self-describing to anything else that reads it.
SOURCE_META: dict[str, dict[str, str]] = {
    SOURCE_YFINANCE: {
        "provider": "Yahoo Finance (via the yfinance package)",
        "endpoint": "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        "notes": "Daily closes, split/dividend adjusted. Unofficial API -- yfinance "
                 "tracks Yahoo's changes; breakage shows up as empty series, not errors.",
    },
    SOURCE_FRED: {
        "provider": "Federal Reserve Bank of St. Louis (FRED)",
        "endpoint": "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}",
        "notes": "Public CSV endpoint, no API key. Monthly series lag 4-8 weeks.",
    },
    SOURCE_BLOCKCHAIN: {
        "provider": "Blockchain.com Charts API",
        "endpoint": "https://api.blockchain.info/charts/{chart}?format=json&timespan=all",
        "notes": "Bitcoin on-chain metrics, full history per request.",
    },
    SOURCE_BITNODES: {
        "provider": "Bitnodes.io",
        "endpoint": "https://bitnodes.io/api/v1/snapshots/latest/",
        "notes": "Current snapshot only -- no free historical API, so this series is "
                 "built forward locally. Rate limited to ~10 req/day/IP.",
    },
    SOURCE_MEMPOOL_LN: {
        "provider": "mempool.space Lightning statistics",
        "endpoint": "https://mempool.space/api/v1/lightning/statistics/3y",
        "notes": "Historical LN series. '3y' empirically returns the most datapoints.",
    },
    SOURCE_MEMPOOL_LN_LATEST: {
        "provider": "mempool.space Lightning statistics (latest)",
        "endpoint": "https://mempool.space/api/v1/lightning/statistics/latest",
        "notes": "Snapshot-only fields (avg fee rate, total node count) -- built "
                 "forward locally, same as Bitnodes.",
    },
}


# ---------------------------------------------------------------------------
# FRED CSV fetcher (no API key required)


# ---------------------------------------------------------------------------

def _fred_fetch(series_id: str, cutoff: str = "") -> list[dict]:
    """Fetch a FRED series from the public CSV endpoint. Returns [{ts, close}]."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        with _open(url, timeout=12) as resp:
            text = resp.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"FRED {series_id}: {type(exc).__name__}: {exc}") from exc

    rows: list[dict] = []
    reader = csv.reader(StringIO(text))
    next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        date, value = row[0], row[1]
        if value in (".", "") or not value.strip():
            continue
        if cutoff and date < cutoff:
            continue
        try:
            rows.append({"ts": date, "close": float(value)})
        except ValueError:
            pass
    return rows


# Minimum overlapping observations before a splice is trusted. Two series that
# barely meet give a scale factor fitted to noise; 24 months is a full seasonal
# cycle either side of the join.
_SPLICE_MIN_OVERLAP = 24


def _splice(old: list[dict], new: list[dict]) -> list[dict]:
    """Chain-link a retired series onto its live successor.

    Needed because providers retire series rather than extending them, and the
    replacement is usually a *different measure on a different base*: Germany's
    national CPI (DEUCPIALLMINMEI, 1955-2025) reads 75.567 for January 1996
    where the harmonised HICP that replaced it (CP0000DEM086NEST, 1996-) reads
    56.950 for the same month. Concatenating them raw draws a 25% cliff at the
    join, and a purchasing-power chart then says the currency gained a quarter
    of its value overnight.

    So the old series is rescaled onto the new one's base by the mean ratio
    across the first 12 overlapping months, and only its pre-join section is
    kept. This is chain-linking -- what statistical agencies do when a series
    is rebased -- and it is an approximation, not an identity: the two measures
    diverge slowly (old/new runs 1.3269 in 1996 to 1.2865 in 2025, ~4.9% over
    29 years). Sound for "how much purchasing power was lost since 1955",
    which is what these instruments show. Not sound for quoting a precise
    historical inflation rate, which is why the anchor is at the join rather
    than fitted across the whole overlap.
    """
    if not old or not new:
        return new or old

    new_by = {r["ts"]: r["close"] for r in new}
    pairs = [(new_by[r["ts"]], r["close"]) for r in old
             if r["ts"] in new_by and r["close"]]
    if len(pairs) < _SPLICE_MIN_OVERLAP:
        # Not enough common ground to fit a scale. Returning the live series
        # alone loses history but never invents a discontinuity.
        return new

    window = pairs[:12]
    scale = sum(n / o for n, o in window) / len(window)

    join = new[0]["ts"]
    head = [{"ts": r["ts"], "close": r["close"] * scale}
            for r in old if r["ts"] < join]
    return head + new


def _fred_series(series_id: str, history_id: str = "", cutoff: str = "") -> list[dict]:
    """A FRED level series, optionally extended backwards through a retired
    predecessor. Every FRED level read goes through here so that the splice
    applies uniformly to raw, YoY and purchasing-power instruments alike."""
    rows = _fred_fetch(series_id, cutoff=cutoff)
    if not history_id:
        return rows
    return _splice(_fred_fetch(history_id, cutoff=cutoff), rows)


def _fred_yoy(series_id: str, history_id: str = "", cutoff: str = "") -> list[dict]:
    """Fetch a FRED level series and return 12-month rolling YoY % change."""
    base_cutoff = (
        datetime.strptime(cutoff, "%Y-%m-%d") - timedelta(days=400)
    ).strftime("%Y-%m-%d") if cutoff else ""

    all_rows = _fred_series(series_id, history_id, cutoff=base_cutoff)
    if len(all_rows) < 13:
        return []

    result: list[dict] = []
    for i in range(12, len(all_rows)):
        curr = all_rows[i]["close"]
        prev = all_rows[i - 12]["close"]
        if prev:
            result.append({
                "ts":    all_rows[i]["ts"],
                "close": round((curr - prev) / prev * 100, 2),
            })

    if cutoff:
        result = [r for r in result if r["ts"] >= cutoff]
    return result


def _fred_pp(series_id: str, history_id: str = "") -> list[dict]:
    """Fetch a CPI-level series and return purchasing-power index (100 = first datapoint)."""
    all_rows = _fred_series(series_id, history_id, cutoff="")
    if not all_rows:
        return []
    base = all_rows[0]["close"]
    if not base:
        return []
    return [{"ts": r["ts"], "close": round(base / r["close"] * 100, 1)} for r in all_rows]


# ---------------------------------------------------------------------------
# Blockchain.com on-chain data fetcher (no API key required)


# ---------------------------------------------------------------------------


def _blockchain_fetch(chart: str) -> list[dict]:
    """Fetch a Bitcoin on-chain metric from Blockchain.com charts API."""
    url = f"https://api.blockchain.info/charts/{chart}?format=json&timespan=all"
    try:
        with _open(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"blockchain.com {chart}: {type(exc).__name__}: {exc}") from exc

    rows: list[dict] = []
    for pt in data.get("values", []):
        try:
            ts = datetime.fromtimestamp(int(pt["x"]), tz=timezone.utc).strftime("%Y-%m-%d")
            y = pt.get("y")
            if y is not None:
                rows.append({"ts": ts, "close": float(y)})
        except (KeyError, TypeError, ValueError):
            continue
    return rows


# ---------------------------------------------------------------------------
# Bitnodes.io reachable-node count (no API key; rate-limited ~10 req/day/IP
# across ALL endpoints). Unlike Blockchain.com, this only exposes a *current*
# snapshot — there is no free historical time series of node counts. We build
# our own history locally by recording one data point per refresh cycle;
# the 6h TTL below (persisted in market_fetch_meta, so it survives server
# restarts) keeps us at ~4 requests/day, well under the limit.


# ---------------------------------------------------------------------------


def _bitnodes_fetch() -> list[dict]:
    """Fetch the current reachable-node count from Bitnodes.io. Returns a single-point list."""
    url = "https://bitnodes.io/api/v1/snapshots/latest/"
    try:
        with _open(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"bitnodes: {type(exc).__name__}: {exc}") from exc

    total = data.get("total_nodes")
    ts = data.get("timestamp")
    if total is None or ts is None:
        return []
    date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    return [{"ts": date, "close": float(total)}]


# ---------------------------------------------------------------------------
# mempool.space Lightning Network stats (no API key required)
#
# Two shapes of data here:
#   1. True historical series (channel_count, total_capacity, tor/clearnet/
#      unannounced node splits) via /api/v1/lightning/statistics/3y — "3y" is
#      the widest range that actually returns the most datapoints (~2.5y of
#      daily history at the time of writing; the API's granularity per range
#      string is quirky and not linear, so 3y empirically wins over 1y/2y).
#   2. Snapshot-only fields (avg fee rate, total node count) that mempool.space
#      only exposes via /latest, no historical endpoint — same situation as
#      Bitnodes below, so we build history forward the same way.


# ---------------------------------------------------------------------------


def _mempool_ln_fetch(field: str) -> list[dict]:
    """Fetch a Lightning Network historical metric from mempool.space (max available range)."""
    url = "https://mempool.space/api/v1/lightning/statistics/3y"
    try:
        with _open(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"mempool.space ln {field}: {type(exc).__name__}: {exc}") from exc

    rows: list[dict] = []
    for pt in data:
        try:
            ts = datetime.fromtimestamp(int(pt["added"]), tz=timezone.utc).strftime("%Y-%m-%d")
            y = pt.get(field)
            if y is not None:
                rows.append({"ts": ts, "close": float(y)})
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["ts"])
    return rows
def _mempool_ln_latest_fetch(field: str) -> list[dict]:
    """Fetch a Lightning Network metric only exposed as a current snapshot
    (fee rates, total node count) from mempool.space's /latest endpoint."""
    url = "https://mempool.space/api/v1/lightning/statistics/latest"
    try:
        with _open(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"mempool.space ln-latest {field}: {type(exc).__name__}: {exc}") from exc

    latest = data.get("latest") or {}
    y     = latest.get(field)
    added = latest.get("added")
    if y is None or added is None:
        return []
    # /latest uses ISO8601 strings ("2026-07-09T00:00:00.000Z"), unlike the
    # historical endpoint's unix timestamps — just take the date portion.
    date = str(added)[:10]
    return [{"ts": date, "close": float(y)}]


# ---------------------------------------------------------------------------
# yfinance bulk fetcher


# ---------------------------------------------------------------------------

def _yf_fetch(
    tickers: list[str],
    period: str = "1mo",
    *,
    start: str | None = None,
) -> dict[str, list[dict]]:
    """Bulk-fetch daily close prices via yfinance. Returns {ticker: [{ts, close}]}."""
    import yfinance as yf

    results: dict[str, list[dict]] = {t: [] for t in tickers}
    if not tickers:
        return results

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            if start:
                data = yf.download(
                    tickers, start=start, interval="1d",
                    auto_adjust=True, progress=False, threads=True,
                )
            else:
                data = yf.download(
                    tickers, period=period, interval="1d",
                    auto_adjust=True, progress=False, threads=True,
                )
        except Exception as exc:
            log.warning("yfinance download error: %s", exc)
            return results

    if data.empty:
        return results

    for ticker in tickers:
        try:
            col = ("Close", ticker)
            if col in data.columns:
                series = data[col].dropna()
            elif "Close" in data.columns and not isinstance(data.columns, type(None)):
                # Single-ticker download returns flat columns (no MultiIndex)
                series = data["Close"].dropna()
            else:
                continue
            results[ticker] = [
                {"ts": idx.strftime("%Y-%m-%d"), "close": round(float(val), 6)}
                for idx, val in series.items()
            ]
        except Exception as exc:
            log.debug("yfinance parse %s: %s", ticker, exc)

    return results
