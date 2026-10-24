#!/bin/bash
# RUNECLAW Watchdog — keeps the bot and the API bridge alive.
# Install: crontab -e →
#   */1 * * * * /home/mulerun/runeclaw/watchdog.sh
# The script appends to logs/watchdog.log itself. Do not add a redirect.
#
# NOTE ON WHERE THIS LIVES. CLAUDE.md says to keep the launcher OUTSIDE the
# repo, because an update of the checkout overwrites anything tracked inside
# it — that is how ~15 consecutive deploys on 2026-08-01 kept restoring a
# flagless launcher. This file is still in the repo for the crontab line
# above to keep working; treat it as a template to copy out, not as the
# permanent home.

set -uo pipefail   # NOT -e: this script's whole job is to react to failures.

BOTDIR="$(cd "$(dirname "$0")" && pwd)"
STATEDIR="${RUNECLAW_STATE_DIR:-$BOTDIR/data}"
BOT_PIDFILE="$STATEDIR/runeclaw.pid"
BRIDGE_PIDFILE="$STATEDIR/api_bridge.pid"
# Not /tmp: /tmp does not survive a reboot, and on a shared host a pre-created
# symlink at a predictable path redirects everything this appends.
LOGFILE="${RUNECLAW_WATCHDOG_LOG:-$BOTDIR/logs/watchdog.log}"
BOT_LOG="$BOTDIR/logs/bot.log"
BRIDGE_LOG="$BOTDIR/logs/api_bridge.log"

# One pattern per process, used for BOTH the check and the kill. They used to
# differ — detection matched `python.*bot\.main.*telegram` while the kill
# matched the broader `python.*bot\.main` — so the watchdog SIGKILLed
# processes it had never looked for, including a second mode or a running test.
BOT_PATTERN='python.*bot\.main.*telegram'
BRIDGE_PATTERN='python.*api_bridge\.py'

mkdir -p "$(dirname "$LOGFILE")" "$STATEDIR" 2>/dev/null || true
cd "$BOTDIR" || exit 1

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Watchdog: $*" >> "$LOGFILE"; }

# Is the recorded PID a LIVE process, not a defunct one?
#
# `kill -0` succeeds on a ZOMBIE. A zombie is a process that has exited and is
# waiting to be reaped — it is exactly the state a crashed bot leaves behind
# when its parent has not collected it, which is to say the precise failure
# this watchdog exists to catch. CLAUDE.md records scripts/verify_bot_alive.sh
# being written to treat a zombie as dead for this reason; the lesson was never
# carried across to here.
pid_is_live() {
    local pid="$1"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    local state
    state="$(ps -o state= -p "$pid" 2>/dev/null | tr -d ' ')"
    [ -n "$state" ] && [ "$state" != "Z" ]
}

# pattern, pidfile. A live recorded PID counts. Otherwise the pattern.
is_running() {
    local pattern="$1" pidfile="$2"
    if [ -f "$pidfile" ]; then
        pid_is_live "$(cat "$pidfile" 2>/dev/null)" && return 0
    fi
    # `pgrep -f` can match this script's own command line when the pattern
    # appears in it; it does not here (this is bash, and the pattern is only
    # ever expanded into pgrep's argv, not into the script's).
    pgrep -f "$pattern" >/dev/null 2>&1
}

# SIGTERM first, and only then SIGKILL. logs/audit_chain.jsonl is a
# TAMPER-EVIDENT chain: losing its continuity is unrecoverable and
# indistinguishable from tampering, and a SIGKILL mid-append is how that
# happens. Give the process ten seconds to close its books before forcing it.
stop_matching() {
    local pattern="$1"
    if pgrep -f "$pattern" >/dev/null 2>&1; then
        log "a stale process matched — asking it to stop"
        pkill -TERM -f "$pattern" 2>/dev/null
        for _ in $(seq 10); do
            pgrep -f "$pattern" >/dev/null 2>&1 || break
            sleep 1
        done
        if pgrep -f "$pattern" >/dev/null 2>&1; then
            log "it did not stop — SIGKILL"
            pkill -9 -f "$pattern" 2>/dev/null
            sleep 1
        fi
    fi
}

# The venv interpreter, not whatever PATH resolves.
#
# deploy.sh exists because the box runs 3.11 inside .venv while its system
# python3 is still 3.10, and numpy 2.3.x is invisible to 3.10. A restart on
# the wrong interpreter fails in a way whose error message argues for
# downgrading a correct pin.
PY="python3"
[ -x "$BOTDIR/.venv/bin/python" ] && PY="$BOTDIR/.venv/bin/python"

# name, pattern, pidfile, logfile, then the interpreter arguments.
ensure_running() {
    local name="$1" pattern="$2" pidfile="$3" outfile="$4"
    shift 4
    if is_running "$pattern" "$pidfile"; then
        return 0
    fi
    log "$name not running, restarting..."
    stop_matching "$pattern"
    mkdir -p "$(dirname "$outfile")" 2>/dev/null || true
    nohup "$PY" "$@" >> "$outfile" 2>&1 &
    local new_pid=$!
    log "started $name PID $new_pid via $PY"
    # Gate on it having SURVIVED, not on it having started.
    #
    # `python -m bot.main` used to default to --mode cli, which finds no TTY
    # and exits ZERO — so a launcher that reported the PID it spawned reported
    # success for a process that was already gone. Starting is not running.
    if [ -x "$BOTDIR/scripts/verify_bot_alive.sh" ]; then
        if "$BOTDIR/scripts/verify_bot_alive.sh" --pid "$new_pid"; then
            log "$name restart verified alive"
            echo "$new_pid" > "$pidfile" 2>/dev/null || true
            return 0
        fi
        log "RESTART FAILED — $name did not survive; see $outfile"
        return 1
    fi
    log "WARNING: scripts/verify_bot_alive.sh missing — $name restart NOT verified"
    echo "$new_pid" > "$pidfile" 2>/dev/null || true
    return 0
}

rc=0
ensure_running bot "$BOT_PATTERN" "$BOT_PIDFILE" "$BOT_LOG" \
    -m bot.main --mode telegram || rc=1
ensure_running bridge "$BRIDGE_PATTERN" "$BRIDGE_PIDFILE" "$BRIDGE_LOG" \
    api_bridge.py || rc=1
exit "$rc"
