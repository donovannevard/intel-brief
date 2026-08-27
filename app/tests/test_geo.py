"""Scope classification: what belongs in Local, Country and Global."""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import os, subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

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

from intel_brief import geo

CASES = [
    # local -- the shipped default is London (see geo.DEFAULT_LOCAL_PLACES)
    ("Hackney council approves new housing plan", "", "local"),
    ("Croydon tram service suspended after signal fault", "", "local"),
    ("Heathrow third runway decision delayed again", "", "local"),
    ("Notting Hill Carnival draws record crowds", "", "local"),
    # country
    ("England vs. Argentina: A football rivalry full of history", "", "country"),
    ("Starmer announces NHS funding boost", "The NHS would receive more money.", "country"),
    ("Hull funeral director jailed for 20 years", "A funeral director in Hull.", "country"),
    ("Bank of England holds rates", "Threadneedle Street decision.", "country"),
    ("Global summit opens", "Delegates from Manchester and Edinburgh joined the NHS panel.", "country"),
    # global -- the ones the model got wrong before
    ("What to watch in Florida primary elections", "", "global"),
    ("Hungary public media reform begins with black screen apology", "", "global"),
    ("India Gen Z protest enters a new online battle", "", "global"),
    ("Canada: British Columbia declares emergency", "", "global"),      # "British"
    ("New South Wales bushfires spread", "", "global"),                  # "Wales"
    ("Ukraine, Russia trade strikes", "Russia accused the UK of supplying drones.", "global"),
    ("Trump announces new tariffs", "The UK was mentioned as a partner.", "global"),
    ("New England patriots win", "", "global"),                          # "England"
]

bad = []
for title, body, want in CASES:
    got = geo.classify(title, body)
    if got != want:
        bad.append((title, got, want))
print(f"1. {len(CASES) - len(bad)}/{len(CASES)} classified correctly")
for title, got, want in bad:
    print(f"   FAIL {got} != {want}: {title}")
assert not bad

# local implies national -- Hackney news is UK news
assert geo.is_national("Hackney council approves new housing plan", "")
print("2. local stories also count as national")

# the SQL prefilter must be a superset of what classify() accepts
clause, params = geo.sql_prefilter("local", ("a.title",))
assert "LIKE ?" in clause and len(params) == len(geo.LOCAL_PLACES)
assert geo.sql_prefilter("global", ("a.title",)) == ("", ())
print("3. prefilter covers every term; global needs none")

# Word boundaries, not substrings. The SQL stage narrows with LIKE '%london%',
# which also matches "Londonderry"; the regex stage is what rejects it. This is
# the check that would fail if someone "simplified" _pattern by dropping \b.
assert not geo.is_local("Londonderry bridge closed after protest")
assert geo.is_local("London bridge closed after protest")
print("4. substring matches are rejected; whole words are not")

# Geography is configuration, not a fixture. Read at import, so this needs a
# fresh interpreter rather than mutating os.environ in place.
env = {**os.environ, "LOCAL_PLACES": "Norfolk,Norwich,Thetford,Cromer"}
probe = (
    "from intel_brief import geo; "
    "print(geo.classify('Norwich bomb squad called to city road', ''), "
    "geo.classify('Hackney council approves new housing plan', ''))"
)
out = subprocess.run(
    [sys.executable, "-c", probe], env=env, capture_output=True, text=True,
    cwd=str(pathlib.Path(__file__).resolve().parent.parent),
)
assert out.returncode == 0, out.stderr
assert out.stdout.split() == ["local", "global"], out.stdout
print("5. LOCAL_PLACES relocates the Local tab")

print("\nALL CHECKS PASSED")
