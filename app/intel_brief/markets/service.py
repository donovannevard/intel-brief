"""Builds the payload one Markets tab renders from -- reading the database
only, never the network.

That read-only property is the point: in the source project this same
assembly could trigger a live fetch when a cache expired, so a page load
sometimes waited on Yahoo. Ingest is now a scheduled stage, and this is
purely a query. If a series is missing here it means ingest hasn't
collected it yet (visible on the Status page), not that the page should go
and get it.

The per-instrument arithmetic -- scaling, FX inversion, period change vs
prior-close change, the bond-yield special case -- is carried over
unchanged, including the reasoning comments, because it was already tuned
against real display bugs.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from intel_brief.markets import registry, sources, store

# Short in-memory cache. Cheap insurance against a page that re-requests the
# same tab repeatedly; the database read is already fast, so this is measured
# in seconds rather than the source project's 15 minutes (which existed to
# avoid re-fetching from the network).
_CACHE_TTL_SECONDS = 60
_cache: dict[str, dict] = {}


def _binding(inst: dict) -> tuple[str, str] | None:
    from intel_brief.markets.ingest import _instrument_binding
    return _instrument_binding(inst)


def _apply_transforms(inst: dict, rows: list[dict]) -> list[dict]:
    """Convert stored provider values into display values.

    Storage keeps what the provider gave us; these two transforms are ours:

    * `scale` converts units the provider reports in but nobody reads in --
      hashes/second to EH/s, millions to billions, satoshis to BTC.
    * `invert_values` flips FX pairs quoted as USD-per-foreign into
      foreign-per-USD, so every pair on the tab moves in the same direction.

    Shared by every read path on purpose. These lived inline in the tab
    builder, so `get_price()` -- the endpoint other projects consume --
    returned raw provider values: Bitcoin hash rate as 925,210,467 "EH/s"
    instead of 925, and JPY/USD as 158.51 (the USD/JPY rate) instead of
    0.0063. Two code paths, two answers, and the wrong one was the one with
    an external consumer.
    """
    if not rows:
        return rows

    dp = inst["dp"]
    scale = inst.get("scale")
    if scale:
        rows = [{"ts": r["ts"], "close": round(r["close"] * scale, dp)} for r in rows]
    if inst.get("invert_values"):
        rows = [
            {"ts": r["ts"], "close": round(1.0 / r["close"], dp)}
            for r in rows if r["close"]
        ]
    return rows


def get_tab_data(tab_id: str, period: str = registry.DEFAULT_PERIOD) -> dict:
    if tab_id not in registry.tab_ids():
        raise ValueError(f"unknown markets tab: {tab_id}")
    period = period if period in registry.PERIOD_DAYS else registry.DEFAULT_PERIOD

    key = f"{tab_id}:{period}"
    hit = _cache.get(key)
    if hit and (time.time() - hit["ts"]) < _CACHE_TTL_SECONDS:
        return hit["data"]

    data = _build(tab_id, period)
    _cache[key] = {"ts": time.time(), "data": data}
    return data


def _build(tab_id: str, period: str) -> dict:
    days = registry.PERIOD_DAYS[period]
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    # FRED monthly series lag 4-8 weeks -- look at least 90 days back so the
    # latest monthly release is never excluded from a short-period view.
    fred_cutoff = min(cutoff, (now - timedelta(days=90)).strftime("%Y-%m-%d"))

    instruments = registry.instruments_for_tab(tab_id)
    conn = store.get_connection()
    try:
        out: list[dict] = []
        for inst in instruments:
            row: dict[str, Any] = {
                "id": inst["id"],
                "label": inst["label"],
                "group": inst["group"],
                "unit": inst["unit"],
                "dp": inst["dp"],
                "desc": inst.get("desc", ""),
                "invert_color": inst.get("invert_color", False),
                "bond_yield": inst.get("bond_yield", False),
                "no_color": inst.get("no_color", False),
                "thresholds": inst.get("thresholds", []),
                "history": [],
                "current": None,
                "first_ts": None,
                "last_ts": None,
                "change_pct": None,        # vs prior close
                "change_abs": None,
                "period_change_pct": None,  # first -> last over the period
                "period_change_abs": None,
                "source": None,
                "source_provider": None,
                "fetched_at": None,
                "error": inst.get("unavailable"),
            }

            binding = _binding(inst)
            if "unavailable" in inst or not binding:
                out.append(row)
                continue

            source, series_id = binding
            row["source"] = source
            row["series_id"] = series_id
            row["source_provider"] = sources.SOURCE_META.get(source, {}).get("provider")

            always_full = inst.get("always_full", False)
            if always_full:
                effective_cutoff = ""
            elif source == sources.SOURCE_YFINANCE:
                effective_cutoff = cutoff
            else:
                effective_cutoff = fred_cutoff

            row["history"] = store.read_prices(conn, source, series_id, effective_cutoff)
            latest = store.latest_price(conn, source, series_id)
            if latest:
                row["fetched_at"] = latest["fetched_at"]

            row["history"] = _apply_transforms(inst, row["history"])

            h = row["history"]
            if h:
                row["current"] = h[-1]["close"]
                row["first_ts"] = h[0]["ts"]
                row["last_ts"] = h[-1]["ts"]

            if len(h) >= 2:
                first, last, prev = h[0]["close"], h[-1]["close"], h[-2]["close"]
                dp = inst["dp"]
                if inst.get("bond_yield"):
                    # Yield series: absolute percentage-point change, not a
                    # percentage of a percentage. "-0.5" reads as "fell 0.5pp",
                    # far less confusing than "-13%" on a value already in %.
                    row["change_abs"] = round(last - prev, 3)
                    row["change_pct"] = round(last - prev, 3)
                    row["period_change_abs"] = round(last - first, 2)
                    row["period_change_pct"] = round(last - first, 2)
                else:
                    if prev:
                        row["change_abs"] = round(last - prev, dp)
                        row["change_pct"] = round((last - prev) / prev * 100, 2)
                    if first:
                        row["period_change_abs"] = round(last - first, dp)
                        row["period_change_pct"] = round((last - first) / first * 100, 2)

            if not h and not row["error"]:
                # Two very different states that used to read identically. A
                # series retired upstream still has years of stored history --
                # it just has nothing inside the requested window -- and saying
                # "no data collected yet" about it sent a real investigation
                # looking for a collection fault that did not exist. `latest`
                # is deliberately read without the cutoff, so it is exactly the
                # evidence needed to tell the two apart.
                if latest:
                    row["error"] = (
                        f"no observations in this period -- the upstream series stops "
                        f"at {latest['date']}"
                    )
                else:
                    row["error"] = "no data collected yet"

            # Anchor every chart to the same x-axis origin with a null sentinel
            # at the period start. Stats above are computed from real data only.
            if not always_full and row["history"] and cutoff and row["history"][0]["ts"] > cutoff:
                row["history"] = [{"ts": cutoff, "close": None}] + row["history"]

            out.append(row)
    finally:
        conn.close()

    tab = registry.tab_by_id(tab_id)
    return {
        "tab": tab_id,
        "tab_label": tab["label"],
        "tab_blurb": tab["blurb"],
        "instruments": out,
        "group_meta": registry.groups_for_tab(tab_id),
        "period": period,
        "periods": list(registry.PERIOD_DAYS),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def get_price(instrument_id: str) -> dict | None:
    """Latest close for one instrument, with provenance attached.

    This is the contract other projects (sovereign-stack) use, so the answer
    carries where the number came from and when it was collected -- enough to
    cite the source without reading this codebase.
    """
    inst = registry.instrument_by_id(instrument_id)
    if not inst:
        return None
    binding = _binding(inst)
    if not binding:
        return None
    source, series_id = binding

    conn = store.get_connection()
    try:
        latest = store.latest_price(conn, source, series_id)
        if not latest:
            return None
        display = _apply_transforms(inst, [{"ts": latest["date"], "close": latest["close"]}])
        if not display:
            return None
        meta = sources.SOURCE_META.get(source, {})
        return {
            "instrument": inst["id"],
            "label": inst["label"],
            "unit": inst["unit"],
            "date": latest["date"],
            "close": display[0]["close"],
            # What the provider actually published, before our unit scaling
            # and FX inversion -- so a consumer can audit the transform.
            "raw_close": latest["close"],
            "source": source,
            "series_id": series_id,
            "provider": meta.get("provider"),
            "endpoint": meta.get("endpoint"),
            "fetched_at": latest["fetched_at"],
        }
    finally:
        conn.close()


def get_price_history(
    instrument_id: str, start: str = "", end: str = ""
) -> dict | None:
    inst = registry.instrument_by_id(instrument_id)
    if not inst:
        return None
    binding = _binding(inst)
    if not binding:
        return None
    source, series_id = binding

    conn = store.get_connection()
    try:
        rows = _apply_transforms(inst, store.read_prices(conn, source, series_id, start))
        if end:
            rows = [r for r in rows if r["ts"] <= end]
        meta = sources.SOURCE_META.get(source, {})
        return {
            "instrument": inst["id"],
            "label": inst["label"],
            "unit": inst["unit"],
            "source": source,
            "series_id": series_id,
            "provider": meta.get("provider"),
            "endpoint": meta.get("endpoint"),
            "source_history": store.source_history(conn, instrument_id),
            "count": len(rows),
            "history": rows,
        }
    finally:
        conn.close()
