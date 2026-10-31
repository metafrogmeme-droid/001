#!/usr/bin/env bash
# Deploy HashKeySeal from the owner's machine.
#
# The server never runs this. bot.main and api_bridge set RUNECLAW_SERVER=1,
# and a child of the server inherits it. This script then refuses to sign.
# It also refuses while a server signing key is set, and while a FOUNDRY_*
# variable that would change the compiled bytecode is set. The shell sources
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
# the broadcaster turns out to be anyone else. On mainnet a raw
# ETH_PRIVATE_KEY is refused unless HASHKEY_ALLOW_ENV_KEY=1 is also set, and
# the testnet publisher recorded in networks.json (the address of the
# server's HASHKEY_SEAL_SIGNER_KEY) is refused as publisher and as owner, so
# no key the server has held gains mainnet power. Mainnet is the owner's own
# deploy. Nothing in the server calls this with --network mainnet.

set -euo pipefail

die() { printf 'hashkey-seal: %s\n' "$*" >&2; exit 1; }
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

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
      # The header comment above, up to its first blank line.
      sed -n '2,/^$/p' "$0" | sed '$d'
      exit 0
      ;;
    # The value is not echoed: an argument pasted by mistake may be a key.
    *) die "unknown argument (not echoed; see --help)" ;;
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

# forge reads FOUNDRY_* from the environment over foundry.toml. Each of these
# changes the compiled bytecode, or the profile it is compiled from, so the
# deploy would not be the bytecode CI tested and the explorer verified.
for override in \
  FOUNDRY_PROFILE \
  FOUNDRY_SOLC \
  FOUNDRY_SOLC_VERSION \
  FOUNDRY_EVM_VERSION \
  FOUNDRY_OPTIMIZER \
  FOUNDRY_OPTIMIZER_RUNS \
  FOUNDRY_VIA_IR \
  FOUNDRY_BYTECODE_HASH \
  FOUNDRY_CBOR_METADATA
do
  if [ -n "${!override:-}" ]; then
    die "refused while ${override} is set: it overrides foundry.toml and changes the bytecode. Unset it."
  fi
done

if [ -n "${HASHKEY_WALLET:-}" ] && [ -n "${ETH_PRIVATE_KEY:-}" ]; then
  die "HASHKEY_WALLET and ETH_PRIVATE_KEY are both set. Unset one so the wallet is unambiguous."
fi

# A raw key exported in a shell lands in that shell's history and in the
# environment of every child process. On mainnet that is the owner key for
# the life of the contract, so the hardware or keystore path is the default.
if [ "$NETWORK" = "mainnet" ] && [ -z "${HASHKEY_WALLET:-}" ] && [ -n "${ETH_PRIVATE_KEY:-}" ] \
   && [ "${HASHKEY_ALLOW_ENV_KEY:-}" != "1" ]; then
  die "refused: a raw ETH_PRIVATE_KEY on mainnet. Use HASHKEY_WALLET=ledger|trezor|account. To accept that the key sits in this shell's history and in every child process, set HASHKEY_ALLOW_ENV_KEY=1 as well."
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


# Four lines: chain id, RPC, explorer API, and the testnet publisher (empty
# when the file does not record one). A recorded publisher that is not an
# address fails the read rather than passing as "nobody".
NET_LINE=$(python3 - "$JSON" "$NETWORK" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
table = json.loads(open(path, encoding="utf-8").read())
row = table[name]
need = ("chain_id", "rpc", "explorer_api")
if any(k not in row for k in need):
    sys.stderr.write("network file is missing fields\n")
    sys.exit(1)
pub = table["testnet"].get("publisher")
if pub is not None:
    body = pub[2:] if isinstance(pub, str) and pub[:2].lower() == "0x" else ""
    if len(body) != 40 or any(c not in "0123456789abcdefABCDEF" for c in body):
        sys.stderr.write("network file testnet.publisher is not a 20-byte hex address\n")
        sys.exit(1)
print(int(row["chain_id"]))
print(row["rpc"])
print(row["explorer_api"])
print(pub if isinstance(pub, str) else "")
PY
)
EXPECT_CHAIN=$(printf '%s\n' "$NET_LINE" | sed -n '1p')
RPC=$(printf '%s\n' "$NET_LINE" | sed -n '2p')
EXPLORER_API=$(printf '%s\n' "$NET_LINE" | sed -n '3p')
TESTNET_PUBLISHER=$(printf '%s\n' "$NET_LINE" | sed -n '4p')
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
  command -v curl >/dev/null 2>&1 || die "curl is not installed"
  # The explorer host in networks.json is a hand-written value. A host that
  # has moved answers a redirect, which a submission would not survive, so
  # the API is asked for its stats first and a redirect stops the run.
  STATS_CODE=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$EXPLORER_API/v2/stats") \
    || die "explorer API unreadable at $EXPLORER_API; nothing was submitted"
  case "$STATS_CODE" in
    2[0-9][0-9]) ;;
    3[0-9][0-9]) die "explorer API at $EXPLORER_API answered HTTP $STATS_CODE, a redirect: the host has moved. Update explorer and explorer_api in networks.json; nothing was submitted" ;;
    *) die "explorer API at $EXPLORER_API answered HTTP $STATS_CODE; nothing was submitted" ;;
  esac
  cd "$HERE/.."
  # The code at the address must be what this tree builds, apart from the
  # CBOR metadata tail (its length is in the last two bytes). A FOUNDRY_*
  # override, another tree, or a wrong address shows up here, before the
  # explorer is asked.
  ONCHAIN=$(cast code --rpc-url "$RPC" "$VERIFY") || die "could not read the code at the address; nothing was submitted"
  BUILT=$(forge inspect src/HashKeySeal.sol:HashKeySeal deployedBytecode) || die "forge could not report the built bytecode; nothing was submitted"
  ONCHAIN="$ONCHAIN" BUILT="$BUILT" python3 - <<'PY' || die "stopped before any verify"
import os, sys
def executable(text):
    h = text.strip()
    h = h[2:] if h[:2].lower() == "0x" else h
    if len(h) < 4 or len(h) % 2 or any(c not in "0123456789abcdefABCDEF" for c in h):
        return None
    raw = bytes.fromhex(h)
    meta = int.from_bytes(raw[-2:], "big")
    if meta + 2 > len(raw):
        return None
    return raw[: len(raw) - meta - 2]
on_chain = executable(os.environ["ONCHAIN"])
built = executable(os.environ["BUILT"])
if on_chain is None or len(on_chain) == 0:
    sys.stderr.write("no code at the address, or the code could not be read\n")
    sys.exit(1)
if built is None or len(built) == 0:
    sys.stderr.write("the built bytecode could not be read\n")
    sys.exit(1)
if on_chain != built:
    sys.stderr.write("the code at the address is not this tree's build (metadata tail excluded); nothing was submitted\n")
    sys.exit(1)
sys.stderr.write(f"hashkey-seal: code at the address matches this build ({len(on_chain)} executable bytes)\n")
PY
  ARGS=$(cast abi-encode "constructor(address)" "$HASHKEY_SEAL_PUBLISHER")
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

# cast asks the endpoint for eth_chainId with the same HTTP client forge
# uses, so an endpoint that refuses one client refuses both, and one that
# answers forge answers here. An unreadable answer stops the run.
GOT=$(cast chain-id --rpc-url "$RPC") || die "chain id unreadable; stopped before any deploy"
case "$GOT" in
  ''|*[!0-9]*) die "chain id unreadable; stopped before any deploy" ;;
esac
[ "$GOT" = "$EXPECT_CHAIN" ] || die "endpoint answered chain $GOT, expected $EXPECT_CHAIN; stopped before any deploy"

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

# The testnet publisher is the address of the server's HASHKEY_SEAL_SIGNER_KEY.
# On mainnet it may be neither the publisher nor (below, once the simulation
# has shown it) the owner. An unrecorded value is not "nobody": the check
# cannot run, so a mainnet run stops until networks.json records it.
LOWER_TESTNET_PUBLISHER=""
if [ "$NETWORK" = "mainnet" ]; then
  [ -n "$TESTNET_PUBLISHER" ] || die "networks.json does not record testnet.publisher, the address of the server's HASHKEY_SEAL_SIGNER_KEY. Record it so this script can refuse that key as mainnet publisher or owner; stopped before any deploy"
  LOWER_TESTNET_PUBLISHER=$(lower "$TESTNET_PUBLISHER")
  if [ "$(lower "$HASHKEY_SEAL_PUBLISHER")" = "$LOWER_TESTNET_PUBLISHER" ]; then
    die "HASHKEY_SEAL_PUBLISHER is the testnet publisher, a key the server holds. A mainnet publisher must be a key that has never been in a server environment; stopped before any deploy"
  fi
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

if [ "$NETWORK" = "mainnet" ] && [ "$(lower "$OWNER")" = "$LOWER_TESTNET_PUBLISHER" ]; then
  die "the owner-to-be is the testnet publisher, a key the server holds. A mainnet owner must be a key that has never been in a server environment; nothing was broadcast"
fi

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
  [ "$(lower "$TYPED")" = "$(lower "$OWNER")" ] || die "owner not confirmed; nothing was broadcast"
fi

# The script contract compares the broadcaster with the owner shown above
# and refuses to deploy for anyone else. The EXIT trap does not run across
# exec, so the simulation transcript is removed here.
rm -f "$SIM_OUT"
exec "${ARGS[@]}" "$EXPECT_CHAIN" "$HASHKEY_SEAL_PUBLISHER" "$OWNER" "${TAIL[@]}" --broadcast
