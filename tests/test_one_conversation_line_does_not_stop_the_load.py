"""One malformed line in the conversation file kept the bot from starting.

`ConversationStore._load` caught `KeyError` and `JSONDecodeError` per line,
and the store is built in the Telegram handler's constructor. A line that is
not an object raised `TypeError` (`entry["user_id"]` on a list, a number, a
string), and a user turn whose content is null raised `AttributeError` in the
mention reader. Both escaped the loop, so one bad line in
`data/conversations.jsonl` raised out of the handler's constructor. Driven:

    content null: RAISED AttributeError: 'NoneType' object has no attribute 'lower'
    a list:       RAISED TypeError: list indices must be integers or slices, not str

An assistant turn with null content did load, as a Message whose content is
None, handed to the model as a turn.

A row this store did not write is unreadable now: skipped, counted at
WARNING, and kept VERBATIM, because compaction rewrites the file from memory
and would otherwise be what erased it. A deletion still takes the user's own
unreadable lines with it.
"""
from __future__ import annotations

import json
import logging

import pytest

from bot.nlp import conversation_store as cs
from bot.nlp.conversation_store import ConversationStore

GOOD = [
    {"user_id": "1", "role": "user", "content": "hello BTC", "timestamp": 100},
    {"user_id": "1", "role": "assistant", "content": "hi", "timestamp": 101},
]

BAD = [
    pytest.param({"user_id": "1", "role": "user", "content": None, "timestamp": 1},
                 id="user-content-null"),
    pytest.param({"user_id": "1", "role": "assistant", "content": None},
                 id="assistant-content-null"),
    pytest.param({"user_id": "1", "role": "user", "content": 42}, id="content-a-number"),
    pytest.param({"user_id": "1", "role": 7, "content": "x"}, id="role-a-number"),
    pytest.param({"user_id": "1", "role": "user", "content": "x", "metadata": "m"},
                 id="metadata-a-string"),
    pytest.param([1, 2], id="a-list"),
    pytest.param(42, id="a-number"),
    pytest.param("text", id="a-string"),
]


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


class TestOneLineDoesNotStopTheLoad:
    @pytest.mark.parametrize("bad", BAD)
    def test_the_rest_of_the_file_loads(self, tmp_path, bad):
        p = tmp_path / "conv.jsonl"
        _write(p, [GOOD[0], bad, GOOD[1]])
        store = ConversationStore(persist_path=p)
        assert [m.content for m in store.get_recent("1", 10)] == ["hello BTC", "hi"]

    @pytest.mark.parametrize("bad", BAD)
    def test_the_line_is_counted_at_warning(self, tmp_path, bad, caplog):
        p = tmp_path / "conv.jsonl"
        _write(p, [GOOD[0], bad, GOOD[1]])
        with caplog.at_level(logging.WARNING, logger=cs.logger.name):
            ConversationStore(persist_path=p)
        rows = [r for r in caplog.records if "could not be read" in r.getMessage()]
        assert rows and rows[0].levelno >= logging.WARNING
        assert "1 line(s)" in rows[0].getMessage()

    def test_a_readable_file_says_nothing(self, tmp_path, caplog):
        p = tmp_path / "conv.jsonl"
        _write(p, GOOD + [{"user_id": "1", "role": "user", "content": "x",
                           "metadata": None}])
        with caplog.at_level(logging.WARNING, logger=cs.logger.name):
            store = ConversationStore(persist_path=p)
        assert len(store.get_recent("1", 10)) == 3, "a null metadata is no metadata"
        assert not [r for r in caplog.records if "could not be read" in r.getMessage()]


class TestCompactionKeepsWhatItCouldNotRead:
    def test_the_line_survives_a_rewrite(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ConversationStore, "COMPACT_THRESHOLD_LINES", 1)
        p = tmp_path / "conv.jsonl"
        bad = [1, 2]
        _write(p, [GOOD[0], bad, GOOD[1]])
        ConversationStore(persist_path=p)          # compacts on load
        lines = p.read_text().splitlines()
        assert json.dumps(bad) in lines, "compaction erased a line nobody could read"
        assert len(lines) == 3

    def test_a_deletion_takes_the_users_own_unreadable_line(self, tmp_path, monkeypatch):
        p = tmp_path / "conv.jsonl"
        mine = {"user_id": "1", "role": "user", "content": None}
        nobodys = [1, 2]
        _write(p, [GOOD[0], mine, nobodys,
                   {"user_id": "2", "role": "user", "content": "hey", "timestamp": 5}])
        store = ConversationStore(persist_path=p)
        store.clear_user("1")
        assert json.dumps(mine) not in p.read_text()
        monkeypatch.setattr(ConversationStore, "COMPACT_THRESHOLD_LINES", 0)
        store._maybe_compact()
        text = p.read_text()
        assert json.dumps(mine) not in text, "a purged user's row came back at compaction"
        assert json.dumps(nobodys) in text, "a line nobody owns was taken by a deletion"
