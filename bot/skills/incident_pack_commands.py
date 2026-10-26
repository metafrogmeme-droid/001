"""The /incidentpack admin command.

A caller who is not an admin gets the same not-found answer a missing
command would. While INCIDENT_PACK_ENABLED is off, an admin still gets
the pack and is told it is a preview. Nothing is sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.admin_preview import BANNER
from bot.incident_pack import command_text, flag_on


class IncidentPackCommands:
    """Admin incident pack. Host contract below."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

    async def _cmd_incidentpack(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/incidentpack — read-only pack for one address and a window."""
        if not self._is_admin(update):
            await self._send(update, "Not found.")
            return
        try:
            args = list(getattr(ctx, "args", None) or [])
            text = command_text(args, src=getattr(self, "_incident_src", None))
            if not flag_on():
                text = BANNER + "\n" + text
            await self._send(update, text)
        except Exception as exc:
            await self._send(update, type(exc).__name__)
