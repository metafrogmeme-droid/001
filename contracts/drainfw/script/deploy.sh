#!/usr/bin/env bash
# Deploy one shared Claw Firewall module from the owner's machine.
#
# Testnet only. The server never runs this. bot.main and api_bridge set
# RUNECLAW_SERVER=1, and a child of the server inherits it. This script then
# refuses to sign. It also refuses while a server signing key is set, and it
# never sources .env or web.env. The key is read by forge inside the script
# contract (ETH_PRIVATE_KEY) or by a hardware wallet / keystore flag. It is
# never placed on a command line.
#
#   contracts/drainfw/script/deploy.sh --network base-sepolia
#   contracts/drainfw/script/deploy.sh --network base-sepolia --delegate
#   contracts/drainfw/script/deploy.sh --network fuji
#   contracts/drainfw/script/deploy.sh --network base-sepolia --broadcast
#
# --delegate deploys the 7702 implementation. That is a test of whether a
# wallet allows delegation, and only on Base Sepolia. Fuji has no EIP-7702.
# Arbitrum Sepolia is policy + hook only from this script. One policy, one
# hasher and one hook per chain. Accounts do not each get a copy.

set -euo pipefail

die() { printf 'claw-firewall: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 is not installed"

NETWORK=""
BROADCAST=0
DELEGATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --network)
      [ $# -ge 2 ] || die "--network needs base-sepolia, arbitrum-sepolia, or fuji"
      NETWORK="$2"
      shift 2
      ;;
    --broadcast) BROADCAST=1; shift ;;
    --delegate) DELEGATE=1; shift ;;
    -h|--help)
      sed -n '2,22p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$NETWORK" ] || die "--network is required"

if [ "${RUNECLAW_SERVER:-}" = "1" ]; then
  die "refused: RUNECLAW_SERVER=1. The server does not deploy and does not sign."
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

case "$NETWORK" in
  base-sepolia|arbitrum-sepolia|fuji) ;;
  *) die "refused: only base-sepolia, arbitrum-sepolia, and fuji. Mainnet is not a target." ;;
esac

if [ "$DELEGATE" -eq 1 ] && [ "$NETWORK" != "base-sepolia" ]; then
  die "refused: a 7702 deploy is only the Base Sepolia wallet-delegation test."
fi

if [ -n "${DRAINFW_WALLET:-}" ] && [ -n "${ETH_PRIVATE_KEY:-}" ]; then
  die "DRAINFW_WALLET and ETH_PRIVATE_KEY are both set. Unset one so the wallet is unambiguous."
fi

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
JSON="$ROOT/contracts/drainfw/networks.json"
[ -f "$JSON" ] || die "missing $JSON"

NET_LINE=$(python3 - "$JSON" "$NETWORK" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
row = json.loads(open(path, encoding="utf-8").read())[name]
need = ("chain_id", "rpc", "allow7702")
if any(k not in row for k in need):
    sys.stderr.write("network file is missing fields\n")
    sys.exit(1)
print(int(row["chain_id"]))
print(row["rpc"])
print("1" if row["allow7702"] is True else "0")
PY
)
EXPECT_CHAIN=$(printf '%s\n' "$NET_LINE" | sed -n '1p')
RPC=$(printf '%s\n' "$NET_LINE" | sed -n '2p')
ALLOW=$(printf '%s\n' "$NET_LINE" | sed -n '3p')
[ -n "$EXPECT_CHAIN" ] && [ -n "$RPC" ] && [ -n "$ALLOW" ] || die "network file could not be read"

if [ "$DELEGATE" -eq 1 ] && [ "$ALLOW" != "1" ]; then
  die "refused: this network does not allow a 7702 deploy."
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

printf 'claw-firewall: network %s chain %s delegate %s\n' "$NETWORK" "$GOT" "$DELEGATE" >&2
if [ "$BROADCAST" -eq 0 ]; then
  printf 'claw-firewall: simulation only. Pass --broadcast to deploy.\n' >&2
fi

command -v forge >/dev/null 2>&1 || die "forge is not installed"

BOOL=false
if [ "$DELEGATE" -eq 1 ]; then
  BOOL=true
fi

SIG="run(bool)"
WALLET=()
if [ -n "${DRAINFW_WALLET:-}" ]; then
  case "$DRAINFW_WALLET" in
    ledger) WALLET=(--ledger) ;;
    trezor) WALLET=(--trezor) ;;
    account)
      [ -n "${DRAINFW_ACCOUNT:-}" ] || die "DRAINFW_WALLET=account needs DRAINFW_ACCOUNT"
      WALLET=(--account "$DRAINFW_ACCOUNT")
      ;;
    *) die "DRAINFW_WALLET must be ledger, trezor, or account" ;;
  esac
  SIG="runWithCliWallet(bool)"
  unset ETH_PRIVATE_KEY
else
  [ -n "${ETH_PRIVATE_KEY:-}" ] || die "set ETH_PRIVATE_KEY, or DRAINFW_WALLET=ledger|trezor|account"
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
ARGS=(forge script script/Deploy.s.sol:DeployDrainFirewall --sig "$SIG" "$BOOL" --rpc-url "$RPC")
if [ "$BROADCAST" -eq 1 ]; then
  ARGS+=(--broadcast)
fi
if [ "${#WALLET[@]}" -gt 0 ]; then
  ARGS+=("${WALLET[@]}")
fi
exec "${ARGS[@]}"
