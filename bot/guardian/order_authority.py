"""The Authority Envelope asked about ONE order, from every door.

A person binds an envelope on the website -- "only majors, max $500 a trade,
$2,000 a day, only on bitget" -- and three surfaces say what it does: the
dashboard's intent panel ("caps and authorizes every live order"), the
readiness panel ("Enforce-mode envelope is authorizing every order") and
`docs/authority_nl.md`. It was asked at ONE door: the web confirm
(`bot/web/user_gateway.py`). Driven with per-user live on and an enforce
envelope bound (BTC only, $50 a trade, $100 a day), a $500 SOL ticket
confirmed on TELEGRAM executed on the person's own account and the 24h ledger
recorded nothing -- the symbol list, both caps and the day's record all
skipped, and every web order after it checked against a day the Telegram
orders were missing from.

So the ask is a LEAF both doors call, and the engine asks it right before
`executor.execute` for every human confirm that runs on a person's own
account under an enforce-mode envelope -- the `_fmt_price(None)` rule: guard
at the boundary, and the door added tomorrow inherits it. The web door keeps
its own earlier ask (its 403 carries the reasons and a checklist); an order
asked about twice on one ref is recorded ONCE (the ledger dedupes by ref) and
the second ask reads the day WITHOUT its own first recording
(`spent(excluding_ref=...)`), so a web order is never denied at the engine by
the notional the web door had just recorded for it.

What is deliberately NOT here, stated: a person with NO envelope bound, or one
in a non-enforce mode, is not asked -- Telegram per-user live never required
an envelope, and requiring one is a product decision about existing per-user
traders, not a wiring line. The operator's shared executor is never asked:
the envelope is a person's grant over THEIR account, and identity decides
(an operator who linked their own keys trades on their own executor, and an
enforce envelope bound for that id is asked like anyone's).
"""
from __future__ import annotations

import time
from typing import Any, NamedTuple, Optional

from bot.utils.logger import audit, system_log

NOT_PENDING = "the proposed trade is no longer pending"
NO_ENVELOPE = "no Authority Envelope is bound"
STAMP_FAILED = "the authorized notional could not be recorded on the order"
CHECK_FAILED = "authorization check failed"
LEVERAGE_UNREAD = "the leverage this order would run at could not be read"


class OrderAuthorization(NamedTuple):
    """What the envelope answered, and whether THIS call recorded a spend.

    ``recorded`` is True only when this call added the trade's notional to
    the 24h ledger -- an allow with a notional the ledger had not seen. A
    deny, an auto-sized order with no notional, and a trade id the ledger
    already held (an earlier attempt's row, or the web door's own ask) all
    answer False. It is the one fact a door needs to RELEASE a refused
    order's spend, and only the recorder can state it: a door asking the
    ledger afterwards whether the ref is held would read an earlier attempt's
    row as this one."""
    ok: bool
    reasons: list
    recorded: bool
    notional: Optional[float] = None


def placement_leverage(executor: Any, idea: Any) -> tuple[Optional[int], Optional[str]]:
    """The leverage THIS order would be placed at on ``executor``, or why not.

    `_compute_target_leverage` is the one number the venue is set to: the
    operator standard (the `/leverage` override under the MAX_LEVERAGE
    ceiling), lowered by the user's own preference and the idea's
    margin-risk cap. Every failure is a reason and never a number: an
    envelope checked at a leverage nobody read is the defect the web door
    was cured of, and the class of the exception is all that travels."""
    try:
        lev = int(executor._compute_target_leverage(str(getattr(idea, "asset", "")), idea))
    except Exception as exc:
        return None, f"{LEVERAGE_UNREAD} ({type(exc).__name__})"
    if lev < 1:
        return None, LEVERAGE_UNREAD
    return lev, None


def spend_ledger():
    """The ONE 24h ledger every authority reader shares."""
    from bot.guardian.authority_ledger import user_spend_ledger
    return user_spend_ledger()


def authorize_order(user_id: str, trade_id: str, idea: Any, *, margin: Any,
                    executor: Any, venue: str, ledger: Any = None,
                    now: Optional[float] = None) -> OrderAuthorization:
    """Ask the user's bound Authority Envelope about ONE order. FAIL-CLOSED.

    ``margin`` is the margin the order is sized at (the typed ticket at the
    web door; the FINAL size at the engine, after every cap and clamp) and
    the notional is margin x the leverage ``executor`` places at. On allow
    the idea is stamped with the authorized notional (the venue-minimum
    round-up bounds itself against it) and the notional is recorded against
    the day, once per trade id. An order with no margin has an unknown
    notional, which the envelope denies under any cap. A stamp that cannot
    be written is a denial: an order the executor cannot bound is not an
    order the envelope authorized."""
    try:
        from bot.guardian.authority import authorize
        from bot.guardian.user_authority_store import get_user_authority_store
        env = get_user_authority_store().get(user_id)
        if not env:
            return OrderAuthorization(False, [NO_ENVELOPE], False)
        if idea is None:
            return OrderAuthorization(False, [NOT_PENDING], False)
        asset = str(getattr(idea, "asset", "")).split("/")[0]
        notional = None
        if margin is not None:
            lev, why = placement_leverage(executor, idea)
            if lev is None:
                return OrderAuthorization(False, [why or LEVERAGE_UNREAD], False)
            try:
                notional = float(margin) * float(lev)
            except (TypeError, ValueError):
                notional = None
        action = {"kind": "trade", "venue": str(venue or ""), "market_type": "swap",
                  "asset": asset, "notional_usd": notional}
        now_ts = time.time() if now is None else float(now)
        ledger = ledger if ledger is not None else spend_ledger()
        # The day WITHOUT this order's own earlier recording: the web door
        # asks before the engine does, and the engine's ask must not read the
        # web door's row as spend already made.
        spent = ledger.spent(user_id, now_ts, excluding_ref=trade_id)
        result = authorize(env, action, now_ts=now_ts, spent_today_usd=spent)
        if result.get("decision") != "allow":
            return OrderAuthorization(
                False, list(result.get("reasons") or ["not authorized"]), False, notional)
        recorded = False
        if notional:
            try:
                setattr(idea, "authorized_notional_usd", float(notional))
            except Exception as exc:
                system_log.warning("Authority: could not stamp the order for %s: %s",
                                   user_id, type(exc).__name__)
                return OrderAuthorization(False, [STAMP_FAILED], False, notional)
            # True only for a row this call ADDED: a duplicate ref (an earlier
            # attempt's row, or the web door's own ask) is the ledger's False.
            recorded = bool(ledger.record(user_id, notional, now_ts, ref=trade_id))
        return OrderAuthorization(True, [], recorded, notional)
    except Exception as exc:
        # The CLASS and never the text: a store's exception names a path, a
        # venue's echoes the request.
        system_log.warning("Authority authorization error for %s: %s", user_id, type(exc).__name__)
        return OrderAuthorization(False, [CHECK_FAILED], False)


def release_order_spend(user_id: str, trade_id: str, ledger: Any = None,
                        now: Optional[float] = None) -> None:
    """Take a REFUSED order's notional back off the 24h ledger.

    Called only when THIS attempt recorded the spend and the order was then
    refused before anything was placed: the order never happened, so the
    day's cap must not count it. Never for an UNVERIFIED outcome: the venue
    may hold that order, and a spend released for an order that filled is
    the loose direction. Every outcome is audited, and a release that could
    not land is said as KEPT with the exception's class -- the spend then
    counts for the rest of the window, which errs strict."""
    now_ts = time.time() if now is None else float(now)
    try:
        ledger = ledger if ledger is not None else spend_ledger()
        released = ledger.release(user_id, trade_id, now_ts)
    except Exception as exc:
        system_log.warning(
            "Authority spend for refused %s could not be released (%s); it stays "
            "counted against the 24h cap", trade_id, type(exc).__name__)
        audit(system_log, f"Web-live spend KEPT after refusal: {trade_id}",
              action="web_live_spend", result="KEPT",
              data={"user": user_id, "error": type(exc).__name__})
        return
    audit(system_log,
          f"Web-live spend {'RELEASED' if released else 'NOT_HELD'} after refusal: {trade_id}",
          action="web_live_spend", result="RELEASED" if released else "NOT_HELD",
          data={"user": user_id})
