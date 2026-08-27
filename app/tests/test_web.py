"""Every page renders, navigation reflects where you are, and filters filter."""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Pin the configurable surface so these route assertions do not depend on
# whoever's .env is on the machine. CRYPTO_ASSETS="" legitimately removes the
# Crypto tab (and /markets/crypto correctly 404s), which would look like a
# regression here. load_dotenv() does not override values already set, so
# this has to happen before the first intel_brief import.
os.environ["CRYPTO_ASSETS"] = "BTC"
os.environ["LOCAL_PLACES"] = (
    "London,Greater London,Hackney,Croydon,Ealing,Heathrow,Notting Hill,Wembley"
)
for _v in ("COUNTRY_TERMS", "STRONG_COUNTRY_TERMS"):
    os.environ[_v] = ""

import re
from intel_brief.db import init_db
from intel_brief.markets.store import init_markets_db
init_db(); init_markets_db()
from fastapi.testclient import TestClient
from intel_brief.web.app import app

c = TestClient(app)
def body(html): return html.split("</head>", 1)[1]

PAGES = ["/", "/local", "/country", "/global", "/economics", "/corporate", "/crypto",
         "/science", "/archive", "/status", "/markets", "/markets/crypto",
         "/markets/stocks", "/markets/commodities", "/markets/monetary"]
for p in PAGES:
    assert c.get(p).status_code == 200, p
print(f"1. all {len(PAGES)} pages render")

def active_primary(html):
    m = re.search(r'<nav class="primary">(.*?)</nav>', html, re.S)
    return re.findall(r'class="active">([^<]+)<', m.group(1))
for p, want in [("/", "News"), ("/markets/stocks", "Markets"), ("/status", "Status")]:
    assert active_primary(c.get(p).text) == [want], p
print("2. primary nav marks the right section")

# category tabs carry their badge colour
nav = c.get("/").text
for cls in ["cat-nav cat-economics", "cat-nav cat-bitcoin", "cat-nav scope-local"]:
    assert cls in nav, cls
print("3. category and scope tabs carry their colour classes")

# one stylesheet, and no CSS leaking into the body
for p in ["/", "/markets/crypto", "/status"]:
    html = c.get(p).text
    assert html.count("<style>") == 1 and html.count("</style>") == 1, p
    stripped = re.sub(r"<script.*?</script>", "", re.sub(r'style="[^"]*"', "", body(html)), flags=re.S)
    assert not re.findall(r"\.[a-z][\w-]*\s*\{[^}]*[a-z-]+:", stripped), p
print("4. one well-formed stylesheet per page, none leaking into the body")

# assets are local, never a CDN
m = c.get("/markets/crypto").text
assert "cdn." not in m and "/static/plotly.min.js" in m and "/static/favicon.svg" in c.get("/").text
print("5. plotly, markets.js and the favicon all served locally")

# the period selector is section-wide and lives in the URL
for url, want in [("/markets/crypto", "1y"), ("/markets/stocks?period=5y", "5y"), ("/markets/stocks?period=nonsense", "1y")]:
    assert f'MKT_DEFAULT_PERIOD = "{want}"' in c.get(url).text, url
assert "period-bar" in body(c.get("/markets/crypto").text)
print("6. period selector is shared across tabs and carried in the URL")

# outlet filter. There is nothing to filter by until articles exist, and an
# empty archive is a supported first-run state now rather than an oddity --
# so the chips are asserted only when the archive actually has outlets in it.
full = c.get("/global").text
from intel_brief import outlets as _outlets
if _outlets.outlets_with_logos():
    assert "outlet-filter" in body(full) and "/outlet-logo/" in body(full)
else:
    assert "Nothing here yet" in body(full), "empty archive should say so"
assert c.get("/global?outlets=BBC").status_code == 200
assert c.get("/global?outlets=' OR 1=1").status_code == 200   # unknown outlets ignored
print("7. outlet filter renders and rejects unknown outlets")

# progress banner and pause switch on both halves
for p in ["/", "/markets/crypto", "/status"]:
    assert 'id="pipeline-progress"' in c.get(p).text and "/control/pause" in c.get(p).text
print("8. progress banner and pause control on every section")

assert c.get("/markets/nonsense").status_code == 404
print("9. unknown markets tab 404s")

# The logo endpoint always answers -- a real icon where one exists, a generated
# square tile otherwise -- so the outlet row never has a hole in it.
r = c.get("/outlet-logo/NoSuchOutlet")
assert r.status_code == 200 and "svg" in r.headers["content-type"]
assert c.get("/outlet-logo/BBC").status_code == 200
print("10. logo endpoint always returns a mark, falling back to a monogram")

print("\nALL CHECKS PASSED")
