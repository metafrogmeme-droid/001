"""``/clawheuristics``. Admin while the flag is off. Anyone signed in to the bot once it is on.

A caller who is not an admin, while the flag is off, gets the same not-found
answer a missing command would. The handler still asks ``_is_admin`` so the
gate is the one the command catalogue records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.approval_hygiene import SECRET_REFUSAL, SECRET_REQUEST, command_argument, looks_like_secret
from bot.claw_watch_heuristics.flag import flag_on
from bot.claw_watch_heuristics.service import add_address, chat_for, describe, remember_chat, remove_address, store_path

_BANNER = "Admin preview. Not public."
_HELP = (
    "Claw Watch heuristics. Public on-chain signal only. This is not a verdict. "
    "Testnet or a local fork. add 0x… | remove 0x… | status"
)


def _chat_id(update: Update) -> Optional[int]:
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return None
    cid = getattr(chat, "id", None)
    if isinstance(cid, bool) or not isinstance(cid, int):
        return None
    return cid


def _argument(update: Update) -> str:
    message = getattr(update, "message", None)
    text = getattr(message, "text", None) if message is not None else None
    if not isinstance(text, str):
        return ""
    return command_argument(text)


class ClawHeuristicsCommands:
    """Host contract: ``_send`` and ``_is_admin``."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

    async def _cmd_clawheuristics(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/clawheuristics — per-user public signals, testnet, admin while the flag is off."""
        try:
            admin = self._is_admin(update) is True
        except Exception:
            admin = False
        if not admin and not flag_on():
            await self._send(update, "Not found.")
            return
        prefix = _BANNER + "\n" if admin and not flag_on() else ""
        try:
            arg = _argument(update)
            if looks_like_secret(arg) or (arg and SECRET_REQUEST.search(arg)):
                await self._send(update, prefix + SECRET_REFUSAL)
                return
            cid = _chat_id(update)
            key = chat_for(cid)
            if key is not None and cid is not None:
                remember_chat(key, cid)
            path = store_path()
            parts = arg.split()
            head = parts[0].lower() if parts else "status"
            if head == "add" and len(parts) >= 2:
                body = add_address(path, key, parts[1])
            elif head == "remove" and len(parts) >= 2:
                body = remove_address(path, key, parts[1])
            elif head in ("status", "help") or not parts:
                body = describe(path, key, cid) if head == "status" or not parts else _HELP
            else:
                body = _HELP
            await self._send(update, prefix + body)
        except Exception as exc:
            await self._send(update, prefix + type(exc).__name__)
