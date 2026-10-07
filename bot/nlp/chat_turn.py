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

from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION

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
#:
#: Read off the gate table the web dispatcher is pinned to
#: (`WEB_ROUTED_PERMISSION`, which `_WEB_SEAM` equals minus `status`),
#: not written out. `status` is the engine's own read and no door left the
#: Node table for it. This was a hand-written copy grown in step with the
#: seam across fifteen PRs and pinned only to its own literal, so a
#: sixteenth door could reach the dispatcher and never the card, or the
#: card could name a door the dispatcher hands to the model.
SHARED_DOORS: tuple[str, ...] = tuple(
    name for name in WEB_ROUTED_PERMISSION if name != "status")


#: What each shared door does, in the WEBSITE's words, with one sentence
#: that reaches it. The card used the command catalogue's line, which is
#: Telegram's: it printed "/wallet [chain]", a slash command the web chat
#: cannot open, and for idle yield it printed the operator's exchange-account
#: scan ("cross-source best-rate scan") over the viewer's linked-wallet card.
#: A test routes every example through `IntentRouter.classify_rules` and
#: requires one for every door the gate lists.
WEB_DOOR_WORDS: dict[str, tuple[str, str]] = {
    "price_alert": ("a price alert armed here and delivered here when it trips",
                    "tell me when BTC drops below 100k"),
    "letter": ("the agent's letter for the last completed week, from the recorded data",
               "show this week's letter"),
    "wallet": ("your linked on-chain wallet, mirrored read-only", "my wallet on base"),
    "replay": ("what-if replay of every recorded agent trade at your stake",
               "replay every signal at a 1000 stake"),
    "rwa": ("tokenized real-world-asset radar", "show the rwa radar"),
    "airdrops": ("airdrop and testnet radar, guided only, never farmed for you",
                 "any airdrops"),
    "venue_router": ("the cheapest venue to hold a position by funding cost",
                     "cheapest venue for BTC"),
    "meme_radar": ("on-chain meme and AI-token snapshot with a safety read, read-only",
                   "meme radar"),
    "nft": ("NFT collections by real 7-day volume, floor and volume, read-only",
            "nft radar"),
    "spot": ("spot pairs across venues and the spot/perp basis, read-only",
             "spot pairs for BTC"),
    "defi": ("your Aave, Lido and Uniswap positions with liquidation risk, read-only",
             "my defi positions"),
    "exposure": ("net per-asset exposure", "my exposure"),
    "research": ("cited research dossier for a symbol", "research SOL"),
    "networth": ("cross-venue net worth snapshot", "my net worth"),
    "idleyield": ("the best rate for idle funds in your linked wallet",
                  "where can my idle USDC earn yield"),
}


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
    out: list[str] = []
    for name in SHARED_DOORS:
        try:
            refused = denial(user_id, name)
        except Exception:
            return []
        if refused is not None:
            continue
        words = WEB_DOOR_WORDS.get(name)
        if words is None:
            # A door with no web words is left off rather than described in
            # Telegram's: a test fails until it has them.
            continue
        what, example = words
        out.append(f"{what} — say \u201c{example}\u201d")
    return out
