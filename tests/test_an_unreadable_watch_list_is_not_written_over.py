"""A watch list the bot could not read at startup was read as EMPTY, and the
next /watch on wrote one chat over it.

`ProactiveMonitor._load_enabled_chats` swallowed any read failure at DEBUG and
left the set empty. The state file EXISTED, so `_maybe_auto_enroll_admin` read
it as an operator who had emptied the list on purpose and enrolled nobody, and
the next `enable_chat` saved the in-memory set -- that one chat -- over the
file. Driven:

    readable:   watching ['1001', '2002', '3003']
    cut short:  watching []
    /watch on 4004  ->  file {"enabled_chats": ["4004"]}

Every chat that had asked for CRITICAL alerts (a position with no stop, the
circuit breaker) stopped getting them after one restart, nothing said so above
DEBUG, and the first person to run /watch on erased the rest for good.

An unreadable list is not an empty one. The load says so at ERROR, the file is
never written over this run (a change holds in memory and the reply says so),
and the operator is enrolled for this run only, so CRITICAL alerts reach
somebody while nobody knows who was watching.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core import proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor
from bot.skills.agent_commands import AgentCommands
from bot.utils.logger import system_log


@contextlib.contextmanager
def _cfg(**overrides):
    """Temporarily override frozen CONFIG fields (nested via dotted keys)."""
    saved = {}
    for key, val in overrides.items():
        if "." in key:
            obj_name, attr = key.split(".", 1)
            obj = getattr(CONFIG, obj_name)
        else:
            obj, attr = CONFIG, key
        saved[key] = (obj, attr, getattr(obj, attr))
        object.__setattr__(obj, attr, val)
    try:
        yield
    finally:
        for obj, attr, old in saved.values():
            object.__setattr__(obj, attr, old)


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def system_records():
    """`system_log` does not propagate, so caplog cannot see it."""
    h = _Records()
    system_log.addHandler(h)
    try:
        yield h.records
    finally:
        system_log.removeHandler(h)


READABLE = {"enabled_chats": ["1001", "2002", "3003"]}


def _cut_short(path):
    text = json.dumps(READABLE)
    cut = text[: len(text) - 7]
    path.write_text(cut)
    return cut


def _hydrated(path, *, operator="", enroll=True):
    with _cfg(proactive_watch_state_file=str(path),
              proactive_auto_enroll_admin=enroll,
              **{"telegram.chat_id": operator}):
        m = ProactiveMonitor(engine=None)
        m.hydrate()
    return m


def _with_path(m, path):
    """Later calls on `m` must write where it read."""
    return _cfg(proactive_watch_state_file=str(path))


# Every shape a file can have that this module did not write. The old reader
# answered an empty list for the dict ones and kept nothing for the rest, and
# then wrote over each of them.
UNREADABLE = [
    pytest.param(None, id="cut-short"),
    pytest.param('{"chats": ["1001", "2002"]}', id="dict-without-the-key"),
    pytest.param('{"enabled_chats": null}', id="key-is-null"),
    pytest.param('{"enabled_chats": "1001"}', id="key-is-a-string"),
    pytest.param("42", id="a-number"),
]


class TestAnUnreadableListIsNotWrittenOver:
    @pytest.mark.parametrize("body", UNREADABLE)
    def test_watch_on_leaves_the_file_as_it_was(self, tmp_path, body):
        p = tmp_path / "watch.json"
        if body is None:
            before = _cut_short(p)
        else:
            p.write_text(body)
            before = body
        m = _hydrated(p)
        assert m.watch_list_unreadable is True
        with _with_path(m, p):
            saved = m.enable_chat("4004")
        assert saved is False
        assert p.read_text() == before, "a list nobody could read was written over"
        assert m.is_enabled("4004"), "the change must still hold for this run"

    def test_watch_off_leaves_the_file_as_it_was(self, tmp_path):
        p = tmp_path / "watch.json"
        before = _cut_short(p)
        m = _hydrated(p)
        with _with_path(m, p):
            m.enable_chat("4004")
            saved = m.disable_chat("4004")
        assert saved is False
        assert p.read_text() == before

    def test_the_failed_read_is_said_at_error(self, tmp_path, system_records):
        p = tmp_path / "watch.json"
        _cut_short(p)
        _hydrated(p)
        rows = [r for r in system_records
                if getattr(r, "action", "") == "watch_load"
                or "WATCH LIST UNREADABLE" in r.getMessage()]
        assert rows, "the failed read was not reported"
        assert rows[0].levelno >= logging.ERROR
        msg = rows[0].getMessage()
        assert "JSONDecodeError" in msg
        assert "not be written over" in msg

    def test_a_refused_write_is_said_at_warning(self, tmp_path, caplog):
        p = tmp_path / "watch.json"
        _cut_short(p)
        m = _hydrated(p)
        with caplog.at_level(logging.WARNING, logger=pm.logger.name):
            with _with_path(m, p):
                m.enable_chat("4004")
        assert any(r.levelno >= logging.WARNING and "NOT written" in r.getMessage()
                   for r in caplog.records)


class TestTheOperatorIsEnrolledForThisRun:
    def test_the_operator_hears_critical_alerts(self, tmp_path):
        p = tmp_path / "watch.json"
        before = _cut_short(p)
        m = _hydrated(p, operator="77700")
        assert m.is_enabled("77700"), (
            "nobody knows who was watching, so nobody would hear a CRITICAL alert")
        assert p.read_text() == before, "the enrolment was written over the list"

    def test_the_enrolment_says_it_is_this_run_only(self, tmp_path, system_records):
        p = tmp_path / "watch.json"
        _cut_short(p)
        _hydrated(p, operator="77700")
        rows = [r for r in system_records if "auto-enrolled" in r.getMessage()]
        assert rows and "this run only" in rows[0].getMessage()

    def test_the_switch_still_decides(self, tmp_path):
        p = tmp_path / "watch.json"
        _cut_short(p)
        m = _hydrated(p, operator="77700", enroll=False)
        assert not m.is_enabled("77700")

    def test_an_emptied_readable_list_is_still_respected(self, tmp_path):
        p = tmp_path / "watch.json"
        p.write_text('{"enabled_chats": []}')
        m = _hydrated(p, operator="77700")
        assert m.watch_list_unreadable is False
        assert m.enabled_chat_count == 0


class TestAReadableListIsUnchanged:
    def test_it_is_read_and_written_as_before(self, tmp_path):
        p = tmp_path / "watch.json"
        p.write_text(json.dumps(READABLE))
        m = _hydrated(p)
        assert m.watch_list_unreadable is False
        assert m.enabled_chat_count == 3
        with _with_path(m, p):
            saved = m.enable_chat("4004")
        assert saved is True
        assert json.loads(p.read_text())["enabled_chats"] == [
            "1001", "2002", "3003", "4004"]

    def test_a_bare_list_is_the_other_shape_it_writes(self, tmp_path):
        p = tmp_path / "watch.json"
        p.write_text('["1001", "2002"]')
        m = _hydrated(p)
        assert m.watch_list_unreadable is False
        assert m.enabled_chat_count == 2

    def test_an_absent_file_is_a_fresh_list(self, tmp_path):
        p = tmp_path / "watch.json"
        m = _hydrated(p, operator="77700")
        assert m.watch_list_unreadable is False
        assert json.loads(p.read_text())["enabled_chats"] == ["77700"]

    def test_a_failed_write_is_said_and_answered_false(self, tmp_path, monkeypatch, caplog):
        p = tmp_path / "watch.json"
        p.write_text(json.dumps(READABLE))
        m = _hydrated(p)

        def _fail(*_a, **_k):
            raise OSError("read-only filesystem")
        monkeypatch.setattr(pm, "atomic_write_json", _fail)
        with caplog.at_level(logging.WARNING, logger=pm.logger.name):
            with _with_path(m, p):
                saved = m.enable_chat("4004")
        assert saved is False
        assert any(r.levelno >= logging.WARNING and "save failed" in r.getMessage()
                   for r in caplog.records)


class _Host(AgentCommands):
    """The real /watch handler with a stand-in for what it reaches for."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.sent = []

    async def _guard(self, update, command, ctx=None):
        return True

    def _get_tg_id(self, update):
        return "4004"

    async def _send(self, update, text, **_kw):
        self.sent.append(text)


def _watch(host, *args):
    ctx = SimpleNamespace(args=list(args))
    asyncio.run(host._cmd_watch(SimpleNamespace(), ctx))
    return host.sent[-1]


class TestTheReplySaysWhatWasNotSaved:
    def test_watch_on_over_an_unreadable_list(self, tmp_path):
        p = tmp_path / "watch.json"
        _cut_short(p)
        m = _hydrated(p)
        with _with_path(m, p):
            out = _watch(_Host(m), "on")
        assert "PROACTIVE ALERTS ON" in out
        assert "could not be read when the bot started" in out
        assert "holds until the bot restarts" in out

    def test_watch_off_over_an_unreadable_list(self, tmp_path):
        p = tmp_path / "watch.json"
        _cut_short(p)
        m = _hydrated(p)
        with _with_path(m, p):
            out = _watch(_Host(m), "off")
        assert "PROACTIVE ALERTS OFF" in out
        assert "could not be read when the bot started" in out

    def test_a_failed_write_is_the_other_sentence(self, tmp_path, monkeypatch):
        p = tmp_path / "watch.json"
        p.write_text(json.dumps(READABLE))
        m = _hydrated(p)
        monkeypatch.setattr(pm, "atomic_write_json",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
        with _with_path(m, p):
            out = _watch(_Host(m), "on")
        assert "could not be saved to the watch list" in out
        assert "could not be read" not in out

    def test_a_saved_change_carries_no_warning(self, tmp_path):
        p = tmp_path / "watch.json"
        p.write_text(json.dumps(READABLE))
        m = _hydrated(p)
        with _with_path(m, p):
            out = _watch(_Host(m), "on")
        assert "⚠" not in out

    @pytest.mark.parametrize("answer", [object(), None], ids=["a-mock", "no-answer"])
    def test_a_stand_in_monitor_adds_nothing(self, answer):
        """A monitor answering something other than a bool (a mock) is not a
        refusal: only a literal False says the change missed the file. None
        is what `enable_chat` answered before it answered at all, so a
        monitor that says nothing is not one that says "not saved"."""
        host = _Host(SimpleNamespace(enable_chat=lambda _id: answer))
        out = _watch(host, "on")
        assert "⚠" not in out

    def test_the_status_count_says_it_is_partial(self, tmp_path):
        p = tmp_path / "watch.json"
        _cut_short(p)
        m = _hydrated(p)
        with _with_path(m, p):
            m.enable_chat("4004")
            out = _watch(_Host(m), "status")
        assert "Active watchers: 1" in out
        assert "only the chats enabled since" in out

    def test_a_readable_status_count_is_whole(self, tmp_path):
        p = tmp_path / "watch.json"
        p.write_text(json.dumps(READABLE))
        m = _hydrated(p)
        out = _watch(_Host(m), "status")
        assert "Active watchers: 3" in out
        assert "⚠" not in out
