"""Outlet logos: fetched once from each publisher, cached locally, served
from here.

Stored as blobs in intel.db rather than as files under the app directory for
two reasons: the deployed app directory is owned by root and the service
shouldn't be writing into it, and a logo cache belongs with the data it
describes. They're small -- a few KB each, a handful of outlets.

The dashboard never hotlinks a publisher's servers. Fetching happens on a
schedule; page loads read the cached bytes, same rule as everything else
here.

Domain derivation is guesswork from the feed URL (feeds.bbci.co.uk is not
where BBC keeps its favicon), so there's an override map for the ones that
matter. New feeds work without touching it -- they just fall back to the
derived domain, and a failure is recorded rather than retried forever.
"""

import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from intel_brief.config import settings
from intel_brief.db import get_connection

log = logging.getLogger("intel_brief.outlets")

# Hand-supplied logos, for publishers whose own icon can't be fetched.
OVERRIDE_DIR = pathlib.Path(__file__).resolve().parent.parent / "logo-overrides"

# Sub-domains that host feeds rather than the site itself.
FEED_SUBDOMAINS = ("feeds", "feed", "rss", "export", "moxie", "api", "static", "www")

# Outlets that publish articles without being RSS feeds, so they never appear
# in the `feeds` table the refresh iterates. Hacker News is collected through
# its API in pipeline/discover.py.
EXTRA_OUTLETS = {
    "Hacker News": "https://news.ycombinator.com/",
}

# Where the derived domain is wrong or has no usable icon.
DOMAIN_OVERRIDES = {
    "NYT": "nytimes.com",
    "arXiv": "arxiv.org",
    "Sky News": "news.sky.com",
}

# Every BBC outlet, national or regional, by prefix rather than by name.
# There are 38 possible regional ones (see uk_regions.py) and which of them a
# given deployment uses is decided by that deployment's LOCAL_PLACES, so
# listing them individually would be both long and a statement about where the
# person running it lives. The feed host is feeds.bbci.co.uk, which serves no
# icon; bbc.co.uk does.
DOMAIN_PREFIXES = (
    ("BBC", "bbc.co.uk"),
)

# Ordered by quality: apple-touch-icon is usually a clean 180px square,
# favicon.ico is often a 16px relic. The homepage <link rel="icon"> parse is
# the fallback for sites that use a hashed asset path.
ICON_PATHS = ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico")

MAX_LOGO_BYTES = 400_000

# Marks have to be roughly square to sit in a row of icons. CoinDesk's only
# published logo is a 144x33 wordmark: shown at icon height it's an unreadable
# sliver, and given the width it needs it towers over every other outlet. A
# generated monogram is better than either -- consistent, legible, and it
# doesn't pretend a wordmark is an icon.
MIN_ASPECT, MAX_ASPECT = 0.6, 1.6
RETRY_AFTER_DAYS = 14  # don't re-attempt a failed outlet on every startup

_CONTENT_TYPES = {
    b"\x89PNG": "image/png",
    b"GIF8": "image/gif",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x00\x00\x01\x00": "image/x-icon",
    b"<svg": "image/svg+xml",
    b"<?xm": "image/svg+xml",
}


def init_outlet_logos() -> None:
    """The table is defined in db.py's SCHEMA; this just guarantees it exists
    for callers that reach this module before init_db() has run."""
    from intel_brief.db import init_db
    init_db()


def _dimensions(data: bytes) -> tuple[int, int] | None:
    """Width and height straight from the file header.

    Only the formats publishers actually serve as icons. SVG is scalable and
    returns None, which counts as acceptable -- it will scale to whatever box
    it's given.
    """
    try:
        if data[:4] == b"\x89PNG":
            import struct
            return struct.unpack(">II", data[16:24])
        if data[:4] == b"\x00\x00\x01\x00":          # ICO
            return (data[6] or 256, data[7] or 256)
        if data[:4] == b"GIF8":
            import struct
            return struct.unpack("<HH", data[6:10])
        if data[:3] == b"\xff\xd8\xff":                # JPEG: walk the segments
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    import struct
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return (w, h)
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:
        return None
    return None


def _is_icon_shaped(data: bytes) -> bool:
    dims = _dimensions(data)
    if not dims or not dims[1]:
        return True  # unknown or scalable: let it through
    return MIN_ASPECT <= (dims[0] / dims[1]) <= MAX_ASPECT


def _override_path(outlet: str) -> pathlib.Path | None:
    """A hand-supplied logo for this outlet, if one has been dropped in.

    Some publishers block automated requests for their own icon (CoinDesk
    returns 403 to everything while serving its feed happily), so there has to
    be a way to supply one by hand. Checked before any network fetch: if it's
    here, someone chose it deliberately and it wins.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", outlet.lower()).strip("-")
    for suffix in (".png", ".ico", ".svg", ".jpg", ".jpeg", ".gif"):
        candidate = OVERRIDE_DIR / f"{slug}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _sniff_type(data: bytes) -> str | None:
    """Trust the bytes, not the Content-Type header -- publishers routinely
    serve an HTML error page with an image content type."""
    head = data[:4]
    for magic, mime in _CONTENT_TYPES.items():
        if data.startswith(magic) or head == magic:
            return mime
    if data.lstrip()[:4].lower() in (b"<svg", b"<?xm"):
        return "image/svg+xml"
    return None


def _domain_for(outlet: str, feed_url: str) -> str:
    if outlet in DOMAIN_OVERRIDES:
        return DOMAIN_OVERRIDES[outlet]
    for prefix, domain in DOMAIN_PREFIXES:
        if outlet == prefix or outlet.startswith(prefix + " "):
            return domain
    host = urlparse(feed_url).netloc
    parts = host.split(".")
    while len(parts) > 2 and parts[0] in FEED_SUBDOMAINS:
        parts = parts[1:]
    return ".".join(parts)


def _get(url: str, timeout: int = 12) -> bytes | None:
    try:
        req = Request(url, headers={"User-Agent": settings.fetch_user_agent})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read(MAX_LOGO_BYTES + 1)
    except Exception as e:
        log.debug("logo fetch %s: %s", url, e)
        return None


def _from_homepage(domain: str) -> tuple[bytes, str, str] | None:
    """Parse <link rel="icon"> off the homepage."""
    html = _get(f"https://{domain}/", timeout=15)
    if not html:
        return None
    text = html.decode("utf-8", errors="ignore")
    for match in re.finditer(r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]*>', text, re.I):
        href = re.search(r'href=["\']([^"\']+)["\']', match.group())
        if not href:
            continue
        url = urljoin(f"https://{domain}/", href.group(1))
        data = _get(url)
        if data and len(data) <= MAX_LOGO_BYTES:
            mime = _sniff_type(data)
            if mime:
                return data, mime, url
    return None


def _from_feed(feed_url: str) -> tuple[bytes, str, str] | None:
    """The logo the publisher declares in their own feed.

    `<channel><image><url>` exists precisely to say "this is our mark", which
    makes it a better first guess than probing favicon paths. It also survives
    sites that block automated requests to the website while serving the feed
    happily -- CoinDesk returns 403/429 to every request for its favicon but
    publishes its logo on a separate downloads host, and that is the only way
    to get it without hotlinking or drawing their brand ourselves.
    """
    raw = _get(feed_url, timeout=15)
    if not raw:
        return None
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
    except Exception:
        return None
    channel = root.find("channel")
    node = channel.find("image") if channel is not None else None
    url = node.findtext("url") if node is not None else None
    if not url:
        return None
    data = _get(url)
    if not data or len(data) > MAX_LOGO_BYTES or not _is_icon_shaped(data):
        return None
    mime = _sniff_type(data)
    return (data, mime, url) if mime else None


def fetch_logo(outlet: str, feed_url: str) -> tuple[bytes, str, str] | None:
    override = _override_path(outlet)
    if override:
        data = override.read_bytes()
        mime = _sniff_type(data) or "image/png"
        if len(data) <= MAX_LOGO_BYTES:
            return data, mime, f"manual override: {override.name}"

    domain = _domain_for(outlet, feed_url)
    for path in ICON_PATHS:
        url = f"https://{domain}{path}"
        data = _get(url)
        if data and len(data) <= MAX_LOGO_BYTES and _is_icon_shaped(data):
            mime = _sniff_type(data)
            if mime:
                return data, mime, url
    # For sites that don't serve an icon at a standard path, or that block
    # automated requests entirely.
    return _from_homepage(domain) or _from_feed(feed_url)


def refresh_outlet_logos(force: bool = False) -> dict:
    """Fetch logos for outlets that don't have one yet."""
    init_outlet_logos()
    stats = {"fetched": 0, "failed": 0, "skipped": 0}
    conn = get_connection()
    try:
        feeds = [
            {"outlet": r["outlet"], "url": r["url"]}
            for r in conn.execute(
                "SELECT outlet, MIN(url) AS url FROM feeds WHERE outlet IS NOT NULL GROUP BY outlet"
            )
        ]
        known = {f["outlet"] for f in feeds}
        feeds += [{"outlet": o, "url": u} for o, u in EXTRA_OUTLETS.items() if o not in known]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETRY_AFTER_DAYS)).isoformat()

        for feed in feeds:
            outlet = feed["outlet"]
            existing = conn.execute(
                "SELECT image, fetched_at FROM outlet_logos WHERE outlet = ?", (outlet,)
            ).fetchone()
            if not force and existing:
                # Keep a good logo; retry a failure only occasionally.
                if existing["image"] or existing["fetched_at"] > cutoff:
                    stats["skipped"] += 1
                    continue

            result = fetch_logo(outlet, feed["url"])
            now = datetime.now(timezone.utc).isoformat()
            if result:
                data, mime, url = result
                conn.execute(
                    """INSERT INTO outlet_logos (outlet, image, content_type, source_url, fetched_at, error)
                       VALUES (?, ?, ?, ?, ?, NULL)
                       ON CONFLICT(outlet) DO UPDATE SET
                           image=excluded.image, content_type=excluded.content_type,
                           source_url=excluded.source_url, fetched_at=excluded.fetched_at, error=NULL""",
                    (outlet, data, mime, url, now),
                )
                stats["fetched"] += 1
                log.info("logo for %s from %s (%s, %d bytes)", outlet, url, mime, len(data))
            else:
                conn.execute(
                    """INSERT INTO outlet_logos (outlet, image, content_type, source_url, fetched_at, error)
                       VALUES (?, NULL, NULL, NULL, ?, 'no usable icon found')
                       ON CONFLICT(outlet) DO UPDATE SET fetched_at=excluded.fetched_at, error=excluded.error""",
                    (outlet, now),
                )
                stats["failed"] += 1
                log.warning("no usable logo for %s", outlet)
            conn.commit()
    finally:
        conn.close()
    return stats


# Distinct hues for generated monograms. Picked to sit on the dark ground
# without vibrating against it, and to stay distinguishable from each other at
# 22px -- which rules out having very many.
MONOGRAM_COLOURS = [
    "#4b9cf5", "#e0834d", "#7fd9ab", "#c98bdb", "#e0c46c",
    "#6fb7c4", "#d97a8e", "#8fa8e0", "#b0c46c", "#d9a05b",
]


def _initials(outlet: str) -> str:
    """One or two letters: initials for a multi-word name, otherwise the first
    two characters. "Sky News" -> "SN", "CoinDesk" -> "Co"."""
    words = [w for w in outlet.split() if w[:1].isalnum()]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return outlet[:2].capitalize() if outlet else "?"


def monogram_svg(outlet: str) -> str:
    """A square stand-in for an outlet with no usable icon.

    Generated rather than stored: it's a deterministic function of the name,
    so it costs nothing to keep and never goes stale. Deliberately not an
    imitation of anyone's branding -- it's a neutral tile, which is the honest
    thing to show when the real mark isn't available in a usable shape.
    """
    colour = MONOGRAM_COLOURS[sum(outlet.encode()) % len(MONOGRAM_COLOURS)]
    text = _initials(outlet)
    size = 15 if len(text) > 1 else 20
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" '
        f'aria-label="{outlet}">'
        f'<rect width="32" height="32" rx="7" fill="{colour}"/>'
        f'<text x="16" y="16" text-anchor="middle" dominant-baseline="central" '
        f'font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" '
        f'font-size="{size}" font-weight="700" fill="#0f1522">{text}</text>'
        "</svg>"
    )


def get_logo(outlet: str) -> tuple[bytes, str] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT image, content_type FROM outlet_logos WHERE outlet = ? AND image IS NOT NULL",
            (outlet,),
        ).fetchone()
    finally:
        conn.close()
    return (row["image"], row["content_type"]) if row else None


def outlets_with_logos() -> list[dict]:
    """Every outlet that has published an analysed article, with whether a
    logo is available -- the input to the filter row."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT a.outlet,
                      COUNT(*) AS n,
                      (SELECT 1 FROM outlet_logos l
                        WHERE l.outlet = a.outlet AND l.image IS NOT NULL) AS has_logo
                 FROM articles a
                WHERE a.status = 'analyzed' AND a.outlet IS NOT NULL
                GROUP BY a.outlet
                ORDER BY n DESC"""
        ).fetchall()
    finally:
        conn.close()
    return [{"outlet": r["outlet"], "count": r["n"], "has_logo": bool(r["has_logo"])} for r in rows]
