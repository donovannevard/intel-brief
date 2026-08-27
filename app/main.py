"""Server entrypoint. Runs the FastAPI dashboard plus the APScheduler-driven
pipeline chain (discover -> extract -> analyze -> derive, once daily at
PIPELINE_RUN_TIME).

Does not unconditionally run the pipeline chain on startup -- a restart at
2pm shouldn't spike GPU load then, since this service shares the GPU with
interactive/gaming use. It DOES catch up once if today has no successful
brief yet and PIPELINE_RUN_TIME has already passed (e.g. the machine was off
overnight through the scheduled time) -- see scheduler.py for the exact
condition."""

import logging

import uvicorn

from intel_brief.config import settings
from intel_brief.db import init_db
from intel_brief.feeds import sync_feeds
from intel_brief.markets.store import init_markets_db
from intel_brief.outlets import init_outlet_logos
from intel_brief.scheduler import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    init_db()
    init_markets_db()  # separate file from intel.db -- see markets/store.py
    init_outlet_logos()
    sync_feeds()

    scheduler = build_scheduler()
    scheduler.start()

    uvicorn.run(
        "intel_brief.web.app:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
    )


if __name__ == "__main__":
    main()
