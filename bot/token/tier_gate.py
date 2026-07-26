"""$RCLAW token-tier gate — DRAFT, feature-flagged OFF by default.

Maps an on-chain **$RCLAW** balance/stake to an access tier and uses it to gate
premium features (today: the /scalp /intraday /swing scan modes). It is modeled
on :mod:`bot.core.onchain`: **completely inert unless explicitly enabled AND
configured**, no import-time side effects, and fail-open on infrastructure
errors so an RPC hiccup never locks a user out.

$RCLAW is a Solana-native SPL Token-2022 (see ``docs/TOKEN_ROADMAP.md``). There
is no Solana client dependency in ``bot/`` yet, so the balance read is a
hand-rolled JSON-RPC call in the same dependency-free ``urllib`` style as
``bot/proofofpnl/anchor.py``. Until a mint + RPC are configured the gate is
inert and never blocks anyone.

Enable (operator, opt-in)::

    TOKEN_TIER_GATE_ENABLED=true
    RCLAW_MINT=<devnet mint address>
    RCLAW_RPC_URL=https://api.devnet.solana.com   # optional, devnet/testnet hosts only
    RCLAW_TIER_PRO_MIN=10000                        # whole tokens for 'pro'
    RCLAW_TIER_ELITE_MIN=100000                     # whole tokens for 'elite'
    RCLAW_STAKING_PROGRAM=<staking program id>      # REQUIRED for staked-tier gating.
                                                    # If unset the gate falls back to raw
                                                    # WALLET balance, so holders pass
                                                    # without ever staking.
    RCLAW_DECIMALS=9                                # OPTIONAL override. Decimals are read
                                                    # from the mint; this only forces a
                                                    # value and warns on divergence. A
                                                    # mismatch mis-scales every threshold
                                                    # by orders of magnitude.

This is draft tooling: no mainnet is assumed and nothing here signs or holds a
key — it only *reads* balances.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Optional

try:  # logging is best-effort; never fail the gate on a logging import
    from bot.utils.logger import system_log
except Exception:  # pragma: no cover - fallback for isolated imports
    import logging

    system_log = logging.getLogger("rclaw.tier_gate")

_DEFAULT_RPC = "https://api.devnet.solana.com"

# Feature -> minimum tier required when the gate is ENABLED.
FEATURE_MIN_TIER: dict[str, str] = {
    "premium_scan": "pro",
}

_TIER_RANK = {"basic": 0, "pro": 1, "elite": 2, "admin": 3}

# Hosts this draft gate is allowed to read from. An *allowlist*, not a denylist:
# an unrecognised host fails closed, so a private or rebranded mainnet endpoint
# (rpc.helius.xyz, *.quiknode.pro, a bare IP) cannot slip through on URL text the
# way the old ``"mainnet" in url`` substring test permitted.
_ALLOWED_RPC_HOSTS = frozenset(
    {
        "api.devnet.solana.com",
        "api.testnet.solana.com",
        "localhost",
        "127.0.0.1",
        "::1",
    }
)

# StakeAccount wire layout. Mirrors ``programs/rclaw_staking/src/lib.rs`` mod
# ``layout``, which has a test asserting these exact values against the Borsh
# encoding. Changing either side alone is a bug.
STAKE_VERSION_OFFSET = 8
STAKE_OWNER_OFFSET = 9
STAKE_MINT_OFFSET = 41
STAKE_AMOUNT_OFFSET = 73
STAKE_UNLOCK_AT_OFFSET = 89
# version(1)+owner(32)+mint(32)+amount(8)+staked_at(8)+unlock_at(8)+bump(1)
STAKE_ACCOUNT_SPACE = 90
# Zeroed growth headroom the program allocates (StakeAccount::RESERVED).
STAKE_ACCOUNT_RESERVED = 64
# Shortest buffer that can be parsed at all.
STAKE_ACCOUNT_MIN_LEN = 8 + STAKE_ACCOUNT_SPACE
# Exact on-chain account size, used as a dataSize filter so arbitrarily-shaped
# accounts from a misconfigured program id are rejected before parsing.
STAKE_ACCOUNT_TOTAL_LEN = STAKE_ACCOUNT_MIN_LEN + STAKE_ACCOUNT_RESERVED
STAKE_ACCOUNT_VERSION = 1


class GateMisconfigured(RuntimeError):
    """Permanent, operator-caused gate misconfiguration.

    Distinct from a transient RPC failure on purpose. A hiccup should fail
    *open* so nobody is locked out by a blip; a deployment that is wired up
    wrongly must fail *closed*, or the paid gate silently unlocks for everyone
    at exactly the moment it is pointed at a real cluster.
    """


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        # Audible, not silent: a typo'd threshold otherwise reverts to the
        # default and the operator sees a gate that quietly ignores their config.
        system_log.warning(
            "tier_gate: %s=%r is not a number; falling back to %s", name, raw, default
        )
        return default


def mint_address() -> str:
    return _env("RCLAW_MINT")


def staking_program() -> str:
    """The $RCLAW staking program id, if the on-chain staking gate is wired.

    When set, tiers are derived from *staked* balance (programs/rclaw_staking)
    instead of raw wallet balance. Empty => fall back to wallet balance.
    """
    return _env("RCLAW_STAKING_PROGRAM")


_DECIMALS_CACHE: dict[str, int] = {}


def _env_decimals() -> Optional[int]:
    raw = _env("RCLAW_DECIMALS")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        system_log.warning("tier_gate: RCLAW_DECIMALS=%r is not an integer; ignoring", raw)
        return None


def _mint_decimals() -> Optional[int]:
    """Decimals as reported by the mint itself, cached per mint address.

    Cached because this is on the path of every gate check and the value is
    immutable for the life of a mint — decimals are fixed at initialization.
    """
    mint = mint_address()
    if not mint:
        return None
    if mint in _DECIMALS_CACHE:
        return _DECIMALS_CACHE[mint]
    result = _rpc("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    if result is None:
        return None  # transient; do not poison the cache
    try:
        value = (result or {}).get("value") or {}
        dec = value["data"]["parsed"]["info"]["decimals"]
        dec = int(dec)
    except (KeyError, TypeError, ValueError) as exc:
        system_log.debug("tier_gate: could not read decimals from mint: %s", exc)
        return None
    _DECIMALS_CACHE[mint] = dec
    return dec


def _decimals() -> int:
    """Scale factor for converting base units to whole tokens.

    Sourced from the **mint**, because that is the only authority on it. The
    environment variable is an override for offline/testing use and warns loudly
    when it disagrees — a wrong value here mis-scales every tier threshold by
    orders of magnitude, in whichever direction happens to be wrong.
    """
    override = _env_decimals()
    try:
        on_chain = _mint_decimals()
    except GateMisconfigured:
        on_chain = None  # the caller's own _rpc will surface this
    if on_chain is not None:
        if override is not None and override != on_chain:
            system_log.warning(
                "tier_gate: RCLAW_DECIMALS=%d disagrees with the mint's %d; using the mint",
                override,
                on_chain,
            )
        return on_chain
    if override is not None:
        return override
    return 9


def gate_enabled() -> bool:
    """True only when the operator opted in AND a mint is configured.

    When this is False the gate is a no-op and existing behavior is preserved
    byte-for-byte.
    """
    return _env_bool("TOKEN_TIER_GATE_ENABLED") and bool(mint_address())


def _rpc(method: str, params: list) -> Optional[dict]:
    """Minimal Solana JSON-RPC call.

    Returns the ``result`` on success and ``None`` on a *transient* failure
    (network, timeout, parse) so callers can fail open. Raises
    :class:`GateMisconfigured` for a permanent configuration fault, which callers
    must translate into a denial rather than an allow.
    """
    url = _env("RCLAW_RPC_URL", _DEFAULT_RPC)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in _ALLOWED_RPC_HOSTS:
        # Not a warn-and-continue: returning None here would land in the
        # transient fail-open path and hand every user a premium tier.
        raise GateMisconfigured(
            f"tier_gate: refusing non-devnet RPC host {host!r}; this is draft "
            "devnet-only tooling (see docs/TOKEN_ROADMAP.md §10-11)"
        )
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            out = json.loads(resp.read().decode())
        if "error" in out:
            system_log.debug("tier_gate rpc error: %s", out["error"])
            return None
        return out.get("result")
    except Exception as exc:  # network/timeout/parse — fail-open upstream
        system_log.debug("tier_gate rpc call failed: %s", exc)
        return None


def balance_of(wallet: Optional[str]) -> Optional[float]:
    """On-chain $RCLAW balance for ``wallet`` in whole tokens.

    Returns ``None`` when it cannot be determined (unconfigured, no wallet, or an
    RPC error) so callers can fail-open. Returns a float ``>= 0`` on success.
    """
    mint = mint_address()
    if not wallet or not mint:
        return None
    result = _rpc(
        "getTokenAccountsByOwner",
        [wallet, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
    )
    if result is None:
        return None
    total = 0.0
    try:
        for acc in result.get("value", []):
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += float(info.get("uiAmount") or 0.0)
    except (KeyError, TypeError, ValueError) as exc:
        system_log.debug("tier_gate parse failed: %s", exc)
        return None
    return total


def staked_of(wallet: Optional[str]) -> Optional[float]:
    """On-chain *staked* $RCLAW for ``wallet`` in whole tokens.

    Reads the rclaw_staking program's ``StakeAccount`` records for this owner via
    ``getProgramAccounts`` + ``memcmp`` filters, summing ``amount`` (u64 LE).
    Offsets mirror the program's ``layout`` module, which tests them against the
    Borsh encoding. Returns ``None`` when it cannot be determined (unconfigured,
    no wallet, or an RPC error) so callers fail-open.

    Only records whose lock has **not** expired count toward a tier. A position
    whose lock has run out is freely withdrawable and can be rotated to another
    wallet, so counting it would restore exactly the abuse the lock prevents.
    """
    program = staking_program()
    if not wallet or not program:
        return None
    # Filter on the discriminator-adjacent version byte and owner, and — when a
    # mint is configured — ALSO on mint, so a stake of some worthless token can
    # never be counted as $RCLAW tier.
    filters = [
        {"memcmp": {"offset": STAKE_OWNER_OFFSET, "bytes": wallet}},
        {"dataSize": STAKE_ACCOUNT_TOTAL_LEN},
    ]
    mint = mint_address()
    if mint:
        filters.append({"memcmp": {"offset": STAKE_MINT_OFFSET, "bytes": mint}})
    result = _rpc(
        "getProgramAccounts",
        [program, {"encoding": "base64", "commitment": "confirmed", "filters": filters}],
    )
    if result is None:
        return None
    now = int(time.time())
    total_base = 0
    try:
        # getProgramAccounts returns a list (not a {"value": ...} envelope).
        entries = result if isinstance(result, list) else result.get("value", [])
        for acc in entries:
            data_field = acc["account"]["data"]
            raw = base64.b64decode(data_field[0] if isinstance(data_field, list) else data_field)
            if len(raw) < STAKE_ACCOUNT_MIN_LEN:
                continue
            if raw[STAKE_VERSION_OFFSET] != STAKE_ACCOUNT_VERSION:
                # Unknown layout: refuse to guess where `amount` lives.
                system_log.warning(
                    "tier_gate: skipping StakeAccount with unsupported version %d",
                    raw[STAKE_VERSION_OFFSET],
                )
                continue
            unlock_at = int.from_bytes(
                raw[STAKE_UNLOCK_AT_OFFSET:STAKE_UNLOCK_AT_OFFSET + 8], "little", signed=True
            )
            if unlock_at <= now:
                continue  # lock expired => liquid => not tier-bearing
            total_base += int.from_bytes(
                raw[STAKE_AMOUNT_OFFSET:STAKE_AMOUNT_OFFSET + 8], "little"
            )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        system_log.debug("tier_gate staked parse failed: %s", exc)
        return None
    return total_base / (10 ** _decimals())


def tier_for_balance(balance: float) -> str:
    """Map a whole-token balance to a stake tier."""
    elite_min = _env_float("RCLAW_TIER_ELITE_MIN", 100_000.0)
    pro_min = _env_float("RCLAW_TIER_PRO_MIN", 10_000.0)
    if balance >= elite_min:
        return "elite"
    if balance >= pro_min:
        return "pro"
    return "basic"


def _resolve_wallet(users, uid) -> Optional[str]:
    """Best-effort telegram-id -> linked Solana wallet lookup.

    Uses ``UserStore.get_sol_wallet`` when available; returns None otherwise so
    the gate degrades gracefully on stores that predate wallet linking.
    """
    if users is None:
        return None
    getter = getattr(users, "get_sol_wallet", None)
    if callable(getter):
        try:
            return getter(uid)
        except Exception:
            return None
    return None


def _is_solana_pubkey(address: Optional[str]) -> bool:
    """Whether ``address`` base58-decodes to exactly 32 bytes.

    Delegates to :mod:`bot.token.solana_verify` so there is one definition of
    "valid Solana address" in the bot. Falls back to a conservative shape check
    only if that import is unavailable, and never raises.
    """
    if not address:
        return False
    try:
        from bot.token.solana_verify import is_valid_address

        return is_valid_address(address)
    except Exception:  # pragma: no cover - solana_verify is a sibling module
        import re as _re

        return bool(_re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address))


def wallet_verified(users, uid) -> bool:
    """Whether ``uid``'s linked wallet carries a recorded ownership proof.

    Fails closed on purpose. A store that predates wallet verification, or a
    record linked before verification existed, counts as **unverified** — the
    alternative grandfathers in exactly the unproven addresses this check exists
    to reject.
    """
    if users is None:
        return False
    checker = getattr(users, "is_sol_wallet_verified", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(uid))
    except Exception:
        return False


def allows_user(users, uid, feature: str) -> bool:
    """Whether ``uid`` may use ``feature``.

    - Gate disabled       -> always True (no behavior change).
    - No wallet linked    -> False (must link a wallet holding $RCLAW).
    - Wallet unverified   -> False (an unproven address carries no tier).
    - Misconfigured gate  -> False (fail CLOSED; permanent operator fault).
    - RPC/infra error     -> True (fail-open; never lock out on a hiccup).
    - Otherwise, the stake-derived tier must meet the feature's minimum.
    """
    if not gate_enabled():
        return True
    required = FEATURE_MIN_TIER.get(feature)
    if required is None:
        return True  # ungated feature
    wallet = _resolve_wallet(users, uid)
    if not wallet:
        return False
    # A stored address that is not a real pubkey makes the RPC return an error,
    # which `staked_of`/`balance_of` report as None — the same sentinel used for
    # a transient outage, which fails OPEN. Invalid input must never reach that
    # path: it is attacker-supplied, so treating it as an infra blip is an
    # authorization bypass, not resilience.
    if not _is_solana_pubkey(wallet):
        system_log.warning("tier_gate: denying %s — stored wallet is not a valid pubkey", uid)
        return False
    # An address nobody proved control of is just a string the user typed. Until
    # it carries a verified signature it must not confer a tier, or anyone can
    # paste a whale's address and inherit their access.
    if not wallet_verified(users, uid):
        system_log.info("tier_gate: denying %s — linked wallet is unverified", uid)
        return False
    # Prefer on-chain STAKED balance when a staking program is configured;
    # otherwise fall back to raw wallet balance.
    try:
        bal = staked_of(wallet) if staking_program() else balance_of(wallet)
    except GateMisconfigured as exc:
        system_log.error("tier_gate: enabled but misconfigured; denying access (%s)", exc)
        return False  # fail CLOSED on a permanent configuration fault
    if bal is None:
        return True  # fail-open on transient infra error
    have = tier_for_balance(bal)
    return _TIER_RANK.get(have, 0) >= _TIER_RANK.get(required, 99)


def upgrade_message(mode: str = "premium") -> str:
    """User-facing prompt shown when the gate blocks a premium scan."""
    pro_min = int(_env_float("RCLAW_TIER_PRO_MIN", 10_000.0))
    return (
        f"\U0001f512 <b>{mode.capitalize()} scan is a staked-tier feature.</b>\n"
        f"Stake at least <b>{pro_min:,} $RCLAW</b>, then link and verify your wallet:\n"
        f"<code>/linkwallet &lt;address&gt;</code> → sign the message → "
        f"<code>/linkwallet verify &lt;signature&gt;</code>\n"
        f"<i>(Draft feature — $RCLAW is a gated Vision item.)</i>"
    )
