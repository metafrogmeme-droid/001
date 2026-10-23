"""/t2c — the duration cap signal, operator only.

A mixin. The handler supplies ``_send``, ``_is_admin`` and ``_lang``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.t2c.reading import assemble_reading
from bot.t2c.text import format_signal_text
from bot.utils.i18n import t

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine


class T2CCommands:
    """Operator chat surface for the time-to-cash cap signal."""

    if TYPE_CHECKING:
        engine: RuneClawEngine

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _lang(self, update: Update) -> str: ...

    async def _cmd_t2c(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await self._send(update, t("admin_only", self._lang(update)))
            return
        try:
            reading = assemble_reading()
        except Exception as exc:
            await self._send(update, type(exc).__name__)
            return
        await self._send(update, format_signal_text(reading))
