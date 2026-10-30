"""Owner approval queue for the paper sleeve's channel call.

A rebalance that changes the book is written here. Nothing is handed to a
marketing group until an admin approves that one item (``/sleeve approve``).
A rejection never sends. A pending item older than ``EXPIRE_AFTER`` expires
and is not sent.

Delivery is per group. A group already in ``sent`` is not posted again. A
group whose send raised is left out of ``sent`` and tried on a later pass.
Two passes at once claim a group before the send, so they cannot both post
it. The queue lives beside the paper record, under the bot's state dir.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from bot.compat import UTC
from bot.utils.json_store import (
    READ,
    UNREADABLE,
    StoreUnreadable,
    read_json_store,
    update_json_store,
)
from bot.utils.logger import audit, system_log

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
EXPIRE_AFTER = timedelta(days=7)
#: Longer than the post-tick maintenance cap (120s). A claim older than this
#: is a pass that died without releasing it; a live pass is not stolen.
STALE_CLAIM = timedelta(seconds=180)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^sleeve:\d{4}-\d{2}-\d{2}$")

USAGE = (
    "<code>/sleeve calls</code> — the approval queue\n"
    "<code>/sleeve approve ID</code> — post that one call\n"
    "<code>/sleeve reject ID</code> — drop it; nothing is posted"
)


def queue_file() -> Path:
    """``<state dir>/sleeve/channel_call_queue.json``, beside the paper record."""
    from bot.sleeve.paper import state_file
    return state_file().parent / "channel_call_queue.json"


def posts_allowed() -> bool:
    """Both sleeve switches, the one reading of each. Off posts nothing."""
    from bot.config import CONFIG
    from bot.sleeve.paper import sleeve_paper_enabled
    return bool(sleeve_paper_enabled()
                and getattr(CONFIG, "sleeve_channel_calls_enabled", False))


def normalize_id(raw: str) -> str:
    """``sleeve:YYYY-MM-DD`` or the bare day. ``""`` when it is neither."""
    text = (raw or "").strip()
    if _ID.match(text):
        return text
    if _DAY.match(text):
        return f"sleeve:{text}"
    return ""


def _check(data: dict) -> str:
    if data.get("version") != 1:
        return "version is not 1"
    if not isinstance(data.get("items"), list):
        return "items is not a list"
    return ""


def _ensure(data: dict) -> None:
    data["version"] = 1
    if not isinstance(data.get("items"), list):
        data["items"] = []


def _find(data: dict, item_id: str) -> Optional[dict]:
    for it in data.get("items") or []:
        if isinstance(it, dict) and it.get("id") == item_id:
            return it
    return None


def _when(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _group_id(value: Any) -> Optional[int]:
    """A Telegram chat id. ``bool`` is an ``int`` and is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _as_str(value: Any) -> str:
    """A string field, or ``""`` when it is missing or some other type."""
    if isinstance(value, str):
        return str(value)
    return ""


def _id_set(raw: Any) -> set[int]:
    if not isinstance(raw, list):
        return set()
    return {g for g in (_group_id(v) for v in raw) if g is not None}


def _read():
    return read_json_store(queue_file(), check=_check)


def _write(change: Callable[[dict], Any]) -> None:
    update_json_store(queue_file(), change, check=_check)


def enqueue_current(*, now: Optional[datetime] = None) -> bool:
    """Queue the current call. True when that call is in the queue afterwards.

    Idempotent: a day already queued, in any state, is not added again and
    its text is not replaced. Off, or a record that is not the newest closed
    day, writes nothing.
    """
    if not posts_allowed():
        return False
    from bot.sleeve.paper import channel_call_text, load_record, record_is_current
    if not record_is_current():
        return False
    record = load_record()
    if record.state != READ:
        return False
    text = channel_call_text(record.data)
    if not text:
        return False
    rebs = [r for r in (record.data.get("rebalances") or []) if isinstance(r, dict)]
    day = rebs[-1].get("day") if rebs else None
    if not isinstance(day, str) or not _DAY.match(day):
        system_log.warning("Sleeve call has no day; not queued")
        return False
    now = now or datetime.now(UTC)
    item_id = f"sleeve:{day}"
    present = False

    def change(data: dict) -> bool:
        nonlocal present
        _ensure(data)
        if _find(data, item_id) is not None:
            present = True
            return False
        data["items"].append({
            "id": item_id,
            "day": day,
            "text": text,
            "status": PENDING,
            "created_at": now.isoformat(),
            "sent": [],
            "claimed": {},
            "failures": {},
        })
        present = True
        return True

    _write(change)
    return present


def expire_stale(*, now: Optional[datetime] = None) -> int:
    """Expire pending items older than ``EXPIRE_AFTER``. Approved ones stay,
    so a send that failed can still be retried. Returns how many expired."""
    now = now or datetime.now(UTC)
    expired = 0

    def change(data: dict) -> bool:
        nonlocal expired
        items = data.get("items")
        if not isinstance(items, list):
            return False
        for it in items:
            if not isinstance(it, dict) or it.get("status") != PENDING:
                continue
            created = _when(it.get("created_at"))
            if created is None or now - created <= EXPIRE_AFTER:
                continue
            it["status"] = EXPIRED
            it["expired_at"] = now.isoformat()
            expired += 1
        return expired > 0

    reading = _read()
    if reading.state != READ:
        return 0
    _write(change)
    return expired


def _set_status(item_id: str, status: str, *, now: datetime,
                allow_from: tuple[str, ...]) -> str:
    """Move ``item_id`` to ``status``. The word says what the queue held."""
    found = "missing"

    def change(data: dict) -> bool:
        nonlocal found
        it = _find(data, item_id)
        if it is None:
            found = "missing"
            return False
        current = it.get("status")
        if current == status:
            found = "already"
            return False
        if current not in allow_from:
            found = current if isinstance(current, str) else "missing"
            return False
        it["status"] = status
        it["decided_at"] = now.isoformat()
        found = status
        return True

    _write(change)
    return found


def approve_item(item_id: str, *, now: Optional[datetime] = None) -> str:
    """Pending → approved. Does not send. ``already`` when it already is."""
    if not _ID.match(item_id):
        return "bad_id"
    return _set_status(item_id, APPROVED, now=now or datetime.now(UTC),
                       allow_from=(PENDING,))


def reject_item(item_id: str, *, now: Optional[datetime] = None) -> str:
    """Pending or approved → rejected. Already-sent groups are not unsent;
    nothing further is sent."""
    if not _ID.match(item_id):
        return "bad_id"
    return _set_status(item_id, REJECTED, now=now or datetime.now(UTC),
                       allow_from=(PENDING, APPROVED))


def claim_group(item_id: str, gid: int, *, now: Optional[datetime] = None) -> bool:
    """Own the send to ``gid``. False when it is sent, freshly claimed, or
    the item is not approved — the caller must not post."""
    now = now or datetime.now(UTC)
    owned = False

    def change(data: dict) -> bool:
        nonlocal owned
        it = _find(data, item_id)
        if not isinstance(it, dict) or it.get("status") != APPROVED:
            return False
        if gid in _id_set(it.get("sent")):
            return False
        claimed = it.get("claimed")
        if not isinstance(claimed, dict):
            claimed = {}
            it["claimed"] = claimed
        key = str(gid)
        existing = _when(claimed.get(key))
        if existing is not None and now - existing < STALE_CLAIM:
            return False
        claimed[key] = now.isoformat()
        owned = True
        return True

    try:
        _write(change)
    except StoreUnreadable:
        return False
    return owned


def mark_sent(item_id: str, gid: int) -> None:
    """Record a send that returned. A later pass skips this group."""
    def change(data: dict) -> bool:
        it = _find(data, item_id)
        if not isinstance(it, dict):
            return False
        changed = False
        sent = it.get("sent")
        if not isinstance(sent, list):
            sent = []
            it["sent"] = sent
        if gid not in sent:
            sent.append(gid)
            changed = True
        claimed = it.get("claimed")
        if isinstance(claimed, dict) and str(gid) in claimed:
            claimed.pop(str(gid), None)
            changed = True
        fails = it.get("failures")
        if isinstance(fails, dict) and str(gid) in fails:
            fails.pop(str(gid), None)
            changed = True
        return changed

    _write(change)


def release_claim(item_id: str, gid: int, exc_name: str = "") -> None:
    """The send did not stick. ``exc_name`` is the exception class, or empty
    when the pass was cancelled before a result."""
    def change(data: dict) -> bool:
        it = _find(data, item_id)
        if not isinstance(it, dict):
            return False
        changed = False
        claimed = it.get("claimed")
        if isinstance(claimed, dict) and str(gid) in claimed:
            claimed.pop(str(gid), None)
            changed = True
        if exc_name:
            fails = it.get("failures")
            if not isinstance(fails, dict):
                fails = {}
                it["failures"] = fails
            if fails.get(str(gid)) != exc_name:
                fails[str(gid)] = exc_name
                changed = True
        return changed

    _write(change)


def _empty_report(held: str = "") -> dict:
    return {"sent": [], "failed": {}, "held": held, "skipped_claimed": 0}


async def deliver_approved(poster, *, only_id: Optional[str] = None) -> dict:
    """Send approved items through ``poster``. Pending, rejected and expired
    items are not sent.

    ``poster(text, skip=, claim=, on_sent=, on_fail=)`` posts ``text`` to each
    marketing group not in ``skip``. ``claim(gid)`` false means do not post
    that group. ``on_sent(gid)`` runs after a send returns, before the next
    group. ``on_fail(gid, exc_class)`` runs when it raises; an empty class
    means the pass was cancelled.
    """
    if not posts_allowed():
        return _empty_report("switch_off")
    if poster is None:
        return _empty_report("no_poster")
    reading = _read()
    if reading.state == UNREADABLE:
        raise StoreUnreadable(queue_file(), reading.detail)
    if reading.state != READ:
        return _empty_report("empty")
    sent_all: list[int] = []
    failed_all: dict[int, str] = {}
    skipped = 0
    held = ""
    for it in reading.data.get("items") or []:
        if not isinstance(it, dict) or it.get("status") != APPROVED:
            continue
        item_id = _as_str(it.get("id"))
        text = _as_str(it.get("text"))
        if not item_id or not text:
            continue
        if only_id is not None and item_id != only_id:
            continue
        already = _id_set(it.get("sent"))

        def _claim(gid: int, _iid: str = item_id) -> bool:
            return claim_group(_iid, gid)

        def _sent(gid: int, _iid: str = item_id) -> None:
            try:
                mark_sent(_iid, gid)
            except StoreUnreadable as exc:
                system_log.warning(
                    "Sleeve call queue could not be read (%s)", exc.detail)

        def _fail(gid: int, name: str, _iid: str = item_id) -> None:
            try:
                release_claim(_iid, gid, name)
            except StoreUnreadable as exc:
                system_log.warning(
                    "Sleeve call queue could not be read (%s)", exc.detail)

        result = await poster(text, skip=already, claim=_claim, on_sent=_sent, on_fail=_fail)
        if not isinstance(result, dict):
            system_log.warning(
                "Sleeve call delivery reported no per-group result; nothing new marked from it")
            continue
        for gid in result.get("sent") or []:
            g = _group_id(gid)
            if g is None:
                continue
            try:
                mark_sent(item_id, g)
            except StoreUnreadable as exc:
                system_log.warning(
                    "Sleeve call queue could not be read (%s)", exc.detail)
            sent_all.append(g)
        failed = result.get("failed")
        if isinstance(failed, dict):
            for gid, name in failed.items():
                g = _group_id(gid)
                if g is None:
                    continue
                failed_all[g] = name if isinstance(name, str) and name else "Exception"
        raw_held = result.get("held")
        if isinstance(raw_held, str):
            held = str(raw_held)
        extra = result.get("skipped_claimed")
        if isinstance(extra, int) and not isinstance(extra, bool):
            skipped += extra
    return {"sent": sent_all, "failed": failed_all, "held": held, "skipped_claimed": skipped}


async def sync_sleeve_calls(poster) -> bool:
    """Queue the current call and send what an owner has approved.

    True when the current call is in the queue and an approved send did not
    fail for every group it tried. False when the switches are off, there is
    nothing to queue, the queue could not be read, or every attempted send
    failed. A group that failed is not marked sent.
    """
    if not posts_allowed():
        return False
    try:
        expire_stale()
        queued = enqueue_current()
        report = await deliver_approved(poster)
    except StoreUnreadable as exc:
        system_log.warning("Sleeve call queue could not be read (%s)", exc.detail)
        return False
    if report["failed"] and not report["sent"]:
        return False
    return queued


def _classes(failed: dict) -> str:
    names = sorted({n for n in failed.values() if isinstance(n, str) and n})
    return html.escape(", ".join(names))


def _approve_reply(item_id: str, report: dict) -> str:
    shown = html.escape(item_id)
    n = len(report["sent"])
    failed = report["failed"]
    m = len(failed)
    held = report["held"]
    if held == "switch_off":
        return (f"Approved <code>{shown}</code>. The sleeve channel switch is off, "
                "so nothing was posted.")
    if n and not m:
        return (f"Approved <code>{shown}</code>. Posted to {n} group(s). "
                "A group that already has it is not posted again.")
    if n and m:
        return (f"Approved <code>{shown}</code>. Posted to {n} group(s). "
                f"{m} failed ({_classes(failed)}). Those are retried later. "
                "A group that already has it is not posted again.")
    if m and not n:
        return (f"Approved <code>{shown}</code>. Every send failed ({_classes(failed)}). "
                "Nothing was marked posted. They will be retried.")
    if held == "no_groups":
        return (f"Approved <code>{shown}</code>. No marketing group is registered, "
                "so nothing was posted.")
    if held == "disabled":
        return (f"Approved <code>{shown}</code>. The channel forwarder is off, "
                "so nothing was posted.")
    if held == "no_bot":
        return (f"Approved <code>{shown}</code>. The poster is not connected, "
                "so nothing was posted. It will be retried.")
    if held == "already_sent":
        return (f"Already posted <code>{shown}</code> to every current marketing group. "
                "Nothing new was sent.")
    if report["skipped_claimed"]:
        return (f"Approved <code>{shown}</code>. A send is already in progress. "
                "A group that does not get it is retried.")
    return f"Approved <code>{shown}</code>. Nothing was posted."


def _queue_card() -> str:
    reading = _read()
    if reading.state == UNREADABLE:
        return ("The approval queue could not be read "
                f"({html.escape(reading.detail)}). Nothing is shown from it.")
    if reading.state != READ:
        return "No sleeve calls are waiting.\n" + USAGE
    rows = [it for it in reading.data.get("items") or [] if isinstance(it, dict)]
    if not rows:
        return "No sleeve calls are waiting.\n" + USAGE
    lines = ["<b>Sleeve channel calls</b>", ""]
    for it in rows[-10:]:
        status = _as_str(it.get("status")) or "unreadable"
        item_id = _as_str(it.get("id")) or "?"
        sent_n = len(_id_set(it.get("sent")))
        fails = it.get("failures")
        fail_n = len(fails) if isinstance(fails, dict) else 0
        lines.append(f"<code>{html.escape(item_id)}</code> — {html.escape(status)}"
                     f", sent {sent_n}, failed {fail_n}")
        if status == PENDING and isinstance(it.get("text"), str):
            lines.append(it["text"])
            lines.append("")
    lines.append(USAGE)
    return "\n".join(lines)


def _poster_for(forwarder):
    async def _post(text, skip=None, claim=None, on_sent=None, on_fail=None):
        return await forwarder.post_sleeve_call(
            text, skip=skip, claim=claim, on_sent=on_sent, on_fail=on_fail)
    return _post


async def handle_admin(forwarder, args: Iterable[str]) -> str:
    """``/sleeve``'s queue subcommands. The caller has already passed the
    admin gate. Returns the reply. Does not post a call the owner did not
    approve, and a rejection posts nothing."""
    words = [w for w in args if isinstance(w, str)]
    if not words:
        return USAGE
    sub = words[0].lower()
    if sub in ("calls", "queue"):
        try:
            expire_stale()
        except StoreUnreadable as exc:
            return ("The approval queue could not be read "
                    f"({html.escape(exc.detail)}). Nothing is shown from it.")
        return _queue_card()
    if sub not in ("approve", "reject"):
        return USAGE
    if len(words) < 2:
        return USAGE
    item_id = normalize_id(words[1])
    if not item_id:
        return USAGE + "\nThe id is <code>sleeve:YYYY-MM-DD</code>."
    try:
        if sub == "reject":
            code = reject_item(item_id)
            audit(system_log, "Sleeve channel call rejected",
                  action="sleeve_call_reject", result=code,
                  data={"id": item_id})
            shown = html.escape(item_id)
            if code == "rejected":
                return f"Rejected <code>{shown}</code>. Nothing will be posted."
            if code == "already":
                return f"Already rejected <code>{shown}</code>. Nothing will be posted."
            if code == "missing":
                return f"No sleeve call <code>{shown}</code> is in the queue. Nothing was posted."
            return f"<code>{shown}</code> is {html.escape(code)}. Nothing was posted."
        code = approve_item(item_id)
        audit(system_log, "Sleeve channel call approved",
              action="sleeve_call_approve", result=code,
              data={"id": item_id})
        shown = html.escape(item_id)
        if code == "missing":
            return f"No sleeve call <code>{shown}</code> is in the queue. Nothing was posted."
        if code in ("rejected", "expired"):
            return f"<code>{shown}</code> is {code}. Nothing will be posted."
        if code != "approved" and code != "already":
            return f"<code>{shown}</code> is {html.escape(code)}. Nothing was posted."
        report = await deliver_approved(_poster_for(forwarder), only_id=item_id)
    except StoreUnreadable as exc:
        return ("The approval queue could not be read "
                f"({html.escape(exc.detail)}). Nothing was posted.")
    return _approve_reply(item_id, report)
