"""Choosing which articles get analysed.

The analyse stage has a fixed time budget (~40 articles a day against a queue
of several hundred), so this decides what the day's GPU time is spent on.
That makes it the difference between a morning round-up and a pile of
whatever one feed published last.

The original rule was `ORDER BY discovered_at DESC`, chosen to stop old
backlog crowding out same-day news. It did that, but it handed the entire
budget to whichever feed polled most recently: on 2026-08-19 all 38 analysed
articles were arXiv preprints, with 85 BBC and 88 Guardian articles sitting
unread in the queue. A news dashboard with no news outlets in it.

So selection is now round-robin across outlets, newest-first within each
outlet: every outlet gets its top story analysed before any outlet gets its
second. Recency still decides *which* of an outlet's articles are picked;
it just no longer decides how the budget is divided.

Deduplication here is deliberately one-sided:

* **Identical text** (same content_hash) is dropped wherever it appears --
  usually syndicated wire copy under two mastheads. Analysing it twice buys
  nothing.
* **Near-identical headlines from the SAME outlet** are dropped -- a live
  story republished through the day.
* **Near-identical headlines from DIFFERENT outlets are kept on purpose.**
  They look like the most obvious waste, and removing them would break the
  thing that makes the brief worth reading: clustering uses those separate
  analyses to say "covered by BBC, Guardian and DW", which is the coverage
  signal, not redundancy.

Topic spread is the second dimension. Real categories come *from* the
analysis, so they can't order the queue that feeds it; `feeds.category_hint`
is per-feed, so every BBC article shares one hint and it gives no spread
within an outlet. What's left is a keyword guess at the headline -- crude,
and deliberately so: it only decides which articles get a slot, never what
anything is labelled. A wrong guess costs a slightly less varied morning, not
a wrong category on the page, because the real classifier still runs
afterwards and overrides it.

Sports and celebrity are pushed to the back rather than dropped. The brief
excludes both, so analysing them ahead of everything else spends GPU on
articles that can't appear in it -- but they still populate the archive if
the budget stretches that far.
"""

import re
from collections import defaultdict

# Headline similarity above which two articles from the same outlet are
# treated as the same story. High on purpose: this drops re-publications,
# not merely related coverage.
TITLE_SIMILARITY = 0.8

# Below this many distinguishing words, Jaccard similarity is too coarse to
# trust -- two three-word headlines that share two words score 0.5 whether
# they are the same story or not, and anything shorter saturates. Short
# headlines simply aren't deduped.
MIN_TITLE_WORDS = 4

# Words that carry no distinguishing signal in a headline.
STOPWORDS = frozenset("""
a an the and or but of to in on at for from by with as is are was were be been
being it its this that these those he she they them his her their you your i we
us our what which who whom how why when where says say said after before over
under new latest live updates
""".split())


# Headline keywords per coarse topic. Only used to interleave the queue.
TOPIC_KEYWORDS: dict[str, frozenset] = {
    "conflict": frozenset("""war strike strikes missile missiles troops military attack
        attacks killed dead casualties ceasefire invasion offensive rebels army
        airstrike drone shelling gaza ukraine russia israel nato""".split()),
    "politics": frozenset("""election elections vote votes voter parliament senate congress
        president minister ministers government policy bill law court ruling
        campaign party coalition referendum sanctions summit diplomatic""".split()),
    "economics": frozenset("""inflation gdp economy economic rates rate central bank markets
        market stocks oil prices price growth recession unemployment jobs tariff
        tariffs trade deficit budget debt currency dollar euro""".split()),
    "corporate": frozenset("""company companies earnings profit revenue shares merger
        acquisition ceo chief executive layoffs ipo startup firm business""".split()),
    "bitcoin": frozenset("""bitcoin crypto cryptocurrency blockchain ethereum stablecoin
        mining miner wallet defi token coinbase satoshi""".split()),
    "science": frozenset("""research researchers study scientists discovery quantum physics
        chemistry biology space nasa telescope climate ai model models neural
        algorithm theorem proof dataset learning computing""".split()),
    "health": frozenset("""health disease virus vaccine patients hospital doctors cancer
        outbreak drug treatment nhs mental""".split()),
    "sports": frozenset("""match cup league goal goals player players team coach striker
        football cricket rugby tennis olympics fixture season champions""".split()),
    "celebrity": frozenset("""actor actress singer star celebrity film movie album tour
        awards oscar grammy netflix royal duchess prince kardashian""".split()),
}

# Analysed last, because the brief filters them out anyway.
DEPRIORITISED_TOPICS = ("sports", "celebrity")


def guess_topic(title: str, category_hint: str | None = None) -> str:
    """A coarse topic for spreading the budget. Not a classifier -- the real
    one runs during analysis and overrides whatever this says."""
    words = normalise_title(title)
    best, best_score = "other", 0
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = len(words & keywords)
        if score > best_score:
            best, best_score = topic, score
    if best_score == 0 and category_hint in ("science", "bitcoin"):
        # A dedicated feed is a better guess than "other" when the headline
        # gives nothing away (arXiv titles rarely contain common keywords).
        return "science" if category_hint == "science" else "bitcoin"
    return best


def normalise_title(title: str) -> frozenset:
    """A headline reduced to its distinguishing words."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(w for w in words if w not in STOPWORDS and len(w) > 2)


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _interleave_topics(rows: list, hints: dict[str, str]) -> list:
    by_topic: dict[str, list] = defaultdict(list)
    for row in rows:
        by_topic[guess_topic(row["title"], hints.get(row["outlet"]))].append(row)
    for topic in by_topic:
        by_topic[topic].sort(key=lambda r: r["discovered_at"] or "", reverse=True)

    ordinary = sorted(
        (t for t in by_topic if t not in DEPRIORITISED_TOPICS),
        key=lambda t: (-len(by_topic[t]), t),
    )
    deferred = [t for t in DEPRIORITISED_TOPICS if t in by_topic]

    out: list = []
    for topics in (ordinary, deferred):
        depth = max((len(by_topic[t]) for t in topics), default=0)
        for index in range(depth):
            for topic in topics:
                if index < len(by_topic[topic]):
                    out.append(by_topic[topic][index])
    return out


def select_for_analysis(rows: list, hints: dict[str, str] | None = None) -> tuple[list, list]:
    """Order the queue for analysis.

    Returns (ordered_rows, duplicates), where `duplicates` are rows that
    shouldn't be analysed at all -- the caller marks them so they leave the
    queue instead of being reconsidered every run.
    """
    hints = hints or {}
    by_outlet: dict[str, list] = defaultdict(list)
    for row in rows:
        by_outlet[row["outlet"] or "(unknown)"].append(row)

    # Within an outlet: rotate through topics, newest-first inside each, so a
    # single busy story-line can't take all of that outlet's slots.
    for outlet in by_outlet:
        by_outlet[outlet] = _interleave_topics(by_outlet[outlet], hints)

    duplicates: list[tuple] = []
    seen_hashes: dict[str, object] = {}
    seen_titles: dict[str, list[tuple[frozenset, object]]] = defaultdict(list)
    ordered: list = []

    # Round-robin: one from each outlet per pass, in descending queue depth so
    # the ordering is stable rather than dict-insertion dependent.
    outlets = sorted(by_outlet, key=lambda o: (-len(by_outlet[o]), o))
    depth = max((len(v) for v in by_outlet.values()), default=0)
    for index in range(depth):
        for outlet in outlets:
            if index >= len(by_outlet[outlet]):
                continue
            row = by_outlet[outlet][index]

            content_hash = row["content_hash"]
            if content_hash and content_hash in seen_hashes:
                duplicates.append((row, seen_hashes[content_hash], "identical text"))
                continue

            title = normalise_title(row["title"])
            match = None
            if len(title) >= MIN_TITLE_WORDS:
                match = next(
                    (kept for kept_title, kept in seen_titles[outlet]
                     if len(kept_title) >= MIN_TITLE_WORDS
                     and _similarity(title, kept_title) >= TITLE_SIMILARITY),
                    None,
                )
            if match is not None:
                duplicates.append((row, match, "near-identical headline from the same outlet"))
                continue

            if content_hash:
                seen_hashes[content_hash] = row
            seen_titles[outlet].append((title, row))
            ordered.append(row)

    return ordered, duplicates


def coverage_summary(ordered: list, limit: int) -> dict[str, int]:
    """How many slots each outlet would get, if `limit` articles are analysed.
    Used for logging so a lopsided run is visible in the stats."""
    counts: dict[str, int] = defaultdict(int)
    for row in ordered[:limit]:
        counts[row["outlet"] or "(unknown)"] += 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
