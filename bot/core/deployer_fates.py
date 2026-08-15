"""How a deployer's previous contracts ENDED — the column nothing could fill.

`deployer_history` has always read `prior_rugged`, and no source could supply
it: an explorer says who deployed what and when, never what became of it. So
every dossier stopped at UNPROVEN with the deployer's record unread, and the
module's own docstring recorded the reason a partial answer was refused.

WHAT A PRICE FEED CAN AND CANNOT PROVE

It can prove a market exists: a pool with real depth and trades in the last day
is a token that is still alive, and that is a fact.

It can prove a market is gone: a pool that has existed for months and now holds
almost nothing is a token whose market ended, and that is also a fact.

It cannot prove a RUG. "The liquidity left" and "somebody pulled the liquidity"
are the same reading; the difference is intent, and no price feed carries it.
Projects die honestly all the time — the team runs out of money, nobody keeps
buying, the pool drains to dust. That is a failure and it is not a theft.

So this module never writes `prior_rugged`. It writes `prior_dead`, a third
outcome, which `deployer_history` scores as a SOFT ratio with no hard threshold.
Feeding a heuristic into `prior_rugged` would hard-fail an honest builder on the
strength of the word "confirmed" — the single most damaging thing this codebase
could output about a person, manufactured from a liquidity number.

    A MARKET THAT ENDED IS NOT A MARKET THAT WAS STOLEN.

WHY "NO PAIR AT ALL" IS NOT DEAD

The tempting shortcut is that a contract DexScreener has never heard of must be
worthless. It is not: a deployer's history is full of contracts that were never
tokens — proxies, multisigs, NFT collections, registries, factories — plus
tokens that only ever traded on a centralised venue. None of those has a DEX
pair, and none of them died. An unindexed contract is UNRESOLVED, which costs a
deployer nothing except the coverage that would have let them be called clean.

The same applies, harder, to a failed request. An unreadable fate is not a bad
one, and "dead" is the damaging direction — so every error, timeout and missing
field lands in `unresolved`.

THE AGE GUARD

A pool with no liquidity that was created an hour ago is a token that has not
launched yet, not one that has ended. Nothing is called dead without a
`pairCreatedAt` old enough to have had a life, so a missing timestamp — like
every other missing field here — resolves to unknown rather than to the
conclusion that happens to be convenient.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

#: A pool this deep, still trading, is a live market. Deliberately well above
#: dust: the question is not "does a pair row exist" but "could someone sell".
ALIVE_LIQUIDITY_USD = 10_000.0

#: Below this, on a pool old enough to have had a life, the market is gone.
#: The gap between the two thresholds is not indecision — it is the band where
#: neither statement is true, and both answers there are `unresolved`.
DEAD_LIQUIDITY_USD = 1_000.0

#: A pool younger than this has not had time to end. Without a creation date we
#: cannot apply this rule, and a fate we cannot date is one we do not assert.
DEAD_MIN_AGE_DAYS = 30.0

#: Contracts examined per deployer. A prolific deployer would otherwise cost
#: hundreds of requests on one command; the ones not examined stay unresolved,
#: which is the honest word for "we did not look" and costs the deployer only
#: the coverage they would have needed to be certified.
MAX_CONTRACTS = 25

#: Simultaneous lookups. DexScreener is generous but not unlimited, and a burst
#: of 25 is how a free tier starts answering 429 to everybody.
CONCURRENCY = 4

ALIVE, DEAD, UNRESOLVED = "alive", "dead", "unresolved"

_DAY_S = 86400.0


def _f(x: Any) -> Optional[float]:
    """float(x) or None — never 0.0 for a value that could not be read."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def classify(features: Optional[dict], now: float) -> tuple:
    """One contract's fate, and why. Returns `(fate, reason)`.

    Pure: the caller fetches, this decides. Every branch that cannot establish
    a fact returns UNRESOLVED, and none of them returns DEAD.
    """
    if not features:
        # `{}` from the source means "no indexed pair", which is a fact about
        # our visibility and about the contract's TYPE, not about its health.
        return UNRESOLVED, "no indexed market (may never have had one)"

    liq = _f(features.get("liquidity_usd"))
    if liq is None:
        return UNRESOLVED, "liquidity unreadable"

    if liq >= ALIVE_LIQUIDITY_USD:
        vol = _f(features.get("volume_24h_usd"))
        if vol is None:
            return UNRESOLVED, "deep pool but volume unreadable"
        if vol > 0:
            return ALIVE, f"${liq:,.0f} liquidity, trading"
        # Deep but frozen: real money is still exitable, so this is not a dead
        # market — but nobody is trading it, so it is not a demonstrated live
        # one either.
        return UNRESOLVED, "deep pool, no trades in 24h"

    if liq < DEAD_LIQUIDITY_USD:
        created_ms = _f(features.get("pair_created_at_ms"))
        if created_ms is None:
            return UNRESOLVED, "drained pool of unknown age"
        age_days = (now - created_ms / 1000.0) / _DAY_S
        if age_days >= DEAD_MIN_AGE_DAYS:
            return DEAD, f"${liq:,.0f} liquidity after {age_days:.0f}d"
        return UNRESOLVED, f"pool only {max(age_days, 0):.0f}d old"

    # The middle band: thin but not gone. Neither statement is true.
    return UNRESOLVED, f"${liq:,.0f} liquidity — neither alive nor ended"


async def resolve_fates(contracts: Optional[Sequence[str]], chain: str = "eth",
                        source: Any = None, timeout: float = 8.0,
                        now: Optional[Callable[[], float]] = None,
                        max_contracts: int = MAX_CONTRACTS) -> dict:
    """Fates for a deployer's prior contracts::

        {prior_alive, prior_dead, examined, unresolved, truncated, fates:[...]}

    `prior_alive` / `prior_dead` are what `assess_deployer` consumes.
    `prior_rugged` is deliberately absent and is not this module's to supply.
    """
    addresses = [a for a in (contracts or []) if isinstance(a, str) and a.strip()]
    out: dict = {"prior_alive": None, "prior_dead": None, "examined": 0,
                 "unresolved": 0, "truncated": False, "fates": []}
    if not addresses:
        return out

    if len(addresses) > max_contracts:
        # Named, not silent. A capped sweep reported as a whole one is the
        # defect this repo spends most of its guard tests preventing.
        out["truncated"] = True
        addresses = addresses[:max_contracts]

    if source is None:
        from bot.core.token_sources import DexScreenerSource
        source = DexScreenerSource()

    clock = now or time.time
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(addr: str) -> tuple:
        async with sem:
            try:
                features = await asyncio.wait_for(source.fetch(chain, addr), timeout)
            except Exception as exc:                              # noqa: BLE001
                # An unreadable fate is not a bad one. This branch is the whole
                # reason `dead` cannot be inferred from absence.
                logger.debug("fate lookup failed for %s: %s", addr, exc)
                return addr, UNRESOLVED, "lookup failed"
            return (addr,) + classify(features, clock())

    results = await asyncio.gather(*(one(a) for a in addresses))

    alive = dead = unresolved = 0
    for addr, fate, reason in results:
        out["fates"].append({"address": addr, "fate": fate, "reason": reason})
        if fate == ALIVE:
            alive += 1
        elif fate == DEAD:
            dead += 1
        else:
            unresolved += 1

    out["prior_alive"] = float(alive)
    out["prior_dead"] = float(dead)
    out["examined"] = len(addresses)
    out["unresolved"] = unresolved
    return out


def human_readable(fates: Optional[dict]) -> str:
    """One line per determined fate; the unresolved are counted, not listed."""
    if not fates or not fates.get("fates"):
        return ""
    lines = []
    for f in fates["fates"]:
        if f["fate"] == UNRESOLVED:
            continue
        mark = "+" if f["fate"] == ALIVE else "x"
        lines.append(f"     {mark} {f['address'][:10]}… {f['fate']} — {f['reason']}")
    unresolved = fates.get("unresolved") or 0
    if unresolved:
        # Counted every time, because the alternative is a list of survivors
        # that reads as the whole record.
        lines.append(f"     ? {unresolved} of {fates.get('examined', 0)} "
                     "could not be determined")
    if fates.get("truncated"):
        lines.append(f"     ! only the most recent {MAX_CONTRACTS} were examined")
    return "\n".join(lines)
