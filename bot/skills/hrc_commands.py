"""The /hrc admin card.

Utilization and rate points. The 24h shock is computed for the admin packet
and left off the card. A caller who is not an admin gets the same not-found
answer a missing command would, not a sentence that the command exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.hrc.flag import flag_on
from bot.hrc.present import admin_packet, card_text, load_reading


class HrcCommands:
    """Admin borrow-rate card. Host contract below."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

    async def _cmd_hrc(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/hrc — Hyperliquid borrow-rate caps, admin only."""
        if not self._is_admin(update):
            await self._send(update, "Not found.")
            return
        try:
            packet = admin_packet(load_reading())
            packet["preview"] = not flag_on()
            await self._send(update, card_text(packet))
        except Exception as exc:
            await self._send(update, type(exc).__name__)
