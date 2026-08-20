"""The /escape card, extracted so a test can plant a book and read the card.

WHY THIS FILE EXISTS. `_cmd_escape` built its card inline, and CLAUDE.md names
it as one of the surfaces still doing that: "the surfaces that still build
cards inline and make halt/breaker/stop-loss claims — `_status_lines`,
`_cmd_escape`, `_cmd_open_positions` — are where to look next."

Inline meant nothing could plant a crashed planner and assert what the operator
would read. Three defects lived behind that, all pointing the same way:

  1. A PLANNER CRASH READ AS A FLAT BOOK. `plan()` returned the same document
     for "no positions" and for "an exception happened", and this card rendered
     it as "🪂 no open positions to unwind" — a confident all-clear, shown to
     an operator who is looking at this screen precisely because something is
     wrong. `escape_agent` now separates the two with `ok`.

  2. UNKNOWN URGENCY RENDERED GREEN. The icon lookup was

         _RISK_ICON.get(report.get("risk", "none"), "⚪")

     The OUTER default is right — ⚪ for a risk word nobody recognises. The
     INNER one guarantees it can never be reached for the case that matters:
     an absent `risk` becomes the string "none", which IS in the map, so it
     comes out 🟢. A guard that works, applied to a set that excludes the case
     that hurts — the shape this repo keeps finding.

  3. AN EMERGENCY EXIT PLAN TRUNCATED IN SILENCE. `steps[:12]` with nothing
     saying so. An operator executes twelve closes believing the book is then
     flat. Twelve of twenty is not a plan, and a bounded list published without
     its total reads as the total.

Colour is a claim, and on this card the claim is "how urgent is it that you get
out". Unknown gets a muted one.
"""

from __future__ import annotations

import html
from typing import Optional

#: How many steps the card shows. Kept here rather than inline so the renderer
#: and its disclosure cannot disagree about the number.
MAX_STEPS = 12

_RISK_ICON = {"none": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴"}

#: What a missing measurement looks like. Never blank, never a zero.
UNKNOWN_ICON = "⚪"
UNKNOWN_WORD = "UNKNOWN"


def _usd(v: Optional[float]) -> str:
    """A dollar figure, or an em dash. `$0` is a measurement of nothing left."""
    if v is None:
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _count(v: Optional[int]) -> str:
    return "—" if v is None else str(v)


def risk_icon(risk: Optional[str]) -> str:
    """The urgency dot. Unknown is muted, never green.

    Both arms matter: `risk is None` (nothing could be assessed) and a word the
    map does not know. The second was already handled; the first was being
    converted into "none" before it ever got here.
    """
    if risk is None:
        return UNKNOWN_ICON
    return _RISK_ICON.get(str(risk), UNKNOWN_ICON)


def risk_word(risk: Optional[str]) -> str:
    return UNKNOWN_WORD if risk is None else str(risk).upper()


def render_escape_card(report: Optional[dict], *, sealed: bool = False) -> str:
    """The whole card, as HTML, from a plan document.

    `report` is `escape_agent.plan()`'s return value, or None when the engine
    could not produce one at all.
    """
    # ── could not plan ──────────────────────────────────────────────────────
    # None from the engine, or ok=False from the planner. Both mean the same
    # thing to a reader and must not mean "you are flat".
    if report is None or report.get("ok") is False:
        return (
            "🪂 <b>Escape plan</b> — ⚪ unwind urgency <b>UNKNOWN</b>\n\n"
            "<b>The escape plan could not be built.</b>\n"
            "<i>This says nothing about whether the book is flat. It is a "
            "failure to read the book, not a reading of an empty one — do not "
            "take it as an all-clear.</i>\n\n"
            "<i>Check <code>/status</code> and <code>/open_positions</code>. "
            "To flatten regardless: <code>/closeall</code>, or "
            "<code>/emergency_stop</code> to halt and flatten.</i>")

    steps = report.get("steps") or []

    # ── genuinely flat ──────────────────────────────────────────────────────
    if not steps:
        return ("🪂 <b>Escape Agent</b> — no open positions to unwind.\n\n"
                "<i>The escape plan orders the book by liquidation urgency so "
                "the most dangerous positions close first. Nothing to plan "
                "while flat.</i>")

    risk = report.get("risk")
    lines = [
        f"🪂 <b>Escape plan</b> — {risk_icon(risk)} unwind urgency "
        f"<b>{html.escape(risk_word(risk))}</b>",
        f"<i>{_count(report.get('position_count'))} position(s) · gross "
        f"{_usd(report.get('gross_notional_usd'))} · margin "
        f"{_usd(report.get('total_margin_usd'))}</i>", ""]

    if risk is None:
        lines.insert(1, "<i>No position reported a readable leverage, so how "
                        "close this book sits to liquidation is unknown — the "
                        "ORDER below still holds, the urgency is not a "
                        "measurement.</i>")

    for s in steps[:MAX_STEPS]:
        liq = s.get("liq_move_pct")
        liq_txt = f" · ~{liq}% to liq" if liq is not None else ""
        lines.append(
            f"<b>{s.get('order')}.</b> close <b>{html.escape(str(s.get('symbol', '')))}</b> "
            f"{html.escape(str(s.get('direction', '')))} "
            f"({_usd(s.get('notional_usd'))}{liq_txt})\n"
            f"   <i>{html.escape(str(s.get('reason', '')))} · frees "
            f"{_usd(s.get('margin_freed_cum_usd'))} cum.</i>")

    # A TRUNCATED EXIT PLAN THAT DOES NOT SAY SO IS THE DANGEROUS KIND.
    hidden = len(steps) - MAX_STEPS
    if hidden > 0:
        lines.append(
            f"\n⚠️ <i>Showing the {MAX_STEPS} most urgent of {len(steps)} "
            f"positions. {hidden} more remain open after these closes — this "
            f"is not the whole book.</i>")

    lines.append("\n<i>Execute with /closeall (flatten) or /emergency_stop (halt + flatten).</i>")
    lines.append(
        f"<i>{'🟢 plan sealed to the evidence chain' if sealed else '🟡 preview only (GUARDIAN_ESCAPE_ENABLED off)'}"
        " · this plans, it does not close</i>")
    return "\n".join(lines)
