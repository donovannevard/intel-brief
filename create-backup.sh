#!/usr/bin/env bash
#
# create-backup.sh -- copy the live archive and this host's config to USB.
#
# What matters is data/intel.db. The archive accumulates for years and no
# amount of reprocessing rebuilds it: RSS serves only what is currently live,
# so a day not collected is gone for good. Alongside it goes what makes this
# install *this* install -- .env and the generated feeds.yaml. The code is not
# copied: it lives on GitHub, and RESTORE.md in the backup says how to clone a
# fresh copy and put the data and config back under it.
#
# Two things this does that a plain `cp -r` gets wrong:
#
#   1. Databases are copied through SQLite's online backup API, not the
#      filesystem. intel-brief runs in WAL mode and may be mid-write; copying
#      the bytes gives a torn snapshot that can restore as "database disk image
#      is malformed". The API snapshots consistently from under a live writer,
#      so the service does not need stopping. The copies are then switched to
#      DELETE journal mode so each is a single self-contained file.
#
#   2. The new backup is staged beside the old one and only swapped in after it
#      verifies. Overwriting in place means a failure halfway leaves neither a
#      good old backup nor a working new one -- which is the one moment a
#      backup actually has to work.
#
# Usage:  sudo ./create-backup.sh [--dest PATH] [--no-secrets]
#                                        [--keep-previous] [--yes]
#
# With one USB volume mounted it uses it. With several it lists them and asks --
# writing a backup onto the wrong stick is both a privacy leak and a backup you
# will not find later. --dest names one outright; --yes requires --dest.
#
set -euo pipefail

BACKUP_NAME="intel-brief-backup"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_ROOT=""
INCLUDE_SECRETS=1
KEEP_PREVIOUS=0
ASSUME_YES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dest)          DEST_ROOT="${2:?}"; shift 2 ;;
        --no-secrets)    INCLUDE_SECRETS=0; shift ;;
        --keep-previous) KEEP_PREVIOUS=1; shift ;;
        --yes|-y)        ASSUME_YES=1; shift ;;
        -h|--help)       sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
die()   { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "must run as root: sudo $0"
    echo "Re-running under sudo (the archive is owned by the service user)..."
    exec sudo -- "${BASH_SOURCE[0]}" ${DEST_ROOT:+--dest "$DEST_ROOT"} \
         $([ "$INCLUDE_SECRETS" -eq 0 ] && echo --no-secrets) \
         $([ "$KEEP_PREVIOUS" -eq 1 ] && echo --keep-previous) \
         $([ "$ASSUME_YES" -eq 1 ] && echo --yes)
fi

# --- where the live archive is ---------------------------------------------
# A deployment keeps it at <prefix>/data; a dev checkout at <repo>/data.
for candidate in /srv/intel-brief/data "$REPO_DIR/data"; do
    [ -f "$candidate/intel.db" ] && { DATA_DIR="$candidate"; break; }
done
[ -n "${DATA_DIR:-}" ] || die "no intel.db found in /srv/intel-brief/data or $REPO_DIR/data"

# --- locate the USB volume --------------------------------------------------
# Describe a mountpoint the way someone would recognise it on a desk: label,
# device, size, free space, and whether it already holds a backup.
describe_volume() {
    local mp="$1" src label size avail existing
    src="$(findmnt -no SOURCE "$mp" 2>/dev/null || echo '?')"
    label="$(lsblk -no LABEL "$src" 2>/dev/null | head -1)"
    size="$(lsblk -no SIZE "$src" 2>/dev/null | head -1 | tr -d ' ')"
    avail="$(df -h --output=avail "$mp" 2>/dev/null | tail -1 | tr -d ' ')"
    existing=""
    [ -d "$mp/$BACKUP_NAME" ] && existing="  [holds a backup from $(cat "$mp/$BACKUP_NAME/BACKUP_DATE" 2>/dev/null | cut -c1-10)]"
    printf '%s (%s, %s, %s free)%s' "${label:-unlabelled}" "$src" "${size:-?}" "${avail:-?}" "$existing"
}

head_ "Destination"
if [ -z "$DEST_ROOT" ]; then
    # TRAN is reported on the parent disk, not its partitions, so walk from
    # each USB disk down to whatever of it is mounted.
    mapfile -t CANDIDATES < <(
        for disk in $(lsblk -dno NAME,TRAN 2>/dev/null | awk '$2=="usb" {print $1}'); do
            lsblk -no MOUNTPOINT "/dev/$disk" 2>/dev/null | grep '^/'
        done
    )
    [ "${#CANDIDATES[@]}" -gt 0 ] || die "no mounted USB volume found. Plug it in, or pass --dest PATH."

    if [ "${#CANDIDATES[@]}" -eq 1 ]; then
        DEST_ROOT="${CANDIDATES[0]}"
        say "USB: $(describe_volume "$DEST_ROOT")"
    else
        # Never guess between sticks. Writing a backup onto the wrong volume is
        # both a privacy leak and a backup you will not find when you need it.
        say "Several USB volumes are mounted:"
        i=1
        for c in "${CANDIDATES[@]}"; do
            printf '    %d) %s\n       %s\n' "$i" "$c" "$(describe_volume "$c")"
            i=$((i + 1))
        done
        if [ "$ASSUME_YES" -eq 1 ]; then
            die "several USB volumes mounted and --yes was given; pass --dest to say which."
        fi
        printf '\n  Which volume? [1-%d, or q] ' "${#CANDIDATES[@]}"
        choice=""
        if : 2>/dev/null < /dev/tty; then
            read -r choice 2>/dev/null < /dev/tty || choice=""
        elif [ ! -t 0 ]; then
            read -r choice || choice=""
        fi
        case "$choice" in
            ''|q|Q) echo; die "no volume chosen; nothing written." ;;
            *[!0-9]*) echo; die "not a number: $choice" ;;
        esac
        [ "$choice" -ge 1 ] && [ "$choice" -le "${#CANDIDATES[@]}" ] \
            || die "out of range: $choice"
        DEST_ROOT="${CANDIDATES[$((choice - 1))]}"
        say "chose $DEST_ROOT"
    fi
fi

[ -d "$DEST_ROOT" ] || die "destination does not exist: $DEST_ROOT"
mountpoint -q "$DEST_ROOT" 2>/dev/null || say "warning: $DEST_ROOT is not a mount point -- is the volume mounted?"
touch "$DEST_ROOT/.write-test" 2>/dev/null || die "destination is not writable: $DEST_ROOT"
rm -f "$DEST_ROOT/.write-test"

LIVE="$DEST_ROOT/$BACKUP_NAME"
STAGING="$DEST_ROOT/.$BACKUP_NAME.staging"
PREVIOUS="$DEST_ROOT/.$BACKUP_NAME.previous"

say "volume  : $DEST_ROOT ($(findmnt -no FSTYPE "$DEST_ROOT" 2>/dev/null || echo '?'), $(df -h --output=avail "$DEST_ROOT" | tail -1 | tr -d ' ') free)"
say "archive : $DATA_DIR"
say "target  : $LIVE"
[ -d "$LIVE" ] && say "existing backup from $(cat "$LIVE/BACKUP_DATE" 2>/dev/null || echo 'unknown date') is replaced once the new one verifies"

rm -rf "$STAGING"; mkdir -p "$STAGING"

# --- 1. the archive ---------------------------------------------------------
head_ "Archive"
mkdir -p "$STAGING/data"
python3 - "$DATA_DIR/intel.db" "$STAGING/data/intel.db" "$DATA_DIR/markets.db" "$STAGING/data/markets.db" <<'PY'
import os, sqlite3, sys

def snapshot(src, dst):
    if not os.path.exists(src):
        print(f"  MISSING  {src} -- skipped"); return
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(dst)
    try:
        s.execute("PRAGMA busy_timeout=60000")
        with d:
            s.backup(d)          # retries internally if a writer holds the lock
        # One self-contained file: whoever restores will copy "intel.db" and
        # reasonably expect that to be all of it.
        d.execute("PRAGMA journal_mode=DELETE"); d.commit()
    finally:
        s.close(); d.close()
    for side in (dst + "-wal", dst + "-shm"):
        if os.path.exists(side):
            os.remove(side)
    print(f"  copied   {os.path.basename(src)} -> {os.path.getsize(dst)/1024/1024:.1f} MB (no WAL)")

for src, dst in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    snapshot(src, dst)
PY

# --- 2. host configuration --------------------------------------------------
head_ "Host configuration"
mkdir -p "$STAGING/host"
if [ -f /etc/systemd/system/intel-brief.service ]; then
    cp /etc/systemd/system/intel-brief.service "$STAGING/host/"
    say "unit -> host/intel-brief.service"
fi

ENV_SRC=""
for f in /srv/intel-brief/app/.env "$REPO_DIR/app/.env"; do
    [ -f "$f" ] && { ENV_SRC="$f"; break; }
done

if [ "$INCLUDE_SECRETS" -eq 1 ] && [ -n "$ENV_SRC" ]; then
    cp "$ENV_SRC" "$STAGING/host/env"
    say "config -> host/env (includes LOCAL_PLACES and any API key you configured)"
elif [ -n "$ENV_SRC" ]; then
    say "secrets skipped (--no-secrets); rebuild .env from app/.env.example"
fi

# feeds.yaml is generated from LOCAL_PLACES at install, so it could be rebuilt
# -- but it is also the one file an operator is told to edit by hand, and a
# regenerated copy would silently drop those edits. Found the way the service
# finds it: FEEDS_PATH in .env if set (relative to app/), else app/feeds.yaml.
FEEDS_SRC=""
if [ -n "$ENV_SRC" ]; then
    APP_DIR="$(dirname "$ENV_SRC")"
    FEEDS_SRC="$(grep -oP '^FEEDS_PATH="?\K[^"]+' "$ENV_SRC" 2>/dev/null | head -1 || true)"
    FEEDS_SRC="${FEEDS_SRC:-$APP_DIR/feeds.yaml}"
    case "$FEEDS_SRC" in /*) ;; *) FEEDS_SRC="$APP_DIR/${FEEDS_SRC#./}" ;; esac
fi
if [ -n "$FEEDS_SRC" ] && [ -f "$FEEDS_SRC" ]; then
    cp "$FEEDS_SRC" "$STAGING/host/feeds.yaml"
    say "feeds -> host/feeds.yaml (from $FEEDS_SRC)"
else
    FEEDS_SRC=""
    say "no feeds.yaml found -- the installer will generate one from LOCAL_PLACES"
fi

# Which code this data was running under. Not copied -- it is on GitHub -- but
# worth knowing when restoring onto a version that has moved on since.
CODE_URL="$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
CODE_REV="$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || true)"
[ -n "$CODE_URL" ] && say "code    : $CODE_URL @ ${CODE_REV:-unknown} (recorded, not copied)"

# --- 3. verify the copies, not the originals --------------------------------
head_ "Verifying"
python3 - "$DATA_DIR/intel.db" "$STAGING/data/intel.db" "$DATA_DIR/markets.db" "$STAGING/data/markets.db" <<'PY'
import os, sqlite3, sys

def counts(conn):
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        try:
            out[t] = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        except sqlite3.DatabaseError:
            pass
    return out

failed = False
for src, dst in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    name = os.path.basename(dst)
    if not os.path.exists(dst):
        print(f"  {name}: MISSING"); failed = True; continue
    d = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    integrity = d.execute("PRAGMA integrity_check").fetchone()[0]
    fk = d.execute("PRAGMA foreign_key_check").fetchall()
    dc = counts(d); d.close()
    if integrity != "ok" or fk:
        print(f"  {name}: FAILED integrity={integrity} fk={len(fk)}"); failed = True; continue

    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True); sc = counts(s); s.close()
    drift = {t: (sc[t], dc.get(t, 0)) for t in sc if sc[t] != dc.get(t, 0)}
    lost = sum(max(0, a - b) for a, b in drift.values())
    pct = lost / (sum(sc.values()) or 1) * 100
    print(f"  {name}: integrity=ok, fk=0, {len(dc)} tables, {sum(dc.values())} rows")
    if [t for t in sc if t not in dc]:
        print(f"    MISSING TABLES: {[t for t in sc if t not in dc]}"); failed = True
    elif drift:
        detail = ", ".join(f"{t} {a}->{b}" for t, (a, b) in list(drift.items())[:4])
        # A small shortfall means the service wrote during the copy: the
        # snapshot is still internally consistent, just a moment older.
        if pct > 1:
            print(f"    FAILED: lost {lost} rows ({pct:.1f}%): {detail}"); failed = True
        else:
            print(f"    ok: {detail} (service wrote during the copy)")

sys.exit(1 if failed else 0)
PY
VERIFY_RC=$?

if [ "$VERIFY_RC" -eq 0 ]; then
    STRAY=$(find "$STAGING" -name '*.db-wal' -o -name '*.db-shm' 2>/dev/null)
    if [ -n "$STRAY" ]; then
        say "stray WAL side-files -- a database here is not self-contained:"
        printf '    %s\n' $STRAY
        VERIFY_RC=1
    fi
fi
if [ "$VERIFY_RC" -ne 0 ]; then
    rm -rf "$STAGING"
    die "verification failed -- staging discarded, the existing backup at $LIVE is untouched."
fi

# --- 4. manifest and restore notes ------------------------------------------
date -Iseconds > "$STAGING/BACKUP_DATE"
{
    echo "intel-brief backup"
    echo "created : $(date -Iseconds)"
    echo "host    : $(hostname) / $(uname -sr)"
    echo "archive : $DATA_DIR"
    echo "code    : ${CODE_URL:-unknown} @ ${CODE_REV:-unknown} (not included -- clone it)"
    echo
    du -sh "$STAGING"/* 2>/dev/null | sed "s|$STAGING/|  |"
    echo
    python3 -c "
import sqlite3
for n, p in (('intel.db', '$STAGING/data/intel.db'), ('markets.db', '$STAGING/data/markets.db')):
    try:
        c = sqlite3.connect('file:%s?mode=ro' % p, uri=True)
        if n == 'intel.db':
            a = c.execute('SELECT count(*) FROM articles').fetchone()[0]
            an = c.execute(\"SELECT count(*) FROM articles WHERE status='analyzed'\").fetchone()[0]
            lo = c.execute('SELECT min(substr(COALESCE(published_ts, discovered_at),1,10)) FROM articles').fetchone()[0]
            hi = c.execute('SELECT max(substr(COALESCE(published_ts, discovered_at),1,10)) FROM articles').fetchone()[0]
            print('  intel.db   : %d articles (%d analysed), %s .. %s' % (a, an, lo, hi))
        else:
            r = c.execute('SELECT count(*) FROM market_prices').fetchone()[0]
            s = c.execute('SELECT count(DISTINCT series_id) FROM market_prices').fetchone()[0]
            print('  markets.db : %d price rows across %d series' % (r, s))
        c.close()
    except Exception as e:
        print('  %s: %s' % (n, e))
"
} > "$STAGING/MANIFEST.txt"

cat > "$STAGING/RESTORE.md" <<'RESTORE_EOF'
# Restoring intel-brief

This backup holds the **data and this host's configuration**, not the code.
The code is on GitHub; you clone a fresh copy and put these back under it.

```
data/intel.db     the archive -- the only part that cannot be rebuilt
data/markets.db   price history (regenerates from the providers, but slowly)
host/env          .env: LOCAL_PLACES, LLM endpoint, any API key
host/feeds.yaml   the feed list, including any hand edits
host/*.service    the systemd unit, for reference -- install.sh writes a fresh one
MANIFEST.txt      counts to check the restore against, and the code version
```

Run everything below from the directory holding this file.

## 1. Get the code

```bash
git clone @CODE_URL@ ~/intel-brief
```

`MANIFEST.txt` records the commit this data was last running under
(`@CODE_REV@`). The current version is almost always what you want; to match
the backup exactly, `git -C ~/intel-brief checkout @CODE_REV@`.

## 2. Put the data and config where the installer will find them

Before installing, not after: the installer keeps an existing archive, `.env`
and generated `feeds.yaml`, so staging them first means one install run sets
up everything with your settings rather than the example ones.

```bash
sudo mkdir -p /srv/intel-brief/app /srv/intel-brief/data
sudo cp data/intel.db data/markets.db /srv/intel-brief/data/
sudo cp host/env        /srv/intel-brief/app/.env
sudo cp host/feeds.yaml @FEEDS_DEST@
```

No `host/env` (backup made with `--no-secrets`)? Skip that line; the installer
seeds `.env` from the example, and you set `LOCAL_PLACES` and your LLM
endpoint in `/srv/intel-brief/app/.env` afterwards, then
`sudo ./service.sh restart`. No `host/feeds.yaml`? Skip that too -- the
installer generates one from `LOCAL_PLACES`.

## 3. Install

```bash
cd ~/intel-brief
sudo ./install.sh
```

Creates the service user, deploys the code to `/srv/intel-brief`, fixes
ownership of everything you just copied (exfat stores no permissions, so it all
arrived owned by whoever copied it), and starts the service.

## 4. Check it

```bash
./service.sh status
python3 -c "import sqlite3; c=sqlite3.connect('file:/srv/intel-brief/data/intel.db?mode=ro', uri=True); \
print(c.execute('PRAGMA integrity_check').fetchone()[0], \
c.execute('SELECT count(*) FROM articles').fetchone()[0], 'articles')"
```

Compare the article count with `MANIFEST.txt`. The dashboard is on
`http://localhost:8300`; the Status page says whether the interpretation layer
is off, configured-but-unreachable, or on.

## Different hardware

intel-brief needs no GPU. With `LLM_BASE_URL` empty it runs as a reader and
archive. Two settings are Intel/llama-swap-specific and safe to turn off
elsewhere: `GPU_GUARD` (reads `intel_gpu_top`) and `LLM_UNLOAD_URL` (a
llama-swap extension, not part of the OpenAI API).

## Restoring to a different prefix

`host/env` has absolute paths for `/srv/intel-brief` in `DB_PATH` and
`MARKETS_DB_PATH` (and `FEEDS_PATH`, if set). Edit them to match before running
`./install.sh --prefix <elsewhere>`.
RESTORE_EOF

# Fill in what is specific to this backup. Placeholders rather than an
# unquoted heredoc, because markdown is full of backticks that the shell would
# otherwise execute.
FEEDS_DEST="${FEEDS_SRC:-/srv/intel-brief/app/feeds.yaml}"
sed -i -e "s|@CODE_URL@|${CODE_URL:-<the repository URL>}|g" \
       -e "s|@CODE_REV@|${CODE_REV:-unknown}|g" \
       -e "s|@FEEDS_DEST@|$FEEDS_DEST|g" "$STAGING/RESTORE.md"

# --- 5. swap in -------------------------------------------------------------
head_ "Installing"
rm -rf "$PREVIOUS"
[ -d "$LIVE" ] && mv "$LIVE" "$PREVIOUS"
mv "$STAGING" "$LIVE"
if [ "$KEEP_PREVIOUS" -eq 1 ] && [ -d "$PREVIOUS" ]; then
    mv "$PREVIOUS" "$LIVE.previous"; say "previous backup kept at $LIVE.previous"
else
    rm -rf "$PREVIOUS"
fi

# No-op on exfat (the mount pins ownership), but a backup written to an
# ext4/btrfs drive would otherwise be root-owned and unreadable to whoever ran it.
OWNER="${SUDO_USER:-}"
[ -z "$OWNER" ] && [ -n "${PKEXEC_UID:-}" ] && OWNER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
[ -n "$OWNER" ] && chown -R "$OWNER" "$LIVE" 2>/dev/null || true

sync
head_ "Done"
say "$LIVE ($(du -sh "$LIVE" | cut -f1))"
grep -E '^  (intel|markets)\.db' "$LIVE/MANIFEST.txt"
say ""
say "Eject with: udisksctl unmount -b $(findmnt -no SOURCE "$DEST_ROOT")"
