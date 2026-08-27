# intel-brief — build record

A personal, fully-local news-and-markets intelligence dashboard: RSS discovery, full-text
extraction, LLM analysis, story clustering, a curated daily morning brief, and a merged
financial-markets half with its own LLM narrative layer. Talks to any
OpenAI-compatible endpoint and ships no inference stack of its own. Full technical specification in
[`spec.md`](../spec.md).

**Status: running as a real systemd service** (`intel-brief.service`, LAN-reachable
dashboard on `DASHBOARD_PORT`, 8300 by default). This document is the combined build record across
all three stages — what was tried, what broke, and why each decision was made.

| Stage | Deliverable | Status |
|---|---|---|
| 1 | Foundation — config, schema, discover/extract/analyze, dashboard, scheduling | complete |
| 2 | Derive — clustering and a genuinely curated morning brief | complete |
| 3 | Markets merge — instruments, price API, per-tab LLM narratives, news↔market correlation | complete |

The API contract other projects consume is documented separately in
[markets-api.md](markets-api.md).

---

## Architecture as it stands

```
Intel Brief
│
├── NEWS ──────────── Today · Local · Country · Global · Economics ·
│                     Corporate · Crypto · Science · Archive
│
├── MARKETS ───────── Crypto · Stock Market · Commodities · Monetary System
│
└── STATUS ────────── pipeline runs, feed health, article counts, ingest health,
                      archive coverage chart, single-day backfill
```

**Daily chain at 05:30, one sequence, news first:**

```
discover → extract → analyze → derive → markets_ingest → market_brief → correlate
```

News is fully processed before markets start, so the correlation stage can read that day's
finished analysis and embeddings. Each stage opens a `pipeline_runs` row through the shared
`track_run()` context manager, so the progress banner and Status page pick up new stages for
free. A separate `markets_ingest` job runs every 6 hours on its own — it's network-only, so
it deliberately does *not* honour the pause switch, since pause means "release the GPU".

**Two databases, deliberately:**

| File | Contents | Backup priority |
|---|---|---|
| `data/intel.db` | Articles, analysis, entities, claims, embeddings, FTS5 index, market briefs, news↔market links | **Irreplaceable — back this up** |
| `data/markets.db` | `market_prices`, `market_fetch_meta` | Regenerable from the APIs; can be thrown away |

The split means `markets.db` can be handed to another process read-only without leaking
anything personal. `market_news_links` references `articles`, so it lives in `intel.db` —
SQLite has no cross-database foreign keys. The correlation stage reads prices via `ATTACH`.

**Inference:** the existing `llama-swap` endpoint (`http://127.0.0.1:8090/v1`) for both chat
(`qwen2.5-7b`) and embeddings (`nomic-embed-text`). Page loads never make an LLM call.

---

## 1. Foundation

Config, full schema, discover/extract/analyze, minimal dashboard and scheduling — validated
with real feeds, real extraction, real LLM analysis, and a real reboot (unplanned, mid-build)
plus a deliberate restart test.

- **`intel-brief.service`** — hardened systemd unit mirroring `llama-swap.service`'s
  hardening, `User=svc-intel-brief`. A *new* dedicated service user rather than a reuse of
  whatever account runs the inference server: this app accumulates a far more detailed
  personal record than shared inference infrastructure does. Deployed to `/var/lib/intel-brief/app/`, `Restart=always`.
- **APScheduler inside the app process**, not a separate timer.

### Corrections carried over from earlier work

- **`qwen2.5-7b`, not DeepSeek 14B.** The spec originally called for DeepSeek 14B via
  Ollama; that model measured both slower and *less* reliable at staying grounded
  in given text for this exact kind of narrative-analysis task. Corrected before writing
  any code.
- **`llama-swap`, not a second Ollama stack.** One inference backend for the whole machine.

### Real bugs found and fixed

1. **FTS5 external-content corruption** — genuine and reproducible. The first schema synced
   `articles_fts` via plain `UPDATE`/`DELETE` triggers against the FTS5 virtual table.
   External-content FTS5 tables don't support that; the documented pattern is a special
   `INSERT INTO fts_table(fts_table, rowid, ...) VALUES ('delete', ...)` before re-inserting
   on update. Got `sqlite3.DatabaseError: database disk image is malformed` on the very
   first real Extract run, reproduced identically in the foreground (ruling out the
   "background task got killed" theory). Fixed in `db.py`; re-verified across a 195-article
   real batch.
2. **arXiv's RSS feed is a firehose, not "today's headlines".** The first Discover run
   pulled **938** arXiv CS entries out of 1267 total — the feed is every recent submission,
   not day-scoped, and would have dominated Extract/Analyze with content the Science tab
   only wants one curated pick from. Added `MAX_NEW_PER_FEED_PER_POLL = 25`. The same fix
   an earlier digest pipeline needed, caught this time *before* burning an hour of extraction.
3. **`trafilatura.fetch_url()` still doesn't take a `timeout` kwarg** — same library
   version, same gotcha as before. Avoided by remembering rather than re-discovering.
4. **`starlette`'s `TemplateResponse` calling convention changed** in the version `uv`
   resolved (1.3.1): now `TemplateResponse(request, name, context)` with `request` as a
   required positional, not embedded in the context dict. `TypeError: unhashable type: 'dict'`
   on every route until fixed across all 9 call sites.
5. **A CDN link for htmx** slipped in initially — caught and fixed before it shipped, in
   line with the spec's own no-CDN principle. `htmx.min.js` is vendored in `web/static/`.
6. **No `busy_timeout` on SQLite connections** — added `PRAGMA busy_timeout=30000` once it
   became clear more than one writer could realistically be active (the scheduler's chain
   and a manual CLI run overlapping, or a resumed backlog run coinciding with the next
   chain). Not yet hit in practice, but connection-per-call in WAL mode doesn't handle it
   for free.

### Continuous 30-minute polling caused real GPU contention

Within a couple of hours of going live, a game (GTA 4) dropped to 18fps. Root cause: the
scheduler ran the full pipeline chain every 30 minutes continuously, so — unlike a digest
job that runs once and finishes — `qwen2.5-7b` was loaded and actively
computing a large fraction of the time. One shared Arc iGPU; there's no way for game
rendering and LLM inference to both be smooth at once.

Fixed two ways:

1. **Immediate relief** — `llama-swap`'s `POST /api/models/unload/{model}` frees the model
   without waiting out the 30-minute idle TTL. Confirmed via `ps`, not just an HTTP 200.
2. **Structural** — the scheduler moved from a 30-minute interval trigger to a single daily
   `CronTrigger` at `PIPELINE_RUN_TIME` (05:30), and the "run immediately on startup"
   behavior was removed entirely; that would spike GPU load at whatever arbitrary time the
   service happens to restart, defeating the point of scheduling it away from active hours.

An **evening brief was considered and dropped**: a single morning roundup of the prior day
is all that's wanted, and it's cheaper on GPU and energy. The `evening_brief` job, its
config and its stub were removed rather than left as unused scaffolding.

### Unplanned validation: the machine rebooted mid-build

`llama-swap.service` came back up correctly on its own — confirming the systemd setup is
genuinely robust rather than tested-once — while intel-brief's manually-launched dev-mode
processes did not, as expected at that point. The DB was untouched, WAL-safe on disk.

### Verified

- Real Discover (8 feeds, capped correctly), Extract (195 candidates → 160 extracted, 35
  failed — almost all NYT paywall, matching the earlier finding exactly), Analyze (105+
  articles, **0 failures**, entities/claims/embeddings all correctly written).
- All 9 routes returned 200 with real rendered content; FTS5 archive search returned correct
  cross-outlet results.
- Real chat + embedding calls through `llama-swap` from the app, not just standalone `curl`.
- Service starts clean, survives a deliberate restart, auto-starts at boot.

---

## 2. Derive — the morning brief

The Today view shows a genuinely curated brief (deduplicated, categorized, sports/celebrity
filtered) rather than a raw article list. No push delivery — the dashboard itself, refreshed
before breakfast, is enough.

- **`pipeline/cluster.py`** — greedy embedding-similarity clustering (cosine threshold 0.80)
  over the last 72h of analyzed articles. Existing clusters are represented by their oldest
  member's embedding; a new article either joins the closest cluster above threshold or
  starts a new one. Writes `story_clusters`, `article_clusters`, `coverage_stats`.
- **`pipeline/derive.py`** — `assemble_morning_brief()` runs clustering, then builds
  `top_stories` (clusters with a fresh article in the last 24h, ranked by importance, each
  showing the representative article and which outlets covered it) and `learn_today` (the
  highest-importance science article, preferring the two dedicated science feeds, deduped
  against prior days' picks and against `top_stories`). Writes one `daily_briefs` row per day.
- **Dashboard `/`** reads today's `daily_briefs` row if present, falling back to a raw top-N
  list if no brief has been assembled yet. Pure read, no LLM calls on page load.

### Validated against real data

Tested against the 187+ real analyzed articles already in the DB, not synthetic data.

**Clustering collapsed genuine duplicates correctly**: 5 separate BBC/DW articles about Andy
Burnham forming his cabinet merged into one cluster (similarities 0.81-0.86), while
genuinely distinct Burnham stories — his wife, oil/gas policy, his political style, an
international-role piece — correctly stayed separate. The threshold is doing real semantic
work, not keyword-matching on "Burnham".

Three bugs caught **before** they reached the dashboard, all by reading rendered output
rather than checking that queries ran without error:

1. **Sports leaked into top stories.** A cricket article ("Garfield Sobers... ultimate
   all-round sportsman") scored importance 8 and would have sat alongside major geopolitical
   news. Fixed by carrying over the same category filter and reasoning from earlier work.
2. **`learn_today` picked "Apple regains top spot as world's most valuable company"** — a
   business headline, not science. Fixed with a cheap targeted preference: rank the two
   dedicated science feeds above other outlets' loosely-tagged science content, rather than
   trying to fix the underlying classifier. Real result after the fix: an Ars Technica piece
   about a planet that survived its star's death.
3. **The same article appeared as both a top story and the `learn_today` pick.** Fixed by
   passing the top-stories article IDs in as an exclusion set.

Clustering and brief assembly both run with **zero LLM calls** (pure SQL plus reused
embeddings), so they're safe to test repeatedly without GPU contention.

### Deliberately out of scope for this version

Disproportionate/underreported coverage (needs a 14-day trailing baseline that didn't exist
yet), foreign-lens diff, follow-up hound, contradictions, predictions/scorecard, historical
parallels. **"On this day"** was skipped rather than shipped: it relies on the model's own
parametric historical knowledge rather than grounded retrieval, making it the
highest-hallucination-risk feature in the spec. **Cluster labels are just the first
article's title** rather than LLM-generated, to avoid an extra call per new cluster — fine
in practice, since titles are usually reasonable labels.

### Taxonomy refinement and two new sources

Feedback after seeing the real dashboard: a dedicated Crypto/Bitcoin section, Maths folded
into Science, and Economics/Corporate tightened up (too many articles from other tabs were
also flagged corporate, so that tab mostly duplicated content).

- **New categories**: `bitcoin` (adoption, regulatory and ecosystem news — explicitly *not*
  pure price moves, which stay under `economics`) and `science.cryptography` (the maths/CS
  discipline, deliberately named to avoid colliding with `bitcoin` — same word, two
  meanings, easy to conflate in a prompt).
- **Tightened `economics`** to macro/market-moving content only: commodity and crypto price
  moves, central-bank decisions, inflation/GDP/employment data.
- **Tightened `corporate`** to require a major corporation as the actual *subject* of the
  story — earnings, executive moves, M&A, major announcements — not merely mentioned in
  passing. This was the direct fix for the duplication complaint.
- **New feeds**: CoinDesk (live-checked; Bitcoin Magazine returned 403/bot-blocked so wasn't
  used) and arXiv Mathematics.
- `PROMPT_VERSION` bumped so future work can identify which articles used the older, looser
  taxonomy.

**Explicit decision: the existing ~187-article backlog was not re-tagged.** The real cost
(~1.5-2.5 hours of continuous GPU, extrapolated from the observed per-article rate) was
stated plainly before proceeding, and the choice was to let the new taxonomy apply going
forward. The brief itself is unaffected (it only looks at the last 24h); the category tabs
showed a mix of old and new tagging until the backlog cycled out.

### `analyze()` ran 5.8 hours unbounded, and the brief went stale

The first two unattended 05:30 runs surfaced a worse problem than anticipated:

- `analyze()` ran at ~90-108s/article in practice, not the ~30s extrapolated from
  earlier digest work — intel-brief's per-article call is heavier (structured JSON
  with summary, interpretation, critique, categories, sentiment, entities and claims, plus
  a separate embedding call). On 2026-07-20: 207 candidates, **20,822 seconds (5.8 hours)**.
- `analyze()` had no explicit ordering, so with a leftover backlog in `extracted` status it
  worked through old articles before reaching same-day fresh ones.
- `morning_brief` was a *separate* 07:00 cron trigger, independent of whether `analyze` had
  finished, so it assembled from whatever partial data existed — 5 top stories instead of
  the usual ~12.
- **Worst part:** once `analyze` finally finished hours later with a full fresh dataset in
  the DB, **nothing re-triggered the brief**. The dashboard stayed frozen on the incomplete
  07:00 snapshot until the next day — found at 14:00 with 193 freshly-analyzed articles
  sitting unused.

Three fixes together:

1. **Newest-first ordering** — when not everything can be processed in the time available,
   what *does* get processed should be the most current news.
2. **`ANALYZE_TIME_BUDGET_SECONDS`** (default 3600) — `analyze()` checks elapsed time before
   starting each article and stops picking up new ones once the budget is exceeded, rather
   than running to unbounded completion.
3. **`derive` now runs immediately after `analyze`**, as the last step of the same
   `run_pipeline_chain` job, instead of on its own cron trigger. This fixes both the partial
   snapshot problem (the brief is only assembled once analyze has stopped, whether by
   finishing or hitting budget) and the staleness problem (there's no longer a separate
   trigger to go stale relative to). `MORNING_BRIEF_TIME` no longer exists — one job,
   sequential, done.

Also added **`STALE_ARTICLE_HOURS`** (default 48): articles sitting unanalyzed past this age
are marked `stale` and permanently excluded, rather than indefinitely losing the newest-first
race and accumulating as dead weight. An article that would only be analyzed after 2+ days
isn't "yesterday's news" for a daily brief anyway.

Expected worst case: discover (~10s) + extract (~6-7min) + analyze (capped at 3600s) +
derive (~5s) ≈ **~67-70 minutes**, 05:30 → done by ~06:40.

**Deliberately not done:** reducing `MAX_NEW_PER_FEED_PER_POLL`. The time budget already
bounds total runtime, but a lower cap would reduce wasted `extract()` effort — roughly 200
articles are discovered daily and only ~35-40 fit the analyze budget, so extract does full
work on articles ~85% of which won't be analyzed same-day. Raised as an available
optimization rather than implemented unilaterally.

### GPU contention guard

The time-budget fix ran cleanly for three consecutive real days, confirmed via
`pipeline_runs` timestamps. Then a request: automatically pause `analyze()` if something
GPU-intensive is actively running — the direct fix for the framerate incidents.

- **`intel_brief/gpu_guard.py`** uses `intel_gpu_top -J -s 500 -n 3` (Intel's own tool, from
  `igt-gpu-tools`) to sample the **Render/3D** engine specifically — what games use. Our own
  inference load appears on the separate **Compute** engine, so the guard naturally never
  mistakes our work for contention.
- **Requires `CAP_PERFMON`** to read GPU perf counters. Rather than root or interactive
  `pkexec` (which can't work from an unattended service), the capability is granted to the
  binary: `setcap cap_perfmon=ep $(which intel_gpu_top)`. Verified as the actual
  unprivileged `svc-intel-brief` account, not just as your own login.
- **`-n 3` matters**: `intel_gpu_top -J` only emits a *valid* JSON array once it exits
  cleanly. Killing it early (e.g. via `timeout`) leaves a truncated, unparseable array;
  `-n <count>` makes it exit gracefully after exactly that many samples.
- Wired into `analyze()`'s per-article loop: if Render/3D is above 30% averaged over 3
  samples, sleep `GPU_PAUSE_CHECK_INTERVAL_SECONDS` (120s) and re-check. **Pause time is
  excluded from the analyze budget** (tracked as `active_elapsed` vs wall clock), so a
  paused run doesn't eat the budget meant for processing. `GPU_PAUSE_MAX_TOTAL_SECONDS`
  (4h) caps cumulative pause per run so a monitoring bug can't stall a run forever.
- **Fails open by design**: if `intel_gpu_top` is unavailable or the check errors, that's
  treated as "not busy" rather than blocking the pipeline on a monitoring failure.
- `GPU_BUSY_THRESHOLD_PERCENT = 30.0` is an initial estimate, **not validated against real
  gameplay** — same caveat as the 0.80 clustering threshold.

A **manual pause switch** complements it: a Pause/Resume button in the status banner holds
analysis indefinitely — no threshold guessing, no 4h cap — *and* releases the GPU
immediately via `llama-swap`'s `/unload` rather than waiting out its idle TTL. An article
cut short by a pause is requeued, not marked failed. State lives in the DB, so it survives a
service restart. See `app/intel_brief/control.py`.

### A missed-run gap, and why "start on login" wouldn't have helped

On 2026-07-24 there was no brief: the machine had been off and only booted at 08:05, after
the 05:30 trigger. The service *did* auto-start correctly at boot; the gap is that a plain
daily `CronTrigger` doesn't retroactively catch up a time it missed while the process wasn't
running.

The first instinct was "start on login" instead. Investigated rather than assumed: this
machine has **autologin configured** (the desktop user in the `autologin` group, `gdm-autologin`
PAM config, confirmed via `loginctl`), so login happens automatically as part of boot.
"Start on login" and "start on boot" are the *same trigger point* here, and switching
wouldn't have changed the outcome on the 24th at all — the machine still wouldn't have been
on before 08:05. It would also have meant either running as the desktop user instead of the
isolated service account, or building real session-tracking machinery, for no behavior
change. See "What catch-up does and doesn't do" below for the rule that was eventually
implemented.

---

## 3. Markets merge

Brought a pre-existing financial-markets dashboard into intel-brief, merged the two
dashboards behind one News/Markets navigation, and added a local-LLM layer over the market
data — including cross-referencing it against the news the pipeline already analyses. The
trading and custody machinery it used to sit beside stayed behind: the markets code was
self-contained enough to lift out cleanly, and nothing in it was imported by that side.
intel-brief holds no execution code and cannot move money.

### What moved

| Source | Size | Disposition |
|---|---|---|
| `dashboard/markets.py` | 1031 lines | Moved wholesale, split into a package |
| `dashboard/server.py` → `/api/markets`, `/api/btc-tracker` | ~20 lines | Rewritten as intel-brief routes |
| `dashboard/static/index.html` → markets CSS/markup/JS | ~1000 lines of 2961 | Moved |
| Plotly 2.32.0 | CDN `<script>` | Vendored locally |

**Data migration: none required.** `market_prices.db` was gitignored, documented as a
regenerable cache, and didn't exist on this machine — verified. Every source except two
serves full history on request. The exceptions are the series the dashboard builds itself
because no free historical API exists (Bitnodes reachable-node count, mempool.space LN avg
fee rate and total node count); they start accumulating from first run, and since the DB was
absent they'd have started from zero regardless.

### Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Theme | Dark-only, markets palette | One palette to maintain. intel-brief's light mode dropped; its *structure* kept |
| Databases | Two files — `intel.db` + `markets.db` | Different exposure, different backup priority |
| Price access for other projects | JSON endpoint + group-readable DB file | Endpoint is the stable contract; the file stays available for bulk queries |
| MSTR | Price + share count over time; no holdings | Position tracking moves to `sovereign-stack` |
| Markets sub-navigation | Crypto / Stock Market / Commodities / Monetary System | Replaces the source repo's two-tab split, mirroring the news side's category organisation |

**On that fourth tab's name:** the content is inflation, central-bank balance sheets,
government debt, fiat purchasing power, FX vs USD, bond yields and wealth concentration.
Every one is an aspect of how the monetary system is run and what it does to the value of
money. "Monetary System" is accurate, covers all seven groups, and doesn't editorialise in
the tab bar itself — the group descriptions already do plenty of that.

Wealth concentration was split off from the old "Risk & Wealth Concentration" group: it's a
consequence of monetary policy, not a market-stress signal. VIX and the HY spread moved to
Stock Market, where they read better next to equities as stress gauges.

### Code layout

```
intel_brief/
├── markets/
│   ├── registry.py     INSTRUMENTS, GROUP_META, tab assignments, period table
│   ├── sources.py      yfinance / FRED / blockchain.info / Bitnodes / mempool.space
│   ├── store.py        markets.db connection, read/write, staleness checks
│   ├── ingest.py       scheduled refresh of every series (network only, no GPU)
│   └── service.py      per-tab payload builder
├── pipeline/
│   ├── (news stages)
│   ├── digest.py       computed factual layer for markets — no LLM
│   ├── market_brief.py digest + LLM narrative per tab
│   └── correlate.py    news ↔ markets association pass
└── web/
    ├── app.py          + markets routes, + price API
    └── templates/      + markets pages, restyled news pages
```

One process, one FastAPI app, one scheduler, as before. The markets module's original
structure survived the split almost unchanged — a file-boundary refactor, not a rewrite.

### Built

- **`intel_brief/markets/`** — `registry.py` (84 instruments, four tabs, import-time
  validation), `sources.py` (six providers), `store.py` (markets.db + provenance),
  `ingest.py`, `service.py`.
- **XMR added** — `xmrusd`, Yahoo `XMR-USD`, 3,206 daily closes back to 2017-11-09. Kraken's
  public OHLC endpoint was tested as a fallback (~720 daily candles, no API key) and is
  documented on the instrument.
- **MSTR added** as a monitored instrument in a new "Bitcoin Treasury" group — price only.
- **Scheduled ingest** every `MARKETS_INGEST_INTERVAL_HOURS` (6), plus once at the end of the
  daily chain. Page loads are now pure DB reads — verified by serving every route with the
  network stubbed to raise. Previously the dashboard fetched from the network *inside the
  request handler* when the 6-hour TTL expired, so an unlucky page load waited on Yahoo and
  FRED; that now matches the principle the news side already held.
- **Price API** — `/api/markets/price/{id}`, `/history`, `/api/markets` (discovery), and
  `/api/markets/{tab}`. Every response carries source, provider, endpoint and `fetched_at`.
- **Provenance everywhere** — `market_prices.source` + `fetched_at` per row, `data_sources`
  describing each provider, `instrument_sources` recording which provider backed which
  instrument over time. News side: `articles.source_feed_url` (snapshotted at discovery, not
  joined from `feeds`), `fetched_at`, and `extractor` (tool + version).
- **Migrations** — `db.py` gained an idempotent ADD COLUMN mechanism, because
  `CREATE TABLE IF NOT EXISTS` silently skips schema changes on a deployed database.
  Verified against a copy of the live 52MB DB: 5,787 articles intact, FTS index healthy.
- **Retention dead code removed** — `ARTICLE_RETENTION_DAYS` was read from the environment
  and used by nothing. The archive was permanent by accident rather than by design; since
  that's the desired outcome, the no-pruning policy is now stated deliberately in `db.py`.

### Five bugs the move surfaced

1. **Four FRED series were overwriting each other.** `GBRCPIALLMINMEI` backs both `pp_gbp`
   (purchasing-power index) and `inf_uk` (YoY %), likewise for Germany, Japan and China.
   Both derived series were stored under the raw series id, so whichever ingested second won
   and one of the two charts rendered the wrong transform. Storage keys now carry a `#pp` /
   `#yoy` suffix. Verified: UK YoY now peaks at 26.87% in August 1975, matching the real
   inflation peak, while the purchasing-power series starts at its 100.0 index base.
2. **bitnodes.io had started rejecting the default urllib user-agent** with a 403, so the
   reachable-node series was collecting nothing. All raw fetches now send an identifying
   User-Agent.
3. **Transport failures were being swallowed** into an empty list, making "the provider
   refused us" indistinguishable from "no new data". Fetchers now raise on transport errors;
   ingest records the real reason and the Status page shows it.
4. **The price API skipped the unit and FX transforms** that the tab payload applied — on
   the one endpoint with an external consumer. Both now share `_apply_transforms()`, and the
   response carries `raw_close` alongside `close` so the conversion is auditable.
5. **A blocked provider could stall a whole run.** urllib's timeout is per socket read, so
   FRED's tarpitted connections reset it indefinitely — a run that should take two minutes
   was still going after thirty. Three consecutive failures from one source now trips a
   circuit breaker that skips its remaining series until the next run.

### Rate-limited by FRED (self-inflicted, fixed)

Three full ingests inside an hour — a live run plus two test runs, ~100 requests from one IP
— got the IP refused by FRED's edge: connections failing in ~0.1s where they had been
succeeding in 0.4s. It cleared within the hour. Fixed by cutting FRED's refresh from 6h to
24h (nothing there publishes intraday) and adding a 1.5s delay between sequential requests to
the same host. blockchain.com and mempool.space got the same treatment at 12h.

### The identifying User-Agent silently broke every FRED series

Found by a later health scan, and worth recording because the failure was invisible from
the dashboard. The fix introduced for bitnodes above — "all raw fetches now send an
identifying User-Agent" — set that agent to the bare product token
`PersonalIntelBrief/1.0`. FRED's edge **silently drops connections from a bare product
token**: not a 403, not a refusal, just a socket that never delivers, so urllib sat there
until its read timeout. Meanwhile `curl` with its own default UA got a 200 in 0.3s from the
same machine, which is what made this look like an upstream outage rather than something we
were doing.

The blast radius was the entire FRED-backed catalogue — **31 instruments**: the whole
Monetary System tab (inflation, purchasing power, central-bank balance sheets, government
debt, bond yields, wealth concentration), plus Brent, natural gas and the HY spread. None
of them had collected a single row since the change.

It was invisible because of how the two failure states render. `service.py` falls back to
`"no data collected yet"` whenever an instrument has no history and no *stored* error — so
a series that has never once succeeded reads exactly like a series that was only just added.
"We have never been able to fetch this" and "this is new" are different facts and should not
share a message.

The accepted form is a UA whose comment carries a contact URL:

```
PersonalIntelBrief/1.0 (+https://example.org)
```

Tested 3/3 against FRED, where prose comments (`(personal dashboard)`,
`(self-hosted personal news and markets dashboard)`) and `Mozilla/5.0`-style strings all
still hang. mempool.space and blockchain.com accept every variant. Set in `config.py`'s
default *and* in `.env` — the environment value was overriding the default, so changing only
the code default fixed nothing, which is its own small lesson about where a "default" really
lives once it's deployed.

**Two things to take from this.** First, a health check that only asks "is the dashboard
returning 200?" would never have caught it; the tab rendered fine, with cards that said
something reasonable. It was caught by comparing what the store *holds* against what the
providers *serve*. Second, an identifying User-Agent is a courtesy that some providers
require and others quietly punish — it needs testing per provider, not setting once
globally and assumed.

### Upstream data that has gone stale

Separately from the UA bug above — and genuinely upstream — the `days_behind` health column
flagged 16 series as likely discontinued. The significant group is the OECD "Main Economic
Indicators" CPI family on FRED, which stopped updating at various points: Japan 2021-06,
Russia 2022-03, UK and Germany 2025-03, China and Switzerland 2025-04. That affects five of
six Inflation instruments and most of Fiat Devaluation. Replacements need to be chosen per
country; not guessed at here. `IRLTLT01RUM156N` (Russia 10Y, ends 2018) was already
documented as dead.

blockchain.com also runs its own publication lag — checked directly during the health scan,
its API served nothing newer than 2026-08-13 for hash rate and difficulty, and our store
matched it row for row. That one is genuinely them, not us.

### The UI merge

- **Two-level navigation** — News / Markets / Status across the top, the section's own tabs
  beneath. The progress banner and Pause switch sit under both, unchanged.
- **One dark palette** for both halves. The news pages kept their structure — brief
  hierarchy, story cards, the accent-bordered analysis block, badges — and changed only
  colour. The old newspaper theme, its `prefers-color-scheme` block and the Georgia serif are
  gone, along with the inline `font-family: sans-serif` overrides scattered through the news
  templates that existed only because the body font used to be serif.
- **One rendering engine, not two.** The source project carried near-duplicate card/chart
  code for its two tabs; with four that would have become four copies. `static/markets.js` is
  one engine parameterised by tab, and group ordering comes from the server rather than a
  hardcoded array.
- **Plotly vendored** at `static/plotly.min.js` (3.5MB, v2.32.0). No CDN — a dashboard that
  needs the internet to draw data it already stores is a contradiction.
- **Provenance in the UI** — the detail panel names the provider, series id and collection
  time, so a number's origin is visible without calling the API.
- Card values are set with `textContent` rather than interpolated into `innerHTML` — labels
  and upstream error strings shouldn't be able to inject markup.

One bug worth recording: the CSS extraction ran one line past the last rule and pulled the
source file's own `</style>` into the middle of the new stylesheet, ending the style block
early and printing the remaining rules as text at the top of every page. Caught by looking at
a rendered screenshot, not by the HTTP tests — they were all still returning 200. There's now
a regression check asserting one well-formed stylesheet per page and no CSS rules in the body.

### htmx and vanilla JS coexist

htmx drives the news pages and the progress banner; the markets pages are plain JS with
Plotly. They don't interact.

---

## 4. The markets LLM layer

Both stages are designed so the model **writes prose, never numbers** — the grounding lesson
already learned twice on the news side, where a model handed thin context confidently
invented names and facts.

### `digest.py` — the factual layer, no LLM

Per instrument: changes over 1/7/14/30 days, a z-score against the trailing 90 days,
position in the 52-week range, and named level crossings (VIX 20/30/40, HY spread 5/8). Runs
whether or not the GPU is available, so "what moved" is always on the dashboard even when
analysis is paused.

Two ranking bugs found by looking at the first real output:

1. **Ranking by raw percentage put noise on top.** Bitcoin mempool count moving 148%
   outranked corn hitting a 52-week high — but ±150% is an ordinary week for mempool count.
   Notability is now the move as a multiple of *that series'* own median weekly move, which
   puts corn at 5.2x and mempool at 1.9x.
2. **Cumulative series are always at a record.** Blockchain size on disk ranked third on a
   0.24% move purely because it was "at a 52-week high" — it is at one every day. Monotonic
   series are now detected and excluded from extremes.

### `market_brief.py` — one narrative per tab

Four calls, ~200s total. The model gets the digest and writes prose; it never sees a price
series and never calculates. Output is JSON: `{summary, whats_changing[], watch[],
regime_note}`. `_verify_figures()` then checks every number in the output against the digest,
tolerating sensible rounding and ignoring bare small integers (day windows).

**The verification earned its place on the first run.** The Monetary System note claimed the
euro rose 208% in a month, the dollar index fell 20.3% and the ruble fell 87.6%. Those pairs
had moved fractions of a percent; every figure was invented, and the grounding rule in the
prompt did not prevent it. The check caught all three.

Two responses beyond the prompt fixes (which also stopped it writing "percentage points" for
prices and restating metrics as observations — unverified figures fell from 6 to 2):

- **A correction pass.** When verification fails, the model is told exactly which figures are
  unsupported and asked to rewrite. Cheap, and only runs when needed.
- **Withhold rather than footnote.** If it still fails, the narrative is not shown. A note
  whose numbers can't be traced isn't worth displaying at reduced confidence — the reader
  would have to check it against the cards below to know which half to believe. The tab says
  so plainly, and the computed cards, which are unaffected, carry the page.

Later, `previous_digest()` was added to feed the prior note's figures into the prompt, so the
model can write "7.5% over 7 days, against 7.25% in yesterday's note" instead of restating
today's number as though nothing came before. Yesterday's figures are added to the allowed
set so verification accepts them.

### `correlate.py` — possible news ↔ market links

The expensive way is asking the LLM about every article × every instrument. That's the
resource trap, and it isn't necessary — **the embeddings already exist**.

1. Take the day's top ~8 movers. Build one sentence each: *"Brent crude fell 6.2% over 7
   days."*
2. Embed those sentences and cosine-match against the article embeddings `analyze()` already
   stored. Only survivors above 0.55 similarity reach the model.
3. **One LLM call** over that shortlist, returning `{instrument_id, article_id, rationale,
   confidence, direction}` per plausible link.
4. Store to `market_news_links`; render as "possible connections" with the confidence shown.

First real run: 97 articles, 8 movers, 14 pairs shortlisted, 3 links kept, 96 seconds.

**One link was rejected because the model invented an article id** the shortlist never
offered. Links are validated against the exact pairs sent, so a hallucinated id can't be
stored as a real association and rendered as something clickable.

Framing is enforced in both the prompt and the UI: these are **associations, not causation**,
and the model is instructed to return an empty list rather than manufacture a story when
nothing lines up. A correlation feature that always finds something is worse than useless.

### Total added GPU cost

~5 minutes/day: four tab briefs (~200s) plus correlation (~96s), with its own
`MARKETS_LLM_TIME_BUDGET_SECONDS` (900) so it can't run away the way `analyze()` once did,
and the same pause discipline as `analyze()`.

### Layout

Content width is the same on both sections (1400px). News articles render in a CSS
multi-column masonry — cards vary a lot in height, and grid would leave ragged gaps to keep
rows aligned. The trade-off is that columns fill top-to-bottom, so for a ranked brief the
second story sits below the first rather than beside it; for importance-ordered reading that
is the right way round. The brief's lead cards span both columns.

---

## 5. Polish pass

- **Favicon**: `static/favicon.svg`, a briefing document with an information "i", on the
  dashboard's own dark ground so it reads on a light tab bar too.
- **Outlet logos**: fetched once per publisher (apple-touch-icon, then favicon.ico, then a
  homepage `<link rel="icon">` parse), cached as blobs in `intel.db`, served from
  `/outlet-logo/{outlet}`. The dashboard never hotlinks a publisher's servers, and the fetch
  rides along with the scheduled markets ingest rather than blocking startup or a page load.
  Content type is sniffed from the bytes, not the header — NYT serves a PNG as `favicon.ico`.
- **Outlet filter**: a chip row above the article grid, each chip toggling its outlet in the
  `outlets` query parameter, plus "Clear all". Plain links, so it survives a reload and is
  bookmarkable; selections are validated against outlets that actually exist, so a
  hand-edited URL can't inject anything. The archive keeps its search term while filtering.
- **`null` on the page**: the model returns the *string* `"null"` for `regime_note` when it
  has nothing to say, which is truthy and rendered as the word. Empty-ish spellings are
  normalised on read.
- **Empty correlation state**: tabs now say when the stage ran and found nothing, rather than
  silently omitting the section — "found nothing" and "hasn't looked" are different, and the
  first is the expected answer on a quiet day.
- **Grouped correlations**: links pointing at the same article are merged, so an energy story
  explaining two grains reads as "Corn & Soybeans" with one rationale instead of the same
  sentence twice with a word changed.

**Today tab filtering** works differently from the list pages and is worth knowing: a brief
story is a cluster covering several outlets, so selecting BBC keeps every story BBC
*covered*, not only stories whose single outlet field is BBC. The learn-something-today pick
and the just-analysed list follow the same filter, and an empty result says it was the filter
rather than an empty brief.

Publisher marks are unplated: an early version drew them on a white rounded square so dark or
transparent artwork couldn't vanish into the dark page, but that read as a border around each
logo rather than as the logo. Checked against every outlet actually held — each is legible on
the dark ground unaided — so the plate came off. Article cards carry the mark at 22px *and*
the outlet name; the brief's "covered by" row stays logos-only, since it lists several at
once and the names would crowd the line.

Two bugs worth recording. The logos rendered as blank white squares in the first screenshot.
The images were fine — `loading="lazy"` was deferring them, and at 2-20KB apiece in a row
that's always above the fold, lazy loading bought nothing and cost exactly that. Diagnosed by
rendering the logos alone on a test page rather than guessing at the CSS.

And the circuit-breaker change broke the ingest stage in production: a regex that added the
new `failures` argument matched only 2 of 5 call sites, because the other three have lambdas
with nested parentheses that defeated the pattern. Nothing caught it until the next scheduled
run logged `TypeError: _ingest_single() missing 1 required positional argument`. There is now
an AST check asserting every call site passes the full argument list — the sort of thing a
type checker would give for free, and the reason to prefer one over a regex for a signature
change.

---

## 6. Analysis coverage — the newspaper round-up

The Today tab had no BBC articles, and the reason turned out to be worse than "the budget is
small". `analyze()` ordered its queue `discovered_at DESC`, so whichever feed polled most
recently took the entire day's allowance. On 2026-08-19 all 38 analysed articles were arXiv
preprints while 85 BBC and 88 Guardian articles sat unread. A news dashboard with no news
outlets in it.

**`pipeline/select.py`** now decides what the GPU time buys:

- **Round-robin across outlets**, newest-first within each. Every outlet gets its top story
  analysed before any outlet gets its second. Measured on the real 394-article queue, the
  first 38 slots went from `DW 18, Fox News 14, arXiv 4, Ars Technica 2` (four outlets, two
  of them barely) to `Guardian 6, BBC 6, Fox News 6, Al Jazeera 5, DW 5, arXiv 5,
  Ars Technica 5` — all seven.
- **Topic rotation inside each outlet**, so one busy story-line can't take an outlet's whole
  share. Real categories come *from* the analysis and `feeds.category_hint` is per-feed, so
  this uses a keyword guess at the headline. Crude on purpose: it only chooses which articles
  get a slot, never what anything is labelled, and the real classifier overrides it minutes
  later. The same 38 slots now span science 8, economics 7, health 6, politics 5, conflict 5,
  other 5, corporate 2.
- **Sports and celebrity go last.** The brief filters both out, so analysing them first
  spends GPU on articles that can't appear in it. They still reach the archive if the budget
  stretches.
- **Deduplication, one-sided on purpose.** Identical text (same `content_hash`) is dropped
  wherever it appears, and near-identical headlines from the *same* outlet are dropped as
  republications. Near-identical headlines from *different* outlets are deliberately kept:
  those separate analyses are what let clustering say "covered by BBC, Guardian and DW",
  which is the coverage signal rather than redundancy. Dropped articles get
  `status='duplicate'` and a pointer to the one that was kept, so they leave the queue
  instead of being reconsidered every run.

Titles with fewer than four distinguishing words aren't deduped at all — Jaccard similarity
is too coarse there, since two three-word headlines sharing two words score 0.5 whether or
not they're the same story. On the real queue, zero of 394 articles were deduped, which is
the right outcome for a 48-hour window of distinct headlines.

**`derive.py`** then guarantees the brief shows it: after ranking by importance, any outlet
that ranking left out entirely contributes its best story. A story counts for every outlet in
its cluster, so a widely-covered story represents several at once and the additions stay few
— and when the ranked twelve already cover everyone, nothing is added.

---

## 7. Archive coverage and backfill

The archive target starts **2026-08-01** (`ARCHIVE_START_DATE`). That date was chosen for a
reason: the days the VPN kill-switch ate — 07-24 and 07-27 through 07-30, when the service
ran but DNS was blocked — collected nothing at all, and RSS only serves what's currently
live, so no amount of processing recovers them. They all fall before the line, which means
the archive from 1 August has **zero unrecoverable gaps**. Articles before that date are kept
and searchable; they're just not counted as holes to fill.

What *is* recoverable is large: every day since has 60-240 articles that were collected and
extracted but never analysed, because the daily stage gets ~40 slots against a couple of
hundred articles. On 2026-08-19 that was **2,174 articles with their full text intact**
across 19 days — roughly 60 hours of GPU at the observed rate.

**`pipeline/backfill.py`** works through them:

- Same per-outlet round-robin as the daily run, so a catch-up that only gets halfway through
  a day still leaves a balanced spread.
- `stale` articles are deliberately in scope — "too old for the daily budget" describes
  exactly this population.
- Yields to the pause switch *and* stops early if `gpu_guard` sees something else using the
  GPU. Catch-up is the low-priority job by definition.
- Won't start while the daily chain is running, and only one runs at a time.
- Runs in a thread so the click returns immediately; progress reports through `pipeline_runs`,
  so the existing banner shows it with no extra plumbing.

**Status page** gains a per-day bar chart: bar height is what was collected, the filled
portion is what was analysed, so a short full bar (quiet day, fully processed) reads
differently from a tall empty one (busy day, barely touched). Days with no collection at all
are drawn as hatched gaps rather than omitted, because a missing day is a fact about the
archive, not an absence of data.

**Date range and single-day bursts.** The chart takes `?start=` and `?end=` (clamped so it
can never reach below the archive start and invent gaps that were never targets), and every
bar is a link that selects that day. A selected day gets a panel with its own counts, an
estimate from the observed rate, and a "Run processing for this day" button that sizes the
budget to what the day actually needs — so a burst finishes the day instead of stopping
partway through it. Days needing more than the 8-hour single-run ceiling say so on the button
and in the panel, rather than promising a whole day and quietly falling short. Days with
nothing collected explain that they can't be recovered instead of offering a button that
would do nothing.

Run length is chosen at the click — 30 minutes, 2 hours, or 6 hours — with the expected
article count computed from the **observed** rate across recent runs rather than a number
hardcoded in a comment. At 99s/article that's ≈18, ≈72 or ≈218 articles. The minutes
parameter is clamped server-side; this is the control that spends hours of GPU, so a
hand-edited URL shouldn't be able to ask for a week of it.

### What catch-up does and doesn't do

Worth writing down because the run history is easy to misread. Since 2026-08-01 the chain ran
at its scheduled 04:30 UTC on exactly **two** days (2 and 4 August). The other seventeen ran
between 07:43 and 11:44 — those are start-up catch-ups firing when the machine was switched
on. The cron trigger almost never fires here, because the machine is off at 05:30.

The rule, confirmed by test: **catch up a missed run on the same day; never attempt a
previous day.** `_needs_catchup()` fires only when today is past `PIPELINE_RUN_TIME` and today
has no successful `derive`. Past days are unreachable by construction rather than by policy —
`run_pipeline_chain()` takes no date, and `discover()` reads what the feeds serve *now*. RSS
keeps no history, so a day missed entirely is gone; that's why `ARCHIVE_START_DATE` begins
after the VPN-outage days rather than pretending they can be filled.

The Status page says this plainly: a "Today's run" panel reporting whether today completed
and when, a **Run today's pipeline now** button for the cases catch-up can't cover (the run
failed, or it was paused through), and a standing note that a missed day can't be recovered
while a collected-but-unprocessed day can, from the chart below.

---

## 8. Geographic scope, sources, and the daily log

### Local and Country were showing the wrong news

`/local` led with Florida primaries and `/country` with Hungary, India and Pakistan. Two
causes, both fixed:

1. **The location was unset.** `USER_LOCALITY` and `LOCAL_PLACES` had not been
   pointed anywhere in particular, so nothing could be filed as local.
2. **The model doesn't classify scope reliably.** Asked for a scope "relative to the reader",
   it labels each story relative to *its own* geography — a US state primary is "local", a
   Hungarian media law is "country". The prompt is now explicit about this, but the tabs no
   longer depend on it.

**`intel_brief/geo.py`** decides scope from place names instead. A place name is a fact about
the text; "is this near the reader" is not something a 7B model should route a whole tab on.
Matching is two-stage — SQLite narrows with `LIKE`, a word-boundary regex confirms — because
substring matching alone put Northern Irish news ("Londonderry") in the London tab.

Which place names count is configuration, never code: `LOCAL_PLACES` in `.env`, with no
shipped default. A default region would quietly make one part of the country stand in for
everybody, and the operator would never find out their Local tab was somebody else's. See
`docs/uk-regions.md`.

National coverage is **scored, not matched**, after "Ukraine, Russia trade strikes" landed in
Country for mentioning the UK once in passing: a UK term in the headline is worth 3, body
mentions 1 each, institutional terms (Westminster, NHS, Bank of England) 1 more, threshold 3.
Exclusions strip "British Columbia", "New South Wales" and "New England" before matching.
The cases are pinned in `tests/test_geo.py`.

### Local sources

Local feeds are not committed. `intel_brief/uk_regions.py` holds all 41 UK regions — 38 BBC
English regions plus Scotland, Wales and Northern Ireland, 61 feeds in total, every one
fetched and confirmed to return items before inclusion. At install time `LOCAL_PLACES` is
matched against that table and `feeds.yaml` is generated with the right regional sources
enabled, then never overwritten.

Two things learned while building it:

- Each source gets **its own outlet name** rather than folding into "BBC". Selection gives
  every outlet one slot per round, so inside a single BBC bucket a regional story loses every
  slot to a national one and the Local tab stays empty — which is the whole point of them.
- Reach plc runs many regional titles that **republish the same copy** under different
  mastheads. Cross-outlet duplicates are deliberately kept by the selector as the "covered by
  N outlets" signal, so listing several stablemates for one area would triple-count a single
  newsroom. The table carries at most one Reach title per region.
- **ITV** was checked and rejected: its feeds time out. Not added rather than added broken.

Ambiguous place names are excluded on purpose — ones that are also common English words
(Reading, Bath, Wells, Street, March, Leek, Rugby) or bigger places elsewhere (Boston,
Washington, Perth, Christchurch), and Westminster, which in a headline means Parliament and
would file every national politics story as local. `tests/test_uk_regions.py` pins that.

### Markets layout

The period selector moved out of the tab and into the section chrome, below the progress
banner, and now applies to every tab. It lives in the query string rather than JS state, so
switching tabs keeps the period and a link to a 5-year view stays a 5-year view. The tab
heading moved inside the description panel.

### Daily log

Market notes were already archived per day; what was missing was continuity and a way to read
them. The last seven days render as native `<details>` elements — today open, the rest
collapsed, each showing that day's note and its news connections. Native disclosure widgets
rather than a scripted accordion: they work without JS, are keyboard-operable, and announce
their state to a screen reader. The only script is the one that closes siblings, which is a
preference rather than a requirement.

---

## 9. Hacker News as a human cross-check

The request was X posts with their top comments. **X isn't possible on these terms**: its API
is paid for reads, logged-out scraping is blocked, and the third-party mirrors that used to
serve public threads stopped working when guest access was restricted. Hacker News provides
the same thing through two documented public APIs with no key — Firebase for items, Algolia
for search.

The design point is *when* it runs. Comments are collected **after** analysis, so the model
forms its reading from the article text alone, having seen no one else's opinion of it. The
dashboard shows the two side by side, visually distinct — blue for the model's analysis, warm
orange for what readers said — and labels neither as correct. Agreement is corroboration;
disagreement is the part worth reading.

Two roles:

- **A source.** HN front-page stories become articles like any other; the linked page is what
  gets extracted and analysed, not the HN post. This carries long-form technical writing the
  mainstream feeds never do.
- **A commentary layer.** Any article already held is matched to its HN thread by URL — never
  by title, because a title search happily returns a different article on the same subject,
  and attaching the wrong discussion is worse than attaching none. URLs are normalised past
  tracking parameters first.

Two bugs found by running it against the real archive rather than trusting the first green
result:

1. **The lookup budget went to the wrong articles.** Candidates were ordered by importance,
   which spent all 60 lookups on major world news and never reached the articles that
   actually have threads. Reordering by likelihood (HN-sourced first, then technical outlets)
   fixed the hit rate but turned the feature into "commentary on the tech articles" — so it
   now uses the *same* ordering as the analysis queue, round-robin across outlets. Most
   lookups return nothing; each is one small request to a free API, which is a fair price for
   not skewing which articles get a second opinion.
2. **A miss was never remembered.** An article with no thread was searched again every run,
   forever. `articles.comments_checked_at` records the attempt; with the 72-hour window and a
   24-hour re-check gap each article is searched about three times — enough to catch a thread
   that appears a day later, not enough to keep asking indefinitely.

Verified end to end on the live archive: 25 HN stories collected, 5 articles matched to
threads, 15 comments stored and rendering. Coverage is modest by nature — most mainstream
news articles simply have no HN thread.

---

## 10. Ordering, and outlet marks

### Tabs were sorted by weekday name

Local led with a 10 July article while everything around it was from the past week. The cause
wasn't the ranking rule — it was that `published_at` stores whatever the feed said, which is
RFC-822 (`"Wed, 8 Jul 2026 15:56:00 GMT"`), and sorting that as text sorts alphabetically **by
weekday name**. "Wed" beats every other day, so a three-week-old Wednesday outranked yesterday.

Fixed with a `published_ts` column holding the same instant in ISO 8601, filled at discovery
from the struct feedparser already provides and backfilled for the 6,147 existing rows. Every
list view now orders by `COALESCE(published_ts, discovered_at) DESC` — newest first, with
articles carrying no date at all falling back to when they were collected. Timezone offsets
are compared as instants, so `04:57 -0400` correctly outranks `08:27 +0000`.

The Today brief keeps its importance ranking: it's a curated round-up of what mattered, not a
reverse-chronological feed, and the coverage pass that guarantees every outlet appears would
be meaningless in date order.

### CoinDesk's logo, and why marks must be icon-shaped

CoinDesk returns 403/429 to every request for its favicon regardless of user-agent, while
serving its RSS feed happily. The feed declares the answer itself: `<channel><image><url>`
exists precisely to say "this is our mark", and CoinDesk publishes it on a separate downloads
host. That's now a fallback source after the icon paths and homepage parse — a fallback rather
than first choice, because a feed logo is usually a wide wordmark while a favicon is a square
icon, which reads better at 20px.

Sizing the row by aspect so the wordmark fit was the wrong answer: it made CoinDesk tower over
every other outlet. A 144x33 mark has no good rendering in a row of icons — shrunk to icon
height it's an unreadable sliver, and given its natural width it dominates.

So marks must be **icon-shaped or not used at all**. Dimensions are read from the file header
(PNG, ICO, GIF, JPEG; SVG passes as scalable) and anything outside a 0.6–1.6 aspect ratio is
rejected at fetch time. Outlets left without one get a **generated monogram** — a square tile
with their initials on a hue derived from the name. Deterministic, so it costs nothing to
store and never goes stale, and deliberately not an imitation of anyone's branding: a neutral
tile is the honest thing to show when the real mark isn't available in a usable shape. The
endpoint therefore always answers, which also removed the `has_logo` branch from the template.

Probed for a square CoinDesk asset and there isn't one reachable: `coindesk.com` returns 403
to every automated request, and the downloads host has only the 144x33 wordmark. They do have
a normal square favicon — a browser fetches it fine — we just can't. So
**`app/logo-overrides/`** takes hand-supplied images, named after the outlet
(`coindesk.png`, `bbc-south-west.png`). Checked before any network fetch: if a file is there,
someone chose it deliberately and it wins. The generated monogram remains the fallback for
outlets with neither.

Outlets that publish without an RSS feed — Hacker News — are included in the logo refresh
through an explicit `EXTRA_OUTLETS` map, since the refresh otherwise iterates the `feeds`
table and would never see them.

---

## Tests

These were scratch files in `/tmp` and were lost when it was cleared — after having caught a
scope classifier that mis-filed Florida primaries, a selection rule that gave one feed the
whole budget, a brief that invented a 208% currency move, and a CSS extraction that broke
every page. They now live in `app/tests/` and run with:

```
cd intel-brief/app && PYTHONPATH=$PWD .venv/bin/python tests/run_all.py
```

Seven suites: geographic scope classification, analysis selection and round-robin,
brief coverage rules, web rendering and stylesheet integrity, the Hacker News client,
outlet-mark shape rules, and article ordering.

---

## Known limitations, stated rather than hidden

- **Entity resolution is heuristic** (exact + case-insensitive + substring match), not
  embedding-based as the spec asks. Handles the common case well — "Andy Burnham"/"Burnham",
  "Sir Keir Starmer"/"Keir Starmer" all correctly merged — but the fuller embedding approach
  would need per-entity embedding storage the schema doesn't have. Deferred deliberately
  rather than half-implemented.
- **Category classification is imperfect.** Observed "Apple regains top spot as world's most
  valuable company" tagged Science, and location-like entities ("Greater Manchester",
  "Downing Street") occasionally typed as `country`. Not a grounding failure — no hallucinated
  facts — just imprecise classification into a fixed enum.
- **Two thresholds are estimates, not validated**: the 0.80 clustering cosine threshold and
  `GPU_BUSY_THRESHOLD_PERCENT = 30.0`. The GPU guard is component-tested (correct permissions,
  correct parsing, real numbers) but its pause-during-gameplay behaviour hasn't been observed
  live, since that requires a gaming session overlapping an analysis window.
- **Five of six Inflation instruments and most of Fiat Devaluation are frozen upstream** —
  the OECD CPI family on FRED stopped updating. Replacements need choosing per country. This
  is separate from the User-Agent bug above, which had frozen *all* 31 FRED instruments and
  is fixed.
- **An instrument that has never fetched successfully looks identical to a brand-new one** —
  both render "no data collected yet". Worth splitting into two messages, since that's what
  hid the FRED breakage.
- **HN comment coverage is modest** — most mainstream news articles have no HN thread.
- **MSTR shares outstanding** is still to do; `get_shares_full()` should provide the
  historical series. MSTR's BTC-held-per-share has no free API.
- **Bitnodes rate limit** is ~10 requests/day/IP across all endpoints; the 6h TTL is tuned for
  that. If another project queries Bitnodes from the same IP, they'll compete.
- **`yfinance` brings `pandas` + `numpy`** (~150-200MB) into an otherwise small dependency
  footprint, and is a scraping library that breaks when Yahoo changes endpoints. Kept
  deliberately rather than rewritten during the move; the alternative is Yahoo's
  `query1.finance.yahoo.com/v8/finance/chart` JSON endpoint, which needs no dependencies at
  the cost of owning the parsing.

## Operations

- **Back up `intel.db`. `markets.db` can be thrown away** and rebuilt from the APIs.
- **`intel-brief` stays the only writer** to `markets.db`; consumers open it read-only
  (`file:...?mode=ro`). Multiple writers into one SQLite file is a bug waiting to happen.
- The archive is **permanent by design** — nothing prunes, and that's now stated in `db.py`
  rather than being an accident. ~52MB after a month, so call it ~600MB/year at the current
  rate.
- A periodic `VACUUM` is worth adding.
