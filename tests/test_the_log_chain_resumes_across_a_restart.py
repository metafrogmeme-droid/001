"""A restart continues the log hash chain; it does not open a second at GENESIS."""
from __future__ import annotations

import hashlib
import json
import logging

from bot.utils.logger import _JSONFormatter, _tail_hash


def _emit(fmt: _JSONFormatter, msg: str) -> str:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    return fmt.format(rec)


def test_a_new_formatter_resumes_from_the_last_line(tmp_path):
    path = tmp_path / "x.jsonl"
    first = _JSONFormatter(resume_from=path)
    lines = [_emit(first, "a"), _emit(first, "b")]
    path.write_text("\n".join(lines) + "\n")
    second = _JSONFormatter(resume_from=path)
    nxt = json.loads(_emit(second, "c"))
    assert nxt["prev_hash"] == hashlib.sha256(lines[-1].encode()).hexdigest()
    assert nxt["prev_hash"] != "GENESIS"


def test_absent_empty_or_unreadable_file_is_a_fresh_chain(tmp_path):
    assert _tail_hash(tmp_path / "missing.jsonl") == "GENESIS"
    empty = tmp_path / "e.jsonl"
    empty.write_text("")
    assert _tail_hash(empty) == "GENESIS"
    assert _tail_hash(tmp_path) == "GENESIS"  # a directory is unreadable


def test_without_resume_the_chain_starts_at_genesis():
    assert json.loads(_emit(_JSONFormatter(), "a"))["prev_hash"] == "GENESIS"
