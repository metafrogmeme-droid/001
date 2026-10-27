#!/usr/bin/env bash
# Deploy HashKeySeal from the owner's machine.
#
# The server never runs this. bot.main and api_bridge set RUNECLAW_SERVER=1,
# and a child of the server inherits it. This script then refuses to sign.
# It also refuses while a server signing key is set. The shell sources
# nothing; forge itself reads a .env inside contracts/hashkey if one exists,
# so this script refuses to run while that file is present. The key is read
# by forge inside the script contract (ETH_PRIVATE_KEY, with the 0x prefix)
# or by a hardware wallet / keystore flag. It is never placed on a command
# line. HASHKEY_SEAL_PUBLISHER is the first allowlisted publisher.
#
#   contracts/hashkey/script/deploy.sh --network testnet
#   contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=ledger contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=trezor contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=account HASHKEY_ACCOUNT=name contracts/hashkey/script/deploy.sh --network testnet --broadcast
#   HASHKEY_WALLET=ledger contracts/hashkey/script/deploy.sh --network mainnet --broadcast
#   contracts/hashkey/script/deploy.sh --network testnet --verify 0xAddress
#
# A simulation always runs first and prints the owner-to-be: the deploying
# wallet is the owner for the life of the contract. A mainnet --broadcast
# then asks for that address back, interactively or in
# HASHKEY_SEAL_CONFIRM_OWNER, and the script contract refuses to deploy if
# the broadcaster turns out to be anyone else. Mainnet is the owner's own
# deploy. Nothing in the server calls this with --network mainnet.

set -euo pipefail

die() { printf 'hashkey-seal: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 is not installed"

# The EVM version forge compiles for. contracts/hashkey/foundry.toml pins the
# same value and tests/test_hashkey_seal.py holds the two equal, so the
# explorer verifies the bytecode CI tested.
EVM_VERSION="cancun"

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
      sed -n '2,27p' "$0"
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

# forge loads a .env from its project root on its own, and the shell's
# refusals above cannot see what that file holds.
if [ -e "$HERE/../.env" ]; then
  die "remove contracts/hashkey/.env: forge loads it on its own and this script cannot check it"
fi

[ -n "${HASHKEY_SEAL_PUBLISHER:-}" ] || die "set HASHKEY_SEAL_PUBLISHER to the first publisher address"

command -v forge >/dev/null 2>&1 || die "forge is not installed"
command -v cast >/dev/null 2>&1 || die "cast is not installed"


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
    --evm-version "$EVM_VERSION" \
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

# 0x + 40 hex. A mixed-case address must carry a correct EIP-55 checksum:
# vm.envAddress accepts a mistyped checksum, and a mistyped publisher is an
# allowlist nobody holds.
PUBLISHER_CASE=$(python3 - "$HASHKEY_SEAL_PUBLISHER" <<'PY'
import sys
raw = sys.argv[1]
body = raw[2:] if raw[:2].lower() == "0x" else ""
ok = len(raw) == 42 and len(body) == 40 and all(c in "0123456789abcdefABCDEF" for c in body)
if not ok:
    sys.stderr.write("HASHKEY_SEAL_PUBLISHER is not a 20-byte hex address\n")
    sys.exit(1)
print("mixed" if any(c.isupper() for c in body) else "lower")
PY
) || die "stopped before any deploy"
if [ "$PUBLISHER_CASE" = "mixed" ]; then
  CHECKSUMMED=$(cast to-check-sum-address "$HASHKEY_SEAL_PUBLISHER") || die "cast could not checksum the publisher"
  [ "$CHECKSUMMED" = "$HASHKEY_SEAL_PUBLISHER" ] || die "HASHKEY_SEAL_PUBLISHER has a wrong EIP-55 checksum (expected $CHECKSUMMED). Fix the typo or pass it in lower case."
fi

SIG="run(uint256,address,address)"
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
  SIG="runWithCliWallet(uint256,address,address)"
else
  [ -n "${ETH_PRIVATE_KEY:-}" ] || die "set ETH_PRIVATE_KEY, or HASHKEY_WALLET=ledger|trezor|account"
  # The consumer is vm.envUint, which reads a string without 0x as DECIMAL.
  # The shell therefore requires the prefix instead of stripping it.
  python3 - <<'PY'
import os, sys
raw = os.environ.get("ETH_PRIVATE_KEY", "")
ok = raw[:2] == "0x" and len(raw) == 66 and all(c in "0123456789abcdefABCDEF" for c in raw[2:])
if not ok:
    sys.stderr.write("ETH_PRIVATE_KEY must be 0x followed by 64 hex characters\n")
    sys.exit(1)
PY
fi

cd "$HERE/.."
# EXPECT_CHAIN travels as a script argument and is compared with
# block.chainid inside the script contract. It is NOT passed as --chain: in
# forge that flag overrides block.chainid in the simulation while the
# broadcast still signs for the endpoint's real chain, which would make the
# in-script comparison true by construction.
ZERO_ADDRESS=0x0000000000000000000000000000000000000000
ARGS=(forge script script/DeployHashKeySeal.s.sol:DeployHashKeySeal --sig "$SIG")
TAIL=(--rpc-url "$RPC")
if [ "${#WALLET[@]}" -gt 0 ]; then
  TAIL+=("${WALLET[@]}")
fi

# The simulation first, always, with no owner expectation. It prints the
# owner-to-be under "== Logs ==".
SIM_OUT=$(mktemp)
trap 'rm -f "$SIM_OUT"' EXIT
"${ARGS[@]}" "$EXPECT_CHAIN" "$HASHKEY_SEAL_PUBLISHER" "$ZERO_ADDRESS" "${TAIL[@]}" 2>&1 | tee "$SIM_OUT" >&2 || die "simulation failed; nothing was broadcast"
OWNER=$(sed -n 's/.*hashkey-seal: chain [0-9]* seal 0x[0-9a-fA-F]* owner \(0x[0-9a-fA-F]\{40\}\) publisher .*/\1/p' "$SIM_OUT" | head -n 1)
[ -n "$OWNER" ] || die "the simulation did not report the owner-to-be; nothing was broadcast"
printf 'hashkey-seal: owner-to-be %s (the deploying wallet owns the allowlist for the life of the contract)\n' "$OWNER" >&2

if [ "$BROADCAST" -eq 0 ]; then
  printf 'hashkey-seal: simulation only. Pass --broadcast to deploy.\n' >&2
  exit 0
fi

if [ "$NETWORK" = "mainnet" ]; then
  if [ -n "${HASHKEY_SEAL_CONFIRM_OWNER:-}" ]; then
    TYPED="$HASHKEY_SEAL_CONFIRM_OWNER"
  elif [ -t 0 ]; then
    printf 'hashkey-seal: mainnet. Type the owner address shown above to broadcast, anything else to stop: ' >&2
    IFS= read -r TYPED
  else
    die "mainnet --broadcast needs a terminal, or HASHKEY_SEAL_CONFIRM_OWNER=$OWNER"
  fi
  LOWER_TYPED=$(printf '%s' "$TYPED" | tr '[:upper:]' '[:lower:]')
  LOWER_OWNER=$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')
  [ "$LOWER_TYPED" = "$LOWER_OWNER" ] || die "owner not confirmed; nothing was broadcast"
fi

# The script contract compares the broadcaster with the owner shown above
# and refuses to deploy for anyone else. The EXIT trap does not run across
# exec, so the simulation transcript is removed here.
rm -f "$SIM_OUT"
exec "${ARGS[@]}" "$EXPECT_CHAIN" "$HASHKEY_SEAL_PUBLISHER" "$OWNER" "${TAIL[@]}" --broadcast
