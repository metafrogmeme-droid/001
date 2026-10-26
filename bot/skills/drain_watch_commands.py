"""The /clawwatch command.

An admin can use it while the public flag is off. Anyone else hears the
same not-found answer a missing command would. When the flag is on, the
command stores an address under an HMAC and can delete that chat's rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.drain_watch.copy import NO_CHAT, NOT_AVAILABLE, NOT_FOUND
from bot.drain_watch.flag import flag_on


class DrainWatchCommands:
    """Claw Watch registration. Host contract below."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

    async def _cmd_clawwatch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/clawwatch — Claw Watch, an address watch with unsigned revokes."""
        if not self._is_admin(update) and not flag_on():
            await self._send(update, NOT_FOUND)
            return
        from bot.drain_watch.service import get_service, texts_for

        service = get_service()
        if service is None:
            await self._send(update, NOT_AVAILABLE)
            return
        who = self._get_tg_id(update)
        if not who:
            await self._send(update, NO_CHAT)
            return
        raw = " ".join(ctx.args or []).strip()
        head = raw.split()[0].lower() if raw else ""
        preview = not flag_on()
        try:
            if head == "stop" and len(raw.split()) == 1:
                text = service.forget_chat(who)
            elif head == "now" and len(raw.split()) == 1:
                alerts, commits = service.prepare(who)
                texts = texts_for(alerts, preview=preview) or [
                    service.on_status(who, preview=preview),
                ]
                for piece in texts:
                    await self._send(update, piece)
                service.commit(who, commits)
                return
            elif not raw:
                text = service.on_status(who, preview=preview)
            else:
                text = service.register_address(who, raw, preview=preview)
        except Exception as exc:
            await self._send(update, type(exc).__name__)
            return
        await self._send(update, text)
