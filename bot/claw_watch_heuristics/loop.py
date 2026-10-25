"""The poll loop. Off unless the flag is on. One tick never raises into the bot.

A failure is logged as the exception class only.
"""

from __future__ import annotations

import asyncio
import logging

from bot.claw_watch_heuristics.flag import flag_on, min_poll_sec
from bot.claw_watch_heuristics.service import poll_all

log = logging.getLogger("claw_watch_heuristics")


async def start_poll(bot, env=None):
    """Start the background poll, or return None when the flag is off."""
    if not flag_on(env):
        return None

    async def _send(chat_id, text):
        await bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True,
        )
        return True

    async def _run():
        while True:
            try:
                await poll_all(env, send=_send)
            except Exception as exc:
                log.warning("claw watch heuristics poll failed (%s)", type(exc).__name__)
            await asyncio.sleep(min_poll_sec(env))

    return asyncio.create_task(_run())
