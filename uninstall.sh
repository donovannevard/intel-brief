#!/usr/bin/env bash
#
# uninstall.sh -- remove an intel-brief deployment, back to pre-install state.
#
# Reverses install.sh exactly and nothing more: the systemd unit, the tree at
# <prefix>, the uv toolchain installed inside it, and the service account. The
# git checkout you run this from is never touched -- "pre-install state" means
# a clean machine and a cloned repo, which is precisely what is left behind.
#
# The archive is the one thing here that cannot be rebuilt. RSS serves only
# what is currently live, so a day not collected is gone for good, and an
# uninstall is exactly the moment someone is least likely to be thinking about
# that. So data/ is archived to a tarball before anything is removed, and
# --purge is the only way to destroy it. The tarball is written outside
# <prefix> by definition, since <prefix> is what gets deleted.
#
# The service is stopped before the archive is copied. That is what makes a
# plain tar safe here where create-backup.sh needs SQLite's online backup API:
# the databases run in WAL mode, and copying their bytes from under a live
# writer gives a torn snapshot that restores as "database disk image is
# malformed". With the writer stopped there is no such race.
#
# Usage:  sudo ./uninstall.sh [--prefix /srv/intel-brief]
#                                    [--user svc-intel-brief]
#                                    [--unit-name intel-brief.service]
#                                    [--backup-to DIR] [--purge]
#                                    [--keep-user] [--dry-run] [--yes]
#
set -euo pipefail

PREFIX=/srv/intel-brief
SVC_USER=svc-intel-brief
UNIT_NAME=intel-brief.service
BACKUP_DIR=""
PURGE=0
KEEP_USER=0
DRY_RUN=0
ASSUME_YES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)    PREFIX="${2:?}"; shift 2 ;;
        --user)      SVC_USER="${2:?}"; shift 2 ;;
        --unit-name) UNIT_NAME="${2:?}"; shift 2 ;;
        --backup-to) BACKUP_DIR="${2:?}"; shift 2 ;;
        --purge)     PURGE=1; shift ;;
        --keep-user) KEEP_USER=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --yes|-y)    ASSUME_YES=1; shift ;;
        -h|--help)   sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
die()   { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
run()   { if [ "$DRY_RUN" -eq 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

[ "$(id -u)" -eq 0 ] || die "must run as root: sudo $0"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"

# --- refuse to delete anything that is not plausibly a deployment ----------
# rm -rf driven by a variable deserves paranoia in proportion to its blast
# radius. A typo'd or empty --prefix must fail loudly, not take a system
# directory with it.
case "$PREFIX" in
    /*) : ;;
    *)  die "--prefix must be an absolute path (got: '$PREFIX')" ;;
esac
PREFIX="${PREFIX%/}"
case "$PREFIX" in
    ""|/|/usr|/usr/*|/etc|/var|/home|/srv|/opt|/boot|/bin|/sbin|/lib|/lib64|/root|/tmp)
        die "refusing to remove '$PREFIX' -- that is a system directory, not a deployment" ;;
esac
[ "$(printf '%s' "$PREFIX" | tr -cd / | wc -c)" -ge 2 ] \
    || die "refusing to remove '$PREFIX' -- too close to the filesystem root"

UNIT="/etc/systemd/system/$UNIT_NAME"

# --- work out what actually exists ----------------------------------------
HAVE_UNIT=0;   [ -f "$UNIT" ] && HAVE_UNIT=1
HAVE_PREFIX=0; [ -d "$PREFIX" ] && HAVE_PREFIX=1
HAVE_USER=0;   id "$SVC_USER" >/dev/null 2>&1 && HAVE_USER=1
HAVE_DATA=0;   [ -d "$PREFIX/data" ] && [ -n "$(ls -A "$PREFIX/data" 2>/dev/null)" ] && HAVE_DATA=1

if [ "$HAVE_UNIT" -eq 0 ] && [ "$HAVE_PREFIX" -eq 0 ] && [ "$HAVE_USER" -eq 0 ]; then
    say "nothing to do -- no unit, no $PREFIX, no $SVC_USER account"
    exit 0
fi

# Mirror install.sh's guard in reverse. If the unit deploys a different prefix,
# the caller is about to delete one deployment while disabling another.
if [ "$HAVE_UNIT" -eq 1 ]; then
    UNIT_PREFIX="$(sed -n 's|^WorkingDirectory=\(.*\)/app$|\1|p' "$UNIT" | head -1)"
    if [ -n "$UNIT_PREFIX" ] && [ "$UNIT_PREFIX" != "$PREFIX" ]; then
        die "$UNIT deploys $UNIT_PREFIX, not $PREFIX.
       Removing it would disable a deployment you are not uninstalling.
       Re-run with --prefix $UNIT_PREFIX, or --unit-name for the right unit."
    fi
fi

# --- backup destination ----------------------------------------------------
if [ -z "$BACKUP_DIR" ]; then
    if [ -n "${SUDO_USER:-}" ]; then
        BACKUP_DIR="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    fi
    [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ] || BACKUP_DIR=/var/backups
fi
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$BACKUP_DIR/intel-brief-data-$STAMP.tar.gz"

# --- plan ------------------------------------------------------------------
head_ "Plan"
say "unit    : $UNIT $([ "$HAVE_UNIT" -eq 1 ] && echo '(will be stopped, disabled, removed)' || echo '(not present)')"
say "prefix  : $PREFIX $([ "$HAVE_PREFIX" -eq 1 ] && echo '(will be removed)' || echo '(not present)')"
if [ "$KEEP_USER" -eq 1 ]; then
    say "account : $SVC_USER (kept, --keep-user)"
else
    say "account : $SVC_USER $([ "$HAVE_USER" -eq 1 ] && echo '(will be deleted)' || echo '(not present)')"
fi
if [ "$HAVE_DATA" -eq 1 ]; then
    SIZE="$(du -sh "$PREFIX/data" 2>/dev/null | cut -f1)"
    if [ "$PURGE" -eq 1 ]; then
        say "archive : $SIZE at $PREFIX/data -- DESTROYED, no backup (--purge)"
    else
        say "archive : $SIZE at $PREFIX/data -- saved to $ARCHIVE"
    fi
else
    say "archive : none found at $PREFIX/data"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    head_ "Dry run"
    say "nothing was changed. Re-run without --dry-run to apply."
fi

# --- confirm ---------------------------------------------------------------
if [ "$ASSUME_YES" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    if [ "$PURGE" -eq 1 ] && [ "$HAVE_DATA" -eq 1 ]; then
        printf '\n  --purge will permanently destroy the archive. It cannot be rebuilt.\n'
        printf '  Type the word DESTROY to continue: '
        read -r reply
        [ "$reply" = "DESTROY" ] || die "aborted"
    else
        printf '\n  Proceed? [y/N] '
        read -r reply
        case "$reply" in [yY]|[yY][eE][sS]) : ;; *) die "aborted" ;; esac
    fi
fi

# --- stop the service ------------------------------------------------------
# Before the archive is copied, so nothing is mid-write. Both calls tolerate a
# unit that is already gone or was never enabled.
if [ "$HAVE_UNIT" -eq 1 ]; then
    head_ "Service"
    run systemctl stop "$UNIT_NAME" 2>/dev/null || true
    run systemctl disable "$UNIT_NAME" 2>/dev/null || true
    say "stopped and disabled $UNIT_NAME"
fi

# --- save the archive ------------------------------------------------------
if [ "$HAVE_DATA" -eq 1 ] && [ "$PURGE" -eq 0 ]; then
    head_ "Archive"
    run mkdir -p "$BACKUP_DIR"
    run tar czf "$ARCHIVE" -C "$PREFIX" data
    if [ "$DRY_RUN" -eq 0 ]; then
        # Readable by whoever invoked sudo, not just root -- a backup nobody
        # can open is not a backup.
        [ -n "${SUDO_USER:-}" ] && chown "$SUDO_USER" "$ARCHIVE" 2>/dev/null || true
        chmod 600 "$ARCHIVE"
        say "saved $(du -h "$ARCHIVE" | cut -f1) to $ARCHIVE"
    fi
fi

# --- remove the unit -------------------------------------------------------
if [ "$HAVE_UNIT" -eq 1 ]; then
    run rm -f "$UNIT"
    run systemctl daemon-reload
    run systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true
    say "removed $UNIT"
fi

# --- remove the deployment -------------------------------------------------
if [ "$HAVE_PREFIX" -eq 1 ]; then
    head_ "Deployment"
    run rm -rf "$PREFIX"
    say "removed $PREFIX"
fi

# --- remove the account ----------------------------------------------------
if [ "$HAVE_USER" -eq 1 ] && [ "$KEEP_USER" -eq 0 ]; then
    head_ "Service account"
    # No -r: install.sh creates the account with --no-create-home and points
    # HOME at the prefix, which is already gone. userdel -r would be asking it
    # to remove a home directory it never made.
    run userdel "$SVC_USER" 2>/dev/null || say "could not delete $SVC_USER (still owns files elsewhere?)"
    say "deleted $SVC_USER"
fi

# --- done ------------------------------------------------------------------
head_ "Done"
if [ "$DRY_RUN" -eq 1 ]; then
    say "dry run only -- nothing was changed"
elif [ "$HAVE_DATA" -eq 1 ] && [ "$PURGE" -eq 0 ]; then
    cat <<NEXT

  The archive is at:
      $ARCHIVE

  To restore it into a fresh install:
      sudo ./install.sh --no-start
      sudo tar xzf $ARCHIVE -C $PREFIX
      sudo chown -R $SVC_USER:$SVC_USER $PREFIX/data
      sudo systemctl enable --now ${UNIT_NAME%.service}
NEXT
else
    say "removed. The checkout you ran this from is untouched."
fi
