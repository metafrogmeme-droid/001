"""A group is a public room: a live call is answered in a private chat.

The website keeps a live call for signed-in members (`app/lib/signal_access.js`).
Telegram had no such line. A group, supergroup or channel is read by whoever
is in it, registered or not, and the bot answered there exactly as it answers
in a private chat:

- about ten commands printed the live calls or the operator's book into the
  group (`/latest_signal`, `/positions`, `/portfolio`, `/fullscan`,
  `/analyze`, `/scalp`, ...), and their buttons did the same when tapped;
- free text in a group was routed to the same skills, and the chat model's
  prompt carried the pending ideas and the open positions;
- a stranger's first command in a group admitted them (`PAPER_AUTO_ACCEPT`),
  so "registered" was one command away for anyone in any group the bot sat in.

THE GATE IS AN ALLOWLIST. In a public room only a command named in
`ROOM_COMMANDS` answers; every other command answers with a pointer to the
private chat. A command added tomorrow that prints a call is private until
somebody lists it here, which is the opposite default from a denylist and the
right one for a room. A button tapped in a room is answered the same way, and
free text gets the public chat the website's anonymous visitor gets (no
history, no account, no live call in its prompt, a reply contract that asks
for no levels).

`telegram_handler.TelegramHandler._remembering` asks this for every command
(the registration loop is the one place every command passes), and
`_remembering_button` for every tap.
"""

from __future__ import annotations

import hashlib
import html
from typing import Any

#: The chat types whose members are not, as such, anyone in particular.
PUBLIC_CHAT_TYPES = frozenset({"group", "supergroup", "channel"})

#: The commands a public room may run. Each one names no live call and no
#: account: help and identity, market reads (movers, chart patterns, the
#: macro calendar, one symbol's event risk, funding rates, sector radars, a
#: contract read), public statistics (signal outcomes per pair, the
#: leaderboard), the token, and the two channel tools: `/channel`, with which
#: a group's admin opts that group in or out, and `/broadcast`, the
#: operator's. `/news` and `/patterns` are NOT here: the first alerts on the
#: positions held and the second groups the book's own closes, both an
#: account. Everything else answers in a private chat.
ROOM_COMMANDS = frozenset({
    "help", "version", "lang",
    "scan", "deepscan", "macro", "eventrisk", "funding", "meme_radar", "rwa",
    "token",
    "signals", "leaderboard",
    "rclaw",
    "channel", "broadcast",
})


#: The buttons a public room may tap, by `button_actions.action_label`: the
#: language picker `/lang` draws. Every other button opens in a private chat.
ROOM_BUTTONS = frozenset({"lang"})


def chat_type(update: Any) -> str:
    """The update's chat type, as Telegram names it, or ''."""
    chat = getattr(update, "effective_chat", None)
    kind = getattr(chat, "type", None)
    if kind is None:
        return ""
    # python-telegram-bot hands an enum whose value is the word.
    return str(getattr(kind, "value", kind))


def is_public_room(update: Any) -> bool:
    """Is this update from a group, supergroup or channel?

    Only those three words read as a room. An update that names no chat
    type (a test double, an inline query) is not one: this gate refuses in
    rooms and nowhere else, and a private chat is where members are served.
    """
    return chat_type(update) in PUBLIC_CHAT_TYPES


def room_allows(command: str) -> bool:
    """May a public room run this command?"""
    return str(command or "").lower() in ROOM_COMMANDS


def room_allows_button(data: Any) -> bool:
    """May a public room tap this button? Read by the transcript's own
    reading of a callback's action (`bot.nlp.button_actions.action_label`)."""
    from bot.nlp.button_actions import action_label
    return action_label(data) in ROOM_BUTTONS


def private_chat_link(bot_username: str | None) -> str:
    """The private chat, as an HTML link when the bot's handle reads."""
    handle = str(bot_username or "").strip().lstrip("@")
    if not handle:
        return ""
    safe = html.escape(handle)
    return f'<a href="https://t.me/{safe}">@{safe}</a>'


def private_only_notice(lang: str, bot_username: str | None, command: str = "") -> str:
    """What a room reads instead of a member's answer: where to ask."""
    from bot.utils.i18n import t
    link = private_chat_link(bot_username) or t("tg_private_chat_words", lang)
    what = f"/{html.escape(command)}" if command else ""
    key = "tg_private_only_cmd" if what else "tg_private_only"
    return "\U0001f512 " + t(key, lang, link=link, cmd=what)


def room_visitor_key(user_id: Any) -> str:
    """The public preview's key for one person in a room.

    The website's visitor key is an HMAC of an address; a room member is a
    Telegram account, so the key is a digest of that id, never the id: the
    quota table is a count, not a directory. Per person, not per room, so one
    busy group does not spend everyone's preview.
    """
    return hashlib.sha256(f"tg-room:{user_id}".encode()).hexdigest()[:32]
