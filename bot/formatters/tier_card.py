"""`/tier`: a member's tier, where it came from, and what each tier buys here.

Every line is read from what decides it (`bot.token.tier_gate.tier_reading`
and `plan_ladder`), so the card cannot list a benefit nothing grants. The
website's plan card renders the same two readings (`app/public/js/dashboard.js`,
through the gateway's `/tier`).
"""
from __future__ import annotations

import html

#: The marker beside each tier's name: the same one `UserStore.tier_label`
#: prints, so a tier looks the same on /set_tier's reply and here.
_TIER_ICON = {"basic": "\U0001f7e2", "pro": "\U0001f535", "elite": "\U0001f7e1",
              "admin": "\U0001f534"}

#: Why a stake earns no tier, by `tier_gate.stake_tier`'s reason.
_STAKE_WHY = {
    "no_wallet": "no wallet linked",
    "unverified": "your linked wallet is not verified yet",
    "bad_wallet": "the linked wallet is not a valid address",
    "misconfigured": "the stake reader is misconfigured on this bot",
    "unavailable": "the chain could not be read just now",
}


def _name(ladder: dict, tier: str) -> str:
    for row in ladder.get("tiers", []):
        if row.get("tier") == tier:
            return str(row.get("name") or tier)
    from bot.token.tier_gate import TIER_NAMES
    return TIER_NAMES.get(tier, tier)


def _commands(row: dict) -> str:
    out: list[str] = []
    for u in row.get("unlocks", []):
        cmds = u.get("commands") or []
        if cmds:
            out.extend(f"/{html.escape(c)}" for c in cmds)
        else:
            out.append(html.escape(str(u.get("feature", ""))))
    return " ".join(out)


def _chat_line(row: dict) -> str:
    n = row.get("chat_per_day")
    if n is None:
        return "chat questions: no daily cap"
    return f"chat questions: {int(n)} a day"


def render_tier_card(reading: dict, ladder: dict) -> str:
    """The `/tier` card for one member."""
    lines = []
    tier = reading.get("tier")
    granted = reading.get("granted")
    if tier is None:
        lines.append("\U000023f3 <b>Your tier could not be read just now.</b>")
        lines.append("The bot could not read its member records. This is on our "
                     "side; please try again in a minute.")
    else:
        lines.append(f"{_TIER_ICON.get(tier, '')} <b>Your tier: "
                     f"{html.escape(_name(ladder, tier))}</b>".strip())
        if granted and not reading.get("granted_known"):
            lines.append(f"Your stored tier <code>{html.escape(str(granted))}</code> "
                         "is not one this bot knows, so it counts as Basic. Ask the "
                         "operator to set it again.")
        elif granted and granted != "basic":
            lines.append(f"Granted by the operator: {html.escape(_name(ladder, granted))}.")
    if ladder.get("stake"):
        staked = reading.get("stake")
        if staked is None:
            why = _STAKE_WHY.get(str(reading.get("stake_reason")),
                                 "it could not be read")
            lines.append(f"Your stake earns no tier: {html.escape(why)}.")
        else:
            lines.append(f"Your stake earns: {html.escape(_name(ladder, staked))}.")
        lines.append("<i>Your tier is the higher of the operator's grant and "
                     "your stake's.</i>")

    lines.append("")
    lines.append("<b>What each tier includes</b>")
    previous = None
    for row in ladder.get("tiers", []):
        name = html.escape(str(row.get("name") or row.get("tier")))
        parts = []
        if previous is not None:
            parts.append(f"everything in {html.escape(previous)}")
        cmds = _commands(row)
        if cmds:
            parts.append(cmds)
        elif previous is None:
            parts.append("every command not listed under a higher tier")
        parts.append(_chat_line(row))
        if row.get("premium_analyze"):
            parts.append("the operator-funded model on /analyze")
        lines.append(f"{_TIER_ICON.get(str(row.get('tier')), '')} <b>{name}</b>: "
                     + "; ".join(parts))
        previous = str(row.get("name") or row.get("tier"))

    lines.append("")
    if not ladder.get("enforced"):
        lines.append("ℹ️ Tiers are not enforced on this bot yet: every "
                     "member can use every command above.")
    lines.append("Never tier-gated: /halt, your own positions and risk, why a "
                 "trade was refused, and live trading limits.")
    route = "Tiers are granted by the operator"
    stake_min = ladder.get("stake_min")
    if ladder.get("stake") and stake_min:
        route += (f", or earned by a stake of {int(stake_min['pro']):,} $RCLAW "
                  f"(Pro) or {int(stake_min['elite']):,} (Elite)")
    lines.append(route + ".")
    return "\n".join(lines)
