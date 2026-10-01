"""The decisions both chat doors share.

Telegram ``_handle_message`` and the website ``_chat_turn`` each render
their own transport. The order they already run is one sequence:

  firewall, manual-trade grammar, action doors, regex fast path,
  the model (``_llm_chat`` through ``_chat_ret``), then memory.

Action doors (halt and the other commands) stay commands. This module
holds the sentences both doors must not each invent. A family that
leaves the website's Node intercept table is named in ``SHARED_DOORS``
and answered by the same seam Telegram already calls.
"""
from __future__ import annotations

import html

#: The order both doors run. A later family moves onto this sequence;
#: the tuple is what a test pins, so a door that answers out of order
#: is a change to this line.
STAGES: tuple[str, ...] = (
    "firewall",
    "manual_trade",
    "action_doors",
    "fast_path",
    "model",
    "memory",
)

#: Website intercepts that now ride the shared door. The command
#: catalogue's own sentence is what the capability card says, so the
#: card and the slash command cannot describe two different things.
#: Order is the order each family left the Node table.
SHARED_DOORS: tuple[str, ...] = (
    "price_alert", "letter", "wallet", "replay", "rwa", "airdrops", "venue_router",
)


def firewall_block_notice(categories: object, *, newlines: bool) -> str:
    """The refusal both doors send when a HIGH verdict is set to block.

    ``newlines`` is Telegram's break. The website joins with ``<br>``,
    which is the markup that surface already renders. The categories
    are escaped here so neither caller can forget.
    """
    raw = categories if isinstance(categories, (list, tuple)) else ()
    named = [str(c) for c in raw[:3] if str(c).strip()]
    cats = ", ".join(named) or "manipulation"
    gap = "\n\n" if newlines else "<br><br>"
    return (
        "\U0001f6e1\ufe0f <b>Blocked by the Guardian firewall.</b>"
        f"{gap}"
        "That message looked like a prompt-injection / unsafe-action "
        f"attempt (<i>{html.escape(cats)}</i>), so I won't act on it. "
        "Rephrase what you actually want and I'll help."
    )


def shared_door_sentences(users: object, user_id: str) -> list[str]:
    """Catalogue lines for doors this caller's role may actually use.

    A missing or unreadable permission is omitted, never granted: the
    card must not offer a write the gate would refuse. An empty list
    is that omission, not "this product has no such door".
    """
    denial = getattr(users, "permission_denial", None)
    if not callable(denial):
        return []
    from bot.skills.command_catalog import all_entries
    entries = all_entries()
    out: list[str] = []
    for name in SHARED_DOORS:
        try:
            refused = denial(user_id, name)
        except Exception:
            return []
        if refused is not None:
            continue
        row = entries.get(name)
        text = row[2] if isinstance(row, tuple) and len(row) > 2 else ""
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out
