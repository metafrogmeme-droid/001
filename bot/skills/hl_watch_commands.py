"""The /hlwatch command.

An admin can use it while CLAW_WATCH_HL_ENABLED is off. Anyone else hears
the same not-found answer a missing command would. When the flag is on, the
command stores an address under an HMAC and can delete that chat's rows.
The read is the public Hyperliquid info API. This command does not sign.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.claw_watch_hl.copy import NO_CHAT, NOT_FOUND
from bot.claw_watch_hl.flag import flag_on
from bot.claw_watch_hl.service import handle_command


class HlWatchCommands:
    """Hyperliquid account watch. Host contract below."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

    async def _cmd_hlwatch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/hlwatch — Hyperliquid account watch, advisory, read-only."""
        admin = self._is_admin(update)
        if not admin and not flag_on():
            await self._send(update, NOT_FOUND)
            return
        who = self._get_tg_id(update)
        if not who:
            await self._send(update, NO_CHAT)
            return
        raw = " ".join(ctx.args or []).strip()
        try:
            texts = await asyncio.to_thread(handle_command, who, raw, is_admin=admin)
        except Exception as exc:
            await self._send(update, type(exc).__name__)
            return
        for text in texts:
            await self._send(update, text)
