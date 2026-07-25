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
    RCLAW_RPC_URL=https://api.devnet.solana.com   # optional, defaults to devnet
    RCLAW_TIER_PRO_MIN=10000                        # whole tokens for 'pro'
    RCLAW_TIER_ELITE_MIN=100000                     # whole tokens for 'elite'

This is draft tooling: no mainnet is assumed and nothing here signs or holds a
key — it only *reads* balances.
"""

from __future__ import annotations

import base64
import json
import os
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


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        raw = _env(name)
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def mint_address() -> str:
    return _env("RCLAW_MINT")


def staking_program() -> str:
    """The $RCLAW staking program id, if the on-chain staking gate is wired.

    When set, tiers are derived from *staked* balance (programs/rclaw_staking)
    instead of raw wallet balance. Empty => fall back to wallet balance.
    """
    return _env("RCLAW_STAKING_PROGRAM")


def _decimals() -> int:
    try:
        return int(_env("RCLAW_DECIMALS", "9"))
    except (TypeError, ValueError):
        return 9


def gate_enabled() -> bool:
    """True only when the operator opted in AND a mint is configured.

    When this is False the gate is a no-op and existing behavior is preserved
    byte-for-byte.
    """
    return _env_bool("TOKEN_TIER_GATE_ENABLED") and bool(mint_address())


def _rpc(method: str, params: list) -> Optional[dict]:
    """Minimal Solana JSON-RPC call. Returns the ``result`` dict or None on error."""
    url = _env("RCLAW_RPC_URL", _DEFAULT_RPC)
    if "mainnet" in url:
        # Draft tooling is devnet-first; refuse mainnet reads to avoid implying
        # a live deployment (see roadmap Guardrails).
        system_log.warning("tier_gate: refusing mainnet RPC %s; treating as unconfigured", url)
        return None
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

    Reads the rclaw_staking program's ``StakeAccount`` for this owner via
    ``getProgramAccounts`` + a ``memcmp`` on the owner field (Anchor 8-byte
    discriminator, then owner Pubkey at offset 8), summing ``amount`` (u64 LE)
    at offset 40. No PDA derivation needed. Returns ``None`` when it cannot be
    determined (unconfigured, no wallet, or an RPC error) so callers fail-open.
    """
    program = staking_program()
    if not wallet or not program:
        return None
    result = _rpc(
        "getProgramAccounts",
        [
            program,
            {
                "encoding": "base64",
                "commitment": "confirmed",
                "filters": [{"memcmp": {"offset": 8, "bytes": wallet}}],
            },
        ],
    )
    if result is None:
        return None
    total_base = 0
    try:
        # getProgramAccounts returns a list (not a {"value": ...} envelope).
        entries = result if isinstance(result, list) else result.get("value", [])
        for acc in entries:
            data_field = acc["account"]["data"]
            raw = base64.b64decode(data_field[0] if isinstance(data_field, list) else data_field)
            # 8 disc + 32 owner => amount (u64 LE) at offset 40.
            if len(raw) >= 48:
                total_base += int.from_bytes(raw[40:48], "little")
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


def allows_user(users, uid, feature: str) -> bool:
    """Whether ``uid`` may use ``feature``.

    - Gate disabled  -> always True (no behavior change).
    - No wallet linked -> False (must link a wallet holding $RCLAW).
    - RPC/infra error  -> True (fail-open; never lock out on a hiccup).
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
    # Prefer on-chain STAKED balance when a staking program is configured;
    # otherwise fall back to raw wallet balance. Both fail open on infra error.
    bal = staked_of(wallet) if staking_program() else balance_of(wallet)
    if bal is None:
        return True  # fail-open on infra error
    have = tier_for_balance(bal)
    return _TIER_RANK.get(have, 0) >= _TIER_RANK.get(required, 99)


def upgrade_message(mode: str = "premium") -> str:
    """User-facing prompt shown when the gate blocks a premium scan."""
    pro_min = int(_env_float("RCLAW_TIER_PRO_MIN", 10_000.0))
    return (
        f"\U0001f512 <b>{mode.capitalize()} scan is a staked-tier feature.</b>\n"
        f"Stake at least <b>{pro_min:,} $RCLAW</b> and link your Solana wallet to unlock "
        f"premium scan modes. See the token roadmap for details.\n"
        f"<i>(Draft feature — $RCLAW is a gated Vision item.)</i>"
    )
