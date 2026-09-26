"""
Tamper-evident hash-chained audit log for the RUNECLAW trading bot.

Each log entry is SHA-256 hash-chained to the previous one. Any edit,
deletion, reorder, or insertion breaks the chain and is caught by verify().
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Optional
from bot.utils.paths import state_path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DecisionRecord:
    """Structured record of a single trading decision."""

    decision_id: str
    symbol: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    idea: Optional[dict] = None
    risk: Optional[dict] = None
    macro: Optional[dict] = None
    compliance: Optional[dict] = None
    outcome: str = "REJECTED"
    is_paper: bool = True

GENESIS_HASH = "0" * 64

#: How much to read per step when walking backwards from the end of the log.
_TAIL_CHUNK = 64 * 1024

#: The entry an append writes, ahead of its own, when the log ends in lines
#: that are not entries.
#:
#: A crash, a kill or a full disk can cut an append short, and the next one
#: used to parse that fragment as its predecessor: two good entries plus
#: '{"sequence": 2, "event_type": "DECI' made EVERY later append raise
#: JSONDecodeError, and one of those appends is the decision sealed after a
#: live fill. The chain is tamper-EVIDENT, so the fragment is never removed or
#: rewritten: the next append links to the last whole entry, records each
#: fragment's sha256 and length in this entry, and `verify()` names the
#: fragment once instead of reporting every entry after it as out of place.
TORN_TAIL_EVENT = "CHAIN_TORN_TAIL"

#: The most non-entry lines an append will resume across. `json.dumps` writes
#: no newline inside an entry, so one cut-short write leaves ONE partial line,
#: and two when the marker's own write is cut too. More than a handful is not a
#: crash, and linking across it would be a guess about where the chain was.
_TORN_SEARCH_LINES = 4


class AuditChainUnreadable(RuntimeError):
    """The end of the log holds no entry the next one could link to."""


def _as_entry(line: str) -> Optional[dict]:
    """*line* as an entry, or None when it is not a whole one.

    `null`, a list, or an object with no integer sequence or string hash all
    parse, and none of them is an entry: reading `["entry_hash"]` off them was
    a TypeError or KeyError at the same place the fragment's ValueError was.
    """
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    seq, entry_hash = data.get("sequence"), data.get("entry_hash")
    if isinstance(seq, bool) or not isinstance(seq, int) or not isinstance(entry_hash, str):
        return None
    return data


def _fragment_record(line: str) -> dict:
    """What a torn-tail entry records of one fragment: enough to identify the
    exact bytes, and none of their content."""
    raw = line.strip().encode("utf-8")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _tail_lines(path: Path, n: int) -> list[str]:
    """The last *n* non-empty lines, read from the END of the file.

    WHY THIS EXISTS. `append()` needs the previous hash and sequence, and it
    used to get them by iterating the whole log forward — so the cost of
    writing one entry was proportional to everything written before it, on a
    file that nothing rotates. Measured on the real class:

        chain length      per append
                 100         0.11 ms
                 500         0.21 ms
               2 000         0.66 ms
               6 000         1.86 ms

    Roughly linear, and a live close fires several appends: 12 ms at 6k
    entries, ~10x that at 60k, forever. It also made building a chain
    quadratic — seeding 20 000 entries did not finish in two minutes.

    NO CACHE, DELIBERATELY. Caching the tail in memory would be faster still
    and would be WRONG here: `docker-compose.yml` runs the bot alongside two
    api_bridge workers over one shared `data/`, so another process can append
    between our calls. A stale cached `prev_hash` would fork the chain —
    manufacturing exactly the discontinuity the chain exists to detect, which
    is worse than being slow. Seeking from the end re-reads reality every
    time and is O(1) in chain length regardless of who else is writing.

    The slice always starts just after a newline, so it never begins
    mid-character: `\\n` is a single ASCII byte, which makes the cut point a
    valid UTF-8 boundary by construction rather than by luck.

    On the decode being strict: with the cut in place it can never fail, and a
    mutation pass confirmed that `errors="ignore"` is indistinguishable here —
    the only bytes it could ever mangle are in the leading fragment, which the
    final `[-n:]` discards. Strict is kept because if the cut is ever removed
    the difference is a loud exception versus silent corruption, and the loud
    one is what the test for that case relies on.
    """
    if n <= 0 or not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        buf = b""
        # One more newline than lines wanted, so the first (possibly partial)
        # line in the buffer can be discarded.
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(_TAIL_CHUNK, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
        if pos > 0:
            cut = buf.find(b"\n")
            buf = buf[cut + 1:] if cut >= 0 else b""
    text = buf.decode("utf-8")
    return [ln for ln in text.splitlines() if ln.strip()][-n:]


def _compute_hash(
    sequence: int,
    event_type: str,
    payload: dict,
    actor: str,
    timestamp: str,
    prev_hash: str,
) -> str:
    """Compute the SHA-256 digest that seals an entry."""
    canonical = (
        f"{sequence}|{event_type}|{json.dumps(payload, sort_keys=True)}"
        f"|{actor}|{timestamp}|{prev_hash}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditEntry:
    """A single link in the hash chain."""

    sequence: int
    event_type: str
    payload: dict
    actor: str
    timestamp: str
    prev_hash: str
    entry_hash: str

    # -- serialisation helpers ------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "payload": self.payload,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AuditEntry:
        return cls(
            sequence=d["sequence"],
            event_type=d["event_type"],
            payload=d["payload"],
            actor=d["actor"],
            timestamp=d["timestamp"],
            prev_hash=d["prev_hash"],
            entry_hash=d["entry_hash"],
        )


def _link(event_type: str, payload: dict, actor: str, prev_hash: str,
          sequence: int) -> AuditEntry:
    """A new entry sealed onto *prev_hash* at *sequence*."""
    ts = datetime.now(timezone.utc).isoformat()
    return AuditEntry(
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        actor=actor,
        timestamp=ts,
        prev_hash=prev_hash,
        entry_hash=_compute_hash(sequence, event_type, payload, actor, ts, prev_hash),
    )


def _acknowledges(entry: dict, run: list[str], prev_hash: Optional[str],
                  expected_seq: int) -> bool:
    """Whether *entry* is the torn-tail record of exactly this *run* of
    non-entry lines: it links to the entry before them, takes the sequence
    they did not, and names each one's bytes."""
    if entry.get("event_type") != TORN_TAIL_EVENT:
        return False
    if entry.get("sequence") != expected_seq or entry.get("prev_hash") != prev_hash:
        return False
    fragments = (entry.get("payload") or {}).get("fragments")
    return fragments == [_fragment_record(ln) for ln in run]

# ---------------------------------------------------------------------------
# AuditChain
# ---------------------------------------------------------------------------

class AuditChain:
    """Append-only, hash-chained audit log stored as JSONL."""

    def __init__(self, path: str = "logs/audit_chain.jsonl") -> None:
        self._path = state_path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries_since_sign = 0
        self._auto_sign_interval = 50  # F-12 FIX: auto-sign every N entries

    # -- public API -----------------------------------------------------------

    def append(
        self,
        event_type: str,
        payload: dict,
        actor: str = "system",
    ) -> AuditEntry:
        """Compute hash chain link, persist to JSONL, and return the entry."""
        with self._lock:
            prev_hash, next_seq, torn, ends_open = self._tail_state()
            links: list[AuditEntry] = []
            if torn:
                marker = _link(TORN_TAIL_EVENT, {
                    "fragments": [_fragment_record(ln) for ln in torn],
                    "resumed_after_sequence": next_seq - 1 if next_seq else None,
                }, "system", prev_hash, next_seq)
                links.append(marker)
                prev_hash, next_seq = marker.entry_hash, next_seq + 1
            entry = _link(event_type, payload, actor, prev_hash, next_seq)
            links.append(entry)
            text = "".join(json.dumps(e.to_dict(), sort_keys=False) + "\n" for e in links)
            # A write cut after a whole entry but before its newline leaves an
            # entry the next append would be written ONTO, and the append after
            # that would parse two entries as one line. Start on a fresh line.
            if ends_open:
                text = "\n" + text
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(text)
            if torn:
                _log.error(
                    "AUDIT CHAIN: %d line(s) at the end of %s are not entries "
                    "(a write cut short). They are left in place; %s at "
                    "sequence %d records them and the chain resumes from the "
                    "last whole entry.",
                    len(torn), self._path, TORN_TAIL_EVENT, links[0].sequence)
            # F-12 FIX: auto-sign every N entries to anchor chain integrity
            self._entries_since_sign += 1
            if self._entries_since_sign >= self._auto_sign_interval:
                try:
                    self.sign_latest_batch(batch_size=self._auto_sign_interval)
                    self._entries_since_sign = 0
                except Exception as e:
                    _log.debug("Auto-sign best-effort failed: %s", e)
            return entry

    def seal_decision(self, record: DecisionRecord) -> AuditEntry:
        """Convenience wrapper: persist a DecisionRecord as a DECISION event."""
        return self.append(
            event_type="DECISION",
            payload=asdict(record),
            actor="system",
        )

    def get_entries(self, limit: int = 100) -> list[AuditEntry]:
        """Return the entries among the last *limit* lines of the log (fewer
        than *limit* when a torn line falls in that window)."""
        # Also read from the end: this parsed every line of the log as JSON
        # and then threw all but the last `limit` away, and `append()` reaches
        # it every 50th entry through sign_latest_batch — so the periodic
        # auto-sign was a second, more expensive full scan on the same hot
        # path.
        #
        # A line that is not an entry (a write cut short) is not returned: it
        # is not one, and parsing it raised out of every reader of the chain.
        # `verify()` is where it is reported.
        entries = (_as_entry(ln) for ln in _tail_lines(self._path, limit))
        return [AuditEntry.from_dict(d) for d in entries if d is not None]

    def get_chain_length(self) -> int:
        """Return the total number of entries in the log."""
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    # -- verification ---------------------------------------------------------

    @staticmethod
    def verify(path: str) -> tuple[bool, list[str]]:
        """Read the entire log and re-derive every hash.

        Returns ``(True, [])`` when the chain is intact, or
        ``(False, [problem, ...])`` listing every inconsistency found.
        """
        file_path = Path(path)
        if not file_path.exists():
            return True, []

        problems: list[str] = []
        prev_hash: Optional[str] = GENESIS_HASH
        expected_seq = 0
        # Lines that are not entries, held until the next entry says whether
        # it is their torn-tail record.
        run: list[tuple[int, str]] = []

        def _unacknowledged() -> None:
            # The reading a non-entry line has always had: a problem, and the
            # sequence counts it as the entry it replaced.
            nonlocal prev_hash, expected_seq
            for n, text in run:
                try:
                    json.loads(text)
                    why = "not an entry"
                except ValueError as exc:
                    why = f"malformed JSON ({exc})"
                problems.append(f"line {n}: {why}")
                # Cannot continue chain verification after corrupt line
                prev_hash = None
                expected_seq += 1
            run.clear()

        with file_path.open("r", encoding="utf-8") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                # --- parse ---------------------------------------------------
                data = _as_entry(raw_line)
                if data is None:
                    run.append((line_no, raw_line))
                    continue
                if run:
                    if _acknowledges(data, [t for _, t in run], prev_hash, expected_seq):
                        # A write cut short, recorded by the entry after it.
                        # Still a line in the log that is not an entry, so it
                        # is reported -- once, by name -- and the linkage is
                        # checked from the entry before it.
                        for n, _ in run:
                            problems.append(
                                f"line {n}: not an entry (a write cut short); "
                                f"recorded by the {TORN_TAIL_EVENT} entry at "
                                f"sequence {expected_seq}"
                            )
                        run.clear()
                    else:
                        _unacknowledged()

                # --- sequence continuity -------------------------------------
                seq = data.get("sequence")
                if seq != expected_seq:
                    problems.append(
                        f"line {line_no}: expected sequence {expected_seq}, "
                        f"got {seq}"
                    )

                # --- prev_hash linkage ---------------------------------------
                recorded_prev = data.get("prev_hash", "")
                if prev_hash is not None and recorded_prev != prev_hash:
                    problems.append(
                        f"line {line_no}: prev_hash mismatch "
                        f"(expected {prev_hash}, got {recorded_prev})"
                    )

                # --- entry_hash integrity ------------------------------------
                recomputed = _compute_hash(
                    data.get("sequence", 0),
                    data.get("event_type", ""),
                    data.get("payload", {}),
                    data.get("actor", ""),
                    data.get("timestamp", ""),
                    data.get("prev_hash", ""),
                )
                recorded_hash = data.get("entry_hash", "")
                if recomputed != recorded_hash:
                    problems.append(
                        f"line {line_no}: entry_hash mismatch "
                        f"(expected {recomputed}, got {recorded_hash})"
                    )

                prev_hash = recorded_hash
                expected_seq += 1

        # A torn tail no append has resumed from yet.
        _unacknowledged()
        return (len(problems) == 0, problems)

    # -- attestation ----------------------------------------------------------

    def sign_latest_batch(self, batch_size: int = 10) -> "AttestationResult":  # noqa: F821
        """Sign the latest batch of entries with Ed25519.

        Returns AttestationResult with signature and Merkle root.
        """
        from bot.utils.attestation import AttestationEngine, AttestationResult

        engine = AttestationEngine()
        entries = self.get_entries(limit=batch_size)
        if not entries:
            return AttestationResult(valid=False, error="No entries to sign")

        hashes = [e.entry_hash for e in entries]
        return engine.sign_batch(hashes)

    # -- internals ------------------------------------------------------------

    def _tail_state(self) -> tuple[str, int, list[str], bool]:
        """(prev_hash, next_sequence, torn, ends_open) from the end of the log.

        ``torn`` is the lines after the last whole entry, oldest first -- empty
        on a healthy log. ``ends_open`` is whether the file's last byte is not
        a newline, so the next write must start one.
        """
        if not self._path.exists() or self._path.stat().st_size == 0:
            return GENESIS_HASH, 0, [], False

        # The comment here used to say "read last non-empty line efficiently"
        # above a forward scan of the entire file. It was the single hottest
        # cost in the whole append path — see _tail_lines. One step back
        # further than the limit, so a log with more non-entries at its end
        # than that is told apart from one that is nothing but them.
        lines = _tail_lines(self._path, _TORN_SEARCH_LINES + 1)
        with self._path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            ends_open = fh.read(1) != b"\n"
        torn: list[str] = []
        for line in reversed(lines):
            data = _as_entry(line)
            if data is not None:
                torn.reverse()
                return data["entry_hash"], data["sequence"] + 1, torn, ends_open
            torn.append(line)
        if len(lines) <= _TORN_SEARCH_LINES:
            # The whole log, and none of it an entry: the chain has none yet.
            torn.reverse()
            return GENESIS_HASH, 0, torn, ends_open
        raise AuditChainUnreadable(
            f"{self._path}: the last {len(lines)} lines are not entries, more "
            f"than a write cut short can leave; refusing to guess what the "
            f"next entry should link to")
