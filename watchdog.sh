#!/bin/bash
# RUNECLAW Watchdog — ensures the bot stays alive
# Install: crontab -e → */1 * * * * /home/mulerun/runeclaw/watchdog.sh >> /tmp/watchdog.log 2>&1
#
# NOTE ON WHERE THIS LIVES. CLAUDE.md says to keep the launcher OUTSIDE the
# repo, because anything inside it is one `git reset --hard` away from
# reverting — that is how ~15 consecutive deploys on 2026-08-01 kept restoring
# a flagless launcher. This file is still in the repo for the crontab line
# above to keep working; treat it as a template to copy out, not as the
# permanent home.

set -uo pipefail   # NOT -e: this script's whole job is to react to failures.

BOTDIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="${RUNECLAW_STATE_DIR:-$BOTDIR/data}/runeclaw.pid"
# Not /tmp: /tmp does not survive a reboot, and on a shared host a pre-created
# symlink at a predictable path redirects everything this appends.
LOGFILE="${RUNECLAW_WATCHDOG_LOG:-$BOTDIR/logs/watchdog.log}"

# One pattern, used for BOTH the check and the kill. They used to differ —
# detection matched `python.*bot\.main.*telegram` while the kill matched the
# broader `python.*bot\.main` — so the watchdog SIGKILLed processes it had
# never looked for, including a second mode or a running test.
BOT_PATTERN='python.*bot\.main.*telegram'

mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null || true
cd "$BOTDIR" || exit 1

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Watchdog: $*"; }

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

is_running() {
    if [ -f "$PIDFILE" ]; then
        pid_is_live "$(cat "$PIDFILE" 2>/dev/null)" && return 0
    fi
    # Fall back to the pattern only when there is no usable pidfile. `pgrep -f`
    # can match this script's own command line when the pattern appears in it;
    # it does not here (this is bash, and BOT_PATTERN is only ever expanded
    # into pgrep's argv, not into the script's), but prefer the pidfile.
    pgrep -f "$BOT_PATTERN" >/dev/null 2>&1
}

if is_running; then
    exit 0
fi

log "bot not running, restarting..."

# SIGTERM first, and only then SIGKILL.
#
# logs/audit_chain.jsonl is a TAMPER-EVIDENT chain (deploy.sh:168-171): losing
# its continuity is unrecoverable and indistinguishable from tampering, and a
# SIGKILL mid-append is how that happens. Give the bot ten seconds to close its
# books before forcing it.
if pgrep -f "$BOT_PATTERN" >/dev/null 2>&1; then
    log "a stale process matched — asking it to stop"
    pkill -TERM -f "$BOT_PATTERN" 2>/dev/null
    for _ in $(seq 10); do
        pgrep -f "$BOT_PATTERN" >/dev/null 2>&1 || break
        sleep 1
    done
    if pgrep -f "$BOT_PATTERN" >/dev/null 2>&1; then
        log "it did not stop — SIGKILL"
        pkill -9 -f "$BOT_PATTERN" 2>/dev/null
        sleep 1
    fi
fi

# The venv interpreter, not whatever PATH resolves.
#
# deploy.sh:44-49 exists because the box runs 3.11 inside .venv while its
# system python3 is still 3.10, and numpy 2.3.x is invisible to 3.10. A restart
# on the wrong interpreter fails in a way whose error message argues for
# downgrading a correct pin.
PY="python3"
[ -x "$BOTDIR/.venv/bin/python" ] && PY="$BOTDIR/.venv/bin/python"

nohup "$PY" -m bot.main --mode telegram >> "$LOGFILE" 2>&1 &
NEW_PID=$!
log "started PID $NEW_PID via $PY"

# Gate on it having SURVIVED, not on it having started.
#
# `python -m bot.main` used to default to --mode cli, which finds no TTY and
# exits ZERO — so a launcher that reported the PID it spawned reported success
# for a process that was already gone. Starting is not running.
if [ -x "$BOTDIR/scripts/verify_bot_alive.sh" ]; then
    if "$BOTDIR/scripts/verify_bot_alive.sh" --pid "$NEW_PID"; then
        log "restart verified alive"
        echo "$NEW_PID" > "$PIDFILE" 2>/dev/null || true
    else
        log "RESTART FAILED — the process did not survive; see $LOGFILE"
        exit 1
    fi
else
    log "WARNING: scripts/verify_bot_alive.sh missing — restart NOT verified"
    echo "$NEW_PID" > "$PIDFILE" 2>/dev/null || true
fi
