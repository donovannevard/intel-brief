"""CLI entrypoints: python -m intel_brief.cli discover|extract|analyze|derive|all"""

import os
import sys

from intel_brief.db import init_db
from intel_brief.feeds import sync_feeds


def _init_feeds():
    """Build this deployment's feeds.yaml from the shipped template plus
    whatever LOCAL_PLACES implies. Never overwrites an existing file."""
    from pathlib import Path
    from intel_brief.config import settings
    from intel_brief.feeds import init_feeds
    app_dir = Path(__file__).resolve().parent.parent
    tpl = Path(os.environ.get("FEEDS_TEMPLATE", app_dir / "feeds.example.yaml"))
    if not tpl.exists():
        print(f"init-feeds: no template at {tpl}")
        sys.exit(1)
    r = init_feeds(tpl, settings.feeds_path, settings.local_places)
    if not r["written"]:
        print(f"init-feeds: {r['path']} {r['reason']} -- left untouched")
        return
    print(f"init-feeds: wrote {r['path']} "
          f"({r['total_feeds']} feeds, {r['local_feeds']} local)")
    if r["regions"]:
        print(f"            regions: {', '.join(r['regions'])}")
    if r["unmatched"]:
        print(f"            no region matched: {', '.join(r['unmatched'])}")
    if not settings.local_places:
        # The old behaviour here was silence, which read as success. An unset
        # LOCAL_PLACES means no local feeds *and* nothing classified as local.
        print("            WARNING: LOCAL_PLACES is not set in .env -- no local feeds,")
        print("            and the Local tab will stay empty. Set it, delete this")
        print("            feeds.yaml, and re-run install.sh. See docs/uk-regions.md.")


def main():
    if len(sys.argv) < 2:
        print("usage: python -m intel_brief.cli "
              "init-feeds|discover|extract|analyze|derive|all")
        sys.exit(1)

    stage = sys.argv[1]

    # init-feeds runs before the database and feed sync on purpose: it is what
    # creates feeds.yaml, and sync_feeds() cannot read a file that does not
    # exist yet. Everything else needs both.
    if stage == "init-feeds":
        _init_feeds()
        return

    init_db()
    sync_feeds()

    if stage in ("discover", "all"):
        from intel_brief.pipeline.discover import discover
        print("discover:", discover())

    if stage in ("extract", "all"):
        from intel_brief.pipeline.extract import extract
        print("extract:", extract())

    if stage in ("analyze", "all"):
        from intel_brief.pipeline.analyze import analyze
        print("analyze:", analyze())

    if stage in ("derive", "all"):
        from intel_brief.pipeline.derive import assemble_morning_brief
        print("derive:", assemble_morning_brief())


    if stage not in ("discover", "extract", "analyze", "derive", "all"):
        print(f"unknown stage: {stage}")
        sys.exit(1)


if __name__ == "__main__":
    main()
