"""/sigdecode — public signals for a signature request. Operator only.

The flag does not open this command to anyone else. While
SIG_DECODER_ENABLED is off the reply says it is an admin preview. The
decoder does not sign and does not call a network.
"""
from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.sigdecoder import DISCLAIMER, InputTooLarge, decode_text, flag_on, render_text
from bot.utils.i18n import t

_COMMAND = re.compile(r"^/sigdecode(?:@\S+)?\s*", re.IGNORECASE)


class SigDecoderCommands:
    """Operator chat surface for the signature decoder."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _lang(self, update: Update) -> str: ...

    async def _cmd_sigdecode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        raw = ""
        message = update.message
        if message is not None and message.text:
            raw = _COMMAND.sub("", message.text, count=1).strip()
        if not raw and message is not None and message.reply_to_message is not None:
            replied = message.reply_to_message
            raw = (replied.text or replied.caption or "").strip()
        if not raw:
            await self._send(
                update,
                "Paste a signature request after /sigdecode, or reply to one.\n"
                + DISCLAIMER,
            )
            return
        preview = "" if flag_on() else "Admin preview. Not public.\n\n"
        try:
            result = decode_text(raw)
        except InputTooLarge:
            await self._send(update, preview + "That request is too large to decode here.\n" + DISCLAIMER)
            return
        except Exception as exc:  # noqa: BLE001 — the chat is told the class only
            await self._send(update, preview + type(exc).__name__ + "\n" + DISCLAIMER)
            return
        body = html.escape(render_text(result))
        await self._send(update, preview + body)
