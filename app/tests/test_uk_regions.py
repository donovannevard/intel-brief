"""The UK region table, and the feeds.yaml it generates.

This is the file that decides what a clone of this repo polls, so the checks
here are mostly about it staying neutral and internally consistent: no region
privileged over another, no duplicate or ambiguous place names, and a
generated feeds.yaml that actually parses.
"""

import os, sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("LOCAL_PLACES", "")

import yaml
from intel_brief import uk_regions as u
from intel_brief.feeds import init_feeds, render_feeds_yaml

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = APP_DIR / "feeds.example.yaml"

fails = []
def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f" -- {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(label)

print("Registry shape")
check("every region has id, label, feeds, places",
      all({"id","label","feeds","places"} <= set(r) for r in u.REGIONS))
ids = [r["id"] for r in u.REGIONS]
check("region ids are unique", len(ids) == len(set(ids)),
      f"dupes: {[i for i in ids if ids.count(i) > 1]}")
check("every region has at least one feed", all(r["feeds"] for r in u.REGIONS))
check("every region has at least three places",
      all(len(r["places"]) >= 3 for r in u.REGIONS))
urls = [f["url"] for r in u.REGIONS for f in r["feeds"]]
check("feed urls are unique", len(urls) == len(set(urls)),
      f"dupes: {sorted({x for x in urls if urls.count(x) > 1})}")
check("every feed url is https", all(x.startswith("https://") for x in urls))
check("every feed has name, url, outlet",
      all({"name","url","outlet"} <= set(f) for r in u.REGIONS for f in r["feeds"]))

print("\nPlace names")
# A place in two regions makes LOCAL_PLACES ambiguous: it would silently pull
# in feeds for somewhere the reader did not ask for.
owners = {}
for r in u.REGIONS:
    for p in r["places"]:
        owners.setdefault(p.lower(), []).append(r["id"])
shared = {p: rs for p, rs in owners.items() if len(rs) > 1}
check("no place name belongs to two regions", not shared, f"{shared}")

# The exclusion rule this table is built on (see the module docstring).
BANNED = {"reading","bath","wells","street","march","leek","rugby","boston",
          "washington","perth","christchurch","westminster","nelson","halifax"}
present = sorted(BANNED & set(owners))
check("no ambiguous place names", not present, f"found: {present}")

print("\nNeutrality")
# No region may be privileged. The concern is a *locality* leaking into the
# code -- somebody's town list pasted into geo.py -- not the national term
# list, which names one major city per region on purpose and so identifies
# nobody. So the test is per-region concentration, not any mention at all:
# three or more places from one English region outside the registry means that
# region is being treated as the default, which is exactly what LOCAL_PLACES
# exists to prevent.
NATIONS = {"scotland", "wales", "northern_ireland"}
src = pathlib.Path(__file__).resolve().parent.parent / "intel_brief"
concentrated = []
for py in sorted(src.rglob("*.py")):
    if py.name == "uk_regions.py":
        continue
    body = py.read_text()
    for r in u.REGIONS:
        if r["id"] in NATIONS:
            continue
        found = [pl for pl in r["places"]
                 if len(pl) >= 5 and (f'"{pl}"' in body or f"'{pl}'" in body)]
        if len(found) >= 3:
            concentrated.append(f"{py.name}:{r['id']}={found}")
check("no single region's places concentrated outside uk_regions.py",
      not concentrated, f"{concentrated}")

geo_src = (src / "geo.py").read_text()
check("geo.py ships no default local region",
      "DEFAULT_LOCAL_PLACES" not in geo_src)
check("geo.py takes LOCAL_PLACES from settings only",
      "LOCAL_PLACES = list(settings.local_places)" in geo_src)

print("\nDerivation")
check("a city selects exactly its own region",
      [r["id"] for r in u.regions_for_places(["Manchester"])] == ["manchester"])
check("matching is case-insensitive",
      u.regions_for_places(["mAnChEsTeR"]) == u.regions_for_places(["Manchester"]))
check("an unknown place selects nothing", u.regions_for_places(["Atlantis"]) == [])
check("an unknown place is reported", u.unmatched(["Atlantis"]) == ["Atlantis"])
check("empty input is safe", u.regions_for_places([]) == [] and u.feeds_for_places([]) == [])
check("feeds are de-duplicated across regions",
      len({f["url"] for f in u.feeds_for_places(["Manchester","Salford"])})
      == len(u.feeds_for_places(["Manchester","Salford"])))

print("\nGenerated feeds.yaml")
if not TEMPLATE.exists():
    check(f"template exists at {TEMPLATE}", False)
else:
    for label, places, want_local in [("a matching region", ["Leeds"], True),
                                      ("no match", ["Atlantis"], False),
                                      ("nothing set", [], False)]:
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "feeds.yaml"
            init_feeds(TEMPLATE, out, places)
            parsed = yaml.safe_load(out.read_text())
            ok = isinstance(parsed, dict) and isinstance(parsed.get("feeds"), list)
            check(f"{label}: parses as YAML with a feeds list", ok)
            if ok:
                local = [f for f in parsed["feeds"] if f.get("category_hint") == "local"]
                check(f"{label}: local feeds {'present' if want_local else 'absent'}",
                      bool(local) == want_local)
                check(f"{label}: every feed has a url",
                      all(f.get("url") for f in parsed["feeds"]))
            # install.sh keeps a feeds.yaml only if it starts with this line,
            # and sets anything else aside as pre-dating LOCAL_PLACES. Losing it
            # would make every deploy discard the operator's edited file.
            from intel_brief.feeds import GENERATED_MARKER
            check(f"{label}: first line is the generator marker",
                  out.read_text().splitlines()[0] == GENERATED_MARKER)
            check("install.sh tests for the same marker text",
                  GENERATED_MARKER in (APP_DIR.parent / "install.sh").read_text())
            # Never clobber: the file is the operator's once written.
            again = init_feeds(TEMPLATE, out, places)
            check(f"{label}: refuses to overwrite", again["written"] is False)

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    sys.exit(1)
print("ALL CHECKS PASSED")
