"""Render the enforcement inventory — `/gates`.

The card answers one question and refuses to answer it vaguely: **which
controls would stop a bad trade right now?**

COLOUR IS A CLAIM, and this is the card where it costs the most. A green
headline reading "everything is armed" over a book with the authority
envelope, the intent policy and the backtest gate all off would be the most
expensive false all-clear in the product. So:

  🟢  only when every refusal gate answered AND none is off or shadow
  🟡  some are off, or some are in shadow — armed count is still real
  ⚪  any refusal gate could not be READ — the posture is unknown, and an
      unknown posture is never painted as a good one

SHADOW IS NOT ARMED, and it gets its own line rather than being folded into
either column. A shadow gate records what it would have rejected and rejects
nothing; counting it as protection is precisely the substitution the whole
inventory exists to prevent. Reading "3 armed" when one of the three only
watches is how an operator ends up trusting a control that has never refused
anything.

OFF IS EXPLAINED, NOT JUST LISTED. "Authority envelope: off" tells a reader
nothing about what they are exposed to. Each entry carries what OFF actually
means, taken from the flag's own documentation, so the card reads as a
consequence rather than a checklist.
"""

from __future__ import annotations

from typing import Optional

from bot.guardian.gate_inventory import (
    ENFORCE,
    KIND_BEHAVE,
    KIND_REFUSE,
    KIND_SEAL,
    KIND_SIZE,
    KIND_TUNE,
    OFF,
    ON,
    SHADOW,
    UNKNOWN,
)

_STATUS_ICON = {
    ON: "🟢",
    ENFORCE: "🟢",
    SHADOW: "🟡",
    OFF: "🔴",
    UNKNOWN: "⚪",
}

#: Deliberately not "off" for the softer kinds — a sizing flag being off is
#: not a red state, and painting it red would train an operator to ignore red.
_SOFT_ICON = {
    ON: "🟢",
    ENFORCE: "🟢",
    SHADOW: "🟡",
    OFF: "⚫",
    UNKNOWN: "⚪",
}

_KIND_HEAD = {
    KIND_REFUSE: "🛑 <b>Can refuse a trade</b>",
    KIND_SIZE: "📏 <b>Size only</b> — never refuses",
    KIND_TUNE: "🎛 <b>Refinements</b> — the check runs either way",
    KIND_SEAL: "🔏 <b>Evidence</b> — sealing, not protection",
    KIND_BEHAVE: "⚙️ <b>Behaviour</b> — not a control",
}


def headline(summary: Optional[dict]) -> str:
    """One line an operator can read without scrolling.

    Never claims a posture it could not measure: any unreadable refusal gate
    makes the whole headline ⚪, because "4 of 11 armed" is a different
    sentence from "4 of 11 armed and 2 we could not read".
    """
    s = summary or {}
    total = s.get("total")
    if not total:
        return "⚪ <b>No controls classified</b> — the inventory could not be read."
    armed, off = s.get("armed", 0), s.get("off", 0)
    shadow, unknown = s.get("shadow", 0), s.get("unknown", 0)
    if not s.get("complete"):
        return (f"⚪ <b>Posture unknown</b> — {unknown} of {total} refusal "
                f"gate(s) could not be read. {armed} confirmed armed; the rest "
                f"is not a measurement.")
    if off == 0 and shadow == 0:
        return (f"🟢 <b>All {total} refusal gates armed</b> — every control "
                f"that can refuse a trade is enforcing.")
    bits = [f"{armed} of {total} armed"]
    if shadow:
        bits.append(f"{shadow} in shadow (records, refuses nothing)")
    if off:
        bits.append(f"{off} off")
    return "🟡 <b>Partial cover</b> — " + ", ".join(bits) + "."


def render_gate_card(rows: Optional[list], summary: Optional[dict]) -> str:
    """The full card: headline, then what is NOT protecting you, then the rest.

    Off refusal gates come FIRST and with their consequence, because they are
    the reason to open this screen. A card that leads with the twenty things
    that are fine buries the three that are not.
    """
    rows = rows or []
    if not rows:
        return ("🛡 <b>Enforcement inventory</b>\n\n"
                "⚪ Nothing to show — the control table could not be read. "
                "This is not the same as 'no controls are active'.")

    out = ["🛡 <b>Enforcement inventory</b>", "", headline(summary), ""]

    exposed = [r for r in rows
               if r["kind"] == KIND_REFUSE and r["status"] in (OFF, SHADOW, UNKNOWN)]
    if exposed:
        out.append("<b>What is not refusing right now</b>")
        for r in exposed:
            icon = _STATUS_ICON.get(r["status"], "⚪")
            if r["status"] == UNKNOWN:
                out.append(f"{icon} <b>{r['label']}</b> — could not be read; "
                           f"whether it would refuse is unknown.")
            elif r["status"] == SHADOW:
                out.append(f"{icon} <b>{r['label']}</b> — shadow: records what "
                           f"it would reject, refuses nothing.")
            else:
                out.append(f"{icon} <b>{r['label']}</b> — {r['off_means']}.")
        out.append("")

    last_kind = None
    for r in rows:
        if r["kind"] != last_kind:
            out.append("")
            out.append(_KIND_HEAD.get(r["kind"], r["kind"]))
            last_kind = r["kind"]
        table = _STATUS_ICON if r["kind"] == KIND_REFUSE else _SOFT_ICON
        icon = table.get(r["status"], "⚪")
        out.append(f"{icon} {r['label']} — <code>{r['status']}</code>")

    out.append("")
    out.append("<i>Only the first group can refuse a trade. A refinement that "
               "is off makes an existing check looser, never absent — and a "
               "control we could not read is unknown, not off.</i>")
    return "\n".join(out)
