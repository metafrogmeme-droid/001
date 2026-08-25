#!/usr/bin/env bash
# Does the port ANSWER? — an ExecStartPost gate for the RUNECLAW units.
#
# A process can be alive and not serving: bound to the wrong interface, stuck
# importing, or listening on a port nobody configured. `kill -0` cannot tell
# any of those from healthy, and neither can systemd's Type=simple, which
# considers a unit started the moment the binary is exec'd. The website talks
# to the PORT, so the port is what gets checked.
#
# Usage:  wait_for_port.sh <url> [seconds]
#
# Exit 0 when the URL answers within the window; 1 when it never does. A
# non-zero ExecStartPost makes systemd fail the unit, which with Restart=always
# means the process is torn down and started again — the correct response to
# "came up, never served".
#
# THE WINDOW IS DELIBERATELY GENEROUS AND THAT IS THE WHOLE DESIGN.
#
# A health check that is too tight is strictly worse than no health check: it
# converts a slow-but-healthy start into a restart, and Restart=always turns
# one restart into a permanent loop. The failure mode of checking too eagerly
# is an outage that this file caused. The failure mode of checking too
# patiently is a delay before an already-broken process is recycled. Those are
# not symmetric, so the default leans long and the callers pass longer.
#
# Dependency-light on purpose (curl + sleep): it runs from a unit file before
# any application environment is guaranteed.

set -uo pipefail

URL="${1:-}"
WINDOW="${2:-120}"

if [ -z "$URL" ]; then
  echo "wait_for_port: no URL given; nothing was checked." >&2
  exit 2
fi

case "$WINDOW" in
  ''|*[!0-9]*)
    echo "wait_for_port: window '$WINDOW' is not a whole number of seconds." >&2
    exit 2
    ;;
esac

if ! command -v curl >/dev/null 2>&1; then
  # 2, not 1: "I could not check" is not "the port is dead". Returning 1 here
  # would tell systemd to restart a process that is very probably fine, and a
  # missing curl would then present as a crashloop of the application — a
  # broken harness manufacturing a verdict, which scripts/verify_bot_alive.sh
  # refuses to do for exactly this reason.
  echo "wait_for_port: curl is not installed; the port was NOT checked." >&2
  echo "  Install curl, or remove the ExecStartPost line from the unit." >&2
  exit 2
fi

# Poll every 2s. `--max-time 3` so one hung request cannot eat the window.
attempts=$(( WINDOW / 2 ))
[ "$attempts" -lt 1 ] && attempts=1

i=0
while [ "$i" -lt "$attempts" ]; do
  if curl -fsS --max-time 3 "$URL" >/dev/null 2>&1; then
    echo "wait_for_port: $URL answering after $(( i * 2 ))s."
    exit 0
  fi
  i=$(( i + 1 ))
  sleep 2
done

echo "wait_for_port: $URL did not answer within ${WINDOW}s." >&2
echo "  The process started but is not serving. systemd will recycle it." >&2
exit 1
