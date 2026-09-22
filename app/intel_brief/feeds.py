"""Loads feeds.yaml and syncs it into the feeds table (insert-or-update by URL,
never delete -- removing a feed from the YAML just stops it being polled, keeps
its history)."""

import yaml

from intel_brief.config import settings
from intel_brief.db import get_connection


def load_feeds_yaml() -> list[dict]:
    with open(settings.feeds_path) as f:
        data = yaml.safe_load(f)
    return data.get("feeds", [])


def sync_feeds() -> int:
    feeds = load_feeds_yaml()
    conn = get_connection()
    try:
        for feed in feeds:
            existing = conn.execute("SELECT id FROM feeds WHERE url = ?", (feed["url"],)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE feeds SET name=?, outlet=?, outlet_type=?, category_hint=?,
                       country=?, enabled=? WHERE url=?""",
                    (
                        feed["name"], feed.get("outlet"), feed.get("outlet_type"),
                        feed.get("category_hint"), feed.get("country"),
                        bool(feed.get("enabled", True)), feed["url"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO feeds (name, url, outlet, outlet_type, category_hint, country, enabled)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        feed["name"], feed["url"], feed.get("outlet"), feed.get("outlet_type"),
                        feed.get("category_hint"), feed.get("country"),
                        bool(feed.get("enabled", True)),
                    ),
                )
        conn.commit()
        return len(feeds)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Generating feeds.yaml for a deployment
#
# feeds.yaml is not in the repository: it names the region someone follows,
# which is theirs and not the project's. It is built once at install time from
# feeds.example.yaml (national and global sources, identical for everyone) plus
# whatever LOCAL_PLACES implies (see uk_regions.py), and then left alone.
# ---------------------------------------------------------------------------

# First line of every generated feeds.yaml. install.sh looks for it to tell a
# file this code wrote (the operator's now, edits and all -- never touched
# again) from one that pre-dates generation, which reflects nothing in
# LOCAL_PLACES and would otherwise be kept forever just because it exists.
GENERATED_MARKER = "# generated-by: intel-brief init-feeds"

_GENERATED_HEADER = """
  # --- Local news (generated from LOCAL_PLACES) -----------------------------
  # Derived at install time from LOCAL_PLACES in .env, via uk_regions.py.
  # Regions matched: {regions}
  #
  # Edit freely -- this file is yours and no deploy will overwrite it. If you
  # change LOCAL_PLACES later, either add the feeds by hand or delete this file
  # and re-run the installer to regenerate it.
  #
  # Each source gets its own outlet name rather than folding into "BBC",
  # because selection gives every outlet one slot per round (pipeline/select.py).
  # Inside a single "BBC" bucket a regional story loses every slot to a national
  # one, and the Local tab stays empty -- which is the whole point of these."""

_NO_MATCH_NOTE = """
  # --- Local news -----------------------------------------------------------
  # No local feeds were generated: LOCAL_PLACES in .env matched none of the
  # regions in uk_regions.py ({unmatched}).
  #
  # Local classification still works off the place names you gave, so anything
  # a national feed publishes about them will be filed as local -- there is
  # just no regional publisher being polled. See docs/uk-regions.md for the
  # place names that select a region."""


def render_feeds_yaml(template: str, local_places) -> str:
    """feeds.example.yaml plus the local feeds implied by LOCAL_PLACES.

    Appends text rather than re-serialising the parsed YAML, because the
    template is mostly comments explaining why each source is there and what
    was rejected -- round-tripping it through a YAML dumper would throw all of
    that away and hand the operator a file with no reasoning in it.
    """
    from intel_brief import uk_regions

    places = list(local_places)
    matched = uk_regions.regions_for_places(places)
    body = GENERATED_MARKER + "\n" + template.rstrip("\n")

    if not matched:
        note = _NO_MATCH_NOTE.format(
            unmatched=", ".join(uk_regions.unmatched(places)) or "none set")
        return body + "\n" + note + "\n"

    out = [body, _GENERATED_HEADER.format(
        regions=", ".join(r["label"] for r in matched))]
    for f in uk_regions.feeds_for_places(places):
        out.append(f'  - name: "{f["name"]}"')
        out.append(f'    url: "{f["url"]}"')
        out.append(f'    category_hint: {f["category_hint"]}')
        out.append(f'    outlet: "{f["outlet"]}"')
        out.append(f'    outlet_type: {f["outlet_type"]}')
        out.append(f'    country: "{f["country"]}"')
        out.append(f'    enabled: true')
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def init_feeds(template_path, out_path, local_places) -> dict:
    """Write feeds.yaml from the template. Never overwrites an existing file."""
    import pathlib
    import yaml as _yaml
    from intel_brief import uk_regions

    out = pathlib.Path(out_path)
    if out.exists():
        return {"written": False, "reason": "already exists", "path": str(out)}

    text = render_feeds_yaml(pathlib.Path(template_path).read_text(), local_places)
    # Parse before writing: a malformed feeds.yaml stops the service booting,
    # and generating one is exactly when nobody is watching.
    parsed = _yaml.safe_load(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("feeds"), list):
        raise ValueError("generated feeds.yaml is not a mapping with a 'feeds' list")

    out.write_text(text)
    matched = uk_regions.regions_for_places(local_places)
    return {
        "written": True, "path": str(out),
        "regions": [r["label"] for r in matched],
        "local_feeds": len(uk_regions.feeds_for_places(local_places)),
        "total_feeds": len(parsed["feeds"]),
        "unmatched": uk_regions.unmatched(local_places),
    }
