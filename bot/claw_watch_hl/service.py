"""Register, delete, and poll. The chat id is hashed before it is stored.

The background loop runs only when ``CLAW_WATCH_HL_ENABLED`` is on and a salt
is set. A failed send does not save the new snapshot, so the same change is
retried rather than marked as delivered. Alerts are advisory.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Optional

from bot.admin_preview import BANNER
from bot.approval_hygiene import (
    INVALID_ADDRESS,
    MAX_ADDRESSES,
    SECRET_REFUSAL,
    looks_like_secret,
    normalize_address,
    short_address,
)
from bot.claw_watch_hl import copy
from bot.claw_watch_hl.flag import flag_on, mainnet_read_on
from bot.claw_watch_hl.hook import ClawWatchRead, read_claw_watch
from bot.claw_watch_hl.store import HlStore
from bot.claw_watch_hl.watch import diff, snapshot
from bot.daily_duel_store import player_key
from bot.utils.paths import state_path

log = logging.getLogger("runeclaw.claw_watch_hl")

Send = Callable[[str, str], Awaitable[None]]
_SALT_MIN = 16
_POLL_DEFAULT = 300
_POLL_MIN = 30
_ALERT_MAX = 3500

_SERVICE: Optional["HlWatch"] = None


def salt_of(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    src = env if env is not None else os.environ
    raw = str(src.get("CLAW_WATCH_HL_ID_SALT") or "").strip()
    if len(raw) < _SALT_MIN:
        return None
    return raw


def store_path(env: Optional[Mapping[str, str]] = None) -> Path:
    src = env if env is not None else os.environ
    raw = str(src.get("CLAW_WATCH_HL_STORE") or "").strip()
    if raw:
        return state_path(raw)
    return state_path("data/claw_watch_hl.sqlite")


def poll_sec(env: Optional[Mapping[str, str]] = None) -> int:
    src = env if env is not None else os.environ
    raw = str(src.get("CLAW_WATCH_HL_POLL_SEC") or "").strip()
    if not raw:
        return _POLL_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _POLL_DEFAULT
    if n < _POLL_MIN:
        return _POLL_DEFAULT
    return n


def host_line(env: Optional[Mapping[str, str]] = None) -> str:
    if mainnet_read_on(env):
        return copy.HOST_MAINNET
    return copy.HOST_TESTNET


def render_messages(alerts: list[dict], *, preview: bool,
                    env: Optional[Mapping[str, str]] = None) -> list[str]:
    """One or more notes. Each one carries the advisory lines."""
    if not alerts:
        return []
    head = [copy.ADVISORY, copy.NEVER_ASK, host_line(env)]
    if preview:
        head.append(BANNER)
    chunks: list[str] = []
    body: list[str] = []

    def pack() -> str:
        return "\n\n".join(head + body)

    for alert in alerts:
        line = f"{alert.get('code')}: {alert.get('text')}"
        if len(line) > _ALERT_MAX - 200:
            line = line[: _ALERT_MAX - 200]
        if body and len(pack()) + len(line) + 2 > _ALERT_MAX:
            chunks.append(pack())
            body = []
        body.append(line)
    if body:
        chunks.append(pack())
    return chunks


class HlWatch:
    def __init__(self, *, salt: str, store: HlStore, env: Optional[Mapping[str, str]] = None,
                 fetch=None, now_ms=None, claw_read=None):
        self.salt = salt
        self.store = store
        self.env = env if env is not None else os.environ
        self.fetch = fetch
        self.now_ms = now_ms
        self.claw_read = claw_read or read_claw_watch
        self.delivery: dict[str, str] = {}

    def hl_watch_key(self, chat_id: str) -> Optional[str]:
        if not chat_id or len(self.salt) < _SALT_MIN:
            return None
        try:
            return player_key(str(chat_id), self.salt)
        except ValueError:
            return None

    def bind(self, chat_id: str) -> Optional[str]:
        key = self.hl_watch_key(chat_id)
        if key and chat_id:
            self.delivery[key] = str(chat_id)
        return key

    def _now(self) -> int:
        if self.now_ms is None:
            return int(datetime.now(timezone.utc).timestamp() * 1000)
        if callable(self.now_ms):
            return int(self.now_ms())
        return int(self.now_ms)

    def _preview(self) -> bool:
        return not flag_on(self.env)

    def hl_status(self, chat_id: str) -> str:
        key = self.hl_watch_key(chat_id)
        lines = [copy.ADVISORY, copy.NEVER_ASK, host_line(self.env)]
        if self._preview():
            lines.append(BANNER)
        if not key:
            lines.append(copy.NOT_AVAILABLE)
            return "\n\n".join(lines)
        held = self.store.listed_addresses(key)
        if not held:
            lines.append("No Hyperliquid address is stored for this chat.")
        else:
            shown = ", ".join(short_address(a) for a in held)
            lines.append(f"Stored addresses: {shown}.")
        claw = self._claw(chat_id)
        if claw.present and claw.addresses is None:
            lines.append(copy.CLAW_UNREAD)
        elif claw.present and claw.addresses:
            lines.append(f"Also reading {len(claw.addresses)} address(es) stored by Claw Watch.")
        if self._preview():
            lines.append("The public flag is off. This reply is the admin preview.")
        lines.append(copy.HOW)
        return "\n\n".join(lines)

    def _claw(self, chat_id: str) -> ClawWatchRead:
        try:
            return self.claw_read(str(chat_id))
        except Exception as exc:
            log.info("claw watch read skipped: %s", type(exc).__name__)
            return ClawWatchRead(True, None)

    def hl_register(self, chat_id: str, raw: str) -> str:
        if looks_like_secret(raw):
            return SECRET_REFUSAL
        addr = normalize_address(raw)
        if addr is None:
            return INVALID_ADDRESS
        key = self.bind(chat_id)
        if not key:
            return copy.NOT_AVAILABLE
        added = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        err = self.store.add_address(key, addr, added)
        if err == "cap":
            return (
                f"This chat already watches {MAX_ADDRESSES} addresses, which is the maximum. "
                "Send stop to delete them."
            )
        lines = [
            f"Stored {short_address(addr)}. Send now to read once. The first read is a baseline.",
        ]
        if self._preview():
            lines.insert(0, BANNER)
        return "\n\n".join(lines)

    def hl_forget(self, chat_id: str) -> str:
        key = self.hl_watch_key(chat_id)
        if not key:
            return copy.NOT_AVAILABLE
        self.delivery.pop(key, None)
        if self.store.forget(key):
            return copy.STOPPED
        return copy.STOPPED_EMPTY

    def hl_prepare(self, chat_id: str) -> tuple[list[str], list[tuple[str, dict]], Optional[set], bool]:
        """Alerts, snapshots to save after a successful send, the keep-set, and baseline.

        The keep-set is None when Claw Watch could not be read. Snapshots for
        addresses that are not in this chat's own list are left in place then,
        because an unread list is not an empty one. ``baseline`` is true when
        any watched address had no snapshot yet.
        """
        key = self.bind(chat_id)
        if not key:
            return [copy.NOT_AVAILABLE], [], set(), False
        owned = self.store.listed_addresses(key)
        claw = self._claw(chat_id)
        watched = list(owned)
        keep: Optional[set] = None
        if claw.addresses is not None:
            for raw in claw.addresses:
                addr = normalize_address(raw)
                if addr and addr not in watched:
                    watched.append(addr)
            keep = set(watched)
        if not watched:
            return [], [], keep, False
        now = self._now()
        alerts: list[dict] = []
        pending: list[tuple[str, dict]] = []
        baseline = False
        for addr in watched:
            old = self.store.snapshot(key, addr)
            if old is None:
                baseline = True
            new = snapshot(addr, now, self.fetch, self.env)
            alerts.extend(diff(old, new))
            pending.append((addr, new))
        texts = render_messages(alerts, preview=self._preview(), env=self.env)
        return texts, pending, keep, baseline

    def hl_commit(self, chat_id: str, pending: list[tuple[str, dict]], keep: Optional[set]) -> None:
        key = self.hl_watch_key(chat_id)
        if not key:
            return
        for addr, body in pending:
            self.store.save_snapshot(key, addr, body)
        if keep is None:
            return
        for addr in self.store.snapshot_addresses(key):
            if addr not in keep:
                self.store.drop_snapshot(key, addr)


def handle_command(chat_id: str, raw: str, *, is_admin: bool,
                   env: Optional[Mapping[str, str]] = None, fetch=None, now_ms=None,
                   claw_read=None) -> list[str]:
    """One ``/hlwatch`` turn. Returns the texts to send. Does not sign."""
    src = env if env is not None else os.environ
    if not is_admin and not flag_on(src):
        return [copy.NOT_FOUND]
    if not chat_id:
        return [copy.NO_CHAT]
    service = service_for(src, fetch=fetch, now_ms=now_ms, claw_read=claw_read)
    if service is None:
        return [copy.NOT_AVAILABLE]
    parts = (raw or "").split()
    head = parts[0].lower() if parts else ""
    try:
        if head == "stop" and len(parts) == 1:
            return [service.hl_forget(chat_id)]
        if head == "now" and len(parts) == 1:
            texts, pending, keep, baseline = service.hl_prepare(chat_id)
            service.hl_commit(chat_id, pending, keep)
            if texts:
                return texts
            if baseline:
                return [copy.BASELINE_QUIET]
            key = service.hl_watch_key(chat_id)
            if key and service.store.listed_addresses(key):
                return [copy.NO_CHANGE]
            return [service.hl_status(chat_id)]
        if not raw.strip():
            return [service.hl_status(chat_id)]
        if len(parts) != 1:
            return [INVALID_ADDRESS]
        return [service.hl_register(chat_id, parts[0])]
    except Exception as exc:
        return [type(exc).__name__]


def service_for(env: Optional[Mapping[str, str]] = None, *, fetch=None, now_ms=None,
                claw_read=None) -> Optional[HlWatch]:
    """The process watch when ``env`` is omitted, else a watch for that mapping.

    A mapping is not cached: tests pass a store path and must not share the
    bot's watch. The process watch is cached so a command and the poll share
    the in-memory delivery map.
    """
    global _SERVICE
    src = env if env is not None else os.environ
    salt = salt_of(src)
    if salt is None:
        return None
    path = store_path(src)
    if env is not None or fetch is not None or now_ms is not None or claw_read is not None:
        return HlWatch(
            salt=salt, store=HlStore(path), env=src, fetch=fetch,
            now_ms=now_ms, claw_read=claw_read,
        )
    if _SERVICE is not None and _SERVICE.salt == salt and _SERVICE.store.path == path:
        return _SERVICE
    _SERVICE = HlWatch(salt=salt, store=HlStore(path), env=src)
    return _SERVICE


def forget_telegram(chat_id: str, env: Optional[Mapping[str, str]] = None) -> bool:
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
    removed = HlStore(path).forget(key)
    global _SERVICE
    if _SERVICE is not None:
        _SERVICE.delivery.pop(key, None)
    return removed


def bound_chats(service: HlWatch) -> list[str]:
    """Chat ids to poll: this watch's binds, plus Claw Watch binds when present."""
    chats = list(dict.fromkeys(service.delivery.values()))
    try:
        from bot.drain_watch.service import get_service
    except ImportError:
        return chats
    except Exception:
        return chats
    try:
        other = get_service()
    except Exception:
        return chats
    if other is None:
        return chats
    for chat in list(getattr(other, "delivery", {}).values()):
        if chat and str(chat) not in chats:
            chats.append(str(chat))
    return chats


async def dispatch(service: HlWatch, send: Send) -> int:
    sent = 0
    for chat in bound_chats(service):
        try:
            texts, pending, keep, _baseline = await asyncio.to_thread(service.hl_prepare, chat)
        except Exception as exc:
            log.warning("hl watch prepare failed: %s", type(exc).__name__)
            continue
        try:
            for text in texts:
                await send(chat, text)
        except Exception as exc:
            log.warning("hl watch send failed: %s", type(exc).__name__)
            continue
        try:
            await asyncio.to_thread(service.hl_commit, chat, pending, keep)
        except Exception as exc:
            log.warning("hl watch commit failed: %s", type(exc).__name__)
            continue
        sent += 1
    return sent


def start_poll(bot) -> Optional[asyncio.Task]:
    """A background poll, or None when the flag is off or the salt is unset."""
    if not flag_on():
        return None
    service = service_for()
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
                log.warning("hl watch poll failed: %s", type(exc).__name__)

    return asyncio.create_task(_loop())
