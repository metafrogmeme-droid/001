"""The AudaceFi outbox: every signal is on disk before it is sent.

SAVE BEFORE SEND. A signal is appended (and fsync'd) as ``saved`` before the
publisher sends it, then a second line records ``sent`` or ``send_failed``.
So a crash between the two leaves a record that says a signal was built and
not confirmed sent -- never a signal that left the process with no record.

STABLE IDS AND SEQUENCES. The sequence is one more than the highest on
record, rebuilt from the file on load, and never restarts. A signal whose id
is already on record is handed back as it was saved -- same sequence, same
body -- so a replay of the same idea sends nothing new.

UNREADABLE IS NEVER EMPTY. A torn LAST line is the one crash this format
expects (an append cut off mid-write); nothing was sent for it, because the
send follows the save, so it is skipped and said. Any other line that will
not parse makes the whole outbox unreadable, and the publisher refuses to
publish rather than start the sequence again from zero.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bot.integrations.audacefi.signal_map import AudaceDraft, encode_audacefi_signal
from bot.utils.durable_io import fsync_dir
from bot.utils.paths import state_path

log = logging.getLogger("runeclaw.audacefi")

#: Where the outbox lives unless a caller names another file: anchored to
#: the repository, not to whoever launched the process.
DEFAULT_OUTBOX = state_path("data/audacefi/outbox.jsonl")

SAVED = "saved"
SENT = "sent"
SEND_FAILED = "send_failed"
EXPIRED_UNSENT = "expired_unsent"


class AudaceOutboxUnreadable(RuntimeError):
    """The outbox file has a line that is not the torn tail of a crash."""


@dataclass(frozen=True)
class OutboxEntry:
    """One saved signal, and what became of it so far."""

    id: str
    sequence: int
    body: bytes
    expires_at_ms: int
    state: str


class AudaceOutbox:
    """Append-only JSONL record of every signal built and every send attempt."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_OUTBOX
        self._lock = threading.Lock()
        self._entries: dict[str, OutboxEntry] = {}
        self._last_sequence = 0
        # Ids this process has already handed to a sender, so a replay while a
        # send is in flight does not send the same signal twice.
        self._claimed: set[str] = set()
        self._load()

    # ── reading ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        lines = raw.split(b"\n")
        torn_tail = not raw.endswith(b"\n")
        for n, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("not an object")
                self._apply(row)
            except (ValueError, KeyError, TypeError) as exc:
                if torn_tail and n == len(lines):
                    log.warning("AudaceFi outbox: skipped a torn last line (%s); "
                                "nothing was sent for it", type(exc).__name__)
                    continue
                raise AudaceOutboxUnreadable(
                    f"{self.path.name} line {n} is unreadable ({type(exc).__name__})") from exc

    def _apply(self, row: dict) -> None:
        sid = str(row["id"])
        state = str(row["state"])
        if state == SAVED:
            seq = int(row["sequence"])
            if seq <= 0 or isinstance(row["sequence"], bool):
                raise ValueError("sequence is not a positive integer")
            self._entries[sid] = OutboxEntry(
                id=sid, sequence=seq, body=str(row["body"]).encode("utf-8"),
                expires_at_ms=int(row["expiresAtMs"]), state=SAVED)
            self._last_sequence = max(self._last_sequence, seq)
            return
        prior = self._entries.get(sid)
        if prior is None or state not in (SENT, SEND_FAILED, EXPIRED_UNSENT):
            raise ValueError(f"result line {state!r} for a signal never saved")
        self._entries[sid] = OutboxEntry(prior.id, prior.sequence, prior.body,
                                         prior.expires_at_ms, state)

    # ── writing ──────────────────────────────────────────────────────────

    def _append(self, row: dict) -> None:
        fresh = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        if fresh:
            fsync_dir(str(self.path.parent))

    def save_audacefi_draft(self, draft: AudaceDraft) -> tuple[OutboxEntry, bool]:
        """Save ``draft`` under the next sequence, before anything sends it.

        Returns ``(entry, newly_saved)``. A draft whose id is already on record
        is not saved again: the stored entry comes back with ``False``.
        """
        with self._lock:
            known = self._entries.get(draft.id)
            if known is not None:
                return known, False
            seq = self._last_sequence + 1
            body = encode_audacefi_signal(draft, seq)
            self._append({"v": 1, "id": draft.id, "sequence": seq, "state": SAVED,
                          "body": body.decode("utf-8"), "expiresAtMs": draft.expiresAtMs,
                          "at_ms": int(time.time() * 1000)})
            entry = OutboxEntry(draft.id, seq, body, draft.expiresAtMs, SAVED)
            self._entries[draft.id] = entry
            self._last_sequence = seq
            return entry, True

    def claim_audacefi_send(self, signal_id: str) -> bool:
        """True the first time this process may send ``signal_id``.

        A failed attempt releases the claim (`record_audacefi_result`), so a
        later replay may retry it until it expires.
        """
        with self._lock:
            if signal_id in self._claimed:
                return False
            self._claimed.add(signal_id)
            return True

    def record_audacefi_result(self, signal_id: str, state: str, *,
                               status: Optional[int] = None,
                               error: Optional[str] = None) -> None:
        """Append what became of a saved signal: sent, failed, or expired unsent."""
        if state not in (SENT, SEND_FAILED, EXPIRED_UNSENT):
            raise ValueError(f"unknown outbox state {state!r}")
        with self._lock:
            prior = self._entries.get(signal_id)
            if prior is None:
                raise KeyError(signal_id)
            row: dict = {"v": 1, "id": signal_id, "state": state,
                         "at_ms": int(time.time() * 1000)}
            if status is not None:
                row["status"] = int(status)
            if error is not None:
                row["error"] = error
            self._append(row)
            self._entries[signal_id] = OutboxEntry(prior.id, prior.sequence, prior.body,
                                                   prior.expires_at_ms, state)
            if state == SEND_FAILED:
                self._claimed.discard(signal_id)
