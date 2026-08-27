"""Brief coverage, backfill scope, and catch-up boundaries."""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from intel_brief.db import init_db, get_connection
init_db()
from intel_brief import scheduler
from intel_brief.config import settings
from intel_brief.pipeline import backfill as bf
from intel_brief.pipeline.derive import _with_outlet_coverage

# --- the brief shows every outlet, not just the loudest ---
stories = ([{"importance_score": 9 - i, "outlets": ["DW"], "title": f"DW {i}"} for i in range(12)]
           + [{"importance_score": 2, "outlets": ["BBC"], "title": "BBC story"}])
out = _with_outlet_coverage(stories)
assert "BBC" in {o for s in out for o in s["outlets"]}
assert any(s.get("included_for_coverage") == ["BBC"] for s in out)
print("1. an outlet ranking left out is added back to the brief")
assert len(_with_outlet_coverage([{"importance_score": 9 - i, "outlets": ["DW", "BBC"], "title": f"s{i}"} for i in range(14)])) == 12
print("2. no padding when the ranked stories already cover everyone")

# --- backfill scope ---
conn = get_connection()
conn.execute("DELETE FROM articles WHERE id > 900000")
for id, disc, status, text in [
    (900001, "2026-07-20T10:00:00+00:00", "stale", 1),       # before the archive start
    (900002, "2026-08-02T10:00:00+00:00", "stale", 1),
    (900003, "2026-08-02T11:00:00+00:00", "extracted", 1),
    (900004, "2026-08-03T10:00:00+00:00", "analyzed", 1),
    (900005, "2026-08-05T10:00:00+00:00", "stale", 0),       # no text: unfixable
]:
    conn.execute("INSERT INTO articles (id, url, title, outlet, discovered_at, status, full_text) VALUES (?,?,?,?,?,?,?)",
                 (id, f"http://x/{id}", f"Story {id} about housing policy", "BBC", disc, status, "body" if text else None))
conn.commit()
cov = {d["day"]: d for d in bf.day_coverage()}
assert "2026-07-20" not in cov
print("3. days before the archive start aren't counted as gaps")
assert cov["2026-08-02"]["pending"] == 2 and cov["2026-08-05"]["pending"] == 0
print("4. stale+extracted are backfillable; textless articles are not")
assert {c["id"] for c in bf._candidates(conn, "2026-08-02")} == {900002, 900003}
print("5. candidate query picks exactly the recoverable rows")
conn.execute("DELETE FROM articles WHERE id > 900000"); conn.commit(); conn.close()

# --- catch-up: today only, never a previous day ---
TZ = ZoneInfo(settings.user_timezone)
def set_derive(*stamps):
    c = get_connection()
    c.execute("DELETE FROM pipeline_runs WHERE stage='derive'")
    for ts in stamps:
        c.execute("INSERT INTO pipeline_runs (stage, started_at, finished_at, ok) VALUES ('derive',?,?,1)", (ts, ts))
    c.commit(); c.close()

now_utc = datetime.now(timezone.utc)
set_derive()
assert scheduler._needs_catchup(min(23, datetime.now(TZ).hour + 2), 0) is False
print("6. before the scheduled time, catch-up leaves it to cron")
assert scheduler._needs_catchup(0, 1) is True
print("7. past the time with nothing done today -> fires")
set_derive(now_utc.isoformat())
assert scheduler._needs_catchup(0, 1) is False
print("8. today already succeeded -> does not fire")
set_derive((now_utc - timedelta(days=1)).isoformat())
assert scheduler._needs_catchup(0, 1) is True
print("9. yesterday ran, today hasn't -> fires for TODAY")
import inspect
chain = inspect.getsource(scheduler.run_pipeline_chain)
assert "def run_pipeline_chain():" in chain and "date" not in chain.split("discover()")[0].split("def run_pipeline_chain")[1]
print("10. the chain takes no date: a previous day is unreachable by design")
c = get_connection(); c.execute("DELETE FROM pipeline_runs WHERE stage='derive'"); c.commit(); c.close()

print("\nALL CHECKS PASSED")
