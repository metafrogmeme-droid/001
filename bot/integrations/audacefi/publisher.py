"""Map, save, then send: the AudaceFi publisher the engine calls.

`RuneClawEngine._publish_engine_ideas` hands over the fresh calls of each scan
(risk-approved, above the confidence floor, not re-offers). That method is a
plain function running inside the event loop, so the send happens on a daemon
thread, as the website's signal sync does; the mapping and the save happen in
the caller, so the outbox already holds a signal before any thread exists to
send it.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable, Optional

from bot.integrations.audacefi.outbox import (
    EXPIRED_UNSENT,
    SAVED,
    SEND_FAILED,
    SENT,
    AudaceOutbox,
    AudaceOutboxUnreadable,
    OutboxEntry,
)
from bot.integrations.audacefi.settings import GATEWAY_PORT, audacefi_dry_run_on
from bot.integrations.audacefi.signal_map import audacefi_draft_for
from bot.integrations.audacefi.transport import post_to_loopback_gateway

log = logging.getLogger("runeclaw.audacefi")

_OUTBOX: Optional[AudaceOutbox] = None
_OUTBOX_LOCK = threading.Lock()


def _default_outbox() -> AudaceOutbox:
    global _OUTBOX
    with _OUTBOX_LOCK:
        if _OUTBOX is None:
            _OUTBOX = AudaceOutbox()
        return _OUTBOX


def _send_one(outbox: AudaceOutbox, entry: OutboxEntry, port: int, clock_ms: int) -> None:
    """Send one saved signal and record what happened. Never raises.

    Expiry is judged on the publisher's clock (``clock_ms``), the one the
    signal's ``expiresAtMs`` was computed from.
    """
    try:
        if clock_ms >= entry.expires_at_ms:
            outbox.record_audacefi_result(entry.id, EXPIRED_UNSENT)
            return
        status = post_to_loopback_gateway(entry.body, port=port)
        outbox.record_audacefi_result(entry.id, SENT if 200 <= status < 300 else SEND_FAILED,
                                      status=status)
    except Exception as exc:
        log.warning("AudaceFi send failed for %s (%s)", entry.id, type(exc).__name__)
        try:
            outbox.record_audacefi_result(entry.id, SEND_FAILED, error=type(exc).__name__)
        except Exception as rec_exc:
            log.warning("AudaceFi outbox could not record the failure (%s)",
                        type(rec_exc).__name__)


def schedule_audacefi_dry_run(ideas: Iterable[Any], *, port: int = GATEWAY_PORT,
                              outbox: Optional[AudaceOutbox] = None,
                              now_ms: Optional[int] = None,
                              send_in_thread: bool = True) -> list[tuple[str, str]]:
    """Publish the SOL calls among ``ideas`` to the loopback gateway, dry run.

    Returns one ``(idea_id, outcome)`` per idea: the signal id it was saved
    under, or the reason it was not. With the flag off it returns ``[]``
    before reading the outbox or opening a socket. A signal already on record
    (a replay) is not sent again unless its last attempt failed and it has
    not expired.
    """
    if not audacefi_dry_run_on():
        return []
    try:
        box = outbox if outbox is not None else _default_outbox()
    except AudaceOutboxUnreadable as exc:
        log.warning("AudaceFi publisher refused: %s", exc)
        return [(str(getattr(i, "id", "")), "outbox unreadable") for i in ideas]
    clock = int(time.time() * 1000) if now_ms is None else int(now_ms)
    outcomes: list[tuple[str, str]] = []
    to_send: list[OutboxEntry] = []
    for idea in ideas:
        idea_id = str(getattr(idea, "id", ""))
        try:
            draft, why = audacefi_draft_for(idea, clock)
            if draft is None:
                outcomes.append((idea_id, why))
                continue
            entry, _new = box.save_audacefi_draft(draft)
        except Exception as exc:
            log.warning("AudaceFi could not save a signal for %s (%s)", idea_id,
                        type(exc).__name__)
            outcomes.append((idea_id, f"not saved ({type(exc).__name__})"))
            continue
        if entry.state in (SAVED, SEND_FAILED) and box.claim_audacefi_send(entry.id):
            to_send.append(entry)
        outcomes.append((idea_id, entry.id))
    for entry in to_send:
        if send_in_thread:
            threading.Thread(target=_send_one, args=(box, entry, port, clock), daemon=True,
                             name=f"audacefi-{entry.id[:8]}").start()
        else:
            _send_one(box, entry, port, clock)
    return outcomes
