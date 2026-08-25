#!/usr/bin/env bash
# A dead-man's switch that only pings when the system is ACTUALLY healthy.
#
# WHY AN EXTERNAL CHECK AT ALL
#
# Every alert path in RUNECLAW runs INSIDE the thing being monitored:
# bot/core/system_health.py, proactive_monitor, the Telegram degraded alerts.
# All of them are excellent and all of them share one flaw — A BOT THAT HAS
# DIED CANNOT TELL YOU IT DIED. That is why every recovery in the week of
# 2026-08-25 began with a human noticing something looked wrong, and why the
# gateway tunnel spent eighteen days giving up after five restart attempts with
# nobody the wiser.
#
# The fix is not more in-process monitoring. It is one check that lives
# somewhere else.
#
# HOW A DEAD-MAN'S SWITCH WORKS
#
# This script pings an external service on a schedule. The service alerts you
# when the pings STOP. Silence is the signal, so it survives the failures an
# in-process alert cannot report: the process died, the box died, cron died,
# the network died. Nothing has to be working for the alarm to fire — that is
# the entire point.
#
# THE TRAP, AND THE ONLY THING THAT MAKES THIS HONEST
#
# The naive version is one line in cron:
#
#     */5 * * * * curl -fsS https://hc-ping.com/<uuid>
#
# That pings whenever CRON is alive. Cron is alive when the bot is dead, when
# the bridge is dead, when the gateway is unreachable — so it reports a green
# light for a system in outage, forever, and the operator learns to trust it.
# A heartbeat that fires regardless of health is worse than none: it is a
# confident all-clear manufactured from no evidence, which is the single defect
# this codebase spends most of its guard tests preventing.
#
# So this script CHECKS FIRST and pings only if everything answered. When a
# check fails it pings the /fail endpoint instead, which alerts immediately
# rather than waiting for the grace period to lapse.
#
# THE THIRD CASE. If a check cannot RUN — no curl, no network from the box —
# this sends nothing at all and exits 3. It must not ping /fail, because that
# would report the bot as down on the strength of a broken harness; and it must
# not ping success, for the obvious reason. Sending nothing lets the dead-man's
# switch do its job: the pings stop, and the service raises the alarm on a
# timer. Silence is the correct output when you do not know.
#
# SETUP — see README.md in this directory. In short:
#   1. make a check at https://healthchecks.io (free), period 5m, grace 10m
#   2. put its ping URL in ~/.runeclaw-heartbeat  (chmod 600)
#   3. crontab -e:
#        */5 * * * * /home/mulerun/runeclaw/scripts/monitoring/heartbeat.sh
#
# The URL is a secret — anyone holding it can silence your alerting by pinging
# it. It is read from a file outside the repo and NEVER logged (§F-15).

set -uo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8000}"
PING_FILE="${RUNECLAW_HEARTBEAT_FILE:-$HOME/.runeclaw-heartbeat}"

log() { printf '%s heartbeat: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

if ! command -v curl >/dev/null 2>&1; then
  log "curl is missing — NOTHING was checked and nothing was sent."
  exit 3
fi

PING_URL="${RUNECLAW_HEARTBEAT_URL:-}"
if [ -z "$PING_URL" ] && [ -r "$PING_FILE" ]; then
  PING_URL="$(head -n 1 "$PING_FILE" | tr -d '[:space:]')"
fi
if [ -z "$PING_URL" ]; then
  log "no ping URL configured (set RUNECLAW_HEARTBEAT_URL or write $PING_FILE)."
  log "  Nothing was checked and nothing was sent. See README.md here."
  exit 3
fi

# ── the checks ──────────────────────────────────────────────────────────────
failures=""

# The gateway REQUIRES a secret, so 401/403 means the server is up and
# correctly refusing an unauthenticated caller. Treating that as an outage
# would page you every five minutes for a healthy system, and an alert that
# cries wolf is how a real one gets ignored.
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        "$GATEWAY_URL/gateway/health" 2>/dev/null)" || code="000"
case "$code" in
  200|401|403) ;;
  000) failures="${failures}gateway unreachable; " ;;
  *)   failures="${failures}gateway HTTP $code; " ;;
esac

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        "$BRIDGE_URL/health" 2>/dev/null)" || code="000"
case "$code" in
  200) ;;
  000) failures="${failures}bridge unreachable; " ;;
  *)   failures="${failures}bridge HTTP $code; " ;;
esac

# ── the ping ────────────────────────────────────────────────────────────────
#
# --max-time is short and failure is tolerated: the monitoring must never be
# the reason a box hangs, and a missed ping is recovered by the next one five
# minutes later. The URL is never echoed.
if [ -n "$failures" ]; then
  log "UNHEALTHY — ${failures%; }"
  curl -fsS -m 10 --retry 2 --data-raw "${failures%; }" "${PING_URL%/}/fail" \
    >/dev/null 2>&1 || log "  (could not reach the ping service to report it)"
  exit 1
fi

log "healthy — gateway and bridge both answering"
curl -fsS -m 10 --retry 2 "$PING_URL" >/dev/null 2>&1 \
  || log "  (could not reach the ping service; the check will alert on silence)"
exit 0
