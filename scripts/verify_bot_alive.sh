#!/usr/bin/env bash
# Did the bot actually stay up? — a post-launch smoke test for deploy scripts.
#
# THE FAILURE THIS EXISTS FOR
#
# On 2026-08-01 the operator ran ~15 consecutive deploys where the bot started,
# found no Telegram connection, and EXITED ZERO. Every deploy printed
# DEPLOY_DONE. None of them left a running bot. The launcher asked "did the
# process start?" when the question that mattered was "is it still there a
# moment later?" -- and those differ by exactly the interval in which a
# misconfigured process gives up.
#
# `python -m bot.main` now defaults to --mode telegram, which removes that
# specific cause. This removes the CLASS: any future reason the bot exits
# early -- a missing secret, an unreadable data dir, a bad venue credential --
# fails the deploy loudly instead of being reported as success.
#
# USAGE — PREFER THE PID FORM
#
#   nohup python -m bot.main >> bot.log 2>&1 &
#   scripts/verify_bot_alive.sh --pid $! || { echo "DEPLOY FAILED"; exit 1; }
#   echo "DEPLOY_DONE"
#
# The launcher already knows the PID it started. Passing it is unambiguous and
# cannot match anything else. The pattern form exists for checking a process
# somebody else started:
#
#   scripts/verify_bot_alive.sh --pattern 'bot\.main'
#   WAIT_SECONDS=20 scripts/verify_bot_alive.sh --pattern api_bridge
#
# WHY THE PID FORM IS THE DEFAULT ADVICE
#
# INVOKE IT ON ITS OWN LINE. `&` binds looser than `&&`, so
#
#     cd ~/runeclaw && nohup python3 -m bot.main ... &  scripts/verify_bot_alive.sh --pid $!
#
# parses as `(cd ~/runeclaw && nohup ...) &  scripts/...` — the cd goes into
# the BACKGROUND subshell and the gate runs in whatever directory the shell
# started in, where the relative path does not resolve. Observed 2026-08-08:
# the gate never executed and the deploy was accepted on visual inspection of
# the ports instead. A launcher that reports success while its verification
# silently did not run is the failure this script exists to prevent, arriving
# through shell grammar rather than through a missing flag.
#
# Correct:
#     cd ~/runeclaw
#     nohup python3 -m bot.main --mode telegram >> logs/bot.log 2>&1 &
#     scripts/verify_bot_alive.sh --pid $! || { echo "DEPLOY FAILED"; exit 1; }
#
# python3, not python — the box has no `python`. And logs/bot.log, not
# bot.log: logs/ is symlinked into the persistent store, the repo root is not,
# so a log written there is lost on the next `git reset --hard`.
#
# `pgrep -f` matches whole command lines, and this script's own command line
# contains whatever pattern it was given. The first draft reported SMOKE OK
# for a process that had never existed -- it was matching itself. That is the
# worst possible behaviour for a smoke test: it turns a real signal into false
# reassurance and would have rubber-stamped exactly the deploys it was written
# to catch. It was caught by running it against a name that was never
# launched. A check that cannot fail has not been tested.
#
# The pattern form below therefore excludes this script BY NAME from the match
# and treats an unreadable /proc entry as "not a match" rather than falling
# through to counting it.
#
# EXIT CODES -- and why there are four rather than two.
#
#   0  the target is alive after the wait        A VERDICT
#   1  the target is NOT alive                   A VERDICT
#   2  this script was called wrong              NOT a verdict
#   3  this script never reached a verdict       NOT a verdict
#
# Everything non-zero still fails a `|| { echo DEPLOY FAILED; exit 1; }`
# launcher, so nothing that worked before stops working; the codes only let a
# caller that cares tell the cases apart.
#
# THE DISTINCTION IS THE WHOLE POINT OF THE FILE. This script exists because a
# deploy reported success without checking. Collapsing "the bot died" into "I
# could not check" is the same defect pointed the other way: it manufactures a
# verdict out of a broken harness, which is what `scripts/red_team.py` refuses
# to do for the same reason. The operator hitting one of those and concluding
# the gate is unreliable is not a hypothetical -- 2026-08-17, exit 144, below.
#
# 144 IS 128+16: KILLED BY A SIGNAL, NOT A FAILED CHECK. On 2026-08-17 this
# script was reported "exiting 144 when run inline in one-liners", and the gate
# was routed around in favour of an out-of-repo restart script. Every intrinsic
# path here returns 0 or 1 -- a pid that never existed, an empty `$!`, a target
# that died instantly, a pattern matching nothing. 144 can only arrive from
# OUTSIDE: something delivered SIGSTKFLT during the ~11s the check sleeps.
# Unhandled, that reads to a launcher as "the bot is dead" when in truth
# nothing was concluded and the bot is probably fine. So the signals are
# trapped and reported as 3 -- no verdict -- in words, rather than as a number
# a caller has to decode.
#
# Dependency-free (pgrep, kill -0, sleep) so it runs on a deploy host without a
# Python environment, and safe to call from a launcher living OUTSIDE the repo
# -- which is where a launcher belongs, since `git reset --hard` overwrites
# anything inside it.
set -uo pipefail

SELF_NAME="verify_bot_alive"
WAIT_SECONDS="${WAIT_SECONDS:-10}"
MODE="" ; TARGET=""

# A run that was killed has not cleared anything. Say so in those words: the
# bare 128+n code is indistinguishable from a verdict to `||`, and that
# ambiguity is what taught an operator to stop trusting this check.
_no_verdict() {
  echo "SMOKE UNKNOWN: killed by SIG$1 before a verdict was reached." >&2
  echo "  This is NOT 'the bot died' -- nothing was concluded, and the bot is" >&2
  echo "  probably running. Something in the calling environment signalled" >&2
  echo "  this check during its ${WAIT_SECONDS}s wait. Re-run it, lower" >&2
  echo "  WAIT_SECONDS, or run it where nothing reaps long-lived children." >&2
  exit 3
}
# STKFLT is the one actually observed; the rest are the plausible neighbours.
#
# BY NAME, THEN BY NUMBER. `trap ... STKFLT` is REJECTED OUTRIGHT by dash, and
# the first version of this loop swallowed that with `|| true`. So on any shell
# that does not know the name, the trap written to REPORT an interrupted run
# was itself silently missing -- precisely where it was needed, and invisibly.
# Signal numbers are portable where names are not.
#
# Verified 2026-08-17:  bash  name=ok  number=ok
#                       dash  name=NO  number=ok
_trapped=0
for _pair in "HUP 1" "INT 2" "QUIT 3" "TERM 15" "USR1 10" "USR2 12" "STKFLT 16"; do
  _name=${_pair% *}; _num=${_pair#* }
  # shellcheck disable=SC2064
  if trap "_no_verdict $_name" "$_name" 2>/dev/null \
     || trap "_no_verdict $_name" "$_num" 2>/dev/null; then
    _trapped=$((_trapped + 1))
  fi
done
unset _pair _name _num
# If NOTHING could be trapped, say so once rather than leaving the next 144 to
# be read as "the bot died" all over again. A guard that could not install is
# not the same as one that found nothing wrong.
[ "$_trapped" -gt 0 ] || echo "SMOKE NOTE: no signal traps could be installed; an" \
  "interrupted run will exit 128+n rather than 3." >&2
unset _trapped

# `shift 2` with only one argument left FAILS and shifts nothing, so the loop
# spins on the same token forever. `verify_bot_alive.sh --pid` (value
# forgotten) hung instead of erroring -- and a deploy gate that hangs is worse
# than one that is merely wrong: the deploy never finishes and never says why.
# Caught by tests/test_deploy_smoke_guard.py, which timed out rather than
# failed. Check the count before consuming.
while [ $# -gt 0 ]; do
  case "$1" in
    --pid|--pattern)
      if [ $# -lt 2 ]; then
        # 2, not 1: nothing was checked, so this is not a claim about the bot.
        echo "SMOKE USAGE: $1 needs a value. Nothing was checked." >&2
        exit 2
      fi
      case "$1" in --pid) MODE="pid" ;; *) MODE="pattern" ;; esac
      TARGET="$2"
      shift 2
      ;;
    -h|--help) sed -n '2,95p' "$0"; exit 0 ;;
    *)         MODE="pattern"; TARGET="$1"; shift ;;
  esac
done
[ -z "$MODE" ] && { MODE="pattern"; TARGET='bot\.main'; }
[ -z "$TARGET" ] && { echo "SMOKE USAGE: --$MODE needs a value. Nothing was checked." >&2; exit 2; }

# Returns 0 when the target is alive. Never prints; never self-matches.
_alive() {
  if [ "$MODE" = "pid" ]; then
    # `kill -0` is NOT enough. A process that exits becomes a ZOMBIE until its
    # parent reaps it, and kill -0 succeeds on a zombie -- so a bot that died
    # instantly would read as alive.
    #
    # That is not a corner case here, it is the intended usage: the documented
    # call is `nohup python -m bot.main & ; verify_bot_alive.sh --pid $!`, so
    # the deploy script IS the parent and has not reaped anything while this
    # check runs. The smoke test would have passed on exactly the failure it
    # exists to catch. Caught by tests/test_deploy_smoke_guard.py.
    #
    # /proc/PID/stat field 3 is the state; Z is defunct. The awk skips the
    # comm field, which can itself contain spaces and parentheses.
    kill -0 "$TARGET" 2>/dev/null || return 1
    if [ -r "/proc/$TARGET/stat" ]; then
      local state
      state="$(awk '{ for (i = NF; i > 0; i--) if ($i ~ /^[RSDZTtWXxKWPI]$/) { print $i; exit } }' \
        "/proc/$TARGET/stat" 2>/dev/null)"
      [ "$state" = "Z" ] && return 1
    fi
    return 0
  fi
  local pid cmd
  for pid in $(pgrep -f -- "$TARGET" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "$PPID" ] && continue
    # Read the cmdline; an unreadable one means the process is already gone,
    # which is NOT a match. The first draft let a failed read fall through to
    # counting the pid, which is how it matched itself.
    # Test readability BEFORE the redirect: a `< /proc/gone/cmdline` failure
    # is reported by the SHELL, before the command runs, so `|| continue`
    # cannot suppress it. That printed "No such file or directory" into deploy
    # output on every successful check -- an alarming-looking error line on a
    # passing run, which is the false-signal problem this script exists to
    # remove, reproduced in its own output.
    [ -r "/proc/$pid/cmdline" ] || continue
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || continue
    [ -z "$cmd" ] && continue
    case "$cmd" in *"$SELF_NAME"*) continue ;; esac
    return 0
  done
  return 1
}

_describe() {
  if [ "$MODE" = "pid" ]; then echo "pid $TARGET"; else echo "'$TARGET'"; fi
}

# Fail fast if it never came up at all: no point waiting ten seconds to learn
# the launch line was wrong.
sleep 1
if ! _alive; then
  echo "SMOKE FAIL: $(_describe) is not running one second after launch." >&2
  echo "  It never started. Check the launch command and the log." >&2
  exit 1
fi

sleep "$WAIT_SECONDS"

if ! _alive; then
  echo "SMOKE FAIL: $(_describe) started and exited within ${WAIT_SECONDS}s." >&2
  echo "  A clean early exit is the dangerous case -- the launcher sees" >&2
  echo "  success and no bot is left running. Check the tail of the log for" >&2
  echo "  a missing secret, an unreadable data dir, or a wrong --mode." >&2
  exit 1
fi

echo "SMOKE OK: $(_describe) still running after ${WAIT_SECONDS}s."
exit 0
