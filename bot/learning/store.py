"""RUNECLAW AI Learning — Persistent Data Store.

JSONL-based append-only stores for all learning data.
Every write is atomic (write-to-temp, rename). Every read is validated.
No record may be deleted or overwritten — append only.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import NamedTuple, Optional, Type, TypeVar

from pydantic import BaseModel

from bot.utils.json_store import UNREADABLE, StoreUnreadable, read_json_store, update_json_store

from .models import (
    DecisionMemory,
    HumanFeedback,
    ImprovementProposal,
    MacroEventMemory,
    ModelComparison,
    PromptVersion,
    ReflectionMemory,
    StrategyScorecard,
)

logger = logging.getLogger("runeclaw.learning.store")

T = TypeVar("T", bound=BaseModel)

# Default data directory
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "learning")


class _DecisionKey(NamedTuple):
    """Where one decision row sits in the file, and the fields similar-setup
    selection reads. The row itself is parsed again only when it is picked."""
    offset: int
    length: int
    direction: str
    market_regime: str
    completed: bool


class _DecisionIndex:
    """Similar-setup selection over the decision file, each line parsed ONCE.

    The engine's confidence nudge asks for a symbol's similar setups once per
    trade idea, on the scan lane, and the ask re-read and re-validated the
    whole file: a row per idea the risk gate rejects, never rotated. On 10
    October it held 110,529 rows, and the nudge (which also read the file a
    second time for patterns it threw away) cost 4 s of a stopped lane per
    idea, about 81 s a sweep, inside an analyze phase at 91% of its cap.

    This keeps, per line, only where it sits and the three fields selection
    reads (about 15 MB at that size; the parsed rows were 502 MB), and parses
    in full only the rows it answers. A re-read parses only the lines
    appended since the last one. A file that shrank, was replaced (another
    inode) or was rewritten in place (its first bytes changed) is read from
    the start, and one that is gone is empty. A final line with no newline
    yet is read as the full read read it, and kept only once it is whole.
    Reads are serialized by a lock: the nudge runs in a worker thread.
    """

    #: How much of the file's start is kept to recognise a rewrite in place.
    HEAD_BYTES = 256

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._reset(None)

    def _reset(self, ident) -> None:
        self._ident = ident
        self._head = b""
        self._offset = 0
        self._line_num = 0
        self._all: list[_DecisionKey] = []
        self._by_symbol: dict[str, list[_DecisionKey]] = {}

    def _parse(self, line: bytes, line_num: Optional[int]) -> Optional[DecisionMemory]:
        try:
            return DecisionMemory.model_validate_json(line)
        except Exception as e:
            if line_num is not None:
                logger.warning("Skipping corrupt line %d in %s: %s", line_num, self._path, e)
            return None

    @staticmethod
    def _key(rec: DecisionMemory, offset: int, length: int) -> _DecisionKey:
        return _DecisionKey(offset, length, sys.intern(str(rec.direction)),
                            sys.intern(str(rec.market_regime)), rec.pnl_result is not None)

    def similar(self, symbol: str, market_regime: str, direction: str,
                window: int, limit: int) -> list[DecisionMemory]:
        """The last ``limit`` completed rows matching ``market_regime`` and
        ``direction`` (empty = any) among the last ``window`` rows of
        ``symbol`` (empty = every symbol): `get_decisions(symbol, window)`
        filtered, without parsing the file each time."""
        with self._lock:
            try:
                with open(self._path, "rb") as f:
                    st = os.fstat(f.fileno())
                    ident = (st.st_dev, st.st_ino)
                    head = f.read(min(self.HEAD_BYTES, st.st_size))
                    if (ident != self._ident or st.st_size < self._offset
                            or head[:len(self._head)] != self._head):
                        self._reset(ident)
                    if not self._head:
                        self._head = head
                    # One line at a time, so the cold read of a large file
                    # holds one line and the index, never the file.
                    f.seek(self._offset)
                    pos, tail = self._offset, b""
                    for raw in f:
                        if not raw.endswith(b"\n"):
                            tail = raw.strip()      # still being written
                            break
                        self._line_num += 1
                        at, pos = pos, pos + len(raw)
                        body = raw.strip()
                        rec = self._parse(body, self._line_num) if body else None
                        if rec is None:
                            continue
                        key = self._key(rec, at, len(raw))
                        self._all.append(key)
                        self._by_symbol.setdefault(rec.symbol, []).append(key)
                    self._offset = pos
                    keys = list(self._by_symbol.get(symbol, []) if symbol else self._all)
                    tail_rec = self._parse(tail, None) if tail else None
                    if tail_rec is not None and (not symbol or tail_rec.symbol == symbol):
                        keys.append(self._key(tail_rec, -1, 0))
                    picked = [k for k in keys[-window:]
                              if (not market_regime or k.market_regime == market_regime)
                              and (not direction or k.direction == direction)
                              and k.completed][-limit:]
                    out: list[DecisionMemory] = []
                    for k in picked:
                        if k.offset < 0:
                            rec = tail_rec
                        else:
                            f.seek(k.offset)
                            rec = self._parse(f.read(k.length).strip(), None)
                        if rec is not None:
                            out.append(rec)
                    return out
            except FileNotFoundError:
                self._reset(None)
                return []
            except OSError as e:
                logger.error("Failed to read %s: %s", self._path, e)
                self._reset(None)
                return []


class LearningStore:
    """Append-only JSONL store for all learning data.

    Files:
    - decision_memory.jsonl      — every trading decision
    - reflection_memory.jsonl    — post-trade reflections
    - strategy_scorecard.json    — strategy rankings (overwrite OK)
    - macro_event_memory.jsonl   — macro event reactions
    - prompt_versions.json       — prompt version registry (overwrite OK)
    - model_comparison.jsonl     — model-vs-model comparisons
    - human_feedback.jsonl       — user feedback
    - improvement_backlog.json   — proposed improvements (overwrite OK)
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = Path(data_dir or DEFAULT_DATA_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._files = {
            "decision": self._dir / "decision_memory.jsonl",
            "reflection": self._dir / "reflection_memory.jsonl",
            "scorecard": self._dir / "strategy_scorecard.json",
            "macro": self._dir / "macro_event_memory.jsonl",
            "prompts": self._dir / "prompt_versions.json",
            "comparison": self._dir / "model_comparison.jsonl",
            "feedback": self._dir / "human_feedback.jsonl",
            "backlog": self._dir / "improvement_backlog.json",
        }
        self._similar = _DecisionIndex(self._files["decision"])

    # ── Append (JSONL) ─────────────────────────────────────────────

    def _append_jsonl(self, key: str, record: BaseModel) -> None:
        """Atomic append to a JSONL file."""
        path = self._files[key]
        try:
            line = record.model_dump_json() + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error("Failed to append to %s: %s", path, e)

    def _read_jsonl(self, key: str, model_cls: Type[T]) -> list[T]:
        """Read all records from a JSONL file."""
        path = self._files[key]
        if not path.exists():
            return []
        records: list[T] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(model_cls.model_validate_json(line))
                    except Exception as e:
                        logger.warning("Skipping corrupt line %d in %s: %s", line_num, path, e)
        except Exception as e:
            logger.error("Failed to read %s: %s", path, e)
        return records

    # ── Overwrite (JSON) ───────────────────────────────────────────
    #
    # Each write is ONE read-modify-write of the file as it is on disk
    # (`bot/utils/json_store.py`). The old pair read a file that would not
    # parse as `{}` and then wrote the one entry being recorded over it, so a
    # corrupt scorecard file lost every other strategy's scorecard to the next
    # evaluation, and the prompt registry every other version to the next one
    # recorded. A file that is there and will not read is left as it is now.

    def _update_json(self, key: str, shape: type, change) -> None:
        """Apply ``change`` to the file, or leave an unreadable one alone.
        Never raises: every caller records as a side effect of other work."""
        path = self._files[key]
        try:
            update_json_store(path, change, shape=shape, indent=2, default=str)
        except StoreUnreadable as exc:
            logger.error("%s could not be read (%s) — the new entry was NOT "
                         "written over it", path, exc.detail)
        except OSError as e:
            logger.error("Failed to write %s: %s", path, e)

    def _read_json(self, key: str, shape: type = dict) -> dict | list:
        """A JSON file for a READER, ``shape()`` for a fresh start.

        An unreadable file is ALSO answered as ``shape()`` here, with an
        error logged, and that is a stated choice rather than the defect:
        these readers rank strategies and list prompt versions and proposals,
        nothing here decides money, and the writes above no longer put that
        empty answer back over the file."""
        path = self._files[key]
        r = read_json_store(path, shape=shape)
        if r.state == UNREADABLE:
            logger.error("Failed to read %s: %s", path, r.detail)
        data: dict | list = r.data if r.data is not None else shape()
        return data

    # ── Public API: Decision Memory ────────────────────────────────

    def record_decision(self, decision: DecisionMemory) -> None:
        self._append_jsonl("decision", decision)
        logger.info("Decision recorded: %s → %s", decision.audit_id, decision.decision)

    def get_decisions(self, symbol: Optional[str] = None, limit: int = 100) -> list[DecisionMemory]:
        records = self._read_jsonl("decision", DecisionMemory)
        if symbol:
            records = [r for r in records if r.symbol == symbol]
        return records[-limit:]

    def similar_decisions(self, symbol: str, market_regime: str, direction: str,
                          window: int = 500, limit: int = 10) -> list[DecisionMemory]:
        """`get_decisions(symbol, window)` filtered to completed rows of that
        regime and direction (empty = any), the last ``limit``: read through
        an index that parses each line of the file once (`_DecisionIndex`)."""
        return self._similar.similar(symbol, market_regime, direction, window, limit)

    # ── Public API: Reflection Memory ──────────────────────────────

    def record_reflection(self, reflection: ReflectionMemory) -> None:
        self._append_jsonl("reflection", reflection)
        logger.info("Reflection recorded: %s", reflection.audit_id)

    def get_reflections(self, limit: int = 50) -> list[ReflectionMemory]:
        return self._read_jsonl("reflection", ReflectionMemory)[-limit:]

    # ── Public API: Strategy Scorecard ─────────────────────────────

    def update_scorecard(self, scorecard: StrategyScorecard) -> None:
        row = json.loads(scorecard.model_dump_json())

        def _put(data: dict) -> None:
            data[scorecard.strategy_name] = row

        self._update_json("scorecard", dict, _put)

    def get_scorecards(self) -> dict[str, StrategyScorecard]:
        data = self._read_json("scorecard")
        if not isinstance(data, dict):
            return {}
        result = {}
        for name, raw in data.items():
            try:
                result[name] = StrategyScorecard.model_validate(raw)
            except Exception:
                pass
        return result

    # ── Public API: Macro Event Memory ─────────────────────────────

    def record_macro_event(self, event: MacroEventMemory) -> None:
        self._append_jsonl("macro", event)

    def get_macro_events(self, event_type: Optional[str] = None, limit: int = 50) -> list[MacroEventMemory]:
        records = self._read_jsonl("macro", MacroEventMemory)
        if event_type:
            records = [r for r in records if r.event_type == event_type]
        return records[-limit:]

    # ── Public API: Prompt Versions ────────────────────────────────

    def record_prompt_version(self, pv: PromptVersion) -> None:
        row = json.loads(pv.model_dump_json())

        def _put(data: dict) -> None:
            data[pv.version_id] = row

        self._update_json("prompts", dict, _put)

    def get_prompt_versions(self) -> dict[str, PromptVersion]:
        data = self._read_json("prompts")
        if not isinstance(data, dict):
            return {}
        result = {}
        for vid, raw in data.items():
            try:
                result[vid] = PromptVersion.model_validate(raw)
            except Exception:
                pass
        return result

    # ── Public API: Model Comparison ───────────────────────────────

    def record_comparison(self, comparison: ModelComparison) -> None:
        self._append_jsonl("comparison", comparison)

    def get_comparisons(self, limit: int = 50) -> list[ModelComparison]:
        return self._read_jsonl("comparison", ModelComparison)[-limit:]

    # ── Public API: Human Feedback ─────────────────────────────────

    def record_feedback(self, feedback: HumanFeedback) -> None:
        self._append_jsonl("feedback", feedback)
        logger.info("Feedback recorded: %s type=%s", feedback.audit_id, feedback.feedback_type)

    def get_feedback(self, limit: int = 50) -> list[HumanFeedback]:
        return self._read_jsonl("feedback", HumanFeedback)[-limit:]

    # ── Public API: Improvement Backlog ────────────────────────────

    def record_proposal(self, proposal: ImprovementProposal) -> None:
        row = json.loads(proposal.model_dump_json())

        def _put(data: list) -> None:
            data.append(row)

        self._update_json("backlog", list, _put)
        logger.info("Proposal recorded: %s class=%s", proposal.audit_id, proposal.classification)

    def set_proposal_statuses(self, statuses: dict[str, str]) -> None:
        """Set the status of the named proposals in the file AS IT IS.

        The orchestrator used to read the backlog through `get_proposals`
        (which drops a row it cannot validate, and answered an unreadable
        file as ``[]``) and write that list back whole: a malformed row was
        erased by the next status change, and an unreadable backlog by the
        next run. Every row but the named ones is kept byte for byte now."""
        def _set(data: list) -> bool:
            changed = False
            for row in data:
                if not isinstance(row, dict):
                    continue
                want = statuses.get(str(row.get("audit_id")))
                if want is not None and row.get("status") != want:
                    row["status"] = want
                    changed = True
            return changed

        self._update_json("backlog", list, _set)

    def get_proposals(self, status: Optional[str] = None) -> list[ImprovementProposal]:
        data = self._read_json("backlog", list)
        if not isinstance(data, list):
            return []
        result = []
        for raw in data:
            try:
                p = ImprovementProposal.model_validate(raw)
                if status is None or p.status == status:
                    result.append(p)
            except Exception:
                pass
        return result

    # ── Stats ──────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        """Return record counts per store."""
        return {
            "decisions": len(self._read_jsonl("decision", DecisionMemory)),
            "reflections": len(self._read_jsonl("reflection", ReflectionMemory)),
            "scorecards": len(self.get_scorecards()),
            "macro_events": len(self._read_jsonl("macro", MacroEventMemory)),
            "prompt_versions": len(self.get_prompt_versions()),
            "comparisons": len(self._read_jsonl("comparison", ModelComparison)),
            "feedback": len(self._read_jsonl("feedback", HumanFeedback)),
            "proposals": len(self.get_proposals()),
        }
