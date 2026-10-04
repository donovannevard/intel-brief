"""The date window on the list views, and collected-but-unanalysed articles.

Two behaviours that are easy to break by accident and would be quiet when
broken. If the window stops filtering, every page shows the whole archive and
looks fine. If the list views go back to requiring analysis, a morning's
collection becomes invisible until someone runs the GPU-bound job -- which is
the exact failure the collect/analyse split exists to prevent.
"""

import os, sqlite3, sys, tempfile, pathlib
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ["LOCAL_PLACES"] = "London,Hackney,Croydon"
for _v in ("COUNTRY_TERMS", "STRONG_COUNTRY_TERMS"):
    os.environ[_v] = ""

FAILURES = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f" -- {detail}" if not cond and detail else ""))
    if not cond:
        FAILURES.append(label)


def seed(path):
    """Three articles, one per window: today, 5 days ago, 40 days ago.

    None analysed, which is also the point -- they must still be listed.
    """
    from intel_brief.db import init_db
    init_db()
    conn = sqlite3.connect(path)
    today = datetime.now(timezone.utc)
    for i, (days, title) in enumerate(
        [(0, "Hackney council approves a plan today"),
         (5, "Hackney council met five days ago"),
         (40, "Hackney council met forty days ago")], start=1,
    ):
        ts = (today - timedelta(days=days)).isoformat()
        conn.execute(
            "INSERT INTO articles (id, title, url, outlet, full_text, status,"
            " discovered_at, published_ts, published_at) VALUES (?,?,?,?,?, 'extracted', ?,?,?)",
            (i, title, f"https://example.invalid/{i}", "The Standard",
             "Hackney Council said something. " * 20, ts, ts, ts))
    conn.commit()
    conn.close()


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "intel.db")
    os.environ["DB_PATH"] = db
    os.environ["MARKETS_DB_PATH"] = os.path.join(tmp, "markets.db")
    os.environ["LLM_BASE_URL"] = ""
    os.environ["GPU_GUARD"] = "off"
    seed(db)

    from intel_brief.web import app as webapp

    print("parse_range resolves anything to a usable window")
    today = datetime.now(timezone.utc).date()
    r = webapp.parse_range("")
    check("empty defaults to today", r["key"] == "today" and r["start"] == today.isoformat())
    check("7d spans seven days inclusive",
          webapp.parse_range("7d")["start"] == (today - timedelta(days=6)).isoformat())
    check("30d spans thirty days inclusive",
          webapp.parse_range("30d")["start"] == (today - timedelta(days=29)).isoformat())
    check("all is unbounded", webapp.parse_range("all") == {"key": "all", "start": "", "end": ""})
    check("junk falls back to today rather than erroring",
          webapp.parse_range("'; DROP TABLE articles--")["key"] == "today")
    check("a non-date custom bound is ignored, not passed to SQL",
          webapp.parse_range("custom", "not-a-date", "")["start"] == "")
    back = webapp.parse_range("custom", "2026-09-10", "2026-09-01")
    check("a backwards custom range is swapped, not empty",
          (back["start"], back["end"]) == ("2026-09-01", "2026-09-10"))

    print("\nThe window filters the list views")
    from starlette.testclient import TestClient
    import re, warnings
    warnings.filterwarnings("ignore")
    client = TestClient(webapp.app, raise_server_exceptions=False)

    def titles(url):
        body = client.get(url).text
        return re.findall(r"Hackney council (?:approves a plan|met) ([a-z ]+)", body)

    check("today shows only today's", titles("/local?range=today") == ["today"],
          titles("/local?range=today"))
    check("7d adds the five-day-old one",
          sorted(titles("/local?range=7d")) == ["five days ago", "today"], titles("/local?range=7d"))
    check("30d still excludes the forty-day-old one",
          "forty days ago" not in titles("/local?range=30d"), titles("/local?range=30d"))
    check("all includes everything", len(titles("/local?range=all")) == 3, titles("/local?range=all"))
    check("a custom range selects the middle article alone",
          titles("/local?range=custom"
                 f"&from={(today - timedelta(days=7)).isoformat()}"
                 f"&to={(today - timedelta(days=2)).isoformat()}") == ["five days ago"])

    print("\nCollected-but-unanalysed articles are readable")
    # Nothing in the seed is analysed. Before the collect/analyse split these
    # pages filtered on status='analyzed' whenever a model was configured.
    check("the scope tabs list them", len(titles("/local?range=all")) == 3)
    check("the archive lists them", len(titles("/archive?range=all")) == 3)
    check("the article query does not require analysis",
          "status = 'analyzed'" not in webapp.article_query())

    print("\nThe filters carry each other")
    body = client.get("/local?range=7d&outlets=The+Standard").text
    check("range links keep the outlet", 'range=30d&amp;outlets=The+Standard' in body
          or 'range=30d&amp;outlets=The%20Standard' in body, "no outlet in range link")
    check("outlet chips keep the range", re.search(r'href="/local\?range=7d[^"]*outlets=', body) is not None)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


main()
