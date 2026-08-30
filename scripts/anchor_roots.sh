#!/usr/bin/env bash
# Anchor a day's seal root on Base — the last leg of Provable Calls.
#
# WHY THIS EXISTS
#
# On 2026-08-25 the roots feed carried THIRTY days of Merkle roots, 191-204
# seals each, and `anchor_tx: null` on every single one. Every piece of the
# pipeline was built and correct — root computation, the dry-run plan, the
# recorder, a live public re-verify with `unknown` as a real status — and the
# one step that is not code had never been taken. The whole apparatus proves
# nothing until somebody sends the transaction.
#
# A mirrored root already proves no call can be back-inserted into a day
# without changing the root. What it cannot prove alone is WHEN the root
# existed — for that you have to trust our database's timestamps. The anchor
# closes that: the block timestamp is a fact nobody here controls, and it
# becomes the independent upper bound on when every seal in that day was
# minted. That is the difference between "trust our track record" and "here is
# the transaction, check it yourself".
#
# NON-CUSTODIAL BY DEFAULT, AND THAT IS THE POINT
#
# The server never holds a key and never sends a transaction, and neither does
# this script unless you explicitly ask it to with --broadcast. The default
# path prints the exact transaction for YOUR wallet and records the hash you
# bring back. The chain is the gatekeeper either way: POST /api/roots/anchor
# re-reads Base and refuses any hash whose calldata is not this day's root,
# whoever submits it.
#
# USAGE
#
#   scripts/anchor_roots.sh --list                  which days are unanchored
#   scripts/anchor_roots.sh --plan 2026-08-24       the exact tx to send
#   scripts/anchor_roots.sh --record 2026-08-24 0x… record a hash you sent
#   scripts/anchor_roots.sh --verify 2026-08-24     ask the chain, live
#   scripts/anchor_roots.sh --broadcast 2026-08-24  sign + send + record (opt-in)
#
# The ERC-8257 tool registration is a different chain object with the same
# three legs, so it lives here too rather than in a second script nobody finds:
#
#   scripts/anchor_roots.sh --tool-plan             the registerTool tx to send
#   scripts/anchor_roots.sh --tool-verify           ask the chain, live
#
#   SITE           default https://humanoid-traders.com
#   RUNECLAW_TOKEN bearer JWT — required for --record and --broadcast.
#                  NEVER printed by this script.
#   ETH_PRIVATE_KEY  read by `cast` itself for --broadcast; never placed on a
#                    command line, where `ps` would show it to every user on
#                    the box.
#
# ON --broadcast: use a DEDICATED key holding only gas dust. The anchor is a
# zero-value self-send, so that key never needs to hold anything, and its blast
# radius should be the dust and nothing else.

set -uo pipefail

SITE="${SITE:-https://humanoid-traders.com}"
TOKEN="${RUNECLAW_TOKEN:-}"

die()  { printf 'anchor: %s\n' "$*" >&2; exit 1; }
note() { printf '  %s\n' "$*"; }

command -v curl >/dev/null 2>&1 || die "curl is not installed; nothing was done."

# python3 rather than jq: jq is not on every deploy box, and a tool that only
# runs where jq happens to be installed is a tool nobody reaches for at 3am.
# python3 is already a hard dependency of this repo.
json() { python3 -c "$1" 2>/dev/null; }

need_token() {
  [ -n "$TOKEN" ] || die "RUNECLAW_TOKEN is not set. Log in to $SITE and export the bearer token."
}

# ── which days still need anchoring ─────────────────────────────────────────
cmd_list() {
  local body
  body="$(curl -fsSL --max-time 25 "$SITE/api/roots" 2>/dev/null)" \
    || die "could not read $SITE/api/roots — the site may be down, or the network is. NOTHING is claimed about the roots."
  printf '%s' "$body" | json '
import json,sys
d=json.load(sys.stdin)
roots=d.get("roots") or []
if not roots:
    print("no roots returned. That is not the same as \"none exist\" — check the feed.")
    sys.exit(0)
un=[r for r in roots if not r.get("anchor_tx")]
an=[r for r in roots if r.get("anchor_tx")]
print(f"{len(roots)} day(s) in the feed: {len(an)} anchored, {len(un)} NOT anchored")
print()
for r in un:
    print("  UNANCHORED  %s  root=%s…  seals=%s" % (r.get("day"), str(r.get("root"))[:16], r.get("seal_count")))
for r in an:
    print("  anchored    %s  tx=%s" % (r.get("day"), r.get("anchor_tx")))
'
}

# ── the exact transaction ───────────────────────────────────────────────────
cmd_plan() {
  local day="$1" body
  body="$(curl -fsSL --max-time 25 "$SITE/api/roots/anchor-plan/$day" 2>/dev/null)" \
    || die "could not read the anchor plan for $day (already anchored, no such day, or the site is unreachable)."
  printf '%s' "$body" | json '
import json,sys
p=json.load(sys.stdin)
if p.get("already_anchored"):
    print("%s is ALREADY anchored: %s" % (p.get("day"), p.get("anchor_tx")))
    sys.exit(0)
print("day        %s" % p.get("day"))
print("root       %s" % p.get("root"))
print("chain      %s (id %s)" % (p.get("chain"), p.get("chain_id")))
print("value      %s" % p.get("value"))
print("to         %s" % p.get("to"))
print()
print("data (send this as the tx calldata):")
print("  %s" % p.get("data"))
print()
for i in (p.get("instructions") or []):
    print("  - %s" % i)
print()
print(p.get("non_custodial_note",""))
'
}

# ── record a hash you already sent ──────────────────────────────────────────
cmd_record() {
  local day="$1" tx="$2" code body
  need_token
  body="$(curl -sS --max-time 40 -w $'\n%{http_code}' \
      -H "Authorization: Bearer $TOKEN" \
      -H 'content-type: application/json' \
      --data "{\"day\":\"$day\",\"tx_hash\":\"$tx\"}" \
      "$SITE/api/roots/anchor" 2>/dev/null)" || die "could not reach $SITE to record the anchor."
  code="$(printf '%s' "$body" | tail -n1)"
  body="$(printf '%s' "$body" | sed '$d')"
  case "$code" in
    200) echo "ANCHORED  $day"; printf '  %s\n' "$body" ;;
    409) echo "already anchored — nothing to do"; printf '  %s\n' "$body" ;;
    # 503 is the server saying it could not READ Base. That is NOT a rejection
    # of your transaction, and re-sending would waste gas anchoring a day twice.
    503) echo "COULD NOT VERIFY — Base was unreadable, so nothing was recorded."
         note "Your transaction is probably fine. Re-run --record with the SAME hash shortly."
         printf '  %s\n' "$body"; exit 3 ;;
    400) echo "REFUSED — that transaction does not anchor this root."
         note "The chain is the gatekeeper here; check you sent the right day's data."
         printf '  %s\n' "$body"; exit 1 ;;
    401) die "unauthorized — RUNECLAW_TOKEN is missing, expired or not an operator token." ;;
    *)   echo "unexpected HTTP $code"; printf '  %s\n' "$body"; exit 1 ;;
  esac
}

# ── ask the chain, live ─────────────────────────────────────────────────────
cmd_verify() {
  local day="$1" body
  body="$(curl -fsSL --max-time 30 "$SITE/api/roots/verify/$day" 2>/dev/null)" \
    || die "could not reach $SITE — NOTHING is claimed about $day."
  printf '%s' "$body" | json '
import json,sys
v=json.load(sys.stdin)
s=v.get("status")
print("day    %s" % v.get("day"))
print("root   %s" % v.get("root"))
if s=="unanchored":
    print("status UNANCHORED — no transaction has been sent for this day yet.")
elif s=="verified":
    print("status VERIFIED against Base")
    print("  tx         %s" % v.get("anchor_tx"))
    print("  block time %s" % v.get("block_time"))
    print("  from       %s" % v.get("from"))
elif s=="unknown":
    # NOT a verdict. Reporting this as a failure would send an operator to
    # re-anchor a day that is perfectly well anchored.
    print("status COULD NOT READ THE CHAIN — this is not a verdict either way.")
    print("  reason %s" % v.get("reason"))
    sys.exit(3)
else:
    print("status %s — the recorded transaction does NOT anchor this root." % str(s).upper())
    print("  reason %s" % v.get("reason"))
    sys.exit(1)
'
}

# ── opt-in: sign and send from this machine ─────────────────────────────────
cmd_broadcast() {
  local day="$1" data tx from
  need_token
  command -v cast >/dev/null 2>&1 \
    || die "--broadcast needs foundry's \`cast\`. Install it, or use --plan and your own wallet."
  [ -n "${ETH_PRIVATE_KEY:-}" ] \
    || die "--broadcast needs ETH_PRIVATE_KEY in the environment (cast reads it directly).
  Use a DEDICATED key holding only gas dust: the anchor is a zero-value
  self-send, so it never needs to hold anything else."

  data="$(curl -fsSL --max-time 25 "$SITE/api/roots/anchor-plan/$day" 2>/dev/null \
          | json 'import json,sys; p=json.load(sys.stdin); print("" if p.get("already_anchored") else (p.get("data") or ""))')"
  [ -n "$data" ] || die "no anchor plan for $day (already anchored, no such day, or unreachable)."

  from="$(cast wallet address 2>/dev/null)" || die "cast could not derive an address from ETH_PRIVATE_KEY."
  echo "sending a zero-value self-send on Base from $from"

  # ETH_PRIVATE_KEY is deliberately NOT passed as an argument: anything on a
  # command line is visible in `ps` to every user on the box. cast reads it
  # from the environment on its own.
  tx="$(cast send "$from" --value 0 --rpc-url https://mainnet.base.org "$data" \
        --json 2>/dev/null | json 'import json,sys; print(json.load(sys.stdin).get("transactionHash",""))')"
  [ -n "$tx" ] || die "broadcast failed — nothing was sent, or the hash could not be read back.
  Check the wallet has gas on Base, then re-run. Nothing has been recorded."

  echo "broadcast $tx"
  # Base needs a moment before the receipt is readable; the recorder answers
  # 503 (not a rejection) if it reads too early, and --record is re-runnable
  # with the same hash.
  sleep 12
  cmd_record "$day" "$tx"
}

# ── the ERC-8257 tool registration, same three legs ─────────────────────────
#
# Separate from the roots because it is a different chain object, but the
# discipline is identical: a plan the operator signs, and a verdict that comes
# from the chain rather than from an environment variable we set ourselves.
cmd_tool_plan() {
  local body
  body="$(curl -fsSL --max-time 25 "$SITE/api/tool/registration-plan" 2>/dev/null)" \
    || die "could not read the registration plan — the site may be down, or the network is."
  printf '%s' "$body" | json '
import json,sys
p=json.load(sys.stdin)
if not p.get("ready"):
    print("NOT READY — do not register:")
    for r in (p.get("not_ready_reasons") or []): print("  - %s" % r)
    sys.exit(1)
print("registry       %s" % p.get("registry"))
print("chain          %s (recommended)" % p.get("recommended_chain_id"))
print("metadata_uri   %s" % p.get("metadata_uri"))
print("manifest_hash  %s" % p.get("manifest_hash"))
print()
print("calldata (send this to the registry, value 0):")
print("  %s" % p.get("calldata"))
print()
print(p.get("non_custodial_note",""))
print()
print("After it is mined, set BOTH on the web container and redeploy:")
print("  REGISTERED_MANIFEST_HASH=%s" % p.get("manifest_hash"))
print("  REGISTERED_TOOL_TX=<the transaction hash>")
print("Then: scripts/anchor_roots.sh --tool-verify")
'
}

cmd_tool_verify() {
  local body code
  # The HTTP code is read separately so a 404 is not reported as "the site is
  # down". Right after this ships and before the web container is redeployed,
  # 404 is the EXPECTED answer, and telling an operator their site is
  # unreachable would send them to debug an outage that is not happening.
  body="$(curl -sSL --max-time 30 -w $'\n%{http_code}' "$SITE/api/tool/registration" 2>/dev/null)" \
    || die "could not reach $SITE at all — NOTHING is claimed about the registration."
  code="$(printf '%s' "$body" | tail -n1)"
  body="$(printf '%s' "$body" | sed '$d')"
  case "$code" in
    200) ;;
    404) die "$SITE has no /api/tool/registration — the web container is running
  code older than this endpoint. Republish it, then re-run. This is NOT a
  statement about the registration." ;;
    503) die "the server could not determine the registration status (503).
  That is 'could not check', not 'not registered'. Try again shortly." ;;
    *)   die "unexpected HTTP $code from $SITE/api/tool/registration." ;;
  esac
  printf '%s' "$body" | json '
import json,sys
v=json.load(sys.stdin)
s=v.get("status")
print("registry      %s" % v.get("registry"))
print("manifest_hash %s" % v.get("manifest_hash"))
hc=v.get("hash_check") or {}
if hc: print("hash check    %s (recorded %s)" % (hc.get("state"), hc.get("recorded")))
if s=="not_submitted":
    print("status NOT SUBMITTED — the plan is ready and nothing has been sent.")
    print("  %s" % v.get("detail"))
elif s=="verified":
    print("status VERIFIED on chain %s" % v.get("chain_id"))
    print("  tx         %s" % v.get("tx"))
    print("  block time %s" % v.get("block_time"))
    print("  from       %s" % v.get("from"))
    print("  note       %s" % v.get("reason"))
elif s=="unknown":
    # Not a verdict. Re-registering over this would spend gas to fix nothing.
    print("status COULD NOT READ THE CHAIN — this is not a verdict either way.")
    print("  reason %s" % v.get("reason"))
    sys.exit(3)
else:
    print("status %s — the recorded transaction does NOT match this registration." % str(s).upper())
    print("  reason %s" % v.get("reason"))
    sys.exit(1)
'
}

case "${1:-}" in
  --list)      cmd_list ;;
  --plan)      [ $# -eq 2 ] || die "usage: --plan <YYYY-MM-DD>"; cmd_plan "$2" ;;
  --record)    [ $# -eq 3 ] || die "usage: --record <YYYY-MM-DD> <0x…>"; cmd_record "$2" "$3" ;;
  --verify)    [ $# -eq 2 ] || die "usage: --verify <YYYY-MM-DD>"; cmd_verify "$2" ;;
  --broadcast) [ $# -eq 2 ] || die "usage: --broadcast <YYYY-MM-DD>"; cmd_broadcast "$2" ;;
  --tool-plan)   cmd_tool_plan ;;
  --tool-verify) cmd_tool_verify ;;
  -h|--help|"") sed -n '2,48p' "$0"; exit 0 ;;
  *) die "unknown argument '$1'. Nothing was done." ;;
esac
