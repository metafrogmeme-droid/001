"""A write cut short at the end of the audit chain broke every append after it.

`AuditChain.append` finds its predecessor by parsing the LAST line of the log,
and it writes with a plain `open("a").write(...)`. A crash, a kill or a full
disk can cut that write short, and the next append then parsed the fragment:

    two good entries + '{"sequence": 2, "event_type": "DECI'
        -> every later append() raised JSONDecodeError, 3 of 3

The chain is written after a live fill (`engine._confirm_trade_inner` seals the
decision once `execute()` has placed the order), so the break reached the money
path; `test_a_seal_failure_does_not_unplace_a_trade.py` is that half.

A write cut exactly before its newline is the same defect one byte over: the
entry is whole, the next append wrote straight onto the end of it, and the
append after THAT parsed two entries on one line ("Extra data") and broke.

THE FIX RESUMES AND SAYS SO; IT REWRITES NOTHING. The chain is tamper-EVIDENT,
so the fragment stays exactly where it is. The next append walks back to the
last whole entry, writes a `CHAIN_TORN_TAIL` entry linked to it that records
each fragment's sha256 and length, and then its own entry. `verify()` reports
the fragment once, by name, and the entries after it verify as linked rather
than as a cascade of sequence errors.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from bot.utils import audit_chain as ac
from bot.utils.audit_chain import GENESIS_HASH, TORN_TAIL_EVENT, AuditChain

FRAGMENT = '{"sequence": 2, "event_type": "DECI'


def _chain(tmp_path, n=2):
    ch = AuditChain(str(tmp_path / "chain.jsonl"))
    made = [ch.append("DECISION", {"a": i}) for i in range(n)]
    return ch, tmp_path / "chain.jsonl", made


def _lines(path):
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestATornTailIsResumedFrom:
    def test_every_append_after_a_torn_line_succeeds(self, tmp_path):
        ch, path, made = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        got = [ch.append("DECISION", {"a": 10 + i}) for i in range(3)]
        # The marker takes sequence 2; the three appends follow it.
        assert [e.sequence for e in got] == [3, 4, 5]

    def test_the_fragment_is_left_in_place_byte_for_byte(self, tmp_path):
        ch, path, made = _chain(tmp_path)
        before = path.read_bytes() + FRAGMENT.encode("utf-8")
        path.write_bytes(before)
        ch.append("DECISION", {"a": 9})
        after = path.read_bytes()
        assert after.startswith(before), "the torn line was rewritten or removed"
        assert _lines(path)[2] == FRAGMENT

    def test_the_marker_links_to_the_last_whole_entry_and_records_the_fragment(self, tmp_path):
        ch, path, made = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        entry = ch.append("DECISION", {"a": 9})
        marker = json.loads(_lines(path)[3])
        assert marker["event_type"] == TORN_TAIL_EVENT
        assert marker["sequence"] == 2
        assert marker["prev_hash"] == made[-1].entry_hash
        assert marker["payload"]["fragments"] == [
            {"sha256": _digest(FRAGMENT), "bytes": len(FRAGMENT)}]
        assert marker["payload"]["resumed_after_sequence"] == 1
        assert entry.prev_hash == marker["entry_hash"]

    def test_the_marker_is_written_once(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        for i in range(3):
            ch.append("DECISION", {"a": i})
        kinds = [json.loads(ln)["event_type"] for ln in _lines(path)[3:]]
        assert kinds.count(TORN_TAIL_EVENT) == 1

    def test_the_failure_is_logged_loudly(self, tmp_path, caplog):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        with caplog.at_level("ERROR", logger=ac.__name__):
            ch.append("DECISION", {"a": 9})
        assert any(r.levelname == "ERROR" and TORN_TAIL_EVENT in r.getMessage()
                   for r in caplog.records)

    def test_a_log_that_is_only_a_fragment_resumes_at_genesis(self, tmp_path):
        path = tmp_path / "chain.jsonl"
        path.write_text('{"sequence": 0, "ev', encoding="utf-8")
        entry = AuditChain(str(path)).append("DECISION", {"a": 1})
        marker = json.loads(_lines(path)[1])
        assert (marker["sequence"], marker["prev_hash"]) == (0, GENESIS_HASH)
        assert marker["payload"]["resumed_after_sequence"] is None
        assert entry.sequence == 1

    def test_two_fragments_are_both_recorded(self, tmp_path):
        """The marker's own write can be cut short too. The next append finds
        two fragments after the last entry and records both."""
        ch, path, made = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT + "\n" + '{"sequence": 2, "event_type": "CHAIN_T')
        ch.append("DECISION", {"a": 9})
        marker = json.loads(_lines(path)[4])
        assert [f["sha256"] for f in marker["payload"]["fragments"]] == [
            _digest(FRAGMENT), _digest('{"sequence": 2, "event_type": "CHAIN_T')]
        ok, problems = AuditChain.verify(str(path))
        assert len(problems) == 2, problems

    def test_a_json_line_that_is_not_an_entry_is_a_fragment_too(self, tmp_path):
        """`null` parses. It is still not an entry, and reading `["entry_hash"]`
        off it was a TypeError."""
        ch, path, made = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("null\n")
        entry = ch.append("DECISION", {"a": 9})
        assert entry.sequence == 3
        assert json.loads(_lines(path)[3])["payload"]["fragments"][0]["sha256"] == _digest("null")

    def test_a_tail_of_nothing_but_non_entries_is_refused(self, tmp_path):
        """More non-entries at the end than a torn write can leave is not a
        crash. Linking across them would be a guess about where the chain
        was, so the append refuses and writes nothing."""
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("garbage\n" * (ac._TORN_SEARCH_LINES + 1))
        before = path.read_bytes()
        with pytest.raises(ac.AuditChainUnreadable):
            ch.append("DECISION", {"a": 9})
        assert path.read_bytes() == before

    def test_the_most_fragments_it_will_resume_across(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("garbage\n" * ac._TORN_SEARCH_LINES)
        assert ch.append("DECISION", {"a": 9}).sequence == 3

    def test_a_short_log_of_only_non_entries_resumes_at_genesis(self, tmp_path):
        path = tmp_path / "chain.jsonl"
        path.write_text("garbage\n" * ac._TORN_SEARCH_LINES, encoding="utf-8")
        assert AuditChain(str(path)).append("DECISION", {}).sequence == 1


class TestAWholeEntryWithNoNewline:
    def test_the_next_append_starts_on_its_own_line(self, tmp_path):
        ch, path, made = _chain(tmp_path)
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
        ch.append("DECISION", {"a": 2})
        # ...so the append after it can read the tail at all.
        assert ch.append("DECISION", {"a": 3}).sequence == 3
        assert [json.loads(ln)["sequence"] for ln in _lines(path)] == [0, 1, 2, 3]
        assert AuditChain.verify(str(path)) == (True, [])

    def test_no_marker_for_an_entry_that_was_whole(self, tmp_path):
        ch, path, made = _chain(tmp_path)
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
        ch.append("DECISION", {"a": 2})
        assert TORN_TAIL_EVENT not in path.read_text(encoding="utf-8")


class TestVerifyNamesTheTornLineOnce:
    def test_one_problem_and_no_cascade(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        for i in range(4):
            ch.append("DECISION", {"a": i})
        ok, problems = AuditChain.verify(str(path))
        assert ok is False, "a log holding a line that is not an entry is not intact"
        assert len(problems) == 1, problems
        assert problems[0].startswith("line 3:")
        assert TORN_TAIL_EVENT in problems[0] and "sequence 2" in problems[0]

    def test_an_unacknowledged_torn_tail_is_still_reported(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        ok, problems = AuditChain.verify(str(path))
        assert ok is False
        assert problems == [problems[0]] and "malformed JSON" in problems[0]

    def test_a_marker_for_a_different_fragment_acknowledges_nothing(self, tmp_path):
        """The marker vouches for the bytes it hashed. Swap the fragment for
        another after the fact and the old reading returns: the line is
        malformed and the linkage after it is unchecked."""
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        ch.append("DECISION", {"a": 9})
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[2] = '{"sequence": 2, "event_type": "OTHER'
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = AuditChain.verify(str(path))
        assert not any(TORN_TAIL_EVENT in p and "recorded" in p for p in problems[:1])
        assert "malformed JSON" in problems[0]
        assert len(problems) > 1, "the marker's sequence should now read as out of place"

    def test_a_marker_whose_sequence_does_not_follow_acknowledges_nothing(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        ch.append("DECISION", {"a": 9})
        lines = path.read_text(encoding="utf-8").splitlines()
        marker = json.loads(lines[3])
        marker["sequence"] = 7
        lines[3] = json.dumps(marker)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = AuditChain.verify(str(path))
        assert "malformed JSON" in problems[0]

    def test_a_marker_that_does_not_link_to_the_last_entry_acknowledges_nothing(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        ch.append("DECISION", {"a": 9})
        lines = path.read_text(encoding="utf-8").splitlines()
        marker = json.loads(lines[3])
        marker["prev_hash"] = "f" * 64
        lines[3] = json.dumps(marker)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = AuditChain.verify(str(path))
        assert "malformed JSON" in problems[0]

    def test_an_ordinary_entry_after_a_malformed_line_acknowledges_nothing(self, tmp_path):
        """The old reading for a malformed line in the middle is unchanged:
        one problem, and the sequence counts the line as the entry it
        replaced."""
        ch, path, _ = _chain(tmp_path, n=3)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = "{not json"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = AuditChain.verify(str(path))
        assert problems == [problems[0]] and "malformed JSON" in problems[0]

    def test_a_line_that_parses_but_is_not_an_entry_does_not_crash_verify(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("[1, 2]\n")
        ok, problems = AuditChain.verify(str(path))
        assert ok is False and "not an entry" in problems[0]


class TestReadersSkipTheFragment:
    def test_get_entries_returns_the_entries_around_it(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        ch.append("DECISION", {"a": 9})
        got = ch.get_entries(limit=10)
        assert [e.sequence for e in got] == [0, 1, 2, 3]
        assert got[2].event_type == TORN_TAIL_EVENT

    def test_get_entries_before_any_append_does_not_raise(self, tmp_path):
        ch, path, _ = _chain(tmp_path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(FRAGMENT)
        assert [e.sequence for e in ch.get_entries(limit=10)] == [0, 1]
