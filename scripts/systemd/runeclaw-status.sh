#!/usr/bin/env bash
# What the supervisor is ACTUALLY doing — `systemctl status` cannot tell you.
#
# WHY THIS FILE EXISTS, AND WHY IT IS NOT OPTIONAL.
#
# Both RUNECLAW units set `Restart=always` with `StartLimitIntervalSec=0`, so
# systemd never gives up. That is deliberate and it is the point: a bot that
# stops retrying at 03:05 has reproduced the outage the units exist to end.
#
# The cost is paid in VISIBILITY. Fifteen seconds after a crash the unit is
# `active (running)` again, so a process that has died two hundred times today
# and one that has run untouched for a week look IDENTICAL to `systemctl
# status`. A green light that rules one cause out and names none is exactly the
# shape CLAUDE.md warns about, and "the supervisor says active" is precisely
# the sentence that would let a crashloop run all week.
#
# NRestarts is the number that distinguishes them, so this script reads it.
#
# THREE OUTCOMES, NOT TWO. `could not read it` is not a synonym for either
# verdict. A box with no systemd, a unit that was never installed, and a
# healthy unit are three different situations, and printing OK for the middle
# one would tell an operator their bot is supervised when nothing is watching
# it at all — the most expensive wrong answer this script could give.
#
# Usage:  scripts/systemd/runeclaw-status.sh
# Exit:   0 all checked units healthy · 1 something is wrong · 3 could not tell

set -uo pipefail

UNITS="runeclaw-bot runeclaw-bridge runeclaw-gateway"

# Port probes, paired with the unit that serves them. A unit can be active and
# not serving; that is the case the ExecStartPost gate catches at start time and
# nothing catches afterwards.
probe_for() {
  case "$1" in
    runeclaw-bot)    echo "http://127.0.0.1:8080/gateway/health" ;;
    runeclaw-bridge) echo "http://127.0.0.1:8000/health" ;;
    *)               echo "" ;;
  esac
}

if ! command -v systemctl >/dev/null 2>&1; then
  echo "UNKNOWN: systemctl is not available — nothing about supervision was"
  echo "  checked. This is NOT 'the services are down'; it means this box does"
  echo "  not manage them with systemd, and something else must be watching."
  exit 3
fi

have_curl=1
command -v curl >/dev/null 2>&1 || have_curl=0

worst=0
for unit in $UNITS; do
  # `systemctl status` exits non-zero for BOTH "inactive" and "no such unit",
  # so the two are separated explicitly. An uninstalled unit reported as
  # "inactive" reads as a service that is merely stopped and could be started —
  # when in fact nothing would ever restart it.
  if ! systemctl list-unit-files "${unit}.service" >/dev/null 2>&1 \
     || [ -z "$(systemctl list-unit-files --no-legend "${unit}.service" 2>/dev/null)" ]; then
    echo "NOT INSTALLED  ${unit} — no unit file. Nothing supervises this process."
    [ "$worst" -lt 1 ] && worst=1
    continue
  fi

  active="$(systemctl is-active "$unit" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
  # NRestarts is what makes a crashloop legible. An empty read is reported as
  # unknown rather than folded into 0 — "it has never restarted" and "I could
  # not find out" are different claims, and only one of them is reassuring.
  restarts="$(systemctl show -p NRestarts --value "$unit" 2>/dev/null || true)"
  case "$restarts" in
    ''|*[!0-9]*) restarts_label="restarts: unknown" ; restarts_n=-1 ;;
    *)           restarts_label="restarts: ${restarts}" ; restarts_n="$restarts" ;;
  esac

  since="$(systemctl show -p ActiveEnterTimestamp --value "$unit" 2>/dev/null || true)"
  [ -z "$since" ] && since="unknown"

  line="  ${unit}  [${active:-unknown}${enabled:+, $enabled}]  ${restarts_label}  since: ${since}"

  if [ "$active" != "active" ]; then
    echo "DOWN           ${line}"
    worst=1
    continue
  fi

  # Active. Now ask the two questions `active` does not answer.
  status="OK"

  # 1. Is it looping? A unit restarted many times is running RIGHT NOW and is
  #    still broken. No threshold is claimed as a diagnosis — the count is
  #    reported and flagged; naming the cause is the operator's job with
  #    `journalctl -u <unit>`.
  if [ "$restarts_n" -gt 5 ]; then
    status="CRASHLOOP?"
    worst=1
  fi

  # 2. Is it serving? Only asked when there is a probe AND a way to run it.
  #    An unrunnable probe leaves the field out rather than printing a pass.
  probe="$(probe_for "$unit")"
  if [ -n "$probe" ]; then
    if [ "$have_curl" -eq 0 ]; then
      line="${line}  port: unchecked (no curl)"
      # Only downgrade a clean run to "incomplete". An existing failure (1) is
      # a verdict and must not be softened into "could not tell".
      [ "$worst" -eq 0 ] && worst=3
    elif curl -fsS --max-time 5 "$probe" >/dev/null 2>&1; then
      line="${line}  port: answering"
    else
      line="${line}  port: NOT ANSWERING"
      status="NOT SERVING"
      worst=1
    fi
  fi

  printf '%-14s %s\n' "$status" "$line"
done

echo
case "$worst" in
  0) echo "All checked units are up, serving, and not looping." ;;
  1) echo "Something is wrong above. journalctl -u <unit> -n 50 --no-pager" ;;
  3) echo "Some checks could not run — read the lines above as incomplete," ;
     echo "  not as a clean bill of health." ;;
esac
exit "$worst"
