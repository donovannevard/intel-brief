"""Article lists are newest first, using a date that actually sorts."""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from datetime import timezone
from email.utils import parsedate_to_datetime
from intel_brief.db import init_db, get_connection, backfill_published_ts

init_db()
conn = get_connection()
conn.execute("DELETE FROM articles WHERE id > 950000")

# The exact formats the real feeds emit, deliberately inserted out of order.
SAMPLES = [
    (950001, "Wed, 8 Jul 2026 15:56:00 GMT",     "DW"),
    (950002, "Thu, 20 Aug 2026 08:41:10 GMT",    "BBC"),
    (950003, "Fri, 17 Jul 2026 18:03:58 -0400",  "Fox News"),
    (950004, "Tue, 19 Aug 2026 07:30:31 +0000",  "Guardian"),
]
for id, published, outlet in SAMPLES:
    conn.execute(
        "INSERT INTO articles (id, url, title, outlet, published_at, discovered_at, status) "
        "VALUES (?,?,?,?,?,?, 'analyzed')",
        (id, f"http://x/{id}", f"Story {id}", outlet, published, "2026-08-20T00:00:00+00:00"))
conn.commit()

# 1. sorting the raw feed strings is meaningless -- it sorts on weekday name
raw = [r["published_at"][:3] for r in conn.execute(
    "SELECT published_at FROM articles WHERE id > 950000 ORDER BY published_at DESC")]
print("1. raw string sort gives weekday order:", raw)
assert raw[0] == "Wed", "the July article wins a text sort -- this is the bug"

# 2. the backfill turns them into sortable instants
filled = backfill_published_ts(conn)
print(f"2. parsed {filled} of {len(SAMPLES)} into ISO")
assert filled == len(SAMPLES)

ordered = [r["id"] for r in conn.execute(
    "SELECT id FROM articles WHERE id > 950000 "
    "ORDER BY COALESCE(published_ts, discovered_at) DESC")]
print("3. sorted newest first:", ordered)
assert ordered == [950002, 950004, 950003, 950001]

# 4. offsets are normalised, not compared as written
a = parsedate_to_datetime("Thu, 20 Aug 2026 04:57:44 -0400").astimezone(timezone.utc)
b = parsedate_to_datetime("Thu, 20 Aug 2026 08:27:55 +0000").astimezone(timezone.utc)
assert a > b, "04:57 -0400 is 08:57 UTC and must outrank 08:27 UTC"
print("4. timezone offsets compared as instants, not as text")

# 5. an article with no publication date still sorts, on when we found it
conn.execute("INSERT INTO articles (id, url, title, outlet, published_at, discovered_at, status) "
             "VALUES (950005,'http://x/5','No date','Hacker News','', '2026-08-21T00:00:00+00:00','analyzed')")
conn.commit()
top = conn.execute("SELECT id FROM articles WHERE id > 950000 "
                   "ORDER BY COALESCE(published_ts, discovered_at) DESC LIMIT 1").fetchone()["id"]
assert top == 950005, "undated articles fall back to discovered_at"
print("5. undated articles fall back to when they were found")

conn.execute("DELETE FROM articles WHERE id > 950000"); conn.commit(); conn.close()
print("\nALL CHECKS PASSED")
