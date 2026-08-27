"""Deciding whether a story is local, national, or neither.

The analysis already asks the model for a `scope`, relative to the reader.
It does not reliably deliver one: on real data it filed "What to watch in
Tuesday's Florida primary elections" as *local* and "Hungary public media
reform" as *country*, because it labels each story relative to that story's
own geography rather than to the person reading it. Both tabs filled up with
news from everywhere.

So scope for the Local and Country tabs is decided here instead, by looking
for place names, and the model's own label is kept only as a badge. A place
name is a fact about the text; "is this near the reader" is not something a
7B model reasons about reliably enough to route a whole tab on.

Matching is two-stage on purpose. SQLite narrows with `LIKE '%london%'`, which
is fast and index-free but also matches "Londonderry"; then a word-boundary
regex in Python confirms it. Substring matching alone would file Northern
Irish news as London news.

Both lists are defaults, not fixtures. Set LOCAL_PLACES (and, if you are not
in the UK, COUNTRY_TERMS and STRONG_COUNTRY_TERMS) to move this anywhere --
the shipped values are London and the UK because a public repo should not
hard-code where its author happens to live.
"""

import re

from intel_brief.config import settings

# Where "local" is, from LOCAL_PLACES in .env. There is deliberately no
# default: a shipped fallback would quietly make one part of the country the
# stand-in for everyone, and the operator would never find out their Local tab
# was somebody else's. Empty means no story is local, which is visibly wrong
# and therefore gets fixed. See docs/uk-regions.md and uk_regions.py, which
# also turns these names into the right regional feeds at install time.
LOCAL_PLACES = list(settings.local_places)

# National coverage: the country, its nations and institutions, and the
# political vocabulary that only makes sense in a UK story.
DEFAULT_COUNTRY_TERMS = [
    "UK", "U.K.", "United Kingdom", "Britain", "British", "Briton", "Britons",
    "England", "English", "Scotland", "Scottish", "Wales", "Welsh",
    "Northern Ireland", "Great Britain",
    "Westminster", "Whitehall", "Downing Street", "House of Commons",
    "House of Lords", "Holyrood", "Senedd", "Stormont",
    "NHS", "HMRC", "Ofcom", "Ofgem", "Ofsted", "DWP", "Met Office",
    "Bank of England", "Metropolitan Police", "Royal Mail",
    "Labour Party", "Conservative Party", "Tory", "Tories", "Lib Dem",
    "Liberal Democrat", "Reform UK", "Starmer", "Badenoch", "Farage",
    "London", "Manchester", "Birmingham", "Leeds", "Liverpool", "Glasgow",
    "Edinburgh", "Cardiff", "Belfast", "Bristol", "Sheffield", "Newcastle",
    "Nottingham", "Leicester", "Southampton", "Portsmouth", "Brighton",
    "Hull", "Sunderland", "Norwich", "Oxford", "Cambridge", "York",
]

COUNTRY_TERMS = list(settings.country_terms) or DEFAULT_COUNTRY_TERMS

# Place names that contain a UK term but aren't the UK. Stripped before
# matching, so "British Columbia declares emergency" is Canadian news.
EXCLUSIONS = ["New South Wales", "British Columbia", "New England", "New Britain"]

# Terms strong enough on their own: nobody writes about Westminster or the NHS
# in passing. City names and "British" are weaker -- they turn up in stories
# that merely mention the UK -- so those need to earn their place (see score()).
DEFAULT_STRONG_COUNTRY_TERMS = [
    "UK", "U.K.", "United Kingdom", "Britain", "Westminster", "Whitehall",
    "Downing Street", "House of Commons", "House of Lords", "Holyrood",
    "Senedd", "Stormont", "NHS", "HMRC", "Ofcom", "Ofgem", "Ofsted", "DWP",
    "Bank of England", "Metropolitan Police", "Royal Mail", "Labour Party",
    "Conservative Party", "Lib Dem", "Liberal Democrat", "Reform UK",
    "Starmer", "Badenoch", "Farage",
]

STRONG_COUNTRY_TERMS = frozenset(
    t.lower() for t in
    (list(settings.strong_country_terms) or DEFAULT_STRONG_COUNTRY_TERMS)
)


def _pattern(terms: list[str]) -> re.Pattern:
    # Longest first so "Northern Ireland" wins over "Ireland"-style prefixes,
    # and \b at both ends so "London" doesn't match "Londonderry".
    ordered = sorted(terms, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in ordered) + r")\b", re.I)


LOCAL_RE = _pattern(LOCAL_PLACES) if LOCAL_PLACES else None
COUNTRY_RE = _pattern(COUNTRY_TERMS)
EXCLUSION_RE = _pattern(EXCLUSIONS)


def _clean(text: str) -> str:
    return EXCLUSION_RE.sub(" ", text or "")


def is_local(*texts: str) -> bool:
    if LOCAL_RE is None:
        return False
    return any(LOCAL_RE.search(_clean(t)) for t in texts if t)


def _country_score(title: str, body: str) -> int:
    """How much of a home-country story this is.

    A mention is not a subject: "Russia accused the UK of supplying drones" is
    a story about Ukraine, and matching any UK word anywhere filed it under
    Country. So the title carries real weight, a body mention counts for
    little on its own, and the institutional terms count wherever they appear.
    """
    title_hits = {m.group().lower() for m in COUNTRY_RE.finditer(_clean(title))}
    body_hits = {m.group().lower() for m in COUNTRY_RE.finditer(_clean(body))}

    score = 0
    if title_hits:
        # A UK place or institution in the headline is the story's subject.
        score += 3
        if title_hits & STRONG_COUNTRY_TERMS:
            score += 1
    # In the body alone it takes more than one reference: "Russia accused the
    # UK of supplying drones" names the UK once and is not UK news.
    score += min(len(body_hits), 2)
    if body_hits & STRONG_COUNTRY_TERMS:
        score += 1
    return score


COUNTRY_SCORE_THRESHOLD = 3


def is_national(title: str = "", body: str = "") -> bool:
    """Country-wide, including anything local -- Hackney news is UK news."""
    return is_local(title, body) or _country_score(title, body) >= COUNTRY_SCORE_THRESHOLD


def classify(title: str = "", body: str = "") -> str:
    if is_local(title, body):
        return "local"
    if is_national(title, body):
        return "country"
    return "global"


def sql_prefilter(scope: str, columns: tuple[str, ...]) -> tuple[str, tuple]:
    """A cheap SQL narrowing clause for one scope.

    Returns more rows than belong -- substring matches, mostly -- which the
    caller confirms with `classify()`. Getting this wrong only costs a wasted
    row, never a wrong tab, because the Python check is what decides.
    """
    terms = LOCAL_PLACES if scope == "local" else COUNTRY_TERMS
    if scope == "global":
        return "", ()
    if not terms:
        # No LOCAL_PLACES set. " AND ()" is not valid SQL, and an empty clause
        # would mean *no* filtering -- every article treated as local, the
        # exact opposite of what an unset LOCAL_PLACES should do. Match nothing
        # instead, which mirrors is_local() returning False.
        return " AND 0", ()
    likes, params = [], []
    for term in terms:
        for column in columns:
            likes.append(f"{column} LIKE ?")
            params.append(f"%{term}%")
    return " AND (" + " OR ".join(likes) + ")", tuple(params)
