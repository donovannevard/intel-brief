# intel-brief

A self-hosted news reader, archive and markets dashboard. It collects from RSS,
extracts full article text, keeps everything in a searchable local archive, and
tracks market instruments from six free providers with no API keys.

Built for the UK: name the places you care about and it polls the right BBC
region and regional papers for you, out of [41 regions](docs/uk-regions.md)
covering England, Scotland, Wales and Northern Ireland.

**It works with no AI at all.** Point `LLM_BASE_URL` at any OpenAI-compatible
endpoint — llama-swap, ollama, LM Studio, a hosted API — and an interpretation
layer switches on over the top.

|  | Without a model | With a model |
|---|---|---|
| **News** | Headlines by outlet, newest first, grouped by where the story is about, each opening at the publisher | Each story also carries a grounded summary, why it matters, and a note on how it is framed — beside what readers said on Hacker News, with neither labelled correct |
| **Today** | Recent articles | A curated brief: clustered coverage, ranked by importance, every outlet represented |
| **Markets** | Computed movement cards — changes over 1/7/14/30 days, z-scores, 52-week range, threshold crossings | A verified narrative per tab, plus possible links between the day's news and the day's moves |
| **Archive** | Full-text search over everything collected | Same, plus filtering by category |

The model never fetches, never decides what is true, and never replaces the
source. It interprets what was already collected, so you can weigh it yourself.
Every figure it writes about the markets is checked against the computed digest
before it is shown, and a narrative whose numbers cannot be traced is withheld
rather than published with a caveat.

## Install

```bash
git clone <this repo> && cd intel-brief
cp app/.env.example app/.env
$EDITOR app/.env          # set LOCAL_PLACES to where you live
sudo ./install.sh
```

Creates a `svc-intel-brief` system user, deploys to `/srv/intel-brief`, seeds
config, builds the Python environment and installs a hardened systemd unit. The
dashboard comes up on `http://localhost:8300`.

### Where you live

`LOCAL_PLACES` is the one setting worth getting right before the first install:

```bash
LOCAL_PLACES="Manchester,Salford,Stockport"
```

The installer matches those names against
[the region table](docs/uk-regions.md) and generates `app/feeds.yaml` with the
right BBC region and regional papers already enabled — for that example, BBC
Manchester and the Manchester Evening News. The same names decide which stories
get filed under **Local** rather than Country or Global.

`app/feeds.yaml` is generated per install and is **not** in this repository: it
names the part of the country someone follows, which is theirs and not the
project's. It is written once and never overwritten, so edit it freely.
[`app/feeds.example.yaml`](app/feeds.example.yaml) is the shared template it is
built from — national and global sources, identical for everyone.

To remove it again, `sudo ./uninstall.sh` reverses exactly that, leaving
a clean machine and this checkout. It tars the archive out before touching
anything — `--purge` is the only way to destroy it, and it asks you to type the
word out in full.

## Controlling the service

```bash
sudo ./service.sh activate     # run now, and on every boot
sudo ./service.sh deactivate   # stop now, stay off across reboots
sudo ./service.sh stop         # this boot only; returns at the next one
./service.sh status            # what state it is actually in
./service.sh logs -f           # follow the journal
```

`activate` and `deactivate` are each two systemctl operations, and doing only
half of either is the usual way a service ends up somewhere unexpected — stopped
but still enabled, so it silently returns at the next boot. These verbs always
do both halves, and `status` calls out the mismatch if it finds one. Use
`stop`/`start` when you want the GPU back for an hour and want the service to
come back on its own afterwards.

Data lives at `/srv/intel-brief/data` — inside the deployment, so the code and
the archive travel together.

Re-run it to upgrade; an existing `.env` and an existing archive are never
overwritten.

### Switching on the interpretation layer

Edit `/srv/intel-brief/app/.env`:

```ini
LLM_BASE_URL="http://127.0.0.1:8090/v1"
LLM_MODEL="qwen2.5-7b"
EMBEDDING_BASE_URL="http://127.0.0.1:8090/v1"
EMBEDDING_MODEL="nomic-embed-text"
```

then `sudo ./service.sh restart`.

The Status page always states which of three states is in effect: **off**,
**configured but not answering**, or **on**. Those are deliberately distinct —
while an endpoint is unreachable, collection carries on and no article is marked
failed, so the queue is picked up whenever the model returns.

## How it runs

One daily chain at 05:30:

```
discover → extract → analyze → derive → markets_ingest → market_brief → correlate
```

Stages are isolated: the ones needing a model are skipped when there isn't one,
and a failure in any stage does not stop the others. Prices refresh separately
every 6 hours. Page loads never make a model call — everything shown was
computed by a scheduled run.

A missed run is caught up the same day only. A day missed entirely cannot be
recovered, because RSS serves only what is currently live — which is why the
archive matters and why there is a backup story.

## Hardware notes

Nothing here needs a GPU. Two optional features are Intel-specific and degrade
cleanly elsewhere:

- `GPU_GUARD` pauses analysis while something else uses the GPU, read from
  `intel_gpu_top`. Set `off` on non-Intel hardware; `auto` (the default) simply
  does nothing if the tool is absent.
- `LLM_UNLOAD_URL` frees VRAM on pause via llama-swap's `/unload`, which is not
  part of the OpenAI API. Leave empty for any endpoint that isn't yours to
  unload.

## Layout

- [`app/`](app/) — the FastAPI application. See [app/README.md](app/README.md) for local dev.
- [`install.sh`](install.sh) / [`uninstall.sh`](uninstall.sh) — deploy and remove the systemd service, with [`intel-brief.service.template`](intel-brief.service.template).
- [`service.sh`](service.sh) — start, stop and inspect it once installed.
- [`create-backup.sh`](create-backup.sh) — mirror the archive to a USB volume.
- [`docs/intel-brief.md`](docs/intel-brief.md) — the build record: architecture, the bugs found and why the design is what it is.
- [`docs/markets-api.md`](docs/markets-api.md) — the read-only price API other projects can consume.
- [`docs/uk-regions.md`](docs/uk-regions.md) — all 41 regions and the place names that select each.
- [`app/feeds.example.yaml`](app/feeds.example.yaml) — the shared feed template; `app/feeds.yaml` is generated per install and gitignored.
- [`spec.md`](spec.md) — the original technical specification.
- [`app/tests/`](app/tests/) — run with `python tests/run_all.py`.

## Backups

```bash
sudo ./create-backup.sh
```

Copies the live archive and this machine's configuration — `.env` (your
`LOCAL_PLACES`, LLM endpoint and any key) and `feeds.yaml` (including hand
edits) — to a USB volume. The code isn't included: it's here on GitHub, and the
backup's `RESTORE.md` walks through cloning a fresh copy and putting the data
and config back under it, with the clone URL and the commit it was running
already filled in. With one volume plugged in it uses it and says which; with several it lists them — label, device, size, free
space, and whether each already holds a backup — and asks. It never guesses,
because writing a backup onto the wrong stick is both a privacy leak and a
backup you will not find when you need it. `--dest` names a path outright.

The new backup is staged beside the old one and only swapped in once it
verifies, so a failure halfway through cannot leave you with neither.

`data/intel.db` is the irreplaceable part: RSS serves only what is currently
live, so a day not collected is gone. The script copies it with SQLite's backup
API rather than `cp`, because the database runs in WAL mode and a byte copy of a
live one can restore as "database disk image is malformed". To do it by hand:

```bash
python3 -c "
import sqlite3
s = sqlite3.connect('file:/srv/intel-brief/data/intel.db?mode=ro', uri=True)
d = sqlite3.connect('/path/to/backup/intel.db')
with d: s.backup(d)
d.execute('PRAGMA journal_mode=DELETE')
"
```

`data/markets.db` regenerates from the providers and does not need backing up.

## Licence

[MIT](LICENSE). Use it, change it, run it, ship it — no conditions beyond
keeping the copyright notice.

This is primarily a personal backup of something built for one person's use, so
there is no support promise and no roadmap. That said, it installs and runs
from a clean clone, everything it needs is free and keyless, and if it is
useful to you then help yourself.
