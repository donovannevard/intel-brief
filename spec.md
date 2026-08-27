# Personal Daily Intelligence Brief — Technical Specification

## Project Overview

A fully local, self-hosted personal intelligence briefing system. Agents ingest news via RSS discovery + full-text extraction, process it through a local LLM (DeepSeek 14B via Ollama's OpenAI-compatible API), persist everything to SQLite, and serve a segmented dashboard via FastAPI. The system accumulates value over time: it remembers claims, predictions, narratives, and coverage patterns, and surfaces contradictions, follow-ups, and historical parallels that no news site provides.

**Core principles:**

1. **Everything runs locally.** No cloud APIs, no external databases. The only outbound traffic is fetching feeds/articles.
2. **The DB is the single source of truth.** Agents write to SQLite; the dashboard only reads. Page loads never trigger LLM calls.
3. **RSS for discovery, extraction for content.** Feeds provide structured metadata (URL, title, timestamp, GUID). Full article text is fetched per-URL with `trafilatura`. The LLM always works from full article text, never feed snippets.
4. **Pipeline stages, not monoliths.** Ingestion, extraction, LLM analysis, and derived-insight agents are separate, independently runnable, idempotent stages. A failure in one stage never blocks the others.
5. **Schema anticipates the roadmap.** Later phases (contradictions, predictions, narrative tracking) depend on entities and claims captured from day one, even before those features have UI.

---

## Configuration

### `.env` file (with a committed `.env.example`)

```ini
# --- User context (drives Local and Country tab content) ---
USER_LOCALITY="<city / metro area, e.g. Manchester>"
USER_REGION="<state / county / province, e.g. Greater Manchester>"
USER_COUNTRY="<country, e.g. United Kingdom>"
USER_COUNTRY_CODE="GB"
USER_TIMEZONE="Europe/London"

# --- LLM ---
LLM_BASE_URL="http://localhost:11434/v1"   # Ollama OpenAI-compatible endpoint
LLM_MODEL="deepseek-r1:14b"
LLM_MAX_CONCURRENT=2
LLM_TIMEOUT_SECONDS=180

# --- Embeddings (for parallels, contradictions, dedup) ---
EMBEDDING_BASE_URL="http://localhost:11434/v1"
EMBEDDING_MODEL="nomic-embed-text"

# --- App ---
DB_PATH="./data/intel.db"
DASHBOARD_HOST="0.0.0.0"        # accessible from other devices on LAN, like Open WebUI
DASHBOARD_PORT=8300
FETCH_USER_AGENT="PersonalIntelBrief/1.0"
FETCH_DELAY_SECONDS=2            # politeness delay between article fetches per domain
ARTICLE_RETENTION_DAYS=0         # 0 = keep forever (archive is the point)
```

`USER_LOCALITY` / `USER_COUNTRY` are injected into LLM prompts for the classification stage (deciding local vs national vs global relevance) and used to select/filter feeds.

### `feeds.yaml`

User-editable feed registry. Each feed entry:

```yaml
feeds:
  - name: "BBC News - UK"
    url: "https://feeds.bbci.co.uk/news/uk/rss.xml"
    category_hint: country        # local | country | global | economics | corporate | science | none
    outlet: "BBC"
    outlet_type: mainstream       # mainstream | independent | foreign | official | aggregator
    country: "GB"
    enabled: true
```

Ship a starter `feeds.example.yaml` with a good default set (wire services, major national outlets, foreign outlets like Al Jazeera/DW/NHK World for the foreign-lens feature, arXiv + science outlets for the science tab, and placeholders with comments explaining how to find local-news RSS feeds for the user's area). The app should log a clear warning listing feeds that fail repeatedly.

---

## Architecture

```
┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────────┐
│ 1. Discover │──▶│ 2. Extract   │──▶│ 3. Analyze  │──▶│ 4. Derive        │
│ (RSS poll)  │   │ (trafilatura)│   │ (LLM/embed) │   │ (agents/insights)│
└─────────────┘   └──────────────┘   └─────────────┘   └──────────────────┘
        │                 │                 │                    │
        └─────────────────┴────────┬────────┴────────────────────┘
                                   ▼
                             SQLite (WAL mode)
                                   ▲
                                   │ reads only
                          ┌────────┴────────┐
                          │ FastAPI + UI    │
                          └─────────────────┘
```

**Tech stack:**

- Python 3.11+, managed with `uv`
- `feedparser` (RSS), `trafilatura` (article extraction), `httpx` (fetching)
- `sqlite3` stdlib + `sqlite-vec` extension for vector search (fallback: store embeddings as BLOBs and do brute-force cosine in Python — fine at this scale)
- `openai` Python client pointed at `LLM_BASE_URL`
- FastAPI + Jinja2 templates + htmx for the dashboard (server-rendered, no build step, works on all devices via LAN). Minimal vanilla JS + one small chart library (e.g. Chart.js from a vendored local copy — no CDN dependency) for trend charts.
- APScheduler running inside the app process for scheduling (simpler than cron for a single-repo project; expose "run now" buttons in a admin page as well as CLI entrypoints `python -m app.pipeline discover|extract|analyze|derive|all`)

**Concurrency/robustness requirements:**

- SQLite in WAL mode; single writer discipline (pipeline stages run sequentially by default).
- Every stage is idempotent and resumable: articles carry a `status` field (`discovered → extracted → analyzed → failed_*`), and each run picks up unfinished work.
- All LLM calls: JSON-mode prompting with a strict "respond ONLY with JSON matching this schema" instruction, parsed with a lenient parser (strip code fences, retry once on parse failure, then mark `failed_analysis` and move on). Never let one bad article kill a run.
- Per-domain politeness delay and conditional GET (ETag/Last-Modified) on feed polls.
- Structured logging to `./data/logs/` plus a `pipeline_runs` table recording every run's stats (fetched, extracted, analyzed, failed, duration) — surfaced on an admin/status page.

---

## Database Schema (core tables)

```sql
-- Feed registry mirror + health
feeds(id, name, url, outlet, outlet_type, category_hint, country, enabled,
      etag, last_modified, last_polled_at, consecutive_failures)

-- One row per unique article
articles(id, feed_id, guid, url, title, published_at, discovered_at,
         author, outlet, raw_html_path NULL, full_text, word_count,
         status,                -- discovered|extracted|analyzed|failed_extract|failed_analysis
         content_hash)          -- for cross-outlet near-dupe detection

-- LLM output per article (1:1)
article_analysis(article_id PK,
    summary,                    -- 3-5 sentence neutral summary
    interpretation,             -- what this means / why it matters
    narrative_critique,         -- assumptions, framing, what's not said
    scope,                      -- local|country|global
    categories,                 -- JSON array: politics, economics, corporate, science.math, science.physics, science.computing, science.ai, science.robotics, health, conflict, ...
    sentiment_framing,          -- JSON: {tone, loaded_language: [...], passive_voice_notable: bool}
    importance_score,           -- 1-10 model estimate
    analyzed_at, model_used, prompt_version)

-- Embeddings (title + summary vector)
article_embeddings(article_id PK, embedding BLOB, model, created_at)

-- Entities & claims: captured from Phase 1 even though their UIs come later
entities(id, canonical_name, type,   -- person|corporation|agency|country|party|other
         aliases JSON, first_seen_at, notes)
article_entities(article_id, entity_id, role)   -- subject|mentioned|source

claims(id, entity_id, article_id, claim_text,   -- verbatim or tight paraphrase
       claim_type,               -- statement|promise|prediction|denial
       claim_date, resolves_by DATE NULL,       -- for predictions
       embedding BLOB, created_at)

contradictions(id, claim_a_id, claim_b_id, explanation, confidence,
               detected_at, dismissed BOOL DEFAULT 0)

predictions(id, claim_id, predictor_entity_id, prediction_text,
            due_date, outcome NULL,             -- correct|incorrect|partial|unresolvable
            outcome_note, resolved_at)

-- Narrative / coverage tracking
story_clusters(id, label, created_at, peak_date NULL, status)  -- active|fading|memory_holed|resolved
article_clusters(article_id, cluster_id, similarity)

coverage_stats(date, cluster_id, article_count, outlet_count)  -- daily rollup

-- Daily products
daily_briefs(date PK, generated_at,
             top_stories JSON, disproportionate JSON, underreported JSON,
             learn_today JSON, on_this_day JSON, follow_ups JSON)

historical_parallels(id, article_id, parallel_description, era, similarity_basis,
                     confidence, created_at)

followups(id, cluster_id, created_at, revisit_at, status, resolution_note)

pipeline_runs(id, stage, started_at, finished_at, stats JSON, ok BOOL)
```

Indexes on `articles(published_at)`, `articles(status)`, `claims(entity_id)`, `article_clusters(cluster_id)`.

---

## Pipeline Stages

### Stage 1 — Discover (every 30 min)
Poll enabled feeds with conditional GET. Insert new items by GUID (fallback: URL). Dedup identical URLs across feeds.

### Stage 2 — Extract (follows discover)
For each `discovered` article: fetch URL, run `trafilatura.extract()` (with `favor_precision=True`), store `full_text` and `word_count`. Articles under ~120 words or failing extraction → `failed_extract` with reason. Respect per-domain delay. Optionally store raw HTML to disk (compressed) for the stealth-edit feature later — behind a config flag, default on.

### Stage 3 — Analyze (LLM, batched, runs after extract)
Per article, one structured LLM call producing the full `article_analysis` row **plus** entity mentions and any notable claims/predictions (written to `entities`/`claims` with alias-matching: exact + case-insensitive + embedding-similarity against existing `canonical_name`/`aliases`; ambiguous matches create a new entity flagged for review). Then compute and store the embedding.

Prompting requirements:
- System prompt includes `USER_LOCALITY`, `USER_REGION`, `USER_COUNTRY` so `scope` classification is user-relative.
- Keep prompts in versioned files under `app/prompts/`; store `prompt_version` on every analysis row.
- Chunk long articles (>~3000 words): summarize chunks, then analyze the merged summary. Keep it simple.

### Stage 4 — Derive (daily, e.g. 05:30 local time)
Runs the insight agents against analyzed data, then assembles `daily_briefs`:

1. **Clustering**: embedding-similarity clustering of the last 72h of articles into `story_clusters` (greedy: cosine > threshold joins cluster; else new cluster; LLM labels new clusters). Update `coverage_stats`.
2. **Disproportionate coverage**: clusters whose `article_count`/`outlet_count` z-score vs trailing 14-day baseline exceeds threshold → LLM writes a short "why might this be saturating coverage" note (hypotheses, clearly framed as hypotheses).
3. **Underreported**: clusters with high mean `importance_score` but low outlet_count; plus stories present in foreign-outlet feeds but absent from domestic clusters (the foreign-lens diff).
4. **Contradiction scan**: for each new claim, vector-search prior claims by the same entity (and same-topic clusters); candidate pairs above similarity threshold go to the LLM with both quotes + dates: "Does B contradict/backtrack on A? JSON: {contradicts: bool, explanation, confidence}". Only high-confidence hits are stored; UI allows dismissing false positives (`dismissed` flag feeds future threshold tuning).
5. **Prediction resolution**: predictions past `due_date` get queued to a review list; the LLM drafts a suggested outcome from any matching recent articles, but **the user confirms outcomes manually** in the UI (keeps the scorecard honest).
6. **Historical parallels**: for the day's top clusters, prompt the LLM: "What historical events (>10 years ago) does this resemble? Basis: structural, not superficial." Store with confidence; render clearly as model-generated speculation.
7. **Follow-up hound**: new major clusters get `followups` rows at +30/+60/+90 days; due follow-ups trigger a search of recent articles in that cluster — if silence, mark candidate `memory_holed` and surface it.
8. **One thing worth learning**: pick from the science/eccentric pool (prefer arXiv/science feeds, novelty-scored by the LLM, dedup against previously featured items).
9. **On this day**: from the LLM's own knowledge, one notable event from this calendar date decades prior, connected (if possible) to a current theme.

---

## Dashboard (FastAPI + Jinja2 + htmx)

**Tabs / routes:**

| Route | Content |
|---|---|
| `/` Today | The assembled daily brief: top stories per scope, learn-today, on-this-day, follow-ups due, new contradictions |
| `/local` | scope=local articles + analysis |
| `/country` | scope=country |
| `/global` | scope=global |
| `/economics` | category filter: economics/markets |
| `/corporate` | category filter: corporate; entity-centric view (group by corporation) |
| `/science` | sub-filters: maths, physics, computing, AI, robotics, other |
| `/coverage` | disproportionate + underreported lists, cluster coverage trend charts, foreign-lens diff |
| `/contradictions` | the archive: entity, quote A (date, source link), quote B, explanation; filter by entity; dismiss button |
| `/scorecard` | predictions table + per-entity accuracy stats; resolution review queue |
| `/parallels` | historical parallels log |
| `/archive` | full-text search (SQLite FTS5) over all articles + analyses |
| `/status` | pipeline run history, feed health, failed articles, "run stage now" buttons |

**Design requirements:** clean, dense, newspaper-like; dark mode; fully usable on mobile (this is read over morning coffee on a phone); every article card links to the original URL; every LLM-generated interpretation is visually distinguished from factual metadata (e.g., an "analysis" treatment) so the user never confuses model opinion with source content.

---

## Build Phases

**Phase 1 — Foundation (build first, end-to-end):**
Config/env loading, feeds.yaml, full schema migration, Stages 1–3, and a minimal dashboard: Today (simple version: top N by importance per scope), the four scope/category tabs, Archive with FTS, Status page. *Milestone: user edits .env + feeds.yaml, runs one command, gets a working segmented brief the next morning.*

**Phase 2 — Insight agents:**
Clustering, coverage stats, disproportionate/underreported, foreign lens, daily brief assembly, learn-today, on-this-day, Coverage tab.

**Phase 3 — Memory features:**
Contradiction scan + tab, predictions + scorecard + resolution queue, follow-up hound, historical parallels, memory-hole detection.

**Phase 4 — Nice-to-haves (stubs/flags acceptable):**
Stealth-edit diffing (from stored raw HTML), talking-point detector (near-identical phrasing across outlets via shingled hashes), narrative lifecycle charts, base-rate checker, steelman generator per dominant narrative, user's own prediction journal.

**Testing:** unit tests for extraction/dedup/claim-matching logic with fixture HTML/RSS files; a `--dry-run` pipeline mode using fixtures so the whole system is testable without network or LLM (LLM calls mockable behind a thin client interface).

**Deliverables:** the repo, `README.md` with setup (uv, Ollama models to pull, systemd unit example for boot persistence), `.env.example`, `feeds.example.yaml`.