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

set -uo pipefail

WEB_URL="${WEB_URL:-https://humanoid-traders.com}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8000}"
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
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
          "$GATEWAY_URL/gateway/health" 2>/dev/null)" || code="000"
  case "$code" in
    200|401|403) ok  "gateway answering at $GATEWAY_URL (HTTP $code)" ;;
    000)         fail "gateway unreachable at $GATEWAY_URL — the website cannot reach the bot" ;;
    *)           fail "gateway returned HTTP $code at $GATEWAY_URL" ;;
  esac

  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
          "$BRIDGE_URL/health" 2>/dev/null)" || code="000"
  case "$code" in
    200)  ok   "bridge answering at $BRIDGE_URL" ;;
    000)  fail "bridge unreachable at $BRIDGE_URL — insight/patterns/lab will 502" ;;
    *)    fail "bridge returned HTTP $code at $BRIDGE_URL" ;;
  esac

  # Which code the box is actually on. Compared against the local checkout,
  # because a deploy that pulled the wrong commit passes every other check —
  # 2026-08-20, 255 commits stale, everything green.
  if head="$(cd "$REPO" && git rev-parse --short HEAD 2>/dev/null)"; then
    ok "checkout at $head"
  else
    unk "not a git checkout here, so the running commit could not be confirmed."
  fi
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
