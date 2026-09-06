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

# ANY HTTP STATUS IS AN ANSWER. THE ABSENCE OF ONE IS NOT.
#
# This polled with `curl -fsS`, and `-f` makes curl exit non-zero on any 4xx or
# 5xx. The URL every RUNECLAW unit points it at is `/gateway/health`, which
# sits behind `secret_middleware` and returns **403 to every request without
# the shared secret** — including this one, which sends no headers. So the
# probe could never succeed: it polled for the full window, exited 1, failed
# the unit's ExecStartPost, and `Restart=always` + `StartLimitIntervalSec=0`
# turned that into a permanent restart loop on a completely healthy box. The
# header above warns about exactly this outcome ("an outage that this file
# caused") and the mechanism was the option letter.
#
# A 403 from a gateway that requires a secret is the *correct* answer and
# proves the thing this gate exists to prove: the process bound the port and
# is serving. `scripts/monitoring/heartbeat.sh` and `scripts/verify_deploy.sh`
# already say so in those words and match on `200|401|403`; this file, the
# launcher and the status tool did not.
#
# It accepts ANY code rather than an allow-list because it is generic over the
# URL its caller passes, and because a 5xx is a process that is up and failing
# a request — restarting it would not fix that, and would be the eager-check
# outage again. Whether a served response is *healthy* is a different question,
# asked by verify_deploy.sh and the heartbeat. This one asks whether anything
# is listening, and prints the code so the log says which.
attempts=$(( WINDOW / 2 ))
[ "$attempts" -lt 1 ] && attempts=1

i=0
while [ "$i" -lt "$attempts" ]; do
  # -o /dev/null -w '%{http_code}' rather than -f: the body is discarded, the
  # STATUS is the signal, and a connection failure prints nothing (curl exits
  # non-zero and the substitution yields ""), which we normalise to 000.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL" 2>/dev/null)" || code=""
  case "$code" in
    ''|000) : ;;   # nothing listening yet — keep waiting
    *)
      echo "wait_for_port: $URL answering (HTTP $code) after $(( i * 2 ))s."
      exit 0
      ;;
  esac
  i=$(( i + 1 ))
  sleep 2
done

echo "wait_for_port: $URL did not answer within ${WINDOW}s." >&2
echo "  Nothing accepted a connection on that port. systemd will recycle it." >&2
exit 1
