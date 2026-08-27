"""Stage 2 -- Extract. Full-text fetch via trafilatura for each `discovered`
article. Per-domain politeness delay. Articles under ~120 words or failing
extraction go to `failed_extract` with a logged reason -- never silently
swallowed. Learned the hard way: a job that drops its failures quietly
reports a clean run while collecting nothing."""

import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import trafilatura

from intel_brief.config import settings
from intel_brief.db import get_connection
from intel_brief.pipeline.runs import track_run

MIN_WORD_COUNT = 120

# Recorded per article so the archive says which tool produced the stored
# text. Extraction quality is version-dependent -- if a future trafilatura
# changes what it keeps, rows are distinguishable rather than silently mixed.
EXTRACTOR = f"trafilatura {getattr(trafilatura, '__version__', 'unknown')}"

_last_fetch_by_domain: dict[str, float] = {}


def _politeness_wait(url: str) -> None:
    domain = urlparse(url).netloc
    last = _last_fetch_by_domain.get(domain)
    if last is not None:
        elapsed = time.monotonic() - last
        remaining = settings.fetch_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
    _last_fetch_by_domain[domain] = time.monotonic()


def extract_one(url: str) -> tuple[str | None, str | None]:
    """Returns (full_text, fail_reason). Exactly one is None."""
    _politeness_wait(url)
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, "fetch_url returned no content (blocked, 404, or network error)"
        text = trafilatura.extract(downloaded, favor_precision=True)
        if not text:
            return None, "extraction produced no text (likely paywalled or non-article page)"
        word_count = len(text.split())
        if word_count < MIN_WORD_COUNT:
            return None, f"extracted text too short ({word_count} words < {MIN_WORD_COUNT})"
        return text, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def extract() -> dict:
    with track_run("extract") as stats:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, url FROM articles WHERE status = 'discovered'"
            ).fetchall()
            stats["candidates"] = len(rows)
            stats["extracted"] = 0
            stats["failed"] = 0
            stats.flush()

            for row in rows:
                text, fail_reason = extract_one(row["url"])
                if text:
                    word_count = len(text.split())
                    content_hash = hashlib.sha256(text.lower().encode()).hexdigest()
                    conn.execute(
                        """UPDATE articles SET full_text=?, word_count=?, status='extracted',
                           content_hash=?, fetched_at=?, extractor=? WHERE id=?""",
                        (
                            text, word_count, content_hash,
                            datetime.now(timezone.utc).isoformat(), EXTRACTOR, row["id"],
                        ),
                    )
                    stats["extracted"] += 1
                else:
                    conn.execute(
                        "UPDATE articles SET status='failed_extract', fail_reason=? WHERE id=?",
                        (fail_reason, row["id"]),
                    )
                    stats["failed"] += 1
                conn.commit()
                stats.flush()
        finally:
            conn.close()
        return stats
