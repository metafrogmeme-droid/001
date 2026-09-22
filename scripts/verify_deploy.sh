#!/usr/bin/env bash
# Did the deploy land — on BOTH targets? — a post-deploy gate.
#
# THE FAILURE THIS EXISTS FOR
#
# On 2026-08-25 a deploy pulled the right commit onto the bot box, passed
# verify_deploy_source.sh, restored the symlinks, restarted cleanly, and every
# check reported success. Sign-in stayed broken all day, because the fix was in
# `app/lib/siwf.js` — the WEB CONTAINER — and the bot box never serves `app/`.
# Nothing in the deploy asked about the other half, so nothing could say so.
#
# RUNECLAW HAS TWO DEPLOY TARGETS AND ONE OF THEM IS EASY TO FORGET:
#
#   bot box         python -m bot.main (gateway :8080) + api_bridge.py (:8000)
#                   serves bot/  — the engine, Telegram, the gateway
#   web container   the express app
#                   serves app/  — the site, the arena, sign-in, the dashboard
#
# A change to app/ is invisible to a bot-box deploy and vice versa. This script
# asks both and refuses to report success when only one moved.
#
# HOW THE WEB HALF IS DECIDED
#
# /api/version carries two content hashes computed by app/lib/version.js:
# `build` over server-side .js, `assets` over what the browser gets. This
# script computes the SAME hashes from the local checkout and compares. Equal
# means the live app is serving THIS code. It is a content comparison, not a
# claim about a deploy log — logs say what was attempted.
#
# THREE OUTCOMES, NOT TWO
#
#   0  every target verified against this checkout        A VERDICT
#   1  a target is down, or serving different code        A VERDICT
#   3  something could not be checked                     NOT a verdict
#
# "I could not reach it" is not "it is broken", and reporting an unreachable
# endpoint as a failed deploy sends an operator to roll back a deploy that
# landed perfectly. Same discipline as scripts/verify_bot_alive.sh, which
# separates its verdicts from its non-verdicts for the same reason.
#
# USAGE
#
#   scripts/verify_deploy.sh                      # all targets, defaults
#   scripts/verify_deploy.sh --web-only           # after a web republish
#   scripts/verify_deploy.sh --box-only           # after a bot-box deploy
#
#   WEB_URL       default https://humanoid-traders.com
#   GATEWAY_URL   default http://127.0.0.1:8080
#   BRIDGE_URL    default http://127.0.0.1:8000
#   PROBE_TIMEOUT default 120 (seconds) — the units wait this long too

set -uo pipefail

WEB_URL="${WEB_URL:-https://humanoid-traders.com}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8000}"
# Matches scripts/systemd/runeclaw-bot.service's own ExecStartPost wait on
# this endpoint. A post-deploy check runs when a cold start is EXPECTED, and
# a refusal still fails instantly (curl exit 7) — only slowness costs the wait.
PROBE_TIMEOUT="${PROBE_TIMEOUT:-120}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CHECK_WEB=1
CHECK_BOX=1
for arg in "$@"; do
  case "$arg" in
    --web-only) CHECK_BOX=0 ;;
    --box-only) CHECK_WEB=0 ;;
    -h|--help)  sed -n '2,48p' "$0"; exit 0 ;;
    *) echo "verify_deploy: unknown argument '$arg'. Nothing was checked." >&2; exit 2 ;;
  esac
done

worst=0
note()  { printf '  %s\n' "$*"; }
fail()  { printf 'FAIL     %s\n' "$*"; worst=1; }
ok()    { printf 'OK       %s\n' "$*"; }
# Only downgrade a clean run. An existing failure is a verdict and must never
# be softened into "could not tell".
unk()   { printf 'UNKNOWN  %s\n' "$*"; [ "$worst" -eq 0 ] && worst=3; }

command -v curl >/dev/null 2>&1 || {
  echo "verify_deploy: curl is not installed; NOTHING was checked." >&2
  exit 3
}

# ── the web container ───────────────────────────────────────────────────────
if [ "$CHECK_WEB" -eq 1 ]; then
  echo "web container — $WEB_URL"

  live="$(curl -fsSL --max-time 20 "$WEB_URL/api/version" 2>/dev/null)" || live=""
  if [ -z "$live" ]; then
    unk "could not read $WEB_URL/api/version — the site may be down, or the network is."
  else
    live_build="$(printf '%s' "$live"  | sed -n 's/.*"build":"\([^"]*\)".*/\1/p')"
    live_assets="$(printf '%s' "$live" | sed -n 's/.*"assets":"\([^"]*\)".*/\1/p')"

    # A FIELD NOBODY SENT IS NOT A FIELD THAT DIFFERED.
    #
    # buildInfo() OMITS `build`/`assets` rather than sending null, and its own
    # comment says why: "an absent field reads as not available here, where a
    # null invites being mistaken for a value". These seds then yield "", "" is
    # never equal to the expected hash, and the comparison below reported
    # FAIL — "serving DIFFERENT code" — about a hash the server never claimed.
    #
    # That is a verdict manufactured from an absence, and the expensive kind: it
    # sends an operator to roll back a deploy that may have landed perfectly.
    # A proxy error page reaches here too — an HTML 502 parses to empty for both
    # fields and produced the same confident FAIL.
    #
    # `unk` already exists for exactly this and simply was not reached on this
    # path. Same rule as everywhere else here: unreadable is not a measurement.
    if [ -z "$live_build" ] || [ -z "$live_assets" ]; then
      unk "$WEB_URL/api/version did not report both hashes — not verified."
      [ -z "$live_build" ]  && note "build:  the server did not send this field"
      [ -z "$live_assets" ] && note "assets: the server did not send this field"
      note "An error page or an older build stamp reaches here. This is NOT a"
      note "mismatch — nothing was compared."
    elif ! command -v node >/dev/null 2>&1; then
      unk "node is not available here, so the expected hashes cannot be computed."
      note "live build=$live_build assets=$live_assets"
    else
      local_out="$(cd "$REPO" && node -e \
        'const v=require("./app/lib/version").buildInfo();console.log(v.build+" "+v.assets)' \
        2>/dev/null)" || local_out=""
      if [ -z "$local_out" ]; then
        unk "could not compute the local build hashes (is app/ present?)."
      else
        want_build="${local_out%% *}"
        want_assets="${local_out##* }"
        # Read the table the same way docs/CLAUDE.md does: the PAIR is the
        # diagnosis, because server-only and client-only deploys are both real
        # and one number could not express either.
        if [ "$live_build" = "$want_build" ] && [ "$live_assets" = "$want_assets" ]; then
          ok "serving this checkout (build $live_build, assets $live_assets)"
        else
          fail "serving DIFFERENT code than this checkout"
          note "build   live=$live_build  expected=$want_build"
          note "assets  live=$live_assets  expected=$want_assets"
          if [ "$live_build" != "$want_build" ] && [ "$live_assets" = "$want_assets" ]; then
            note "-> server-side files differ. A change under app/lib or app/routes"
            note "   has not been published. THIS IS THE SIGN-IN SHAPE."
          elif [ "$live_build" = "$want_build" ] && [ "$live_assets" != "$want_assets" ]; then
            note "-> client files differ. Republish, and bump the ?v= cache-buster"
            note "   in every page referencing a changed bundle."
          else
            note "-> both differ: this container has not taken the deploy at all."
          fi
        fi
      fi
    fi
  fi
  echo
fi

# ── the bot box ─────────────────────────────────────────────────────────────
if [ "$CHECK_BOX" -eq 1 ]; then
  echo "bot box"

  # The gateway REQUIRES a secret, so 401/403 is a HEALTHY answer: the server
  # is up and refusing us, which is exactly what it should do. Treating that as
  # a failure would report a working gateway as broken on every run — and an
  # alert that cries wolf is how a real one gets ignored.
  # A TIMEOUT IS NOT A REFUSAL, AND 10s WAS NEVER THE RIGHT BUDGET.
  #
  # `--max-time 10` with `|| code="000"` collapsed two different facts into
  # one verdict: "nothing is listening" and "it is still waking up" both
  # printed `fail  gateway unreachable — the website cannot reach the bot`.
  # Measured on 2026-09-22 a cold gateway answered in 58.7 SECONDS and this
  # script called a healthy service unreachable — while the repo's OWN systemd
  # units wait 120s and 150s on these very endpoints
  # (scripts/systemd/*.service, ExecStartPost=wait_for_port.sh). Two parts of
  # one repo disagreeing by a factor of twelve about the same probe.
  #
  # curl already knows the difference and the old code threw it away: exit 7 is
  # "could not connect" (nothing there — a VERDICT, and it returns instantly,
  # so a dead service still fails fast), exit 28 is "timed out" (something is
  # there and slow, which is NOT a statement that the website cannot reach it —
  # the website has its own, longer budget). Same rule the header states: an
  # alert that cries wolf is how a real one gets ignored.
  probe() {   # $1 label, $2 url -> sets PROBE_CODE, PROBE_RC, PROBE_SECS
    PROBE_CODE="$(curl -s -o /dev/null -w '%{http_code} %{time_total}' \
                  --max-time "$PROBE_TIMEOUT" "$2" 2>/dev/null)"
    PROBE_RC=$?
    PROBE_SECS="${PROBE_CODE##* }"
    PROBE_CODE="${PROBE_CODE%% *}"
    [ -n "$PROBE_CODE" ] || PROBE_CODE="000"
  }

  probe "gateway" "$GATEWAY_URL/gateway/health"
  case "$PROBE_CODE" in
    # The gateway REQUIRES a secret, so 401/403 is a HEALTHY answer.
    200|401|403)
      ok "gateway answering at $GATEWAY_URL (HTTP $PROBE_CODE in ${PROBE_SECS}s)"
      # 58.7s is an answer and also worth knowing about.
      case "$PROBE_SECS" in [1-9][0-9].*|[1-9][0-9][0-9]*)
        note "that is slow for a health check — a cold start, or the box is loaded." ;;
      esac ;;
    000)
      case "$PROBE_RC" in
        28) unk  "gateway did not answer within ${PROBE_TIMEOUT}s at $GATEWAY_URL — \
slow, which is not the same as absent. Raise PROBE_TIMEOUT or look at the box." ;;
        *)  fail "gateway unreachable at $GATEWAY_URL (curl $PROBE_RC) — the website cannot reach the bot" ;;
      esac ;;
    *) fail "gateway returned HTTP $PROBE_CODE at $GATEWAY_URL" ;;
  esac

  probe "bridge" "$BRIDGE_URL/health"
  code="$PROBE_CODE"
  if [ "$code" = "000" ] && [ "$PROBE_RC" = "28" ]; then
    unk "bridge did not answer within ${PROBE_TIMEOUT}s at $BRIDGE_URL — slow, not absent."
    code="__slow__"
  fi
  case "$code" in
    __slow__) : ;;
    200)  ok   "bridge answering at $BRIDGE_URL (in ${PROBE_SECS}s)" ;;
    000)  fail "bridge unreachable at $BRIDGE_URL — insight/patterns/lab will 502" ;;
    *)    fail "bridge returned HTTP $code at $BRIDGE_URL" ;;
  esac

  # ── which code the box is actually RUNNING ────────────────────────────────
  #
  # THIS ASKS THE BOX. The previous version read `git rev-parse HEAD` in
  # "$REPO" — the local checkout, which is the very thing it was supposed to
  # be comparing against — and printed OK whenever that directory was a git
  # repo. Nothing was compared. Run from a laptop it reported the laptop's
  # commit as the bot's, under a comment promising the 2026-08-20 check: a
  # deploy that landed 255 commits stale while every other check agreed it was
  # fine. The gap the file's own header exists to close was open in the file.
  #
  # The answer was already published and unread: bot/web/dashboard_server.py's
  # unauthenticated /health carries `build` (bot/utils/build_info.short()) for
  # exactly this purpose, on the port this script already probes.
  # THE SPACE AFTER THE COLON IS NOT OPTIONAL HERE, AND THAT COST A ROUND.
  # The web half's sed is `"build":"..."` because Express's res.json emits
  # JSON.stringify output with no spaces. The bot is aiohttp, whose
  # json_response uses Python's json.dumps default separators — so it emits
  # `"build": "abc1234"`. Copying the web sed verbatim matched nothing and
  # reported an answering box as "sent no build". Tolerate both.
  box_health="$(curl -fsS --max-time 10 "$GATEWAY_URL/health" 2>/dev/null)"
  live_build="$(printf '%s' "$box_health" \
                | sed -n 's/.*"build"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  # COMPARED LITERALLY, THESE TWO CAN NEVER MATCH, AND THE MISMATCH IS A
  # `fail` — so a perfectly landed deploy printed WRONG CODE with instructions
  # to re-fetch and reset. Observed 2026-09-22: live `7939268 (git)` against
  # local `7939268e`, the same commit, reported as stale code.
  #
  # Two independent differences, and each alone defeats `=`:
  #
  #   * bot/utils/build_info.short() appends a SOURCE NOTE by design — the one
  #     renderer every surface prints, so surfaces cannot disagree about the
  #     same fact: `7939268 (git)`, `7939268-dirty (git)`,
  #     `7939268 (build stamp, tree unchecked)`.
  #   * it abbreviates to a fixed `sha[:7]`, while `git rev-parse --short` is
  #     ADAPTIVE and returns 8 here and more in a bigger repo.
  #
  # So compare the first 7 characters of EACH, which is the part both sides
  # agree is the commit — of both, not just the live one: `--short` is
  # adaptive, so a caller (or a test stub) handing back 8 characters must not
  # read as a different commit either. The note is not discarded: `-dirty` is a
  # real finding and gets said out loud rather than folded into a hash
  # mismatch.
  #
  # The comment below already insists a field nobody SENT is not a field that
  # DIFFERED. A field that was sent, is correct, and is merely spelled
  # differently is the same defect one step further on.
  want_build="$(cd "$REPO" && git rev-parse --short=7 HEAD 2>/dev/null)"
  # the box's label, stripped of its source note and cut to the same width
  live_sha7="$(printf '%s' "${live_build%%[- (]*}" | cut -c1-7)"

  # A FIELD NOBODY SENT IS NOT A FIELD THAT DIFFERED — the same rule the web
  # half above states at length. An older bot omits `build`, and "" must never
  # be compared against a real hash and reported as serving different code.
  if [ -z "$want_build" ]; then
    unk "not a git checkout here, so there is nothing to compare the box against."
  elif [ -z "$live_build" ]; then
    unk "$GATEWAY_URL/health sent no 'build' — an older bot, or the dashboard is down."
  elif [ "$live_build" = "unknown" ]; then
    unk "the box reports build=unknown — it could not resolve its own commit."
  elif [ "$live_sha7" = "$want_build" ]; then
    ok "box is running this checkout ($live_build)"
    case "$live_build" in
      *-dirty*) fail "the box's tree is MODIFIED — it is not running $want_build as committed" ;;
      *"tree unchecked"*)
        unk "the box resolved its commit from a build stamp and did not check its tree, \
so a local edit there would not show." ;;
    esac
  else
    fail "box is running $live_build, this checkout is $want_build — WRONG CODE"
    note "The deploy did not land here. Check the remote it reset to:"
    note "  git fetch https://github.com/metafrogmeme-droid/001 main && git reset --hard FETCH_HEAD"
  fi

  # Did /gateway/* actually mount? A 403 above proves something is serving
  # :8080; it does not prove the gateway sub-app exists, and dashboard_server
  # publishes that separately for precisely that reason.
  gw_state="$(printf '%s' "$box_health" \
              | sed -n 's/.*"gateway"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  case "$gw_state" in
    mounted)       ok "gateway sub-app mounted" ;;
    failed)        fail "gateway did NOT mount — /gateway/* will 404 while :8080 answers 200" ;;
    not_requested) unk "gateway was not requested on this box (no Telegram handler)." ;;
    *)             unk "the box did not report a gateway state." ;;
  esac
  echo
fi

case "$worst" in
  0) echo "DEPLOY VERIFIED on every target checked." ;;
  1) echo "DEPLOY NOT VERIFIED — see the FAIL lines above."
     echo "  A target serving different code has NOT taken your change,"
     echo "  whatever the deploy log said." ;;
  3) echo "INCOMPLETE — some checks could not run. This is NOT a clean bill of"
     echo "  health; it means the question went unanswered." ;;
esac
exit "$worst"
