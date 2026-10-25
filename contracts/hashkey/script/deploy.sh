#!/usr/bin/env bash
# Deploy HashKeySeal from the owner's machine.
#
# The server never runs this. bot.main and api_bridge set RUNECLAW_SERVER=1,
# and a child of the server inherits it. This script then refuses to sign.
# It also refuses while a server signing key is set, and it never sources
# .env or web.env. The key is read by forge inside the script contract
# (ETH_PRIVATE_KEY) or by a hardware wallet / keystore flag. It is never
# placed on a command line.
#
#   contracts/hashkey/script/deploy.sh --network testnet
#   contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=ledger contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=trezor contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=account HASHKEY_ACCOUNT=name contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   contracts/hashkey/script/deploy.sh --network mainnet --broadcast
#   contracts/hashkey/script/deploy.sh --network testnet --verify 0xAddress
#
# Mainnet is the owner's own deploy. Nothing in the server calls this with
# --network mainnet. HASHKEY_SEAL_PUBLISHER is the first allowlisted publisher.

set -euo pipefail

die() { printf 'hashkey-seal: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 is not installed"

NETWORK=""
BROADCAST=0
VERIFY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --network)
      [ $# -ge 2 ] || die "--network needs testnet or mainnet"
      NETWORK="$2"
      shift 2
      ;;
    --broadcast) BROADCAST=1; shift ;;
    --verify)
      [ $# -ge 2 ] || die "--verify needs the deployed address"
      VERIFY="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$NETWORK" ] || die "--network testnet or --network mainnet is required"
[ "$NETWORK" = "testnet" ] || [ "$NETWORK" = "mainnet" ] || die "--network must be testnet or mainnet"

if [ "${RUNECLAW_SERVER:-}" = "1" ]; then
  die "refused: RUNECLAW_SERVER=1. The server does not deploy and does not sign on mainnet."
fi

for leaked in \
  WEB3_SIGNER_PRIVATE_KEY \
  HYPERLIQUID_PRIVATE_KEY \
  SAR_ORACLE_SIGNING_KEY \
  HRC_FEED_SIGNER_KEY \
  HASHKEY_SEAL_SIGNER_KEY
do
  if [ -n "${!leaked:-}" ]; then
    die "refused while ${leaked} is set. That key belongs to the server. Use the owner's wallet in a shell that does not source the server env."
  fi
done

if [ -n "${HASHKEY_WALLET:-}" ] && [ -n "${ETH_PRIVATE_KEY:-}" ]; then
  die "HASHKEY_WALLET and ETH_PRIVATE_KEY are both set. Unset one so the wallet is unambiguous."
fi

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
JSON="$ROOT/contracts/hashkey/networks.json"
[ -f "$JSON" ] || die "missing $JSON"

[ -n "${HASHKEY_SEAL_PUBLISHER:-}" ] || die "set HASHKEY_SEAL_PUBLISHER to the first publisher address"

NET_LINE=$(python3 - "$JSON" "$NETWORK" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
row = json.loads(open(path, encoding="utf-8").read())[name]
need = ("chain_id", "rpc", "explorer_api")
if any(k not in row for k in need):
    sys.stderr.write("network file is missing fields\n")
    sys.exit(1)
print(int(row["chain_id"]))
print(row["rpc"])
print(row["explorer_api"])
PY
)
EXPECT_CHAIN=$(printf '%s\n' "$NET_LINE" | sed -n '1p')
RPC=$(printf '%s\n' "$NET_LINE" | sed -n '2p')
EXPLORER_API=$(printf '%s\n' "$NET_LINE" | sed -n '3p')
[ -n "$EXPECT_CHAIN" ] && [ -n "$RPC" ] && [ -n "$EXPLORER_API" ] || die "network file could not be read"

if [ -n "$VERIFY" ]; then
  command -v forge >/dev/null 2>&1 || die "forge is not installed"
  command -v cast >/dev/null 2>&1 || die "cast is not installed"
  VERIFY="$VERIFY" python3 - <<'PY'
import os, sys
raw = os.environ.get("VERIFY", "")
body = raw[2:] if raw[:2].lower() == "0x" else ""
ok = len(raw) == 42 and len(body) == 40 and all(c in "0123456789abcdefABCDEF" for c in body)
if not ok:
    sys.stderr.write("verify address is not a 20-byte hex address\n")
    sys.exit(1)
PY
  ARGS=$(cast abi-encode "constructor(address)" "$HASHKEY_SEAL_PUBLISHER")
  cd "$HERE/.."
  exec forge verify-contract \
    --verifier blockscout \
    --verifier-url "$EXPLORER_API" \
    --verifier-api-key empty \
    --chain "$EXPECT_CHAIN" \
    --compiler-version 0.8.24 \
    --num-of-optimizations 200 \
    --constructor-args "$ARGS" \
    --watch \
    "$VERIFY" \
    src/HashKeySeal.sol:HashKeySeal
fi

GOT=$(python3 - "$RPC" "$EXPECT_CHAIN" <<'PY'
import json, sys, urllib.request
rpc, expect = sys.argv[1], int(sys.argv[2])
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
req = urllib.request.Request(rpc, data=body, headers={"content-type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode())
except Exception:
    sys.stderr.write("chain id unreadable\n")
    sys.exit(1)
got = payload.get("result") if isinstance(payload, dict) else None
if not isinstance(got, str) or not got.startswith("0x"):
    sys.stderr.write("chain id unreadable\n")
    sys.exit(1)
try:
    cid = int(got, 16)
except ValueError:
    sys.stderr.write("chain id unreadable\n")
    sys.exit(1)
if cid != expect:
    sys.stderr.write(f"endpoint answered chain {cid}, expected {expect}\n")
    sys.exit(1)
print(cid)
PY
) || die "stopped before any deploy"

printf 'hashkey-seal: network %s chain %s\n' "$NETWORK" "$GOT" >&2
if [ "$BROADCAST" -eq 0 ]; then
  printf 'hashkey-seal: simulation only. Pass --broadcast to deploy.\n' >&2
fi

command -v forge >/dev/null 2>&1 || die "forge is not installed"

SIG="run()"
WALLET=()
if [ -n "${HASHKEY_WALLET:-}" ]; then
  case "$HASHKEY_WALLET" in
    ledger) WALLET=(--ledger) ;;
    trezor) WALLET=(--trezor) ;;
    account)
      [ -n "${HASHKEY_ACCOUNT:-}" ] || die "HASHKEY_WALLET=account needs HASHKEY_ACCOUNT"
      WALLET=(--account "$HASHKEY_ACCOUNT")
      ;;
    *) die "HASHKEY_WALLET must be ledger, trezor, or account" ;;
  esac
  SIG="runWithCliWallet()"
  unset ETH_PRIVATE_KEY
else
  [ -n "${ETH_PRIVATE_KEY:-}" ] || die "set ETH_PRIVATE_KEY, or HASHKEY_WALLET=ledger|trezor|account"
  python3 - <<'PY'
import os, sys
raw = os.environ.get("ETH_PRIVATE_KEY", "")
body = raw[2:] if raw[:2].lower() == "0x" else raw
ok = len(body) == 64 and all(c in "0123456789abcdefABCDEF" for c in body)
if not ok:
    sys.stderr.write("ETH_PRIVATE_KEY is not a 32-byte hex scalar\n")
    sys.exit(1)
PY
fi

cd "$HERE/.."
ARGS=(forge script script/DeployHashKeySeal.s.sol:DeployHashKeySeal --sig "$SIG" --rpc-url "$RPC")
if [ "$BROADCAST" -eq 1 ]; then
  ARGS+=(--broadcast)
fi
if [ "${#WALLET[@]}" -gt 0 ]; then
  ARGS+=("${WALLET[@]}")
fi
exec "${ARGS[@]}"
