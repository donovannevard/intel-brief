"""Which articles get the day's GPU budget (pipeline/select.py).

The rule this pins down: no single feed takes the whole budget. Before it,
one real run analysed 38 arXiv preprints and nothing else while 85 BBC and
88 Guardian articles sat unread.
"""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from intel_brief.pipeline.select import select_for_analysis, coverage_summary, guess_topic

def row(id, outlet, title, discovered, content_hash=None):
    return {"id": id, "outlet": outlet, "title": title,
            "discovered_at": discovered, "content_hash": content_hash}

SUBJECTS = ["housing", "railways", "schools", "farming", "shipping", "energy grids",
            "pensions", "water quality", "border checks", "care homes", "airports",
            "steelworks", "fisheries", "broadband", "flood defences", "prisons",
            "universities", "ferries", "vaccines", "forestry"]

# 1. a dominant feed cannot monopolise the budget
rows = ([row(i, "arXiv", f"Spectral bounds for {SUBJECTS[i % 20]} optimisation", f"2026-08-19T12:00:{i:02d}") for i in range(50)]
        + [row(100 + i, "BBC", f"Council announces {SUBJECTS[i]} overhaul", f"2026-08-19T09:00:{i:02d}") for i in range(20)]
        + [row(200 + i, "Guardian", f"Minister questioned over {SUBJECTS[i]} failures", f"2026-08-19T08:00:{i:02d}") for i in range(20)])
cov = coverage_summary(select_for_analysis(rows)[0], 30)
print("1. coverage of 30 slots:", cov)
assert set(cov) == {"arXiv", "BBC", "Guardian"} and max(cov.values()) - min(cov.values()) <= 1

# 2. identical text is dropped wherever it appears
rows = [row(1, "BBC", "Wire story about shipping", "2026-08-19T10:00:00", "hash-a"),
        row(2, "Guardian", "Wire story about shipping reprinted", "2026-08-19T09:00:00", "hash-a"),
        row(3, "BBC", "Something else entirely different", "2026-08-19T08:00:00", "hash-b")]
ordered, dupes = select_for_analysis(rows)
assert len(dupes) == 1 and dupes[0][2] == "identical text" and {r["id"] for r in ordered} == {1, 3}
print("2. identical text deduped across outlets")

# 3. the same outlet republishing is dropped...
rows = [row(1, "BBC", "Flooding hits northern towns after record rainfall", "2026-08-19T10:00:00"),
        row(2, "BBC", "Flooding hits northern towns after record rainfall", "2026-08-19T08:00:00")]
ordered, dupes = select_for_analysis(rows)
assert len(ordered) == 1 and len(dupes) == 1
print("3. same-outlet republication dropped")

# 4. ...but two outlets covering one story are BOTH kept: that is the
#    "covered by N outlets" signal the brief is built on
rows = [row(1, "BBC", "Flooding hits northern towns after record rainfall", "2026-08-19T10:00:00"),
        row(2, "Guardian", "Flooding hits northern towns after record rainfall", "2026-08-19T09:00:00")]
ordered, dupes = select_for_analysis(rows)
assert len(ordered) == 2 and not dupes
print("4. cross-outlet coverage of one story kept")

# 5. short headlines are never deduped -- Jaccard is meaningless there
rows = [row(1, "BBC", "Corn prices rise", "2026-08-19T10:00:00"),
        row(2, "BBC", "Corn prices fall", "2026-08-19T09:00:00")]
assert len(select_for_analysis(rows)[0]) == 2
print("5. short headlines exempt from dedupe")

# 6. topics rotate inside one outlet
PLACES = ["Kharkiv", "Odesa", "Lviv", "Kherson", "Sumy"]
GOODS = ["food", "energy", "housing", "transport", "clothing"]
WARDS = ["cancer", "maternity", "cardiac", "paediatric", "emergency"]
rows = ([row(i, "BBC", f"Missile attack kills civilians in {PLACES[i]}", f"2026-08-19T12:00:{i:02d}") for i in range(5)]
        + [row(50 + i, "BBC", f"Inflation climbs as {GOODS[i]} prices rise", f"2026-08-19T11:00:{i:02d}") for i in range(5)]
        + [row(80 + i, "BBC", f"Hospital doctors warn over {WARDS[i]} patients waiting", f"2026-08-19T10:00:{i:02d}") for i in range(5)])
topics = [guess_topic(r["title"]) for r in select_for_analysis(rows)[0][:6]]
print("6. first six topics from one outlet:", topics)
assert len(set(topics)) >= 3

# 7. sport goes last -- the brief filters it out anyway
rows = [row(1, "BBC", "Champions league match ends in late goal for the team", "2026-08-19T12:00:00"),
        row(2, "BBC", "Parliament votes on the new policy bill today", "2026-08-19T09:00:00")]
assert select_for_analysis(rows)[0][0]["id"] == 2
print("7. sport deprioritised behind news")

print("\nALL CHECKS PASSED")
