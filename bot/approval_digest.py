"""Opt-in approval digest. This module has no Telegram process of its own.

The Daily Duel bot is the process that runs it today. Another bot wires the
same object, without copying this file:

* ``ApprovalDigest(salt=..., store_path=...)`` with its own salt and sqlite
  file. The salt is the HMAC key (``player_key``). The file holds addresses
  and the schedule only.
* Command handlers call ``on_watch(chat_id, address_text)``,
  ``on_digest(chat_id)`` and ``on_stop(chat_id)``. The methods return the
  reply text. They do not know the slash names.
* On each inbound update, ``bind(chat_id)`` then ``dispatch_due(send)``.
* On a timer of ``poll_sec``, ``dispatch_due(send)`` again.

``send`` is an async ``(chat_id, text)`` callable. The chat id used for
delivery stays in ``delivery``. A restart drops it; the next inbound message
binds it again and a digest that came due is sent then.

On the main bot, ``/watch`` is already the price-alert command. That wiring
picks command names that are free and points them at the three methods.
Nothing here signs, holds a wallet key, or asks for one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from bot.approval_hygiene import (
    INVALID_ADDRESS,
    NO_CHAT,
    NOT_OPTED,
    SECRET_REFUSAL,
    digest_interval_sec,
    digest_poll_sec,
    looks_like_secret,
    normalize_address,
    render_digest,
    shift_iso,
    stop_reply,
    summarize_address,
    unread_chains,
    watch_reply,
)
from bot.approval_reader import ApprovalReader, ExploitDirectory
from bot.approval_watch_store import ApprovalWatchStore
from bot.daily_duel_store import player_key

log = logging.getLogger("runeclaw.approval_digest")

Send = Callable[[str, str], Awaitable[None]]


class ApprovalDigest:
    def __init__(self, *, salt: str, store: Optional[ApprovalWatchStore] = None,
                 store_path: Optional[Path] = None,
                 reader: Optional[ApprovalReader] = None,
                 exploits: Optional[ExploitDirectory] = None,
                 now: Optional[Callable[[], str]] = None,
                 interval_sec: Optional[int] = None,
                 poll_sec: Optional[int] = None):
        self.salt = salt
        self.store = store
        self.store_path = Path(store_path) if store_path is not None else Path("approval_watches.sqlite")
        self._reader = reader
        self._exploits = exploits
        self.now = now or (lambda: datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"))
        self.interval_sec = digest_interval_sec() if interval_sec is None else interval_sec
        self.poll_sec = digest_poll_sec() if poll_sec is None else poll_sec
        self.delivery: dict[str, str] = {}

    @property
    def watches(self) -> ApprovalWatchStore:
        if self.store is None:
            self.store = ApprovalWatchStore(self.store_path)
        return self.store

    @property
    def reader(self) -> ApprovalReader:
        if self._reader is None:
            self._reader = ApprovalReader()
        return self._reader

    @property
    def exploits(self) -> ExploitDirectory:
        if self._exploits is None:
            self._exploits = ExploitDirectory()
        return self._exploits

    def chat_key(self, chat_id: str) -> Optional[str]:
        if not chat_id:
            return None
        try:
            return player_key(chat_id, self.salt)
        except ValueError:
            return None

    def bind(self, chat_id: str) -> None:
        """Remember where to deliver a digest. Not written to the store."""
        key = self.chat_key(chat_id)
        if key and chat_id:
            self.delivery[key] = chat_id

    async def on_watch(self, chat_id: str, argument: str) -> str:
        self.bind(chat_id)
        key = self.chat_key(chat_id)
        if key is None:
            return NO_CHAT
        if looks_like_secret(argument):
            return SECRET_REFUSAL
        address = normalize_address(argument)
        if address is None:
            return INVALID_ADDRESS
        try:
            nxt = shift_iso(self.now(), self.interval_sec)
            status, count = await asyncio.to_thread(
                self.watches.add, key, address, self.now(), nxt,
            )
        except ValueError:
            return "That address was not stored."
        return watch_reply(status, address, count)

    async def on_digest(self, chat_id: str) -> list[str]:
        self.bind(chat_id)
        key = self.chat_key(chat_id)
        if key is None:
            return [NO_CHAT]
        held = await asyncio.to_thread(self.watches.addresses, key)
        if not held:
            return [NOT_OPTED]
        return await self.digest_texts(key)

    async def on_stop(self, chat_id: str) -> str:
        key = self.chat_key(chat_id)
        if key is None:
            return NO_CHAT
        removed = await asyncio.to_thread(self.watches.forget, key)
        self.delivery.pop(key, None)
        return stop_reply(removed)

    async def digest_texts(self, chat_key: str) -> list[str]:
        addresses = await asyncio.to_thread(self.watches.addresses, chat_key)
        if not addresses:
            return [NOT_OPTED]
        try:
            now = int(datetime.fromisoformat(self.now().replace("Z", "+00:00")).timestamp())
        except ValueError:
            now = int(datetime.now(timezone.utc).timestamp())
        try:
            listed = await asyncio.to_thread(self.exploits.get)
        except Exception as exc:
            log.info("exploit list failed: %s", type(exc).__name__)
            listed = None
        summaries = []
        for address in addresses:
            try:
                chains = await asyncio.to_thread(self.reader.read, address)
            except Exception as exc:
                log.info("approval digest read failed: %s", type(exc).__name__)
                chains = unread_chains()
            summaries.append(summarize_address(address, chains, now, listed))
        return render_digest(summaries, scam_known=listed is not None)

    async def dispatch_due(self, send: Send) -> int:
        """Send digests that are due to chats bound in this process.

        Returns how many chats got one. A chat id that is not in memory is
        left due, so a restart delivers on the next message that binds it.
        A failed send does not move the schedule.
        """
        due = await asyncio.to_thread(self.watches.due, self.now())
        sent = 0
        for key in due:
            chat = self.delivery.get(key)
            if not chat:
                continue
            try:
                texts = await self.digest_texts(key)
                for text in texts:
                    await send(chat, text)
            except Exception as exc:
                log.warning("approval digest send failed: %s", type(exc).__name__)
                continue
            try:
                nxt = shift_iso(self.now(), self.interval_sec)
                await asyncio.to_thread(self.watches.schedule_next, key, nxt)
            except ValueError:
                continue
            sent += 1
        return sent
