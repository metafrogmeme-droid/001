"""/fees — Silent Fee Check, mounted from Panic Button.

Admin only, and off unless SILENT_FEE_CHECK_ENABLED is 1 or true. A caller
who is not an admin gets the same not-found answer a missing command would.
The reading is read-only. This command does not sign.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.approval_hygiene import command_argument
from bot.panic_button import read_silent_fees
from bot.silent_fee.flag import flag_on
from bot.silent_fee.render import render_text


class SilentFeeCommands:
    """Admin fee reading. Host contract below."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

    async def _cmd_fees(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/fees <address> [30|90] — builder caps, headroom and referrer."""
        if not self._is_admin(update):
            await self._send(update, "Not found.")
            return
        if not flag_on():
            await self._send(update, "not available yet")
            return
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        raw = command_argument(getattr(msg, "text", "") if msg is not None else "")
        parts = raw.split()
        address = parts[0] if parts else ""
        if len(parts) > 2 or (len(parts) == 2 and parts[1] not in ("30", "90")):
            reading = {"available": True, "refused": "window"}
        else:
            days = int(parts[1]) if len(parts) == 2 else 30
            try:
                reading = await asyncio.to_thread(read_silent_fees, address, days=days)
            except Exception as exc:
                await self._send(update, type(exc).__name__)
                return
        await self._send(update, render_text(reading))
