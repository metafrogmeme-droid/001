"""The /venues card: what a user chose, and whether it is being acted on.

A PURE renderer, extracted before it was ever written inline, because the
inline version is the one this repository keeps paying for — a card built in a
handler, source-scanned, shipped, and rendering something nobody could read
back (#999, where a per-position SL/TP outcome rendered zero times in
production while every test passed).

THE MOST IMPORTANT LINE ON THIS CARD IS THE ONE ABOUT THE FLAG.

A user can select two venues while ``MULTI_VENUE_TRADING_ENABLED`` is off or in
shadow. The selection saves, the card could plausibly show two venues with
ticks beside them, and the user would believe their book is spread across both
while every order still goes to one. That is not a cosmetic gap: it is somebody
sizing their risk against a diversification they do not have. So the mode is
stated first, in the user's own terms, and the venue list is never rendered as
though it were in force when it is not.

The second is the one about a venue that stopped being connected. It is shown
as a PROBLEM rather than dropped from the list — a shorter list reads as "I
deselected that", and the difference between "you turned this off" and "your
keys stopped working" is the whole reason `raw_selection` and `active_venues`
are separate reads.
"""
from __future__ import annotations

from typing import Optional


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def venue_card(*, connected, selected, dropped, mode: str,
               enforce_available: bool = True,
               positions: Optional[dict] = None) -> str:
    """The /venues card as HTML for Telegram.

    ``connected``  venues with usable credentials
    ``selected``   what the user chose (RAW — including any now disconnected)
    ``dropped``    selected but no longer connected
    ``mode``       off | shadow | enforce
    ``positions``  optional ``{venue: open_count}``; a venue with open
                   positions cannot be deselected, so saying so here saves the
                   user discovering it from a refusal.
    """
    conn = [str(v).lower() for v in (connected or [])]
    sel = [str(v).lower() for v in (selected or [])]
    gone = {str(v).lower() for v in (dropped or [])}
    live = [v for v in sel if v not in gone]
    pos = {str(k).lower(): int(v or 0) for k, v in (positions or {}).items()}

    lines = ["\U0001f3e6 <b>YOUR TRADING VENUES</b>", "────────────────"]

    # ── What is actually in force. FIRST, always. ────────────────────────
    if not sel:
        lines.append("You have not chosen any venues, so trading uses your "
                     "<b>single connected venue</b> — the default.")
    elif mode == "off":
        # The dangerous case. The selection exists and is doing nothing.
        lines.append(
            f"⚠️ You have selected <b>{len(live)}</b> venue(s), but "
            "multi-venue trading is <b>OFF</b> on this deployment — every order "
            "still goes to your single default venue. Your book is <b>not</b> "
            "spread across them.")
    elif mode == "shadow":
        lines.append(
            f"\U0001f50d Multi-venue is in <b>SHADOW</b>: the bot records which "
            f"of your {len(live)} venue(s) each order WOULD go to, and still "
            "sends it to your single default venue. Nothing is spread yet.")
    elif mode == "enforce" and not enforce_available:
        lines.append("⚠️ Enforce was requested but order routing is "
                     "not available on this build — running as shadow. Nothing "
                     "is spread across venues.")
    else:
        lines.append(
            f"✅ Orders are routed across your <b>{len(live)}</b> selected "
            "venue(s). Each trade goes to ONE of them — the one with the most "
            "free margin — never to all of them.")

    # ── The venues themselves ────────────────────────────────────────────
    lines.append("")
    if conn or sel:
        lines.append("<b>Connected</b>")
        for v in sorted(set(conn)):
            mark = "●" if v in live else "○"
            tail = " · <i>trading</i>" if v in live else " · not selected"
            n = pos.get(v, 0)
            if n:
                tail += f" · {n} open"
            lines.append(f"  {mark} <code>{_esc(v)}</code>{tail}")
        if not conn:
            lines.append("  <i>none</i>")
    else:
        lines.append("<i>No venues connected yet — /connect to link one.</i>")

    # ── A selected venue that stopped working is a PROBLEM, not an absence ─
    if gone:
        lines.append("")
        lines.append("\U0001f534 <b>Selected but not connected</b>")
        for v in sorted(gone):
            lines.append(f"  ✗ <code>{_esc(v)}</code> — its keys are "
                         "missing or unreadable, so nothing is routed there")
        lines.append("  <i>Re-/connect it, or deselect it with /venues.</i>")

    lines.append("────────────────")
    lines.append("\U0001f449 <code>/venues bitget bybit</code> — choose which "
                 "venues trade")
    lines.append("\U0001f449 <code>/venues none</code> — back to a single venue")
    return "\n".join(lines)
