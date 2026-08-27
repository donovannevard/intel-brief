"""Market digest: what actually moved, computed in plain Python from stored
prices. No LLM anywhere in this file.

This is the factual layer the LLM stages are grounded in. The model gets
this table and writes prose about it; it never sees a raw series and never
invents a number, because the numbers are all here already. That split is
the direct lesson from the news side, where a model handed thin context
confidently substituted names and facts from its training data.

Everything here is cheap -- a few hundred rows of arithmetic per tab -- so
it runs whether or not the LLM stages do, and the dashboard can show
"what moved" even when the GPU is paused.
"""

import math
from datetime import datetime, timedelta, timezone

from intel_brief.markets import registry, store

# Windows the digest reports on. 1d is noisy on its own but matters for
# "what happened overnight"; 30d is where a trend becomes visible.
CHANGE_WINDOWS = (1, 7, 14, 30)

# Trailing window for the z-score. 90 days is long enough to characterise
# "normal" for a daily series without reaching back into a different regime.
ZSCORE_WINDOW_DAYS = 90

# How far a move has to stand out before it counts as notable. 2 sigma on a
# 90-day window is a starting threshold, not a validated one -- same caveat
# class as the clustering threshold in pipeline/cluster.py.
ZSCORE_NOTABLE = 2.0

# Threshold crossings worth stating outright, because they carry meaning a
# percentage change doesn't: {instrument_id: [(level, description)]}
LEVEL_CROSSINGS: dict[str, list[tuple[float, str]]] = {
    "vix": [(20, "elevated fear"), (30, "high fear"), (40, "panic")],
    "hyspread": [(5, "credit stress"), (8, "credit crisis territory")],
}


def _pct_change(series: list[dict], days: int, bond_yield: bool) -> float | None:
    """Change over the last `days`, in percent -- or in percentage points for
    a series that is itself a rate, where a "percent change of a percent" is
    meaningless to a reader."""
    if len(series) < 2:
        return None
    last = series[-1]
    cutoff = (datetime.fromisoformat(last["ts"]) - timedelta(days=days)).strftime("%Y-%m-%d")
    prior = [p for p in series if p["ts"] <= cutoff]
    if not prior:
        return None
    before = prior[-1]["close"]
    if before is None or last["close"] is None:
        return None
    if bond_yield:
        return round(last["close"] - before, 3)
    if not before:
        return None
    return round((last["close"] - before) / before * 100, 2)


def _zscore(series: list[dict]) -> float | None:
    """How unusual the latest daily move is against the trailing window."""
    if len(series) < 20:
        return None
    cutoff = (datetime.fromisoformat(series[-1]["ts"]) - timedelta(days=ZSCORE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    window = [p["close"] for p in series if p["ts"] >= cutoff and p["close"] is not None]
    if len(window) < 20:
        return None
    daily = [b - a for a, b in zip(window, window[1:]) if a is not None and b is not None]
    if len(daily) < 10:
        return None
    mean = sum(daily) / len(daily)
    var = sum((d - mean) ** 2 for d in daily) / len(daily)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return round((daily[-1] - mean) / sd, 2)


def _is_monotonic(values: list[float]) -> bool:
    """True for series that only ever go one way -- cumulative totals like
    blockchain size on disk.

    They are at a 52-week high every single day, so reporting that as a
    notable extreme is noise: it ranked "Blockchain Size rose 0.24%" third
    among all movers on the first real run. Their *rate* of change is
    interesting; their level being a record never is.
    """
    if len(values) < 10:
        return False
    diffs = [b - a for a, b in zip(values, values[1:])]
    rising = sum(1 for d in diffs if d >= 0)
    return rising / len(diffs) > 0.97 or rising / len(diffs) < 0.03


def _extremes(series: list[dict]) -> dict:
    """Position against the 52-week range, and whether this is an all-time
    high in the data we hold (not in all history -- worth saying plainly)."""
    values = [p["close"] for p in series if p["close"] is not None]
    if not values:
        return {}
    if _is_monotonic(values):
        return {"cumulative_series": True}
    last = values[-1]
    cutoff = (datetime.fromisoformat(series[-1]["ts"]) - timedelta(days=365)).strftime("%Y-%m-%d")
    year = [p["close"] for p in series if p["ts"] >= cutoff and p["close"] is not None]
    out: dict = {}
    if year:
        hi, lo = max(year), min(year)
        out["week52_high"] = hi
        out["week52_low"] = lo
        if hi != lo:
            out["pct_of_52w_range"] = round((last - lo) / (hi - lo) * 100)
        if last >= hi:
            out["at_52w_high"] = True
        if last <= lo:
            out["at_52w_low"] = True
    if last >= max(values):
        out["highest_on_record"] = True
    if last <= min(values):
        out["lowest_on_record"] = True
    return out


def _crossings(instrument_id: str, series: list[dict]) -> list[str]:
    """Named level crossings, reported only when the level was crossed
    recently -- a VIX that has sat above 20 for a month is not news."""
    levels = LEVEL_CROSSINGS.get(instrument_id)
    if not levels or len(series) < 8:
        return []
    values = [p["close"] for p in series if p["close"] is not None]
    if len(values) < 8:
        return []
    last, week_ago = values[-1], values[-8]
    out = []
    for level, description in levels:
        if last >= level > week_ago:
            out.append(f"crossed above {level} ({description}) in the past week")
        elif last < level <= week_ago:
            out.append(f"fell back below {level} in the past week")
    return out


def instrument_digest(conn, inst: dict) -> dict | None:
    """The full fact sheet for one instrument, or None if there's no data."""
    from intel_brief.markets.ingest import _instrument_binding
    from intel_brief.markets.service import _apply_transforms

    binding = _instrument_binding(inst)
    if not binding:
        return None
    source, series_id = binding

    # Two years is enough for every window above plus the 52-week range,
    # without pulling decades of history for a one-line summary.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=760)).strftime("%Y-%m-%d")
    series = _apply_transforms(inst, store.read_prices(conn, source, series_id, cutoff))
    series = [p for p in series if p["close"] is not None]
    if len(series) < 2:
        return None

    bond_yield = inst.get("bond_yield", False)
    changes = {f"{d}d": _pct_change(series, d, bond_yield) for d in CHANGE_WINDOWS}
    entry = {
        "id": inst["id"],
        "label": inst["label"],
        "group": inst["group"],
        "unit": inst["unit"],
        "dp": inst["dp"],
        "latest": series[-1]["close"],
        "as_of": series[-1]["ts"],
        "change": {k: v for k, v in changes.items() if v is not None},
        "change_is_percentage_points": bond_yield,
        "rising_is_bad": inst.get("invert_color", False),
        "source": source,
    }
    typical = _typical_move(series, 7, bond_yield)
    if typical:
        entry["typical_7d_move"] = round(typical, 3)
        if "7d" in entry["change"]:
            # "this move is 4x the usual weekly move for this series"
            entry["move_vs_typical"] = round(abs(entry["change"]["7d"]) / typical, 1)
    z = _zscore(series)
    if z is not None:
        entry["latest_move_zscore"] = z
    entry.update(_extremes(series))
    crossings = _crossings(inst["id"], series)
    if crossings:
        entry["crossings"] = crossings
    return entry


def _typical_move(series: list[dict], days: int, bond_yield: bool) -> float | None:
    """Median absolute `days`-move over the past year -- what "normal" looks
    like for this particular series."""
    values = [p for p in series if p["close"] is not None]
    if len(values) < 40:
        return None
    cutoff = (datetime.fromisoformat(values[-1]["ts"]) - timedelta(days=365)).strftime("%Y-%m-%d")
    window = [p for p in values if p["ts"] >= cutoff]
    if len(window) < 20:
        return None

    step = max(1, len(window) // 50)  # ~50 samples is plenty and keeps this cheap
    moves = []
    for i in range(days, len(window), step):
        before, now = window[i - days]["close"], window[i]["close"]
        if before is None or now is None:
            continue
        if bond_yield:
            moves.append(abs(now - before))
        elif before:
            moves.append(abs((now - before) / before * 100))
    if len(moves) < 5:
        return None
    moves.sort()
    return moves[len(moves) // 2] or None


def _notability(entry: dict) -> float:
    """Ranking score for "what deserves attention".

    The first version scored raw percentage move, which ranked Bitcoin
    mempool count (routinely ±150% and meaningless at that amplitude) above
    corn hitting a 52-week high. What matters is whether a move is unusual
    *for that series*, so the score is the move as a multiple of that
    series' own typical move, with bonuses for the things that carry meaning
    regardless of size: named level crossings, range extremes, records.
    """
    change = entry.get("change", {})
    move = abs(change.get("7d") or 0)
    typical = entry.get("typical_7d_move")
    score = (move / typical) if typical else 0.0

    z = abs(entry.get("latest_move_zscore") or 0)
    if z >= ZSCORE_NOTABLE:
        score += z
    if entry.get("crossings"):
        score += 10
    if entry.get("at_52w_high") or entry.get("at_52w_low"):
        score += 3
    if entry.get("highest_on_record") or entry.get("lowest_on_record"):
        score += 5
    return score


def tab_digest(conn, tab_id: str) -> dict:
    """Digest for every instrument on one tab, ranked by notability."""
    entries = []
    for inst in registry.instruments_for_tab(tab_id):
        entry = instrument_digest(conn, inst)
        if entry:
            entries.append(entry)
    entries.sort(key=_notability, reverse=True)
    return {
        "tab": tab_id,
        "instruments": entries,
        "covered": len(entries),
        "missing": len(registry.instruments_for_tab(tab_id)) - len(entries),
    }


def top_movers(conn, limit: int = 8) -> list[dict]:
    """The most notable moves across every tab -- the input to correlation."""
    entries = []
    for tab in registry.tab_ids():
        entries.extend(tab_digest(conn, tab)["instruments"])
    entries.sort(key=_notability, reverse=True)
    return entries[:limit]


def describe_move(entry: dict) -> str:
    """One plain sentence about a move.

    Used two ways, which is why it lives here: as the text embedded to find
    related articles, and as the phrasing the correlation prompt sees. Both
    need to say the same thing, and neither should be the model's own wording.
    """
    change = entry.get("change", {})
    window = "7d" if "7d" in change else next(iter(change), None)
    if window is None:
        return f"{entry['label']} is at {entry['latest']}{entry['unit']}."
    value = change[window]
    unit = " percentage points" if entry.get("change_is_percentage_points") else "%"
    direction = "rose" if value > 0 else "fell" if value < 0 else "was flat"
    days = window.rstrip("d")
    latest = f"{entry['latest']:,.{entry.get('dp', 2)}f}"
    sentence = (
        f"{entry['label']} {direction} {abs(value)}{unit} over {days} days "
        f"to {latest}{entry['unit']}."
    )
    if entry.get("crossings"):
        sentence += " " + entry["label"] + " " + entry["crossings"][0] + "."
    if entry.get("at_52w_high"):
        sentence += f" That is a 52-week high for {entry['label']}."
    elif entry.get("at_52w_low"):
        sentence += f" That is a 52-week low for {entry['label']}."
    return sentence
