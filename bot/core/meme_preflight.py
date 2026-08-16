"""The one meme buy-preflight, shared by `/memeplan` and the web gateway.

This exists because it was about to be written twice. `_cmd_memeplan` had the
whole sequence inline — gather the venue read, derive the pool's age, ask the
user's Authority Envelope, call `meme_executor.plan_swap` — and the gateway
needed exactly that before it could build a transaction. Two copies of a
fail-closed gate is one copy that stops being fail-closed without anybody
noticing, because the surface nobody is looking at is the one that drifts.

WHAT FAIL-CLOSED MEANS AT EACH STEP, since every one of them can be unreadable:

  the venue read   A source that 503s yields no features, and no features means
                   no liquidity, no age and no buy/sell counts. Those absences
                   travel to the gate as None, and the gate refuses on them.
                   They are NOT flattened to zero — `0 liquidity` and `unknown
                   liquidity` are different facts, and only one of them is a
                   measurement.
  the pool's age   Derived from the pair's creation stamp, and left as None
                   when the venue did not report one. An undateable pool is the
                   textbook rug shape, so unknown must not become "old enough".
  the envelope     An envelope that cannot be read is not an authorizing one.
                   The `except` below returns False rather than propagating,
                   and that is the whole of the decision.

`allowed` is never invented here: this module gathers inputs and hands them to
`meme_executor.plan_swap`, which owns the verdict. It also never executes —
`would_execute` is a hardcoded False one level down, and `meme_swap.build_swap`
refuses any plan claiming otherwise.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: How long to wait on the venue read before treating the market as unreadable.
GATHER_TIMEOUT_S = 8.0

DEFAULT_SIZE_USD = 25.0


def age_hours(features: Optional[dict], now: Optional[Callable[[], float]] = None):
    """Hours since the pair was created, or None when it cannot be dated.

    None on purpose, and it is the important case: an undateable pool is the
    shape a rug takes, and a caller that substituted 0 would report the pool as
    brand new while a caller that substituted a large number would report it as
    seasoned. Both are inventions. The gate handles None by refusing.
    """
    created_ms = (features or {}).get("pair_created_at_ms")
    if not created_ms:
        return None
    try:
        created = float(created_ms) / 1000.0
    except (TypeError, ValueError):
        return None
    if created != created:                                    # NaN
        return None
    return max(0.0, ((now or time.time)() - created) / 3600.0)


def envelope_authorized(tg_id: Any) -> bool:
    """Is this user's Authority Envelope set to enforce? Unreadable is False.

    Isolated into a function so the "unreadable is not authorization" decision
    has one home and one test, rather than an `except: pass` in each caller.
    """
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        return bool(get_user_authority_store().is_enforcing(tg_id))
    except Exception as exc:                                      # noqa: BLE001
        logger.debug("authority envelope unreadable for %s: %s", tg_id, exc)
        return False


async def preflight(mint: str, size_usd: float = DEFAULT_SIZE_USD, *,
                    tg_id: Any = None, side: str = "buy",
                    sources: Optional[list] = None,
                    authorized: Optional[bool] = None,
                    now: Optional[Callable[[], float]] = None) -> dict:
    """Gather the market, ask the envelope, and return `plan_swap`'s verdict.

    Returns the plan dict from `meme_executor.plan_swap`, with two extra keys
    the callers need and cannot recompute later:

      market       what was actually read, so a surface can show the figures
                   the verdict was based on rather than re-fetching different
                   ones and quietly disagreeing with itself.
      created_at   when this verdict was formed. `meme_swap.build_swap` refuses
                   a plan older than MAX_PLAN_AGE_S, and it can only do that if
                   somebody wrote the timestamp down.
    """
    from bot.core import meme_executor
    from bot.core.token_safety import assess_token
    from bot.core.token_sources import DexScreenerSource, gather

    clock = now or time.time
    srcs = sources if sources is not None else [DexScreenerSource()]
    g = await gather(srcs, "solana", mint, timeout=GATHER_TIMEOUT_S)
    feats = (g or {}).get("features") or {}

    market = {
        "liquidity_usd": feats.get("liquidity_usd"),
        "age_hours": age_hours(feats, now=clock),
        "buys_24h": feats.get("buys_24h"),
        "sells_24h": feats.get("sells_24h"),
    }
    auth = envelope_authorized(tg_id) if authorized is None else bool(authorized)

    plan = meme_executor.plan_swap(
        intent={"side": side, "token_mint": mint, "size_usd": size_usd},
        safety_report=assess_token(feats),
        market=market,
        envelope_authorized=auth)
    plan["market"] = market
    plan["created_at"] = clock()
    return plan
