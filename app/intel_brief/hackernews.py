"""Hacker News: human commentary to set against the model's analysis.

The point isn't more headlines. Every article here already gets an AI summary,
interpretation and framing critique, all produced from the article text alone.
HN comments are the other half of that: people who read the same piece, often
with domain knowledge, saying what it got wrong. Where the two agree the
reading is stronger; where they diverge, the disagreement is the interesting
part. Neither is presented as the truth.

Two free endpoints, no key, no account:

* **Firebase** (`hacker-news.firebaseio.com`) -- the official read-only API.
  One request per item, so fetching a story plus its top comments is a handful
  of small calls.
* **Algolia** (`hn.algolia.com/api/v1`) -- full-text and URL search. This is
  what lets an article we already hold be matched to its HN thread, which the
  Firebase API alone can't do.

Comment text arrives as HTML fragments with entities and `<p>` separators, so
it's converted to plain text here rather than being trusted into a template.

X was the original request and isn't possible on the same terms: its API is
paid for reads, logged-out scraping is blocked, and the third-party mirrors
that used to work are gone. HN gives the same thing -- popular comment threads
attached to a linked article -- through a documented public API.
"""

import html
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from intel_brief.config import settings

log = logging.getLogger("intel_brief.hackernews")

FIREBASE = "https://hacker-news.firebaseio.com/v0"
ALGOLIA = "https://hn.algolia.com/api/v1"

# A thread with almost no discussion isn't worth attaching -- the value is in
# the argument, not in the fact that someone posted the link.
MIN_COMMENTS = 5
MIN_SCORE = 10

# Comments per article. HN orders a story's `kids` by its own ranking, so the
# first few are the ones the site itself surfaces.
TOP_COMMENTS = 3

# Long comments are usually the substantive ones, but a wall of text doesn't
# belong on a card; truncated on a sentence boundary where possible.
MAX_COMMENT_CHARS = 700


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": settings.fetch_user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _plain_text(fragment: str) -> str:
    """HN comment HTML -> readable text."""
    if not fragment:
        return ""
    text = re.sub(r"<p>", "\n\n", fragment, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _truncate(text: str) -> str:
    if len(text) <= MAX_COMMENT_CHARS:
        return text
    cut = text[:MAX_COMMENT_CHARS]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: stop + 1] if stop > MAX_COMMENT_CHARS * 0.5 else cut.rstrip()) + " […]"


def item(item_id: int) -> dict | None:
    try:
        return _get(f"{FIREBASE}/item/{item_id}.json")
    except Exception as e:
        log.debug("hn item %s: %s", item_id, e)
        return None


def top_comments(story_id: int, limit: int = TOP_COMMENTS) -> list[dict]:
    """Top-level comments on a story, in HN's own ranking order.

    Deleted and dead comments are skipped rather than counted, so `limit` is
    how many usable comments come back, not how many were examined.
    """
    story = item(story_id)
    if not story:
        return []
    out = []
    for kid in (story.get("kids") or []):
        if len(out) >= limit:
            break
        comment = item(kid)
        if not comment or comment.get("deleted") or comment.get("dead"):
            continue
        text = _plain_text(comment.get("text", ""))
        if not text:
            continue
        out.append({
            "hn_id": comment["id"],
            "author": comment.get("by") or "unknown",
            "text": _truncate(text),
            "posted_at": datetime.fromtimestamp(
                comment.get("time", 0), tz=timezone.utc).isoformat(),
            "permalink": f"https://news.ycombinator.com/item?id={comment['id']}",
            "rank": len(out),
        })
    return out


def find_discussion(article_url: str) -> dict | None:
    """The HN thread for an article we already hold, if there is one.

    Matched on URL rather than title: a title search would happily return a
    different article about the same subject, and attaching the wrong
    discussion is worse than attaching none.
    """
    if not article_url:
        return None
    try:
        query = urllib.parse.quote(article_url, safe="")
        data = _get(
            f"{ALGOLIA}/search?restrictSearchableAttributes=url&query={query}&tags=story&hitsPerPage=5"
        )
    except Exception as e:
        log.debug("hn search %s: %s", article_url[:60], e)
        return None

    normalised = _normalise_url(article_url)
    best = None
    for hit in data.get("hits", []):
        if _normalise_url(hit.get("url") or "") != normalised:
            continue
        comments = hit.get("num_comments") or 0
        points = hit.get("points") or 0
        if comments < MIN_COMMENTS or points < MIN_SCORE:
            continue
        if best is None or comments > best["comments"]:
            best = {
                "hn_id": int(hit["objectID"]),
                "title": hit.get("title"),
                "points": points,
                "comments": comments,
                "permalink": f"https://news.ycombinator.com/item?id={hit['objectID']}",
            }
    return best


def _normalise_url(url: str) -> str:
    """Compare URLs without tracking parameters or scheme noise."""
    try:
        parts = urllib.parse.urlsplit(url.lower())
    except ValueError:
        return url.lower()
    host = parts.netloc.removeprefix("www.")
    query = "&".join(
        sorted(
            p for p in parts.query.split("&")
            if p and not p.split("=")[0].startswith(("utm_", "cmpid", "ito", "fbclid", "at_"))
        )
    )
    return f"{host}{parts.path.rstrip('/')}{('?' + query) if query else ''}"


def top_stories(limit: int = 30) -> list[dict]:
    """Front-page stories that link somewhere, for use as a feed.

    Ask/Show HN posts and polls are skipped: they have no article to extract,
    and this pipeline is built around fetching an article's text.
    """
    try:
        ids = _get(f"{FIREBASE}/topstories.json")[: limit * 2]
    except Exception as e:
        log.warning("hn topstories failed: %s", e)
        return []

    out = []
    for story_id in ids:
        if len(out) >= limit:
            break
        story = item(story_id)
        if not story or story.get("type") != "story" or not story.get("url"):
            continue
        if (story.get("score") or 0) < MIN_SCORE:
            continue
        out.append({
            "hn_id": story["id"],
            "title": story.get("title") or "(untitled)",
            "url": story["url"],
            "points": story.get("score") or 0,
            "comments": story.get("descendants") or 0,
            "posted_at": datetime.fromtimestamp(
                story.get("time", 0), tz=timezone.utc).isoformat(),
            "permalink": f"https://news.ycombinator.com/item?id={story['id']}",
        })
    return out
