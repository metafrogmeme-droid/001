"""A file that will not read is not an empty store, and nothing is written over it.

Every small JSON store here read its file with a loader that answered ``{}``
for a failed read, and wrote the whole map back on its next change. A MISSING
file is an empty store; a file that is there and will not read holds every
other user's row, and the next write replaced it with the one row the writer
knew about. Driven on the unfixed code, each of these is what happened:

* a leverage file holding three users, one failed read, one ``set_pref`` for a
  fourth: the file held the fourth alone;
* the strategy selections, the same, and a strategy ``clear`` that ran out of
  disk truncated the file instead of leaving it;
* the equity-peak store answered a drawdown of 0.0 -- the all-clear -- and its
  next save erased every other person's high-water mark;
* the authority spend ledger reported $0 spent and its next record erased
  every other user's 24h spend;
* the venue selection read "you chose nothing";
* the authority envelopes: one failed read and one ``bind`` erased a revoked
  user's REVOKE;
* the per-user recall and the per-user profile, on every ordinary question and
  every profile sync;
* the Proof-of-PnL seasons (every past season's standings) and the public
  board (every other member's row).

``bot/utils/json_store.py`` is the one reading all of them use now: three
states (fresh, read, unreadable), an empty file is a fresh start, and the one
write -- ``update_json_store`` -- reads the file again, refuses to write over a
file that will not read, and applies one change to what it read. What a READER
answers for an unreadable store is decided per store and driven here too.
"""
from __future__ import annotations

import builtins
import errno
import json
import os
from pathlib import Path

import pytest

from bot.utils import json_store as js
from bot.utils.json_store import (
    FRESH,
    READ,
    UNREADABLE,
    StoreUnreadable,
    load_json_store,
    read_json_store,
    update_json_store,
)

_REAL_OPEN = builtins.open
_REAL_FDOPEN = os.fdopen
CORRUPT = b'{"111": 2, "222": 3, "sk-live-THIS-IS-FILE-TEXT'


@pytest.fixture
def fail_reads(monkeypatch):
    """``fail_reads(path, times)``: the next ``times`` read-mode opens of
    ``path`` raise EIO -- a file that is there and did not read this time."""
    def _plant(path, times=1):
        left = [times]
        target = os.path.abspath(str(path))

        def _open(p, mode="r", *a, **k):
            if (isinstance(p, (str, os.PathLike)) and "r" in mode
                    and os.path.abspath(str(p)) == target and left[0] > 0):
                left[0] -= 1
                raise OSError(errno.EIO, "Input/output error")
            return _REAL_OPEN(p, mode, *a, **k)

        monkeypatch.setattr(builtins, "open", _open)
    return _plant


class _HalfWriter:
    """A file whose write lands half its bytes and then runs out of disk."""

    def __init__(self, fh):
        self._fh = fh

    def write(self, data):
        self._fh.write(data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    def __getattr__(self, name):
        return getattr(self._fh, name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._fh.close()
        return False


@pytest.fixture
def disk_full(monkeypatch):
    """Every write -- a direct ``open(..., "w")`` or the atomic writer's temp
    file -- lands half and raises ENOSPC. Planted on both, so the drive asks
    what happens to the FILE whichever way a store writes it."""
    def _open(p, mode="r", *a, **k):
        fh = _REAL_OPEN(p, mode, *a, **k)
        return _HalfWriter(fh) if ("w" in mode or "a" in mode) else fh

    def _fdopen(fd, mode="r", *a, **k):
        fh = _REAL_FDOPEN(fd, mode, *a, **k)
        return _HalfWriter(fh) if ("w" in mode or "a" in mode) else fh

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(os, "fdopen", _fdopen)


def _put(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj), encoding="utf-8")


def _corrupt(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(CORRUPT)


def _bytes(path):
    return Path(path).read_bytes()


# ── the reading ─────────────────────────────────────────────────────────────

class TestTheReading:
    def test_a_missing_file_is_a_fresh_start(self, tmp_path):
        assert read_json_store(tmp_path / "none.json") == (FRESH, None, "")
        assert load_json_store(tmp_path / "none.json") == {}

    def test_an_empty_file_is_a_fresh_start(self, tmp_path):
        """Nothing in it for a write to erase -- the answer RiskEngine's own
        loader gives a zero-byte file."""
        for text in ("", "  \n"):
            (tmp_path / "e.json").write_text(text)
            assert read_json_store(tmp_path / "e.json").state == FRESH

    def test_a_readable_file_is_read(self, tmp_path):
        _put(tmp_path / "r.json", {"a": 1})
        assert read_json_store(tmp_path / "r.json") == (READ, {"a": 1}, "")

    def test_a_corrupt_file_is_unreadable_and_says_only_its_class(self, tmp_path):
        """The detail is the exception's CLASS, never its text: a parser's
        message quotes the bytes it choked on, and the file can hold a key."""
        _corrupt(tmp_path / "c.json")
        r = read_json_store(tmp_path / "c.json")
        assert r.state == UNREADABLE and r.data is None
        assert r.detail == "JSONDecodeError"
        with pytest.raises(StoreUnreadable) as info:
            load_json_store(tmp_path / "c.json")
        assert "sk-live" not in str(info.value)
        assert info.value.detail == "JSONDecodeError"
        assert info.value.path == str(tmp_path / "c.json")

    def test_a_read_that_raised_is_unreadable_not_fresh(self, tmp_path, fail_reads):
        _put(tmp_path / "r.json", {"a": 1})
        fail_reads(tmp_path / "r.json")
        assert read_json_store(tmp_path / "r.json") == (UNREADABLE, None, "OSError")

    def test_a_file_of_the_wrong_shape_is_somebody_elses(self, tmp_path):
        """A list is somebody's file, and an empty map written over it would
        erase it just the same."""
        _put(tmp_path / "l.json", [1, 2])
        assert read_json_store(tmp_path / "l.json") == (
            UNREADABLE, None, "not a JSON dict")
        _put(tmp_path / "d.json", {"a": 1})
        assert read_json_store(tmp_path / "d.json", shape=list).state == UNREADABLE

    def test_a_check_can_refuse_a_file_that_parses(self, tmp_path):
        _put(tmp_path / "k.json", {"book": [1]})
        r = read_json_store(tmp_path / "k.json",
                            check=lambda d: "" if isinstance(d.get("book"), dict)
                            else "book is not a JSON dict")
        assert r == (UNREADABLE, None, "book is not a JSON dict")


class TestTheOneWrite:
    def test_it_refuses_an_unreadable_file_and_writes_nothing(self, tmp_path):
        _corrupt(tmp_path / "c.json")
        asked = []
        with pytest.raises(StoreUnreadable):
            update_json_store(tmp_path / "c.json", asked.append)
        assert asked == [], "the change was applied to a reading of nothing"
        assert _bytes(tmp_path / "c.json") == CORRUPT

    def test_it_applies_one_change_to_what_the_file_holds(self, tmp_path):
        _put(tmp_path / "s.json", {"111": 2, "222": 3})
        data, written = update_json_store(tmp_path / "s.json",
                                          lambda d: d.__setitem__("444", 5))
        assert written is True and data == {"111": 2, "222": 3, "444": 5}
        assert json.loads((tmp_path / "s.json").read_text()) == data

    def test_nothing_to_change_writes_nothing(self, tmp_path):
        data, written = update_json_store(tmp_path / "absent.json",
                                          lambda d: False)
        assert (data, written) == ({}, False)
        assert not (tmp_path / "absent.json").exists()

    def test_a_fresh_start_creates_the_file(self, tmp_path):
        (tmp_path / "empty.json").write_text("")
        update_json_store(tmp_path / "empty.json", lambda d: d.update(a=1))
        assert json.loads((tmp_path / "empty.json").read_text()) == {"a": 1}

    def test_a_write_that_did_not_land_raises_and_leaves_the_file(self, tmp_path,
                                                                   disk_full):
        """The OSError is the caller's: "the file could not be read" and "the
        write did not land" are different facts. The atomic writer leaves the
        file as it was and no temp file behind."""
        _put(tmp_path / "s.json", {"111": 2})
        before = _bytes(tmp_path / "s.json")
        with pytest.raises(OSError):
            update_json_store(tmp_path / "s.json", lambda d: d.update(x=1))
        assert _bytes(tmp_path / "s.json") == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["s.json"]


# ── the leverage preference ─────────────────────────────────────────────────

class TestTheLeveragePreference:
    @pytest.fixture(autouse=True)
    def _dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
        from bot.core import user_leverage_store
        self.store = user_leverage_store
        self.path = Path(user_leverage_store._path())

    def test_an_unreadable_file_answers_no_preference_for_nobody(self):
        _corrupt(self.path)
        with pytest.raises(StoreUnreadable):
            self.store.get("111")

    def test_no_writer_writes_over_it(self):
        _corrupt(self.path)
        with pytest.raises(StoreUnreadable):
            self.store.set_pref("444", 5)
        with pytest.raises(StoreUnreadable):
            self.store.clear("111")
        assert _bytes(self.path) == CORRUPT

    def test_one_failed_read_does_not_cost_the_others_their_rows(self, fail_reads):
        _put(self.path, {"111": 2, "222": 3, "333": 2})
        fail_reads(self.path)
        with pytest.raises(StoreUnreadable):
            self.store.set_pref("444", 5)
        assert self.store.set_pref("444", 5) == 5
        assert json.loads(self.path.read_text()) == {
            "111": 2, "222": 3, "333": 2, "444": 5}

    def test_a_clear_that_ran_out_of_disk_leaves_the_file(self, disk_full):
        """The clear used to open the file for writing directly, so a disk
        that filled mid-write truncated every preference in it."""
        _put(self.path, {"111": 2, "222": 3})
        before = _bytes(self.path)
        with pytest.raises(OSError):
            self.store.clear("111")
        assert _bytes(self.path) == before


# ── the strategy selections ─────────────────────────────────────────────────

class TestTheStrategySelections:
    @pytest.fixture(autouse=True)
    def _dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
        from bot.core import user_strategy_store
        self.store = user_strategy_store
        self.path = Path(user_strategy_store._path())

    def test_an_unreadable_file_answers_no_selection_for_nobody(self):
        _corrupt(self.path)
        with pytest.raises(StoreUnreadable):
            self.store.get_entry("111")
        with pytest.raises(StoreUnreadable):
            self.store.get("111")

    def test_no_writer_writes_over_it(self):
        _corrupt(self.path)
        with pytest.raises(StoreUnreadable):
            self.store.set_pref("444", "conservative", ["conservative"])
        with pytest.raises(StoreUnreadable):
            self.store.set_custom("444", "longonly", "Long only",
                                  {"direction": "long_only"})
        with pytest.raises(StoreUnreadable):
            self.store.clear("111")
        assert _bytes(self.path) == CORRUPT

    def test_one_failed_read_does_not_cost_the_others_their_rows(self, fail_reads):
        armed = {"kind": "community", "slug": "longonly", "label": "x",
                 "gates": {"direction": "long_only"}, "armed_at": "t"}
        _put(self.path, {"111": "conservative", "222": armed})
        fail_reads(self.path)
        with pytest.raises(StoreUnreadable):
            self.store.set_pref("444", "conservative", ["conservative"])
        assert self.store.set_pref("444", "conservative", ["conservative"]) == "conservative"
        assert self.store.get_entry("222") == armed
        assert self.store.get("111") == "conservative"

    def test_a_clear_that_ran_out_of_disk_leaves_the_file(self, disk_full):
        _put(self.path, {"111": "conservative", "222": "aggressive"})
        before = _bytes(self.path)
        with pytest.raises(OSError):
            self.store.clear("111")
        assert _bytes(self.path) == before


# ── the person-level equity peak ────────────────────────────────────────────

class TestThePersonPeak:
    def _store(self, path):
        from bot.risk.person_peak import PersonPeakStore
        return PersonPeakStore(str(path))

    def test_an_unreadable_peak_is_no_drawdown_reading_not_zero(self, tmp_path):
        """0.0 is the all-clear the drawdown gate reads; nobody measured it."""
        p = tmp_path / "peaks.json"
        _corrupt(p)
        s = self._store(p)
        assert s.drawdown_pct("111", 800.0) is None
        assert s.observe("111", 800.0) is None
        with pytest.raises(StoreUnreadable):
            s.reseed("111")
        assert _bytes(p) == CORRUPT

    def test_a_failed_read_erases_nobody_and_the_store_recovers(self, tmp_path,
                                                                 fail_reads):
        p = tmp_path / "peaks.json"
        _put(p, {"peaks": {"111": 1000.0, "222": 5000.0}})
        # Two failed reads: the constructor's, and the next call's. The store
        # tries the read again on every call, so one failure alone is healed
        # before anything is asked of it.
        fail_reads(p, times=2)
        s = self._store(p)
        assert s.drawdown_pct("111", 800.0) is None
        assert json.loads(p.read_text())["peaks"] == {"111": 1000.0, "222": 5000.0}
        assert s.drawdown_pct("111", 800.0) == pytest.approx(20.0)
        s.observe("333", 50.0)
        assert json.loads(p.read_text())["peaks"] == {
            "111": 1000.0, "222": 5000.0, "333": 50.0}

    def test_two_processes_keep_each_others_peaks(self, tmp_path):
        p = tmp_path / "peaks.json"
        a, b = self._store(p), self._store(p)
        a.observe("111", 1000.0)
        b.observe("222", 5000.0)
        assert json.loads(p.read_text())["peaks"] == {"111": 1000.0, "222": 5000.0}

    def test_a_write_keeps_a_higher_peak_another_process_recorded(self, tmp_path):
        """The merge keeps the MAX, never the writer's own copy. Process A
        holds 111 at 1000; B then sees 1500; A's next write, for somebody
        else, must not put A's stale 1000 back over it."""
        p = tmp_path / "peaks.json"
        a = self._store(p)
        a.observe("111", 1000.0)
        b = self._store(p)
        b.observe("111", 1500.0)
        a.observe("222", 5000.0)
        assert json.loads(p.read_text())["peaks"] == {"111": 1500.0, "222": 5000.0}


# ── the authority spend ledger ──────────────────────────────────────────────

class TestTheSpendLedger:
    def _ledger(self, path):
        from bot.guardian.authority_ledger import AuthoritySpendLedger
        return AuthoritySpendLedger(state_file=str(path))

    def test_an_unreadable_ledger_is_not_zero_spent(self, tmp_path):
        """$0 spent is what lets the next order through a daily cap."""
        p = tmp_path / "ledger.json"
        _corrupt(p)
        led = self._ledger(p)
        with pytest.raises(StoreUnreadable):
            led.spent("web:7", 1000.0)
        with pytest.raises(StoreUnreadable):
            led.remaining("web:7", 100.0, 1000.0)
        with pytest.raises(StoreUnreadable):
            led.record("web:7", 50.0, 1000.0, ref="T1")
        assert _bytes(p) == CORRUPT

    def test_a_failed_read_erases_nobodys_day_and_the_ledger_recovers(
            self, tmp_path, fail_reads):
        p = tmp_path / "ledger.json"
        seed = self._ledger(p)
        seed.record("web:8", 40.0, 1000.0, ref="A")
        fail_reads(p, times=2)          # the constructor's read and the next
        led = self._ledger(p)
        with pytest.raises(StoreUnreadable):
            led.spent("web:8", 1001.0)
        assert led.record("web:7", 50.0, 1002.0, ref="T1") is True
        again = self._ledger(p)
        assert again.spent("web:8", 1003.0) == 40.0
        assert again.spent("web:7", 1003.0) == 50.0
        assert again.record("web:7", 50.0, 1004.0, ref="T1") is False, (
            "a duplicate ref recorded twice after a restart")

    def test_a_record_that_did_not_land_is_kept_and_carried(self, tmp_path,
                                                            monkeypatch):
        p = tmp_path / "ledger.json"
        led = self._ledger(p)
        real = js.atomic_write_json
        monkeypatch.setattr(js, "atomic_write_json",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            led.record("web:7", 30.0, 1000.0, ref="T1")
        assert led.spent("web:7", 1001.0) == 30.0, "this process forgot the spend"
        monkeypatch.setattr(js, "atomic_write_json", real)
        led.record("web:7", 20.0, 1002.0, ref="T2")
        assert self._ledger(p).spent("web:7", 1003.0) == 50.0

    def test_two_processes_keep_each_others_day(self, tmp_path):
        """B loaded before A recorded, so B's memory has no web:7. B's write
        must start from the FILE, or it erases A's spend for the day."""
        p = tmp_path / "ledger.json"
        a, b = self._ledger(p), self._ledger(p)
        a.record("web:7", 50.0, 1000.0, ref="A")
        b.record("web:8", 40.0, 1001.0, ref="B")
        again = self._ledger(p)
        assert again.spent("web:7", 1002.0) == 50.0
        assert again.spent("web:8", 1002.0) == 40.0

    @pytest.mark.parametrize("book", [["web:7"], "web:7", 5])
    def test_a_book_that_is_not_a_map_is_somebody_elses_file(self, tmp_path, book):
        p = tmp_path / "ledger.json"
        _put(p, {"book": book})
        before = _bytes(p)
        led = self._ledger(p)
        with pytest.raises(StoreUnreadable):
            led.spent("web:7", 1000.0)
        with pytest.raises(StoreUnreadable):
            led.record("web:7", 50.0, 1000.0, ref="T1")
        assert _bytes(p) == before


# ── the venue selection ─────────────────────────────────────────────────────

class TestTheVenueSelection:
    @pytest.mark.parametrize("sel", [["bitget"], "bitget", 5])
    def test_a_selection_that_is_not_a_map_is_somebody_elses_file(
            self, tmp_path, sel):
        p = tmp_path / "sel.json"
        _put(p, {"selection": sel})
        before = _bytes(p)
        s = self._store(p)
        with pytest.raises(StoreUnreadable):
            s.raw_selection("111")
        ok, why = s.set_selection("111", [], connected=lambda uid: [])
        assert ok is False and "could not be read" in why
        assert _bytes(p) == before

    def _store(self, path):
        from bot.core.venue_selection import VenueSelectionStore
        return VenueSelectionStore(str(path))

    def test_an_unreadable_selection_is_not_you_chose_nothing(self, tmp_path):
        from bot.core.venue_selection import UNREAD_REFUSAL
        p = tmp_path / "sel.json"
        _corrupt(p)
        s = self._store(p)
        with pytest.raises(StoreUnreadable):
            s.raw_selection("7")
        ok, why = s.set_selection("7", ["bitget"], connected=lambda uid: ["bitget"])
        assert (ok, why) == (False, UNREAD_REFUSAL)
        assert _bytes(p) == CORRUPT

    def test_a_failed_read_erases_nobody_and_the_store_recovers(self, tmp_path,
                                                                 fail_reads):
        p = tmp_path / "sel.json"
        seed = self._store(p)
        assert seed.set_selection("8", ["bybit"], connected=lambda uid: ["bybit"])[0]
        fail_reads(p, times=2)          # the constructor's read and the next
        s = self._store(p)
        with pytest.raises(StoreUnreadable):
            s.raw_selection("8")
        ok, _ = s.set_selection("7", ["bitget"], connected=lambda uid: ["bitget"])
        assert ok is True
        again = self._store(p)
        assert again.raw_selection("8") == ["bybit"]
        assert again.raw_selection("7") == ["bitget"]

    def test_the_routing_reading_says_why_it_is_single_venue(self, tmp_path):
        from bot.core.venue_selection import routing_decision
        p = tmp_path / "sel.json"
        _corrupt(p)
        d = routing_decision("7", connected=lambda uid: ["bitget", "bybit"],
                             store=self._store(p))
        assert d["effective"] == () and "unreadable" in d["reason"]


# ── the authority envelopes ─────────────────────────────────────────────────

def _env(mode="enforce"):
    from bot.guardian import authority as auth
    return auth.compile_envelope({"mode": mode, "allowed_venues": ["bitget"],
                                  "max_notional_per_trade_usd": 100})


class TestTheAuthorityEnvelopes:
    def _store(self, path):
        from bot.guardian.user_authority_store import UserAuthorityStore
        return UserAuthorityStore(str(path))

    def test_an_unreadable_store_authorizes_nothing_and_is_not_written(self, tmp_path):
        p = tmp_path / "ua.json"
        _corrupt(p)
        s = self._store(p)
        with pytest.raises(StoreUnreadable):
            s.is_enforcing("7")
        with pytest.raises(StoreUnreadable):
            s.bind("333", _env())
        with pytest.raises(StoreUnreadable):
            s.revoke("7")
        assert _bytes(p) == CORRUPT

    def test_one_failed_read_does_not_undo_somebody_elses_revoke(self, tmp_path,
                                                                  fail_reads):
        """The kill-switch undone by an unrelated user's write."""
        p = tmp_path / "ua.json"
        seed = self._store(p)
        seed.bind("111", _env())
        seed.revoke("111")
        seed.bind("222", _env())
        fail_reads(p, times=2)          # the constructor's read and the next
        s = self._store(p)
        with pytest.raises(StoreUnreadable):
            s.bind("333", _env())
        assert s.bind("333", _env()) is True
        again = self._store(p)
        assert again.get("111")["revoked"] is True
        assert again.is_enforcing("111") is False
        assert again.is_enforcing("222") is True and again.is_enforcing("333") is True

    def test_two_processes_keep_each_others_envelopes(self, tmp_path):
        p = tmp_path / "ua.json"
        a, b = self._store(p), self._store(p)
        a.bind("111", _env())
        b.bind("222", _env())
        assert set(json.loads(p.read_text())) == {"111", "222"}

    def test_a_row_that_is_not_an_envelope_is_left_in_the_file(self, tmp_path):
        p = tmp_path / "ua.json"
        _put(p, {"note": "kept by whoever wrote it"})
        s = self._store(p)
        assert s.get("note") is None
        s.bind("111", _env())
        assert json.loads(p.read_text())["note"] == "kept by whoever wrote it"

    def test_a_revoke_that_did_not_land_survives_another_bind(self, tmp_path,
                                                              monkeypatch):
        """The pending revoke is carried by the next write that lands, and the
        next person's bind cannot un-revoke it here or on disk."""
        p = tmp_path / "ua.json"
        s = self._store(p)
        s.bind("111", _env())
        real = js.atomic_write_json
        monkeypatch.setattr(js, "atomic_write_json",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        assert s.revoke("111") is False
        monkeypatch.setattr(js, "atomic_write_json", real)
        assert s.bind("222", _env()) is True
        assert s.get("111")["revoked"] is True
        assert self._store(p).get("111")["revoked"] is True


# ── the per-user recall and profile ─────────────────────────────────────────

class TestTheRecallAndTheProfile:
    @pytest.fixture(autouse=True)
    def _files(self, tmp_path, monkeypatch):
        self.mem = tmp_path / "mem.json"
        self.prof = tmp_path / "prof.json"
        monkeypatch.setenv("RUNECLAW_USER_MEMORY_FILE", str(self.mem))
        monkeypatch.setenv("RUNECLAW_USER_PROFILE_FILE", str(self.prof))
        from bot.core import user_memory_store, user_profile_store
        self.ums, self.ups = user_memory_store, user_profile_store

    def test_an_ordinary_question_does_not_write_over_an_unreadable_recall(self):
        _corrupt(self.mem)
        assert self.ums.observe("444", "analyze_asset", {"symbol": "BTC"}) is None
        assert _bytes(self.mem) == CORRUPT
        with pytest.raises(StoreUnreadable):
            self.ums.clear("111")

    def test_a_failed_read_costs_nobody_their_recall(self, fail_reads):
        self.ums.observe("111", "analyze_asset", {"symbol": "ETH"})
        fail_reads(self.mem)
        assert self.ums.observe("444", "analyze_asset", {"symbol": "BTC"}) is None
        self.ums.observe("444", "analyze_asset", {"symbol": "BTC"})
        assert set(json.loads(self.mem.read_text())) == {"111", "444"}

    def test_a_profile_sync_does_not_write_over_an_unreadable_file(self):
        _corrupt(self.prof)
        assert self.ups.set_profile("444", {"risk_pref": "balanced"}) is None
        assert _bytes(self.prof) == CORRUPT
        with pytest.raises(StoreUnreadable):
            self.ups.clear("111")

    def test_sizing_names_the_unreadable_profile(self):
        """The reason `user_sizing` has always printed for this case and could
        never reach, because the store folded it into "no preference"."""
        from bot.core.user_sizing import multiplier_for_user
        _corrupt(self.prof)
        assert multiplier_for_user("111") == (1.0, "profile unreadable: no reduction")


# ── the Proof-of-PnL board and seasons ──────────────────────────────────────

class TestTheBoardAndTheSeasons:
    def test_a_freeze_does_not_erase_the_past_seasons(self, tmp_path):
        from bot.proofofpnl.seasons import SeasonStore, season_window
        p = tmp_path / "seasons.json"
        _corrupt(p)
        start, _end = season_window("2026-09")
        s = SeasonStore(str(p))
        with pytest.raises(StoreUnreadable):
            s.record_current([{"handle": "h", "publication": {"published_at": start + 5}}],
                             start + 10)
        with pytest.raises(StoreUnreadable):
            s.season_ids()
        assert _bytes(p) == CORRUPT

    def test_a_freeze_keeps_the_seasons_the_file_holds(self, tmp_path):
        from bot.proofofpnl.seasons import SeasonStore, season_window
        p = tmp_path / "seasons.json"
        _put(p, {"2026-08": {"old": {"published_at": 1}}})
        start, _end = season_window("2026-09")
        s = SeasonStore(str(p))
        assert s.record_current(
            [{"handle": "h", "publication": {"published_at": start + 5}}],
            start + 10) == 1
        assert sorted(json.loads(p.read_text())) == ["2026-08", "2026-09"]

    def test_an_unreadable_board_is_not_an_empty_one(self, tmp_path):
        from bot.proofofpnl.leaderboard import LeaderboardRegistry
        p = tmp_path / "board.json"
        _corrupt(p)
        reg = LeaderboardRegistry(str(p))
        with pytest.raises(StoreUnreadable):
            reg.all_entries()
        with pytest.raises(StoreUnreadable):
            reg.remove("someone")
        assert _bytes(p) == CORRUPT

    def test_the_public_board_answers_503_for_an_unreadable_file(self, tmp_path,
                                                                 monkeypatch):
        """`_leaderboard_payload`'s own docstring says a failed read answers
        503; with the reader folding every failure into {} it answered 200
        and "0 verified agents"."""
        import asyncio
        import types

        from bot.proofofpnl import leaderboard as lb
        from bot.web import user_gateway
        p = tmp_path / "board.json"
        _corrupt(p)
        monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_PATH", str(p))
        lb.reset_leaderboard_registry()
        try:
            resp = asyncio.run(user_gateway.handle_leaderboard_public(
                types.SimpleNamespace(query={})))
        finally:
            lb.reset_leaderboard_registry()
        assert resp.status == 503
        assert b"leaderboard_unavailable" in resp.body
        assert b"sk-live" not in resp.body
