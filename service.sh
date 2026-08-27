#!/usr/bin/env bash
#
# service.sh -- start, stop and inspect the intel-brief service.
#
# A thin wrapper over systemctl, worth having for one reason: "activate" and
# "deactivate" are two operations each, and getting only half of either is the
# usual way a service ends up in a state nobody expects. `systemctl stop`
# without `disable` comes back at the next boot; `disable` without `stop`
# leaves it running until then. The verbs here always do both halves.
#
# Verbs:
#   activate     enable + start   -- runs now, and on every boot
#   deactivate   stop + disable   -- stops now, stays off across reboots
#   start        start only       -- this boot only; still enabled
#   stop         stop only        -- until the next boot, then it returns
#   restart      restart in place
#   status       what state it is actually in (default)
#   logs         recent journal;  -f to follow
#
# start/stop are the pair to reach for when you want the GPU back for an hour
# and want the service to come back on its own afterwards. deactivate is for
# turning it off and meaning it.
#
# Usage:  sudo ./service.sh <verb> [--unit-name intel-brief.service]
#                                         [--prefix /srv/intel-brief]
#
set -euo pipefail

UNIT_NAME=intel-brief.service
PREFIX=/srv/intel-brief
FOLLOW=0
VERB=""

while [ $# -gt 0 ]; do
    case "$1" in
        activate|deactivate|start|stop|restart|status|logs)
            [ -z "$VERB" ] || { echo "only one verb at a time (got '$VERB' and '$1')" >&2; exit 2; }
            VERB="$1"; shift ;;
        --unit-name) UNIT_NAME="${2:?}"; shift 2 ;;
        --prefix)    PREFIX="${2:?}"; shift 2 ;;
        -f|--follow) FOLLOW=1; shift ;;
        -h|--help)   sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

VERB="${VERB:-status}"
SHORT="${UNIT_NAME%.service}"

say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
die()   { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v systemctl >/dev/null 2>&1 || die "systemd is required"

# status and logs are read-only; everything else changes state.
case "$VERB" in
    status|logs) ;;
    *) [ "$(id -u)" -eq 0 ] || die "'$VERB' needs root: sudo $0 $VERB" ;;
esac

if ! systemctl list-unit-files "$UNIT_NAME" >/dev/null 2>&1 \
   || [ -z "$(systemctl list-unit-files "$UNIT_NAME" --no-legend 2>/dev/null)" ]; then
    die "$UNIT_NAME is not installed. Run: sudo ./install.sh"
fi

show_status() {
    local active enabled
    active="$(systemctl is-active "$UNIT_NAME" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$UNIT_NAME" 2>/dev/null || true)"

    head_ "$SHORT"
    say "running now   : $active"
    say "starts at boot: $enabled"

    if [ "$active" = "active" ]; then
        say "since        : $(systemctl show "$UNIT_NAME" -p ActiveEnterTimestamp --value)"
        local port
        # Only the port is read out of .env -- never anything else from it.
        port="$(grep -oE '^DASHBOARD_PORT="?[0-9]+' "$PREFIX/app/.env" 2>/dev/null \
                | grep -oE '[0-9]+$' || echo 8300)"
        say "dashboard    : http://localhost:$port"
    fi

    # The combination people actually get caught by: stopped, but still
    # enabled, so it silently returns at the next reboot.
    if [ "$active" != "active" ] && [ "$enabled" = "enabled" ]; then
        printf '\n  Note: stopped but still enabled -- it will start again at the next boot.\n'
        printf '        Use "deactivate" to keep it off.\n'
    fi
    if [ "$active" = "active" ] && [ "$enabled" != "enabled" ]; then
        printf '\n  Note: running but not enabled -- it will NOT come back after a reboot.\n'
        printf '        Use "activate" to make it persistent.\n'
    fi
}

case "$VERB" in
    activate)
        systemctl enable --now "$UNIT_NAME"
        sleep 2
        systemctl is-active --quiet "$UNIT_NAME" \
            && say "activated -- running now and on every boot" \
            || say "enabled, but it did not come up: $0 logs"
        show_status ;;
    deactivate)
        systemctl disable --now "$UNIT_NAME"
        say "deactivated -- stopped, and will not start at boot"
        say "the archive at $PREFIX/data is untouched"
        show_status ;;
    start)
        systemctl start "$UNIT_NAME"; sleep 2
        say "started (this boot; boot behaviour unchanged)"; show_status ;;
    stop)
        systemctl stop "$UNIT_NAME"
        say "stopped -- it will start again at the next boot unless you deactivate"
        show_status ;;
    restart)
        systemctl restart "$UNIT_NAME"; sleep 2; show_status ;;
    status)
        show_status ;;
    logs)
        if [ "$FOLLOW" -eq 1 ]; then
            say "following the journal -- Ctrl-C to stop"
            journalctl -u "$SHORT" -f
        else
            # Bounded by default and deliberately so: `journalctl -f` blocks
            # until interrupted, which reads as a hang when you only wanted
            # to look at what just happened.
            journalctl -u "$SHORT" -n 50 --no-pager
        fi ;;
esac
