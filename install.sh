#!/usr/bin/env bash
#
# install.sh -- deploy intel-brief as a hardened systemd service.
#
# Deploys to /srv/intel-brief by default rather than running from a checkout in
# someone's home directory. That is not arbitrary: the service runs as its own
# unprivileged user with ProtectHome=true, and a WorkingDirectory under /home
# would mean giving that account write access inside your home and relaxing the
# hardening. Outside /home, the isolation costs nothing.
#
# Data lives at <prefix>/data -- inside the deployment, gitignored, so the code
# and the archive travel together and a backup is one directory.
#
# Idempotent: safe to re-run to upgrade -- and it restarts the service, so new
# code actually takes effect. `systemctl enable --now` does not: it starts a
# stopped unit and leaves a running one alone, which silently leaves the old
# code in memory with the new code on disk.
#
# Config the deployment owns is never overwritten: .env and feeds.yaml are both
# seeded once and then left alone.
#
# Usage:  sudo ./install.sh [--prefix /srv/intel-brief] [--user svc-intel-brief]
#                                  [--from <dir>] [--no-start]
#
set -euo pipefail

PREFIX=/srv/intel-brief
SVC_USER=svc-intel-brief
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DO_START=1
UNIT_NAME=intel-brief.service
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)   PREFIX="${2:?}"; shift 2 ;;
        --user)     SVC_USER="${2:?}"; shift 2 ;;
        --from)     SOURCE_DIR="${2:?}"; shift 2 ;;
        --no-start) DO_START=0; shift ;;
        --unit-name) UNIT_NAME="${2:?}"; shift 2 ;;
        --force)    FORCE=1; shift ;;
        -h|--help)  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
die()   { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root: sudo $0"
[ -d "$SOURCE_DIR/app" ] || die "no app/ directory in $SOURCE_DIR -- run this from the repo, or pass --from"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"

head_ "Plan"
say "source : $SOURCE_DIR"
say "prefix : $PREFIX"
say "user   : $SVC_USER"
say "data   : $PREFIX/data"

# --- service account -------------------------------------------------------
head_ "Service account"
if id "$SVC_USER" >/dev/null 2>&1; then
    say "$SVC_USER already exists"
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
    say "created $SVC_USER"
fi

# --- code ------------------------------------------------------------------
head_ "Code"
mkdir -p "$PREFIX"
# Deliberately excludes data/, .venv and .env:
#   data/  the archive must never be clobbered by a deploy
#   .venv  rebuilt for the target machine
#   .env   it is the *developer's* config, with their real keys in it. Copying
#          it would both leak those onto every machine this is deployed to and
#          make the seeding below silently skip, leaving DB_PATH pointing at
#          whatever the source checkout used.
tar cf - -C "$SOURCE_DIR" \
    --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='data' --exclude='.git' --exclude='.env' \
    --exclude='app/feeds.yaml' \
    app docs README.md spec.md \
    install.sh uninstall.sh service.sh create-backup.sh \
    intel-brief.service.template 2>/dev/null | tar xf - -C "$PREFIX"
say "synced app/, docs/ and the scripts to $PREFIX"

# --- data ------------------------------------------------------------------
head_ "Data"
mkdir -p "$PREFIX/data"
if [ -f "$PREFIX/data/intel.db" ]; then
    say "existing archive kept: $(du -h "$PREFIX/data/intel.db" | cut -f1) at $PREFIX/data/intel.db"
else
    say "empty data directory created (the archive builds from the first run)"
    say "restoring from a backup? copy intel.db here before starting the service"
fi

# --- configuration ---------------------------------------------------------
head_ "Configuration"
ENV_FILE="$PREFIX/app/.env"
if [ -f "$ENV_FILE" ]; then
    say ".env already present -- left untouched"
else
    cp "$PREFIX/app/.env.example" "$ENV_FILE"
    # Point at the data root. DB_PATH defaults to ./data relative to the
    # working directory, which would put the archive inside app/; keeping it
    # at the prefix root means code and data are siblings, not nested.
    if grep -q '^DB_PATH=' "$ENV_FILE"; then
        sed -i "s|^DB_PATH=.*|DB_PATH=\"$PREFIX/data/intel.db\"|" "$ENV_FILE"
    else
        printf '\nDB_PATH="%s/data/intel.db"\n' "$PREFIX" >> "$ENV_FILE"
    fi
    if grep -q '^MARKETS_DB_PATH=' "$ENV_FILE"; then
        sed -i "s|^MARKETS_DB_PATH=.*|MARKETS_DB_PATH=\"$PREFIX/data/markets.db\"|" "$ENV_FILE"
    else
        printf 'MARKETS_DB_PATH="%s/data/markets.db"\n' "$PREFIX" >> "$ENV_FILE"
    fi
    if grep -q '^FEEDS_PATH=' "$ENV_FILE"; then
        sed -i "s|^FEEDS_PATH=.*|FEEDS_PATH=\"$PREFIX/app/feeds.yaml\"|" "$ENV_FILE"
    else
        printf 'FEEDS_PATH="%s/app/feeds.yaml"\n' "$PREFIX" >> "$ENV_FILE"
    fi
    say "seeded .env from .env.example, pointed at $PREFIX/data"
fi


# --- ownership -------------------------------------------------------------
chown -R "$SVC_USER:$SVC_USER" "$PREFIX"
chmod 640 "$ENV_FILE"
say "ownership set to $SVC_USER, .env mode 640"

# --- dependencies ----------------------------------------------------------
head_ "Dependencies"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$PREFIX/.local/bin/uv" ]; then
    say "installing uv for $SVC_USER"
    su -s /bin/bash "$SVC_USER" -c "export HOME=$PREFIX; curl -LsSf https://astral.sh/uv/install.sh | sh" \
        || die "uv install failed -- install it manually and re-run"
fi
UV="$PREFIX/.local/bin/uv"
[ -x "$UV" ] || UV="$(command -v uv)"
su -s /bin/bash "$SVC_USER" -c "export HOME=$PREFIX; cd $PREFIX/app && $UV sync --quiet" \
    || die "uv sync failed"
say "python environment ready"

# --- feeds -----------------------------------------------------------------
# feeds.yaml is not in the repository: it names the region someone follows.
# It is generated here from feeds.example.yaml plus whatever LOCAL_PLACES in
# .env implies (see app/intel_brief/uk_regions.py), and then never touched
# again -- a deploy that silently reverted it would hand back a Local tab for
# the wrong part of the country.
head_ "Feeds"
# Where the service will read it from: FEEDS_PATH if .env sets one (relative
# paths resolve from app/, the service's working directory), else app/.
FEEDS_FILE="$(grep -oP '^FEEDS_PATH="?\K[^"]+' "$ENV_FILE" 2>/dev/null | head -1 || true)"
FEEDS_FILE="${FEEDS_FILE:-$PREFIX/app/feeds.yaml}"
case "$FEEDS_FILE" in /*) ;; *) FEEDS_FILE="$PREFIX/app/${FEEDS_FILE#./}" ;; esac

# A feeds.yaml without the generator's marker pre-dates LOCAL_PLACES: it was
# written by hand or by an older installer, and reflects nothing in .env. Left
# in place it would be kept forever simply because it exists, and the Local
# tab would go on polling whatever region it happened to name. Set aside (not
# deleted -- it may hold edits worth copying across) so a fresh one is built.
if [ -f "$FEEDS_FILE" ] && ! head -1 "$FEEDS_FILE" | grep -q '^# generated-by: intel-brief init-feeds'; then
    mv "$FEEDS_FILE" "$FEEDS_FILE.pre-regions"
    say "$(basename "$FEEDS_FILE") pre-dates LOCAL_PLACES -- kept as $(basename "$FEEDS_FILE").pre-regions, regenerating"
fi

su -s /bin/bash "$SVC_USER" -c \
    "export HOME=$PREFIX FEEDS_TEMPLATE=$PREFIX/app/feeds.example.yaml; \
     cd $PREFIX/app && $UV run python3 -m intel_brief.cli init-feeds" \
    || die "could not generate feeds.yaml"
chown "$SVC_USER:$SVC_USER" "$FEEDS_FILE" 2>/dev/null || true

# --- unit ------------------------------------------------------------------
head_ "Service"
UNIT="/etc/systemd/system/$UNIT_NAME"

# Refuse to repoint an existing unit at a different prefix. Installing to a
# throwaway prefix to try something out must not silently rewrite the unit that
# a real deployment is running from -- the running service would keep going and
# then come up against the wrong paths at its next restart, which is a horrible
# way to find out.
if [ -f "$UNIT" ] && [ "$FORCE" -eq 0 ]; then
    EXISTING_PREFIX="$(sed -n 's|^WorkingDirectory=\(.*\)/app$|\1|p' "$UNIT" | head -1)"
    if [ -n "$EXISTING_PREFIX" ] && [ "$EXISTING_PREFIX" != "$PREFIX" ]; then
        die "$UNIT already deploys $EXISTING_PREFIX, not $PREFIX.
       Installing would repoint the existing service at a different tree.
       Use --unit-name for a second instance, or --force to repoint this one."
    fi
fi

sed -e "s|__PREFIX__|$PREFIX|g" -e "s|__USER__|$SVC_USER|g" \
    "$PREFIX/intel-brief.service.template" > "$UNIT"
systemctl daemon-reload
say "installed $UNIT"

if [ "$DO_START" -eq 1 ]; then
    systemctl enable "$UNIT_NAME" >/dev/null 2>&1 || true
    systemctl restart "$UNIT_NAME"
    sleep 3
    if systemctl is-active --quiet "$UNIT_NAME"; then
        say "service is running"
    else
        say "service did not come up -- journalctl -u ${UNIT_NAME%.service} -n 40"
    fi
else
    say "not started (--no-start). Start with: systemctl enable --now ${UNIT_NAME%.service}"
fi

# --- what next -------------------------------------------------------------
PORT="$(grep -oP '^DASHBOARD_PORT="?\K[0-9]+' "$ENV_FILE" 2>/dev/null || echo 8300)"
head_ "Done"
say "Dashboard: http://localhost:$PORT"
cat <<NEXT

  Interpretation layer (optional)
  ------------------------------
  As installed, intel-brief runs as a reader and archive: headlines from every
  feed linking out to the publisher, grouped by where the story is about, with
  full-text search and computed market figures. No model required.

  To switch on the interpretation layer -- per-article summaries, why it
  matters, how it is framed, the morning brief, and the market narratives --
  set LLM_BASE_URL in $ENV_FILE to any OpenAI-compatible endpoint:

      LLM_BASE_URL="http://127.0.0.1:8090/v1"     # llama-swap on this machine
      LLM_BASE_URL="http://<lan-host>:11434/v1"   # ollama on the LAN
      LLM_MODEL="qwen2.5-7b"
      EMBEDDING_BASE_URL="..."   EMBEDDING_MODEL="nomic-embed-text"

  then: systemctl restart ${UNIT_NAME%.service}

  The Status page always says which of the three states is in effect --
  off, configured-but-unreachable, or on.
NEXT
