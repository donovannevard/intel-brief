"""pipeline_runs logging -- one row per stage invocation, surfaced on the
Status page. The discipline is that every run's stats are logged, not just
success or failure: a stage that "succeeded" having done nothing is the
failure mode worth catching, and it is invisible from a boolean.

The row is inserted at the START of a stage (finished_at/ok still NULL),
which doubles as the live-progress signal the dashboard polls: a stage is
"currently running" iff a pipeline_runs row exists with finished_at IS
NULL. RunStats.flush() lets a long stage (analyze) push its current
counters into that row's `stats` column mid-run, without marking it
finished, so the dashboard can show real progress instead of just
before/after."""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from intel_brief.db import get_connection


class RunStats(dict):
    """A plain dict (existing call sites just do stats["x"] = y, unchanged)
    that can also push its current contents to the DB mid-run."""

    def __init__(self, run_id: int):
        super().__init__()
        self._run_id = run_id

    def flush(self) -> None:
        conn = get_connection()
        try:
            conn.execute("UPDATE pipeline_runs SET stats=? WHERE id=?", (json.dumps(self), self._run_id))
            conn.commit()
        finally:
            conn.close()


@contextmanager
def track_run(stage: str):
    conn = get_connection()
    started_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO pipeline_runs (stage, started_at) VALUES (?, ?)", (stage, started_at)
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()

    stats = RunStats(run_id)
    t0 = time.monotonic()
    ok = True
    try:
        yield stats
    except Exception:
        ok = False
        raise
    finally:
        stats["duration_seconds"] = round(time.monotonic() - t0, 2)
        conn = get_connection()
        conn.execute(
            "UPDATE pipeline_runs SET finished_at=?, stats=?, ok=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), json.dumps(stats), ok, run_id),
        )
        conn.commit()
        conn.close()
