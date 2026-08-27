# intel-brief/app

FastAPI application source for [intel-brief](../). See the [top-level intel-brief README](../README.md) for what this project is, and [../spec.md](../spec.md) for the full design.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in USER_LOCALITY/REGION/COUNTRY and LLM/EMBEDDING endpoints
cp feeds.example.yaml feeds.yaml   # then edit the feed list
```

`LLM_BASE_URL`/`EMBEDDING_BASE_URL` take any OpenAI-compatible endpoint — llama-swap, ollama, LM Studio, a hosted API. Leave them empty and intel-brief runs as a reader and archive with no model at all; set them and the interpretation layer switches on. Whatever you point them at has to be reachable for analysis and embeddings to work. See `.env.example` for every setting, with inline comments explaining the time-budget and GPU-contention-guard options.

## Running

**As a service** (what `main.py` does): starts the FastAPI dashboard plus an in-process APScheduler that runs the full pipeline chain (`discover → extract → analyze → derive`) once daily at `PIPELINE_RUN_TIME`. Does *not* run the pipeline on startup — the app is meant to stay idle outside its scheduled window.

```bash
uv run python main.py
```

**Individual pipeline stages**, for development/debugging (does not start the dashboard or scheduler):

```bash
uv run python -m intel_brief.cli discover|extract|analyze|derive|all
```

## Layout

- `intel_brief/pipeline/` — `discover.py` (RSS ingestion), `extract.py` (`trafilatura` full-text fetch), `analyze.py` (LLM categorization/scoring), `cluster.py` (embedding-similarity dedup), `derive.py` (morning brief assembly).
- `intel_brief/web/` — FastAPI routes + Jinja templates for the dashboard.
- `intel_brief/gpu_guard.py` — checks `intel_gpu_top` so `analyze()` can pause itself when something else is using the GPU. Requires `setcap cap_perfmon=ep $(which intel_gpu_top)` on the host.
- `intel_brief/db.py` — SQLite schema (WAL mode, FTS5 search over articles).
- `intel_brief/scheduler.py` — the daily pipeline-chain job.
- `intel_brief/cli.py` — manual per-stage entrypoints.

## Data

`data/intel.db` (SQLite, gitignored) is the single source of truth — the dashboard only reads from it, page loads never trigger LLM calls. `data/logs/` holds run logs.
