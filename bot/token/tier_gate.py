"""Membership tiers, and the $RCLAW token-tier gate — OFF by default.

A member's tier is the higher of the one the operator GRANTS (``/set_tier``,
read by :func:`granted_tier`) and the one a stake EARNS (:func:`stake_tier`,
read only while the token gate below is on): :func:`tier_reading` is the one
reading, and every tier decision and card asks it. The ladder refuses nobody
until :func:`tiers_enforced` (``TIER_GATE_ENABLED``, or the token gate), and
:func:`plan_ladder` says what each tier buys, read from what enforces it.

The token half maps an on-chain **$RCLAW** balance/stake to an access tier and
uses it to gate premium features across two tiers: `pro` for per-call analysis (/scalp
/intraday /swing /deepscan /patterns /analyze) and `elite` for research-grade
work (/backtest /walkforward /optimize /learn). Safety commands — /halt,
risk, portfolio, whynot — are in NEVER_GATED and can never be sold. It is modeled
on :mod:`bot.core.onchain`: **completely inert unless explicitly enabled AND
configured**, no import-time side effects, and fail-open on infrastructure
errors so an RPC hiccup never locks a user out.

$RCLAW is a Solana SPL token. The roadmap (``docs/TOKEN_ROADMAP.md``) proposed
Token-2022; the mint that exists on mainnet is a classic SPL Token under
``TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`` (``token/config/rclaw.mainnet.json``,
read by ``bot.token.record``), which this reader handles the same way. There
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

from bot.token import tier_weight as tier_weight_mod

try:  # logging is best-effort; never fail the gate on a logging import
    from bot.utils.logger import system_log
except Exception:  # pragma: no cover - fallback for isolated imports
    import logging

    system_log = logging.getLogger("rclaw.tier_gate")

_DEFAULT_RPC = "https://api.devnet.solana.com"

# Commands that must NEVER require a token, at any tier, for any reason.
#
# This is a product-safety line, not a pricing decision, and it is written down
# because pricing pressure is exactly what erodes it later. Everything here helps
# a user UNDERSTAND or REDUCE their exposure. Gating any of it means a user whose
# stake lapsed — or whose RPC is down — cannot see their own positions or hit the
# kill switch while a trade runs against them. No amount of revenue justifies
# that, and a gate that can reach these is a gate that will eventually be pointed
# at them by someone tuning conversion.
#
# `_assert_never_gated` enforces this against FEATURE_MIN_TIER, including the
# operator's env override, and refuses rather than warns.
NEVER_GATED: frozenset[str] = frozenset({
    "halt",             # the kill switch
    "check_risk",
    "get_portfolio",
    "proposals",
    "rejected_trades",  # why we did NOT trade — the explainability promise
    "whynot",
    "costs",
})

# Feature -> minimum tier required when the gate is ENABLED.
#
# The ladder is built on COST TO SERVE, which is the only basis that stays
# honest as the product changes: `pro` is per-call LLM/compute analysis, `elite`
# is research-grade work that runs for minutes. Free is everything a user needs
# to operate safely and understand what the bot did.
#
# It used to be `{"premium_scan": "pro"}` alone — one entry, so `elite` bought
# nothing `pro` did not and the whole staking thesis rested on a single dict key.
_DEFAULT_FEATURE_MIN_TIER: dict[str, str] = {
    # pro — real compute per call
    "premium_scan": "pro",     # /scalp /intraday /swing
    "deepscan": "pro",         # the full universe sweep
    "patterns": "pro",
    "analyze_asset": "pro",
    # /quant — a 150-bar fetch plus regime, GARCH, factor scoring and Hurst.
    # Heavier than analyze_asset and lighter than anything in `elite`, which
    # buys MINUTES (backtest, walk-forward, optimize). Seconds of in-process
    # modelling is what `pro` means on this ladder, so that is where it sits.
    "quant_analyze": "pro",
    # elite — minutes of compute, the most expensive things this bot does
    "run_backtest": "elite",
    "walk_forward": "elite",
    "optimize": "elite",
    "learning": "elite",
}


def _load_feature_min_tier() -> dict[str, str]:
    """The ladder, with an operator override.

    ``RCLAW_TIER_FEATURES="deepscan:elite,patterns:basic"`` retunes without a
    code change, because the right split depends on real serving costs that
    nobody knows yet. Unparseable entries are refused loudly rather than
    silently dropped: a typo that quietly ungates a feature is a revenue leak
    nobody would notice, and one that quietly gates a safety command is worse.
    """
    table = dict(_DEFAULT_FEATURE_MIN_TIER)
    raw = _env("RCLAW_TIER_FEATURES")
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            feature, _, tier = pair.partition(":")
            feature, tier = feature.strip(), tier.strip().lower()
            if not feature or tier not in _TIER_RANK:
                raise GateMisconfigured(
                    f"tier_gate: RCLAW_TIER_FEATURES entry {pair!r} is not "
                    f"'<feature>:<tier>' with tier in {sorted(_TIER_RANK)}"
                )
            if tier == "basic":
                table.pop(feature, None)  # explicitly ungate
            else:
                table[feature] = tier
    _assert_never_gated(table)
    return table


def _assert_never_gated(table: dict[str, str]) -> None:
    """Refuse a configuration that puts a safety command behind the token."""
    trespass = sorted(set(table) & NEVER_GATED)
    if trespass:
        raise GateMisconfigured(
            f"tier_gate: refusing to gate {trespass} — these let a user see or "
            "reduce their own exposure and must stay free at every tier. If this "
            "came from RCLAW_TIER_FEATURES, remove it; if from a code change, "
            "read the NEVER_GATED comment before changing it."
        )


#: Each tier's rank, from the one list of tiers (`bot.utils.user_store.TIERS`).
#: This module had its own copy; a fourth tier added to the store would have
#: been unknown here and refused by `RCLAW_TIER_FEATURES`.
from bot.utils.user_store import TIER_RANK as _TIER_RANK  # noqa: E402

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


# Evaluated here rather than beside its definition: the loader needs _env,
# _TIER_RANK and GateMisconfigured, all of which are defined above this point.
FEATURE_MIN_TIER: dict[str, str] = _load_feature_min_tier()

#: A tier's name, as a person reads it.
TIER_NAMES: dict[str, str] = {"basic": "Basic", "pro": "Pro", "elite": "Elite",
                              "admin": "Operator"}

#: The commands a person types for each gated feature, each with the role
#: permission its handler's `@guard` asks, for every card that says what a
#: tier unlocks. A test reads every `_token_gate_blocks` call site and checks
#: its handler is registered under a command named here with the permission
#: named here, so a feature gated tomorrow cannot reach a card as an internal
#: identifier, and a card cannot offer a command the caller's role refuses
#: (`/patterns` is on the ladder at Pro and held by no role but the
#: operator's: a Pro member was being sold a command that answers "your role
#: cannot use that").
FEATURE_COMMANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "premium_scan": (("scalp", "scan"), ("intraday", "scan"), ("swing", "scan")),
    "deepscan": (("deepscan", "deepscan"),),
    "patterns": (("patterns", "patterns"),),
    "analyze_asset": (("analyze", "analyze"),),
    "quant_analyze": (("quant", "analyze"),),
    "run_backtest": (("backtest", "backtest"),),
    "walk_forward": (("walkforward", "walkforward"),),
    "optimize": (("optimize", "optimize"),),
    "learning": (("learn", "learn"),),
}


def _role_allows(users, uid, permission: str) -> bool:
    """May this caller's role run a command guarded by ``permission`` at all?
    A store that cannot say is not a yes: the card leaves the command out."""
    try:
        return users.permission_denial(uid, permission) is None
    except Exception:  # noqa: BLE001
        return False


def plan_ladder(users=None, uid=None) -> dict:
    """What each tier buys on THIS deployment, read from what enforces it.

    Every card that names a tier renders this, so none of them can list a
    benefit nothing grants. Three things differ by tier, each read from its
    own enforcer: the commands on the ladder (FEATURE_MIN_TIER, refused only
    while ``tiers_enforced``), the daily chat questions (``chat_quota``, which
    has its own switch), and the operator-funded model on a hand-run
    ``/analyze`` (``PER_USER_LLM_TIERS_ENABLED``). Live trading, position size
    and leverage are never on it (docs/TIER_MODEL.md §6).

    With a caller (``users``, ``uid``) a command is listed only if the
    caller's role may run it: the tier is not the only gate in front of it.
    """
    from bot.web import chat_quota
    try:
        from bot.config import CONFIG
        routed = bool(getattr(CONFIG.analyzer, "per_user_llm_tiers_enabled", False))
    except Exception:  # noqa: BLE001
        routed = None  # unread: the card says it could not tell
    from bot.llm.provider import routing_for_user_tier

    limit = chat_quota.free_daily_limit() if chat_quota.quota_enabled() else None
    shown = [t for t in _TIER_RANK if t != "admin"]
    if any(v == "admin" for v in FEATURE_MIN_TIER.values()):
        shown.append("admin")
    tiers = []
    for tier in shown:
        unlocks = []
        for f, need in sorted(FEATURE_MIN_TIER.items()):
            if need != tier:
                continue
            pairs = FEATURE_COMMANDS.get(f)
            if pairs is None:
                # An operator-gated feature with no command of its own
                # (RCLAW_TIER_FEATURES): named as the operator wrote it.
                unlocks.append({"feature": f, "commands": []})
                continue
            commands = [c for c, perm in pairs
                        if users is None or _role_allows(users, uid, perm)]
            if commands:
                unlocks.append({"feature": f, "commands": commands})
        tiers.append({
            "tier": tier,
            "name": TIER_NAMES.get(tier, tier),
            "unlocks": unlocks,
            # None: no daily cap (the quota is off, or this tier is exempt).
            "chat_per_day": None if chat_quota.is_quota_exempt(tier) else limit,
            "premium_analyze": (None if routed is None
                                else bool(routed and routing_for_user_tier(tier))),
        })
    stake = gate_enabled()
    return {
        "enforced": tiers_enforced(),
        "stake": stake,
        "stake_min": ({"pro": int(_env_float("RCLAW_TIER_PRO_MIN", 10_000.0)),
                       "elite": int(_env_float("RCLAW_TIER_ELITE_MIN", 100_000.0))}
                      if stake else None),
        "tiers": tiers,
    }


def tier_reading(users, uid) -> dict:
    """One member's tier and where it came from, for ``/tier`` and the
    website's plan card. The same three readings ``check_user`` asks."""
    granted, readable = granted_tier(users, uid)
    out: dict = {
        "granted": granted,
        "granted_readable": readable,
        "granted_known": granted in _TIER_RANK,
        "stake": None,
        "stake_reason": None,
    }
    best = granted if granted in _TIER_RANK else None
    if gate_enabled():
        staked, out["stake_reason"] = stake_tier(users, uid)
        out["stake"] = staked
        if staked is not None and (best is None or _TIER_RANK[staked] > _TIER_RANK[best]):
            best = staked
    # A stored word this bot does not rank is no grant: it counts as Basic,
    # and `granted_known` lets the card name it. None is the one answer that
    # is not Basic: the grant could not be read and no stake stood in.
    out["tier"] = best if best is not None else ("basic" if readable else None)
    return out


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


def tiers_enforced() -> bool:
    """Whether the ladder (FEATURE_MIN_TIER) refuses anyone at all.

    TWO SWITCHES, because a tier has two sources. ``TIER_GATE_ENABLED`` turns
    the ladder on for the tier the operator grants with ``/set_tier``;
    ``TOKEN_TIER_GATE_ENABLED`` (with a mint) adds the tier a stake earns.
    Either one enforces it. Before this, the only switch was the token's, so a
    granted Pro or Elite bought nothing: with the token gate off nobody was
    refused, and with it on a grant was ignored and only a stake counted.

    Off by default, and that is the operator's pricing decision rather than a
    safety one: switched on, the ladder refuses Basic members the features it
    names, and nobody should lose a command in a deploy they did not choose.
    """
    return _env_bool("TIER_GATE_ENABLED") or gate_enabled()


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
    records = _stake_records(wallet, program, _stake_filters(wallet))
    if records is None:
        return None
    scale = 10 ** _decimals()
    return sum(amount for amount, _lock in records) / scale


def _stake_filters(wallet: str) -> list:
    """``getProgramAccounts`` filters selecting this owner's stake records.

    Shared by ``staked_of`` and ``staked_profile`` so the two cannot drift into
    reading different sets of accounts — a mint filter present on one path and
    absent on the other would let a stake of some worthless token confer a tier
    down whichever path forgot it.

    Filters on the Anchor discriminator and owner, and — when a mint is
    configured — ALSO on mint. The discriminator is sent base64-encoded because
    memcmp's default is base58 and these are raw bytes, not a pubkey. Verified
    against a live RPC rather than assumed: with the correct discriminator the
    filter returns every stake account, with a wrong one it returns none.
    """
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
    return filters


def _stake_records(wallet: str, program: str, filters: list) -> Optional[list[tuple[int, int]]]:
    """Live stake records for ``wallet`` as ``(amount_base_units, lock_seconds_left)``.

    Extracted so the tier weight (:mod:`bot.token.tier_weight`) can read the lock
    remainders this parse already computes, rather than a second RPC round trip
    re-deriving them. ``staked_of`` sums the amounts and throws the locks away;
    ``staked_profile`` keeps both.

    ``None`` on a transient RPC failure or an unreadable response, matching
    ``staked_of``'s contract — a partial list read as a whole one would
    under-report a stake and deny a tier the holder actually has.
    """
    result = _rpc(
        "getProgramAccounts",
        [program, {"encoding": "base64", "commitment": "confirmed", "filters": filters}],
    )
    if result is None:
        return None
    now = int(time.time())
    out: list[tuple[int, int]] = []
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
            out.append((
                int.from_bytes(raw[STAKE_AMOUNT_OFFSET:STAKE_AMOUNT_OFFSET + 8], "little"),
                unlock_at - now,
            ))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        system_log.debug("tier_gate staked parse failed: %s", exc)
        return None
    return out


def staked_profile(wallet: Optional[str]) -> Optional[tuple[float, float]]:
    """``(staked_whole_tokens, amount_weighted_lock_seconds_remaining)``.

    The inputs :func:`bot.token.tier_weight.tier_weight` needs, read from the
    same accounts ``staked_of`` already fetches. ``None`` under exactly the same
    conditions ``staked_of`` returns ``None``, so the two agree about what a
    failed read is.
    """
    program = staking_program()
    if not wallet or not program:
        return None
    if _account_exists(program, "staking program") is False:
        return None
    records = _stake_records(wallet, program, _stake_filters(wallet))
    if records is None:
        return None
    scale = 10 ** _decimals()
    folded = tier_weight_mod.stake_weight_inputs(
        (amount / scale, lock) for amount, lock in records
    )
    if folded is None:
        # No live records at all. A real, readable zero — distinct from the
        # `None` above, which is "we could not look".
        return 0.0, 0.0
    return folded


def tier_for_balance(balance: float) -> str:
    """Map a whole-token balance to a stake tier."""
    elite_min = _env_float("RCLAW_TIER_ELITE_MIN", 100_000.0)
    pro_min = _env_float("RCLAW_TIER_PRO_MIN", 10_000.0)
    if balance >= elite_min:
        return "elite"
    if balance >= pro_min:
        return "pro"
    return "basic"


def weight_enabled() -> bool:
    """Whether the tier WEIGHT (docs/TIER_MODEL.md) decides tiers.

    Off by default and separate from ``TOKEN_TIER_GATE_ENABLED`` on purpose.
    TIER_MODEL.md §11 asks for the weight to be computed and inspected before it
    is allowed to rank anyone, and a flag that arrived switched on would have
    skipped exactly that step.
    """
    return _env_bool("RCLAW_TIER_WEIGHT_ENABLED")


def tier_for_stake(balance: float, *, lock_seconds_remaining: float = 0.0,
                   standing=None) -> str:
    """Tier for a stake, honouring the weight model when it is enabled.

    **With a neutral lock and neutral standing this returns exactly what
    ``tier_for_balance`` returns**, and not by coincidence: the bands are
    converted with ``√`` too, and ``√`` is monotonic, so ``√x ≥ √t`` is the same
    predicate as ``x ≥ t``. That identity is what makes ``RCLAW_TIER_WEIGHT_ENABLED``
    safe to switch on — it is also why ``√`` alone fixes nothing, which
    TIER_MODEL.md §8 originally got backwards. Only a lock or a standing score
    moves a verdict, and only relative bands (Phase D) address plutocracy.

    An unreadable weight falls back to the linear tier rather than inventing
    one. That is the conservative direction here: the fallback is the behaviour
    the gate has today, not a promotion.
    """
    if not weight_enabled():
        return tier_for_balance(balance)
    elite_min = _env_float("RCLAW_TIER_ELITE_MIN", 100_000.0)
    pro_min = _env_float("RCLAW_TIER_PRO_MIN", 10_000.0)
    tier = tier_weight_mod.tier_for_weight(
        tier_weight_mod.tier_weight(
            balance, lock_seconds_remaining=lock_seconds_remaining, standing=standing),
        tier_weight_mod.weight_of_threshold(pro_min),
        tier_weight_mod.weight_of_threshold(elite_min),
    )
    if tier is None:
        return tier_for_balance(balance)
    return tier


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

# The lock remainder that came with the cached reading, so an outage does not
# silently strip a locked staker's commitment premium and demote them. Written
# and expired in lockstep with _BALANCE_CACHE; absent means "no lock", which is
# the un-premiumed direction.
_LOCK_CACHE: dict[str, float] = {}


def _grace_seconds() -> float:
    """How long a successful stake reading stays usable during an outage.

    Long enough to ride out a genuine blip, short enough that an induced outage
    is not a standing bypass. Env-tunable because the right value depends on how
    reliable the operator's RPC actually is.
    """
    return _env_float("RCLAW_TIER_GRACE_SECONDS", 900.0)


def _remember_balance(wallet: str, bal: float, lock_seconds_remaining: float = 0.0) -> None:
    _BALANCE_CACHE[wallet] = (float(bal), time.time())
    _LOCK_CACHE[wallet] = float(lock_seconds_remaining or 0.0)


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
        _LOCK_CACHE.pop(wallet, None)
        return None
    return bal


def _cached_lock(wallet: str) -> float:
    """Lock remainder recorded with the cached reading; 0.0 if there is none.

    Read only AFTER ``_cached_balance`` has confirmed freshness — it is that
    call which expires the pair, so asking here first would serve a lock whose
    balance has already been dropped.
    """
    return _LOCK_CACHE.get(wallet, 0.0)


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


#: SKILL name -> the FEATURE it is sold as, for the one skill where they
#: differ. `FEATURE_MIN_TIER` is keyed by FEATURE; eight of its nine keys are
#: also the name of the skill that runs them, and the ninth is not:
#: `premium_scan` is run by the skill `pro_scan`.
#:
#: A convention that holds eight times in nine is exactly why nobody checks
#: the ninth — the `/setllm` ten-of-eleven shape. This map used to exist ONLY
#: in `tests/test_tier_gate_coverage.py`, where production could not read it,
#: so a caller passing the SKILL name got `FEATURE_MIN_TIER.get("pro_scan")
#: -> None`, which `check_user` treats as UNGATED. Driven with the gate on and
#: no wallet linked: every other gated skill answered `(False, 'no_wallet')`
#: and `pro_scan` answered `(True, 'ok')`.
#:
#: Telegram never had the bug because `_token_gate_blocks` passes the FEATURE
#: explicitly (`"deepscan" if _deep else "premium_scan"`). The web passes the
#: SKILL, so it needs this reading, and so does anything else that gates by
#: what it is about to DISPATCH.
_SKILL_FEATURE: dict[str, str] = {"pro_scan": "premium_scan"}


def feature_for(skill: str) -> str:
    """The feature `skill` is sold as — itself, unless the two names differ.

    Call this whenever the thing you hold is a SKILL name and the thing
    `check_user` wants is a FEATURE. Answering `skill` unchanged is right for
    the eight that share a name and is the reason the ninth went unnoticed;
    the map above is the whole difference, in one place both surfaces read.
    """
    return _SKILL_FEATURE.get(skill, skill)


def granted_tier(users, uid) -> tuple[Optional[str], bool]:
    """The tier the operator granted ``uid`` (``/set_tier``), and whether the
    store could say.

    ``(tier, True)`` is a reading: the stored word, which is Basic for anyone
    never granted anything. ``(None, True)`` is a store that keeps no grants at
    all (a wallet-only stand-in). ``(None, False)`` is a store that raised:
    UNREADABLE, which is not Basic, because Basic is the answer that refuses a
    member who paid.
    """
    getter = getattr(users, "get_tier", None)
    if getter is None:
        return None, True
    try:
        tier = getter(uid)
    except Exception as exc:  # noqa: BLE001
        system_log.warning("tier_gate: could not read %s's granted tier (%s)",
                           uid, type(exc).__name__)
        return None, False
    return (str(tier).strip().lower() if tier else None), True


def stake_tier(users, uid) -> tuple[Optional[str], str]:
    """The tier ``uid``'s stake earns, or ``None`` and the reason there is none.

    Reasons: ``ok``, ``no_wallet``, ``unverified``, ``bad_wallet``,
    ``misconfigured``, ``unavailable``. Asked only while the token gate is on
    (``gate_enabled``): it reads the chain.
    """
    wallet = _resolve_wallet(users, uid)
    if not wallet:
        return None, "no_wallet"
    # A stored address that is not a real pubkey makes the RPC return an error,
    # which `staked_of`/`balance_of` report as None — the same sentinel used for
    # a transient outage, which fails OPEN. Invalid input must never reach that
    # path: it is attacker-supplied, so treating it as an infra blip is an
    # authorization bypass, not resilience.
    if not _is_solana_pubkey(wallet):
        system_log.warning("tier_gate: %s's stored wallet is not a valid pubkey; it earns no tier", uid)
        return None, "bad_wallet"
    # An address nobody proved control of is just a string the user typed. Until
    # it carries a verified signature it must not confer a tier, or anyone can
    # paste a whale's address and inherit their access.
    if not wallet_verified(users, uid):
        system_log.info("tier_gate: %s's linked wallet is unverified; it earns no tier", uid)
        return None, "unverified"
    # Prefer on-chain STAKED balance when a staking program is configured;
    # otherwise fall back to raw wallet balance.
    lock_secs = 0.0
    try:
        if staking_program():
            if weight_enabled():
                # One read, both inputs. The lock remainders come off the same
                # accounts the amount does, so asking for them costs nothing —
                # and a second RPC round trip could disagree with the first.
                profile = staked_profile(wallet)
                bal, lock_secs = profile if profile is not None else (None, 0.0)
            else:
                bal = staked_of(wallet)
        else:
            _warn_wallet_balance_fallback()
            bal = balance_of(wallet)
    except GateMisconfigured as exc:
        system_log.error("tier_gate: enabled but misconfigured; denying access (%s)", exc)
        return None, "misconfigured"  # fail CLOSED on a permanent configuration fault
    if bal is not None:
        _remember_balance(wallet, bal, lock_secs)
        return tier_for_stake(bal, lock_seconds_remaining=lock_secs), "ok"

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
            "tier_gate: no stake tier for %s — RPC unavailable and no recent stake reading for this wallet", uid
        )
        return None, "unavailable"
    have = tier_for_stake(cached, lock_seconds_remaining=_cached_lock(wallet))
    system_log.warning(
        "tier_gate: RPC unavailable — serving %s from cached stake reading (tier %s)", uid, have
    )
    return have, "ok"


def effective_tier(users, uid) -> Optional[str]:
    """The tier ``uid`` holds: the higher of the operator's grant and the
    stake's, the one reading every tier decision asks.

    The stake is read only while the token gate is on. A stored word this bot
    does not rank is no grant, so it counts as Basic (``/tier`` names it). None
    means the grant could not be read and no stake reading stands in for it:
    the caller says so rather than serving Basic.
    """
    tier = tier_reading(users, uid)["tier"]
    return None if tier is None else str(tier)


def check_user(users, uid, feature: str) -> tuple[bool, str]:
    """Whether ``uid`` may use ``feature``, and WHY if not.

    Returns ``(allowed, reason)``. The reason matters because "you have not
    staked enough" and "we could not check your stake" are different messages to
    show a user, and conflating them tells someone holding 100,000 $RCLAW to go
    and stake more during an RPC outage.

    Reasons: ``ok``, ``insufficient``, ``tier_unreadable``, and the stake's
    own (``no_wallet``, ``unverified``, ``bad_wallet``, ``misconfigured``,
    ``unavailable``).

    - Ladder not enforced    -> always True (see ``tiers_enforced``).
    - Granted tier suffices  -> True, without reading the chain.
    - No token gate          -> the grant is the whole reading: ``insufficient``,
                                or ``tier_unreadable`` when the store raised.
    - Token gate on          -> the stake decides what the grant did not:
      - No wallet linked     -> False (must link a wallet holding $RCLAW).
      - Wallet unverified    -> False (an unproven address carries no tier).
      - Misconfigured gate   -> False (fail CLOSED; permanent operator fault).
      - RPC/infra error      -> last known good tier if read recently, else False.
                                (bounded grace, not an open gate; see _grace_seconds)
      - Otherwise, the stake-derived tier must meet the feature's minimum.
    """
    if not tiers_enforced():
        return True, "ok"
    required = FEATURE_MIN_TIER.get(feature)
    if required is None:
        return True, "ok"  # ungated feature
    need = _TIER_RANK.get(required, 99)
    granted, readable = granted_tier(users, uid)
    if granted is not None and _TIER_RANK.get(granted, -1) >= need:
        return True, "ok"
    if not gate_enabled():
        return False, ("insufficient" if readable else "tier_unreadable")
    have, reason = stake_tier(users, uid)
    if have is None:
        return False, reason
    if _TIER_RANK.get(have, 0) >= need:
        return True, "ok"
    return False, ("insufficient" if readable else "tier_unreadable")


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


def upgrade_message(mode: str = "premium scan", feature: Optional[str] = None) -> str:
    """User-facing prompt shown when the gate blocks a paid capability.

    `mode` IS THE WHOLE NOUN PHRASE. It used to be a bare word with " scan"
    appended here, which is the THREE NOUNS defect half-fixed: `display` was
    separated from `feature` so the refusal could show a word the CALLER
    chooses, and the sentence went on appending a category the caller cannot
    choose. Driven over the eleven display words in the tree, six of them read
    false — "Backtest scan", "Walk-forward scan", "Learning scan", "Optimize
    scan", "Analysis scan", "Patterns scan" — on the sentence asking somebody
    to go and buy something. The capability this refuses is the caller's to
    name, and it is the only party that knows.

    THE TIER IS THE FEATURE'S. The sentence used to say "a staked-tier feature"
    and ask for the Pro stake whatever was refused, so a Basic member refused
    /backtest (an Elite feature) was told to stake 10,000 $RCLAW, which would
    have bought them nothing; and with the token gate off it asked for a stake
    the bot could not read. ``feature`` names the tier, and the route to it is
    the one this deployment has: a grant, and a stake only where one is read.
    """
    required = FEATURE_MIN_TIER.get(feature or "", "pro")
    name = TIER_NAMES.get(required, required.capitalize())
    article = "an" if name[:1].lower() in "aeiou" else "a"
    head = f"\U0001f512 <b>{mode.capitalize()} is {article} {name} feature.</b>\n"
    if not gate_enabled():
        return head + (
            "Tiers are granted by the operator. <code>/tier</code> shows yours and "
            "what each one includes."
        )
    stake_min = int(_env_float(
        "RCLAW_TIER_ELITE_MIN" if required == "elite" else "RCLAW_TIER_PRO_MIN",
        100_000.0 if required == "elite" else 10_000.0))
    return head + (
        f"Your tier is the higher of the one the operator grants and the one your "
        f"stake earns. {name} takes a stake of at least <b>{stake_min:,} $RCLAW</b>; "
        f"link and verify your wallet:\n"
        f"<code>/linkwallet &lt;address&gt;</code> → sign the message → "
        f"<code>/linkwallet verify &lt;signature&gt;</code>\n"
        f"<i>(Draft feature — $RCLAW is a gated Vision item.)</i>"
    )


def tier_unreadable_message() -> str:
    """Shown when the GRANTED tier could not be read: not "upgrade", which is
    the answer that sells a paying member what they already have."""
    return (
        "\U000023f3 <b>Can't read your tier right now.</b>\n"
        "The bot could not read its member records, so it could not confirm what "
        "your tier includes. This is on our side, not yours. Please try again in "
        "a minute."
    )


def refusal_message(reason: str, mode: str, feature: Optional[str] = None) -> str:
    """The sentence for one refusal: a different fix for each reason.

    The three dispatch doors each chose between two messages inline, and a
    third reason would have been added to one of them.
    """
    if reason == "unavailable":
        return unavailable_message()
    if reason == "tier_unreadable":
        return tier_unreadable_message()
    return upgrade_message(mode, feature)
