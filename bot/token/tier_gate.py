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
import hashlib
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

# Anchor's account discriminator: the first 8 bytes of
# sha256("account:StakeAccount"), written at offset 0 of every account the
# program creates. Computed rather than pasted, so it cannot drift from the
# type name; checked against a live account on a validator (509e437c32bdc0ff).
#
# What filtering on it buys, stated precisely, because the obvious reading is
# wrong: it does NOT defend against `RCLAW_STAKING_PROGRAM` being pointed at a
# hostile program. A hostile program controls its own account bytes and can
# write these eight as easily as any other. What it does defend against is
# *accidental type confusion within this program* — a future account type that
# happens to be 162 bytes with a pubkey at offset 9 would otherwise be summed
# as though it were stake. That is a smaller claim than the audit's phrasing
# suggested, and it is the true one.
STAKE_ACCOUNT_DISCRIMINATOR = hashlib.sha256(b"account:StakeAccount").digest()[:8]


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

# Accounts already proven to exist on the configured RPC. Only *positive*
# results are cached: a missing account is a misconfiguration the operator is
# expected to fix while the process runs, and re-probing it costs one RPC call
# on a path that is already denying.
_ACCOUNT_EXISTS_CACHE: set[str] = set()


def _account_exists(address: str, what: str) -> Optional[bool]:
    """Whether ``address`` exists on the RPC this gate is actually reading.

    ``False`` here is not "the user has nothing" — it means the configured mint
    or program is not deployed on the configured cluster, so every read against
    it will come back empty no matter who is asking.

    This exists because that failure is otherwise invisible. ``RCLAW_RPC_URL``
    defaults to devnet, so a mint or program id from any other cluster (a local
    validator, testnet) yields a *confident zero* — ``getProgramAccounts``
    returns ``[]``, ``getTokenAccountsByOwner`` returns ``[]``, and the gate
    hard-denies every user while reporting a perfectly ordinary balance of 0.
    It never reaches the bounded fail-open path, because nothing failed. The
    only signal is that everyone is suddenly on the free tier.

    Returns ``None`` on a transient RPC failure so the caller keeps its existing
    fail-open behavior rather than treating an outage as a misconfiguration.
    """
    if address in _ACCOUNT_EXISTS_CACHE:
        return True
    result = _rpc("getAccountInfo", [address, {"encoding": "base64", "commitment": "confirmed"}])
    if result is None:
        return None  # transient; do not poison the cache or claim a verdict
    if (result or {}).get("value") is None:
        system_log.error(
            "tier_gate: %s %s does not exist on %s — every balance read will be "
            "empty and every user will be denied. Check RCLAW_RPC_URL: it is "
            "pointing at a different cluster than the one this %s was created on.",
            what,
            address,
            _env("RCLAW_RPC_URL", _DEFAULT_RPC),
            what,
        )
        return False
    _ACCOUNT_EXISTS_CACHE.add(address)
    return True


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
    # Same trap as staked_of: a mint that is not on this cluster makes every
    # holder look like a non-holder.
    if _account_exists(mint, "mint") is False:
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
    # A zero from a cluster that has never heard of this program is not a zero
    # stake. Fail to `None` so the caller's bounded fail-open decides, instead
    # of denying everyone on evidence that does not exist.
    if _account_exists(program, "staking program") is False:
        return None
    # Filter on the Anchor discriminator and owner, and — when a mint is
    # configured — ALSO on mint, so a stake of some worthless token can never be
    # counted as $RCLAW tier.
    #
    # The discriminator is sent base64-encoded because memcmp's default is
    # base58 and these are raw bytes, not a pubkey. Verified against a live RPC
    # rather than assumed: with the correct discriminator the filter returns
    # every stake account, with a wrong one it returns none.
    filters = [
        {"memcmp": {
            "offset": 0,
            "bytes": base64.b64encode(STAKE_ACCOUNT_DISCRIMINATOR).decode(),
            "encoding": "base64",
        }},
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


# ── Last-known-good stake readings, for RPC outages ─────────────────────────
#
# Keyed by wallet, because a tier is a property of the wallet rather than of the
# Telegram user. Deliberately in-process and unpersisted: a restart should not
# resurrect a stale entitlement, and losing the cache only means denying until
# the next successful read, which is the safe direction.
_BALANCE_CACHE: dict[str, tuple[float, float]] = {}


def _grace_seconds() -> float:
    """How long a successful stake reading stays usable during an outage.

    Long enough to ride out a genuine blip, short enough that an induced outage
    is not a standing bypass. Env-tunable because the right value depends on how
    reliable the operator's RPC actually is.
    """
    return _env_float("RCLAW_TIER_GRACE_SECONDS", 900.0)


def _remember_balance(wallet: str, bal: float) -> None:
    _BALANCE_CACHE[wallet] = (float(bal), time.time())


def _cached_balance(wallet: str) -> Optional[float]:
    """The last successful reading for ``wallet``, if it is still fresh."""
    entry = _BALANCE_CACHE.get(wallet)
    if entry is None:
        return None
    bal, at = entry
    age = time.time() - at
    if age > _grace_seconds():
        # Expire rather than keep serving it: an entitlement nobody has been able
        # to confirm for this long is not one to keep honouring.
        _BALANCE_CACHE.pop(wallet, None)
        return None
    return bal


_WARNED_WALLET_FALLBACK = False


def _warn_wallet_balance_fallback() -> None:
    """Say out loud that the gate is grading on holdings, not on stake.

    With ``RCLAW_STAKING_PROGRAM`` unset the gate falls back to raw wallet
    balance. That is a documented fallback, but it silently removes the one
    control the staking program exists to provide: **the 30-day lock**.

    The lock is what makes a tier cost something to hold. Without it a tier is a
    live spot balance — user A proves ownership of a wallet, receives the
    tokens, qualifies, forwards them to user B's wallet, and B qualifies too.
    Both wallets are genuinely verified, so no ownership proof is violated; the
    tokens simply move. One position serves an unlimited number of users, and
    the only cost is one transfer per rotation. With the lock, the same rotation
    costs 30 days per user.

    So this is not a stylistic downgrade from a better balance source. It is the
    difference between a paywall and a queue. It was reachable in complete
    silence: the fallback is documented in the module docstring and nowhere in
    the running system. Logged once per process rather than per call — this sits
    on a hot path, and a message repeated on every scan is one an operator
    filters out.
    """
    global _WARNED_WALLET_FALLBACK
    if _WARNED_WALLET_FALLBACK:
        return
    _WARNED_WALLET_FALLBACK = True
    system_log.error(
        "tier_gate: RCLAW_STAKING_PROGRAM is unset while the gate is ENABLED, so "
        "tiers are being graded on raw wallet balance. There is no lock-up: the "
        "same tokens can be forwarded from wallet to wallet and confer the tier "
        "again each time, so one position serves unlimited users for the cost of "
        "a transfer. Set RCLAW_STAKING_PROGRAM to grade on staked balance."
    )


def check_user(users, uid, feature: str) -> tuple[bool, str]:
    """Whether ``uid`` may use ``feature``, and WHY if not.

    Returns ``(allowed, reason)``. The reason matters because "you have not
    staked enough" and "we could not check your stake" are different messages to
    show a user, and conflating them tells someone holding 100,000 $RCLAW to go
    and stake more during an RPC outage.

    Reasons: ``ok``, ``no_wallet``, ``unverified``, ``bad_wallet``,
    ``misconfigured``, ``unavailable``, ``insufficient``.

    - Gate disabled       -> always True (no behavior change).
    - No wallet linked    -> False (must link a wallet holding $RCLAW).
    - Wallet unverified   -> False (an unproven address carries no tier).
    - Misconfigured gate  -> False (fail CLOSED; permanent operator fault).
    - RPC/infra error     -> last known good tier if read recently, else False.
                             (bounded grace, not an open gate; see _grace_seconds)
    - Otherwise, the stake-derived tier must meet the feature's minimum.
    """
    if not gate_enabled():
        return True, "ok"
    required = FEATURE_MIN_TIER.get(feature)
    if required is None:
        return True, "ok"  # ungated feature
    wallet = _resolve_wallet(users, uid)
    if not wallet:
        return False, "no_wallet"
    # A stored address that is not a real pubkey makes the RPC return an error,
    # which `staked_of`/`balance_of` report as None — the same sentinel used for
    # a transient outage, which fails OPEN. Invalid input must never reach that
    # path: it is attacker-supplied, so treating it as an infra blip is an
    # authorization bypass, not resilience.
    if not _is_solana_pubkey(wallet):
        system_log.warning("tier_gate: denying %s — stored wallet is not a valid pubkey", uid)
        return False, "bad_wallet"
    # An address nobody proved control of is just a string the user typed. Until
    # it carries a verified signature it must not confer a tier, or anyone can
    # paste a whale's address and inherit their access.
    if not wallet_verified(users, uid):
        system_log.info("tier_gate: denying %s — linked wallet is unverified", uid)
        return False, "unverified"
    # Prefer on-chain STAKED balance when a staking program is configured;
    # otherwise fall back to raw wallet balance.
    try:
        if staking_program():
            bal = staked_of(wallet)
        else:
            _warn_wallet_balance_fallback()
            bal = balance_of(wallet)
    except GateMisconfigured as exc:
        system_log.error("tier_gate: enabled but misconfigured; denying access (%s)", exc)
        return False, "misconfigured"  # fail CLOSED on a permanent configuration fault
    if bal is not None:
        _remember_balance(wallet, bal)
        have = tier_for_balance(bal)
        ok = _TIER_RANK.get(have, 0) >= _TIER_RANK.get(required, 99)
        return ok, ("ok" if ok else "insufficient")

    # The RPC could not answer. This used to `return True` unconditionally, and
    # the intent was right — a paying user should not lose access to a blip —
    # but the implementation granted far more than that:
    #
    #   * it PROMOTED users who were never entitled. Link a wallet, verify it,
    #     hold zero $RCLAW, and any RPC error hands you elite. There is no
    #     "don't lock out a paying user" argument for someone who never paid.
    #   * it was UNBOUNDED. No cache, no window — an hour-long outage meant an
    #     hour with every gate open.
    #   * it was INDUCIBLE. Public Solana RPCs rate-limit aggressively; this
    #     repo's own network tests self-skip because of it. Making the bot's
    #     reads fail is cheap, and the reward was free premium access.
    #   * it was SILENT. Nothing logged at the moment the gate opened, so an
    #     operator could not tell "everyone is entitled" from "the gate has been
    #     open all day".
    #
    # So the grace is now what it was meant to be: LAST KNOWN GOOD, bounded. A
    # user whose stake we successfully read recently keeps that tier through the
    # outage. A user we have never read, or have not read in a while, is denied
    # — which is the honest answer, because we do not know.
    cached = _cached_balance(wallet)
    if cached is None:
        system_log.warning(
            "tier_gate: DENYING %s — RPC unavailable and no recent stake reading for this wallet", uid
        )
        return False, "unavailable"
    have = tier_for_balance(cached)
    system_log.warning(
        "tier_gate: RPC unavailable — serving %s from cached stake reading (tier %s)", uid, have
    )
    ok = _TIER_RANK.get(have, 0) >= _TIER_RANK.get(required, 99)
    return ok, ("ok" if ok else "insufficient")


def allows_user(users, uid, feature: str) -> bool:
    """Back-compatible boolean form of :func:`check_user`."""
    return check_user(users, uid, feature)[0]


def unavailable_message() -> str:
    """Shown when the stake could not be READ — not when it is too small.

    Telling someone who holds 100,000 $RCLAW to go and stake more because an RPC
    timed out is both wrong and the kind of thing that gets reported as the token
    being broken.
    """
    return (
        "\U000023f3 <b>Can't verify your stake right now.</b>\n"
        "The Solana RPC did not answer, so your tier could not be confirmed. "
        "This is on our side, not yours — please try again in a minute.\n"
        "<i>(Draft feature — $RCLAW is a gated Vision item.)</i>"
    )


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
