"""Pro and Elite time, paid in Telegram Stars. Off unless the operator sets it up.

The one payment path on the main bot (docs/plans/REVENUE_REVIEW_2026-10.md,
R-3). What is sold is compute: the tiers of ``tier_gate.plan_ladder``, for a
fixed number of days, never calls, live access or limits (``NEVER_GATED``).

    STARS_PAYMENTS_ENABLED=false   # master switch, off by default
    STARS_PRO_PRICE=               # whole Stars for one period of Pro; empty => not sold
    STARS_ELITE_PRICE=             # the same for Elite
    STARS_PERIOD_DAYS=30           # length of one period

A tier is offered only while it adds something on THIS deployment
(`benefits`): selling a period that changes nothing is the defect the revenue
review found in every upgrade prompt. Digital goods in a Telegram bot are paid
in Stars (currency ``XTR``) with an empty provider token.
"""

from __future__ import annotations

import os
from typing import Optional

from bot.utils.logger import system_log

CURRENCY = "XTR"
SOLD = ("pro", "elite")
_PAYLOAD_TAG = "rc1"
_DEFAULT_DAYS = 30


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _positive_int(name: str) -> Optional[int]:
    raw = _env(name)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        system_log.warning("stars: %s=%r is not a whole number; that tier is not sold", name, raw)
        return None
    if value <= 0:
        system_log.warning("stars: %s=%r is not above zero; that tier is not sold", name, raw)
        return None
    return value


def period_days() -> int:
    return _positive_int("STARS_PERIOD_DAYS") or _DEFAULT_DAYS


def price(tier: str) -> Optional[int]:
    """Stars for one period of ``tier``, or None when it is not for sale."""
    if tier not in SOLD:
        return None
    return _positive_int(f"STARS_{tier.upper()}_PRICE")


def payments_on() -> bool:
    """Whether /upgrade sells anything: the switch is on and a price is set."""
    on = _env("STARS_PAYMENTS_ENABLED").lower() in ("1", "true", "yes", "on")
    return on and any(price(t) is not None for t in SOLD)


def benefits(ladder: dict, tier: str) -> list[str]:
    """What ``tier`` adds over Basic on this deployment, read from the ladder.

    Each line comes from the enforcer the ladder read: the chat cap, the
    operator-funded /analyze model, and the commands on the ladder, which
    count only while tiers are enforced (otherwise every member already has
    them). An empty list means the tier buys nothing here.
    """
    rows = {str(r.get("tier")): r for r in ladder.get("tiers", [])}
    basic, row = rows.get("basic"), rows.get(tier)
    if basic is None or row is None:
        return []
    out: list[str] = []
    if basic.get("chat_per_day") is not None and row.get("chat_per_day") is None:
        out.append(f"no daily cap on AI questions (Basic: {int(basic['chat_per_day'])} a day)")
    if row.get("premium_analyze") is True and basic.get("premium_analyze") is not True:
        out.append("the operator-funded model on /analyze")
    if ladder.get("enforced"):
        order = [str(r.get("tier")) for r in ladder.get("tiers", [])]
        upto = order[: order.index(tier) + 1] if tier in order else []
        cmds = [c for t in upto if t != "basic"
                for u in rows[t].get("unlocks", []) for c in u.get("commands", [])]
        if cmds:
            out.append(" ".join("/" + c for c in sorted(set(cmds))))
    return out


def offers(ladder: dict) -> list[dict]:
    """The tiers on sale right now: priced, and adding something."""
    if not payments_on():
        return []
    out = []
    for tier in SOLD:
        stars = price(tier)
        adds = benefits(ladder, tier)
        if stars is not None and adds:
            out.append({"tier": tier, "stars": stars, "days": period_days(),
                        "benefits": adds})
    return out


def payload(uid: str, tier: str, days: int, stars: int) -> str:
    """The invoice payload Telegram hands back at checkout (at most 128 bytes)."""
    return f"{_PAYLOAD_TAG}:{tier}:{int(days)}:{int(stars)}:{uid}"


def parse_payload(raw: str) -> Optional[dict]:
    parts = str(raw or "").split(":")
    if len(parts) != 5 or parts[0] != _PAYLOAD_TAG:
        return None
    _, tier, days, stars, uid = parts
    try:
        return {"tier": tier, "days": int(days), "stars": int(stars), "uid": uid}
    except ValueError:
        return None


def check_checkout(raw_payload: str, from_uid: str, currency: str, total: int,
                   ladder: dict, held: Optional[str], until: Optional[float],
                   now: float) -> Optional[str]:
    """Why this checkout must be refused, or None to accept it.

    Asked at Telegram's pre-checkout step, the last moment a refusal costs
    the buyer nothing. The invoice must be one this bot would issue now: same
    buyer, same tier, period and price as today's offer, in Stars, and a tier
    that would change what the buyer holds.
    """
    p = parse_payload(raw_payload)
    if p is None:
        return "This invoice is not one this bot issued."
    if p["uid"] != str(from_uid):
        return "This invoice was issued to someone else. Send /upgrade for your own."
    if currency != CURRENCY:
        return "Only Telegram Stars are accepted."
    live = {o["tier"]: o for o in offers(ladder)}
    offer = live.get(p["tier"])
    if offer is None:
        return "That plan is not on sale any more. Send /upgrade to see what is."
    if (p["days"], p["stars"]) != (offer["days"], offer["stars"]) or int(total) != offer["stars"]:
        return "The price changed since this invoice was issued. Send /upgrade for a new one."
    from bot.utils.user_store import paid_tier_buys_nothing
    if paid_tier_buys_nothing(held, until, p["tier"], now):
        return "You already have this plan or a higher one, so this would buy nothing."
    return None
