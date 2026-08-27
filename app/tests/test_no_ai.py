"""intel-brief must work with no model at all.

Without AI it is a reader and an archive: headlines linking out to the
publisher, grouped by where the story is about, plus full-text search and the
computed markets figures. The model adds interpretation on top. These checks
pin that contract, and in particular pin the bug that made a no-AI run
destructive: a transport failure used to mark every article 'failed_analysis',
a status the daily run never retries.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

# Geography is configurable (LOCAL_PLACES and friends), so these assertions
# would otherwise depend on whoever's .env happens to be on the machine --
# a developer running with their own region set would see spurious failures.
# Pinned to an explicit list here: geo.py has no default region (that would
# quietly make one part of the country stand in for everyone), so these
# assertions have to name the places they are asserting about.
# load_dotenv() does not override values already in os.environ, so setting
# these before the first intel_brief import is what makes it stick.
os.environ["LOCAL_PLACES"] = (
    "London,Greater London,Hackney,Croydon,Ealing,Heathrow,Notting Hill,Wembley"
)
for _v in ("COUNTRY_TERMS", "STRONG_COUNTRY_TERMS"):
    os.environ[_v] = ""


FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def _seed(path):
    """A tiny archive: articles with real text, none analysed.

    Uses the application's own schema rather than a hand-written one, so this
    test cannot quietly drift from the real tables.
    """
    from intel_brief.db import init_db
    init_db()
    conn = sqlite3.connect(path)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("Hackney council approves new London housing plan",
         "Hackney Council has approved a plan for London. " * 20, "The Standard"),
        ("Bank of England holds rates as UK inflation cools",
         "The Bank of England said Westminster expects UK inflation. " * 20, "BBC"),
        ("Typhoon makes landfall in the Philippines",
         "Authorities in Manila evacuated thousands of residents. " * 20, "Al Jazeera"),
    ]
    for i, (title, text, outlet) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO articles (id, title, url, outlet, full_text, status, discovered_at, published_ts, published_at)"
            " VALUES (?,?,?,?,?, 'extracted', ?, ?, ?)",
            (i, title, f"https://example.invalid/{i}", outlet, text, now, now, now))
    conn.commit()
    conn.close()


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "intel.db")

    # Set before anything imports config: Settings is frozen and loaded once,
    # which is correct for a service (reconfiguring means a restart) but means
    # a test has to decide the environment up front.
    os.environ["DB_PATH"] = db
    os.environ["MARKETS_DB_PATH"] = os.path.join(tmp, "markets.db")
    os.environ["LLM_BASE_URL"] = ""
    os.environ["GPU_GUARD"] = "off"

    _seed(db)

    from intel_brief import ai
    ai.invalidate()

    print("AI mode with no endpoint configured")
    check("reports 'disabled', not 'degraded'", ai.mode() == ai.MODE_DISABLED, ai.mode())
    check("analyze gate is closed", ai.available("analyze") is False)

    print("\nThe reader view renders without analysis")
    from intel_brief.web import app as web
    for scope, expected_title in (("local", "Hackney"), ("country", "Bank of England")):
        rows = web.fetch_by_scope(scope, [])
        check(f"/{scope} returns articles", len(rows) > 0, f"got {len(rows)}")
        check(f"/{scope} picks the right story",
              any(expected_title.split()[0] in r["title"] for r in rows),
              [r["title"][:40] for r in rows])
        if rows:
            check(f"/{scope} rows carry a link", all(r.get("url") for r in rows))
            check(f"/{scope} rows carry no analysis", all(not r.get("summary") for r in rows))
            check(f"/{scope} does not leak geo_body", all("geo_body" not in r for r in rows))

    print("\nGeographic scope works off article text, not the model")
    from intel_brief import geo
    check("London story classifies local",
          geo.classify("Hackney council approves new London housing plan", "") == "local")
    check("UK story classifies country",
          geo.classify("Bank of England holds rates as UK inflation cools", "") == "country")

    print("\nA dead endpoint must not burn the queue")
    from intel_brief.config import settings
    object.__setattr__(settings, "llm_base_url", "http://127.0.0.1:9/v1")
    ai.invalidate()
    check("reports 'degraded', not 'disabled'", ai.mode() == ai.MODE_DEGRADED, ai.mode())

    conn = sqlite3.connect(db)
    before = dict(conn.execute("SELECT status, count(*) FROM articles GROUP BY status").fetchall())
    conn.close()

    from intel_brief.pipeline.analyze import analyze
    try:
        analyze()
    except Exception as e:                      # must not propagate
        check("analyze() does not raise", False, f"{type(e).__name__}: {e}")

    conn = sqlite3.connect(db)
    after = dict(conn.execute("SELECT status, count(*) FROM articles GROUP BY status").fetchall())
    conn.close()

    check("nothing marked failed_analysis", after.get("failed_analysis", 0) == 0, after)
    check("articles stay retryable",
          after.get("extracted", 0) == before.get("extracted", 0), f"{before} -> {after}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
