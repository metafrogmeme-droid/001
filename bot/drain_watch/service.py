"""Register, delete, and poll. The chat id is hashed before it is stored.

The background loop runs only when the public flag is on and a salt is set.
A failed send does not advance the cursor, so a missed alert is retried
rather than marked as delivered.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from bot.approval_hygiene import (
    CHAINS,
    INVALID_ADDRESS,
    SECRET_REFUSAL,
    SECRET_REQUEST,
    looks_like_secret,
    normalize_address,
)
from bot.approval_reader import TOPIC_APPROVAL, TOPIC_FOR_ALL, TOPIC_PERMIT2
from bot.daily_duel_store import player_key
from bot.drain_watch.alert import render_alert, render_watch_status
from bot.drain_watch.classify import TOPIC_TRANSFER, events_from_chain
from bot.drain_watch.copy import NO_CHAT
from bot.drain_watch.flag import flag_on
from bot.drain_watch.reader import RangeReader
from bot.drain_watch.store import DrainWatchStore, address_cap
from bot.utils.paths import state_path

log = logging.getLogger("runeclaw.drain_watch")

Send = Callable[[str, str], Awaitable[None]]
_SALT_MIN = 16
_POLL_DEFAULT = 300
_POLL_MIN = 30


def salt_of(env: Optional[dict] = None) -> Optional[str]:
    src = env if env is not None else os.environ
    raw = str(src.get("DRAIN_WATCH_ID_SALT") or "").strip()
    if len(raw) < _SALT_MIN:
        return None
    return raw


def store_path(env: Optional[dict] = None) -> Path:
    """Where the HMAC store lives. A relative path is anchored at the repo,
    so the file does not follow whoever launched the process."""
    src = env if env is not None else os.environ
    raw = str(src.get("DRAIN_WATCH_STORE") or "").strip()
    if raw:
        return state_path(raw)
    return state_path("data/drain_watches.sqlite")


def poll_sec(env: Optional[dict] = None) -> int:
    src = env if env is not None else os.environ
    raw = str(src.get("DRAIN_WATCH_POLL_SEC") or "").strip()
    if not raw:
        return _POLL_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _POLL_DEFAULT
    if n < _POLL_MIN:
        return _POLL_DEFAULT
    return n


# Imported name used by the stop reply. Defined here so copy stays free of
# the digest's STOPPED sentence, which a duel test matches exactly.
STOPPED_ROWS = "Removed. No Claw Watch address, cursor, or label is stored for this chat."
STOPPED_EMPTY = "Nothing was stored for this chat."


class DrainWatch:
    def __init__(
        self,
        *,
        salt: str,
        store: DrainWatchStore,
        reader: Optional[RangeReader] = None,
        exploits: Any = None,
        now: Optional[Callable[[], datetime]] = None,
        families: Optional[dict[str, str]] = None,
    ):
        self.salt = salt
        self.store = store
        self.reader = reader
        self.exploits = exploits
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.families = families or {}
        self.delivery: dict[str, str] = {}

    def watch_key(self, chat_id: str) -> Optional[str]:
        if not chat_id or len(self.salt) < _SALT_MIN:
            return None
        try:
            return player_key(chat_id, self.salt)
        except ValueError:
            return None

    def bind(self, chat_id: str) -> Optional[str]:
        key = self.watch_key(chat_id)
        if key and chat_id:
            self.delivery[key] = chat_id
        return key

    def _reader(self) -> RangeReader:
        if self.reader is None:
            self.reader = RangeReader()
        return self.reader

    def _listed(self) -> Optional[set]:
        if self.exploits is None:
            return None
        try:
            got = self.exploits.get() if hasattr(self.exploits, "get") else self.exploits()
        except Exception as exc:
            log.info("exploit list failed: %s", type(exc).__name__)
            return None
        if got is None:
            return None
        if isinstance(got, set):
            return got
        return None

    def register_address(self, chat_id: str, argument: str, *, preview: bool) -> str:
        key = self.bind(chat_id)
        if key is None:
            return NO_CHAT
        if looks_like_secret(argument) or SECRET_REQUEST.search(argument or ""):
            return SECRET_REFUSAL
        address = normalize_address(argument)
        if address is None:
            return INVALID_ADDRESS
        moment = self.now().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        try:
            status, count = self.store.add(key, address, moment)
        except ValueError:
            return "That address was not stored."
        if status == "duplicate":
            text = "That address is already watched. Nothing new was stored."
        elif status == "full":
            text = (
                f"This chat already watches {address_cap()} addresses, which is the maximum. "
                "stop removes them."
            )
        else:
            text = f"Watching {address}. Stored under an anonymous id. Count: {count}."
        if preview:
            text = "Admin preview. Not public.\n\n" + text
        return text

    def forget_chat(self, chat_id: str) -> str:
        key = self.watch_key(chat_id)
        if key is None:
            return NO_CHAT
        removed = self.store.forget(key)
        self.delivery.pop(key, None)
        return STOPPED_ROWS if removed else STOPPED_EMPTY

    def on_status(self, chat_id: str, *, preview: bool) -> str:
        key = self.bind(chat_id)
        if key is None:
            return NO_CHAT
        held = self.store.addresses(key)
        return render_watch_status(held, preview=preview, watching=not preview)

    def prepare(self, chat_id: str) -> tuple[list, list]:
        """``(alert events, commits)``. Commits apply only after a successful send."""
        key = self.bind(chat_id)
        if key is None:
            return [], []
        held = self.store.addresses(key)
        if not held:
            return [], []
        listed = self._listed()
        moment = self.now().astimezone(timezone.utc)
        now = int(moment.timestamp())
        day = moment.strftime("%Y-%m-%d")
        alerts: list = []
        commits: list = []
        reader = self._reader()
        for address in held:
            for chain_id, chain_name in CHAINS:
                outcome, pending = self._one_chain(
                    key, address, chain_id, chain_name, listed, now, day, reader,
                )
                for ev in outcome.events:
                    if not ev.alert:
                        if ev.kind in ("dust", "lookalike_dust"):
                            self.store.hide(key, ev.event_id, ev.kind)
                        continue
                    if self.store.has_seen(key, ev.event_id):
                        continue
                    alerts.append(ev)
                    pending.setdefault("seen", []).append(ev.event_id)
                for token, spender, updated, unlimited in outcome.save_approvals:
                    self.store.save_approval(key, chain_id, token, spender, updated, unlimited)
                for other in outcome.counterparties:
                    self.store.add_counterparty(key, other)
                if not pending.get("seen"):
                    self._apply(key, chain_id, outcome)
                else:
                    commits.append((chain_id, outcome, pending["seen"]))
        return alerts, commits

    def _apply(self, key: str, chain_id: int, outcome) -> None:
        if outcome.advance_to is not None:
            self.store.set_cursor(key, chain_id, outcome.advance_to)
        if outcome.delegate is not None:
            status, delegate = outcome.delegate
            self.store.set_delegate(key, chain_id, status, delegate)

    def commit(self, chat_id: str, commits: list) -> None:
        key = self.watch_key(chat_id)
        if key is None:
            return
        for chain_id, outcome, seen in commits:
            for event_id in seen:
                self.store.mark_seen(key, event_id)
            self._apply(key, chain_id, outcome)

    def _one_chain(self, key, address, chain_id, chain_name, listed, now, day, reader):
        head = reader.head(chain_id)
        cursor = self.store.cursor(key, chain_id)
        stored = self.store.delegate(key, chain_id)
        delegation_status, delegation = reader.account_delegation(chain_id, address)
        logs: list = []
        txs: list = []
        logs_ok = True
        txs_ok = True
        senders: dict = {}
        if cursor is not None and head is not None and cursor < head:
            start = cursor + 1
            logs, logs_ok = self._logs(reader, chain_id, address, start, head)
            raw_txs = reader.transactions(chain_id, address, start, head)
            txs_ok = raw_txs is not None
            txs = [_tx(row) for row in (raw_txs or [])]
            if logs_ok:
                for row in logs:
                    topics = [str(t).lower() for t in (row.get("topics") or [])]
                    if topics and topics[0] == TOPIC_TRANSFER:
                        tx_hash = str(row.get("tx_hash") or "")
                        if tx_hash and tx_hash not in senders:
                            senders[tx_hash] = reader.tx_sender(chain_id, tx_hash)
        outcome = events_from_chain(
            owner=address,
            chain_id=chain_id,
            chain_name=chain_name,
            head=head,
            cursor=cursor,
            logs_ok=logs_ok,
            logs=logs,
            txs_ok=txs_ok,
            txs=txs,
            senders=senders,
            delegation_status=delegation_status,
            delegation=delegation,
            stored_delegate=stored,
            stored_approvals=self._approvals_for(key, chain_id, logs, senders),
            counterparties=self.store.counterparties(key),
            exploits=listed,
            families=self.families,
            now=now,
            day=day,
        )
        return outcome, {}

    def _approvals_for(self, key: str, chain_id: int, logs: list, senders: dict) -> dict:
        found: dict = {}
        for row in logs:
            topics = [str(t).lower() for t in (row.get("topics") or [])]
            if len(topics) < 3 or topics[0] != TOPIC_TRANSFER:
                continue
            token = str(row.get("address") or "").lower()
            tx_hash = str(row.get("tx_hash") or "")
            status, sender = senders.get(tx_hash, ("unreadable", None))
            if status != "ok" or not isinstance(sender, str):
                continue
            spender = sender.lower()
            remembered = self.store.approval(key, chain_id, token, spender)
            if remembered is not None:
                found[(token, spender)] = remembered
        return found

    def _logs(self, reader, chain_id, address, start, head):
        merged: list = []
        queries = (
            (TOPIC_APPROVAL, 1),
            (TOPIC_FOR_ALL, 1),
            (TOPIC_PERMIT2, 1),
            (TOPIC_TRANSFER, 1),
            (TOPIC_TRANSFER, 2),
        )
        for topic, pos in queries:
            rows = reader.logs(chain_id, address, topic, pos, start, head)
            if rows is None:
                return [], False
            merged.extend(_log(row) for row in rows)
        return merged, True


def _log(row: dict) -> dict:
    return {
        "address": row.get("address") or row.get("token") or "",
        "topics": row.get("topics") or [],
        "data": row.get("data") or "0x",
        "block": row.get("blockNumber", row.get("block")),
        "log_index": row.get("logIndex", row.get("log_index")),
        "tx_hash": row.get("transactionHash", row.get("tx_hash", "")),
        "timestamp": row.get("timeStamp", row.get("timestamp")),
    }


def _tx(row: dict) -> dict:
    raw_value = row.get("value", row.get("value_wei"))
    value = None
    if isinstance(raw_value, int) and not isinstance(raw_value, bool):
        value = raw_value
    elif isinstance(raw_value, str) and raw_value.isdigit():
        value = int(raw_value)
    sender = row.get("from", row.get("sender"))
    failed = row.get("failed")
    if row.get("isError") in ("1", 1):
        failed = True
    return {
        "hash": row.get("hash") or row.get("transactionHash") or "",
        "sender": sender if isinstance(sender, str) else None,
        "to": row.get("to") or "",
        "value_wei": value,
        "failed": failed,
    }


_SERVICE: Optional[DrainWatch] = None


def get_service() -> Optional[DrainWatch]:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    salt = salt_of()
    if salt is None:
        return None
    _SERVICE = DrainWatch(salt=salt, store=DrainWatchStore(store_path()))
    return _SERVICE


def forget_telegram(chat_id: str, env: Optional[dict] = None) -> bool:
    """Delete this chat's rows. No file and no salt mean there was nothing to delete."""
    salt = salt_of(env)
    if not salt or not chat_id:
        return False
    path = store_path(env)
    if not path.exists():
        return False
    try:
        key = player_key(str(chat_id), salt)
    except ValueError:
        return False
    return DrainWatchStore(path).forget(key)


def texts_for(events: list, *, preview: bool) -> list[str]:
    out: list[str] = []
    for ev in events:
        out.extend(render_alert(ev, preview=preview))
    return out


async def dispatch(service: DrainWatch, send: Send) -> int:
    sent = 0
    for key, chat in list(service.delivery.items()):
        try:
            alerts, commits = await asyncio.to_thread(service.prepare, chat)
            preview = not flag_on()
            for text in texts_for(alerts, preview=preview):
                await send(chat, text)
            await asyncio.to_thread(service.commit, chat, commits)
        except Exception as exc:
            log.warning("claw watch send failed: %s", type(exc).__name__)
            continue
        sent += 1
    return sent


def start_poll(bot) -> Optional[asyncio.Task]:
    """A background poll, or None when the flag is off or the salt is unset."""
    if not flag_on():
        return None
    service = get_service()
    if service is None:
        return None
    interval = poll_sec()

    async def _send(chat_id: str, text: str) -> None:
        from bot.utils.outbound import reply_safe
        await bot.send_message(chat_id=int(chat_id), text=reply_safe(text))

    async def _loop() -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                await dispatch(service, _send)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("claw watch poll failed: %s", type(exc).__name__)

    return asyncio.create_task(_loop())
