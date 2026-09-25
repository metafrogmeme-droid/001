"""One user, one venue, one book.

A person who trades two venues through their own linked keys has one live
executor per venue (``_user_executors["7"]`` for bitget, ``["bybit/7"]`` for
bybit), and before this every one of them kept its book in the SAME two files:
``data/live_positions_7.json`` and ``data/closed_trades_7.json``. The venue was
a directory for the risk engine and the paper portfolio (``venue_key``'s whole
argument is "the venue is a DIRECTORY") and a fact the executor's own paths
never saw. Driven: the second venue's executor LOADED the first venue's
positions as its own when it was built, and its first save of its own book
erased them. A position on bitget, with a stop resting there, stopped being
monitored the moment the person connected bybit.

Three more fell out of the same reading:

* ``_executor_for(uid, venue)`` overwrote its own ``venue`` argument with the
  person's stored active venue, so every caller that named one — the
  multi-venue router's per-venue margin read, the executor it then placed on,
  ``/venues``' open-position check — got the ACTIVE venue's executor whatever
  it asked for.
* When the active venue's keys were unusable, a request NAMING another venue
  answered the OPERATOR's executor: an order routed to the operator's account.
* ``/venues`` counted the operator's book when per-user live is off, and
  refused a person's deselect over positions on somebody else's account.

No network: every executor here is built from credentials that never reach a
venue, and every figure is read back from the files the executors write.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition, move_legacy_user_book, saved_book_holds_positions
from bot.core.venue_key import executor_state_dir

UID = "7"
BITGET = {"api_key": "bg-key-1", "api_secret": "bg-secret-1", "passphrase": "p"}
BYBIT = {"api_key": "by-key-1", "api_secret": "by-secret-1"}


class _Store:
    """A credential store for one person, with the four reads the engine asks."""

    def __init__(self, active="bitget", creds=None, raises=False):
        self.active = active
        self.creds = dict(creds if creds is not None
                          else {"bitget": BITGET, "bybit": BYBIT})
        self.raises = raises

    def _check(self):
        if self.raises:
            raise RuntimeError("store unreadable")

    def user_ids(self):
        return [UID]

    def get_venue(self, uid):
        self._check()
        return self.active

    def get(self, uid):
        self._check()
        return self.creds.get(self.active)

    def get_for_venue(self, uid, venue):
        self._check()
        return self.creds.get(venue)

    def list_venues(self, uid):
        self._check()
        return sorted(self.creds)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def per_user():
    original = engine_mod.CONFIG.per_user_live_enabled

    def _set(value):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", value)

    _set(True)
    yield _set
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


def _engine(monkeypatch, store):
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: store)
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._user_executors = {}
    eng._balance_view_executors = {}
    eng.live_executor = SimpleNamespace(name="OPERATOR")
    eng.risk = None
    eng.ws_feed = None
    eng.slippage = None
    eng._on_live_position_closed = lambda pos, uid: None
    return eng


def _position(tid, symbol):
    return LivePosition(trade_id=tid, symbol=symbol, direction="LONG",
                        entry_price=100.0, quantity=1.0, cost_usd=20.0,
                        stop_loss=95.0, take_profit=110.0, leverage=5)


def _open(ex, tid, symbol):
    ex._positions[tid] = _position(tid, symbol)
    ex._save_positions()


def _rows(path):
    return json.loads(Path(path).read_text())


def _default_book(state):
    return state / f"live_positions_{UID}.json"


def _bybit_book(state):
    return state / "venue" / "bybit" / f"live_positions_{UID}.json"


def _legacy_row(tid, symbol):
    """A row as every build before this one wrote it: no ``venue`` key."""
    row = {
        "trade_id": tid, "symbol": symbol, "direction": "LONG",
        "entry_price": 100.0, "quantity": 1.0, "cost_usd": 20.0,
        "stop_loss": 95.0, "take_profit": 110.0, "leverage": 5,
        "opened_at": "2026-09-20T00:00:00+00:00", "status": "open",
    }
    return row


# ── the defect, driven ─────────────────────────────────────────────────────


class TestTwoVenuesKeepTwoBooks:

    def test_a_second_venue_does_not_load_or_erase_the_first_venues_book(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        bitget = eng._executor_for(UID, "bitget")
        _open(bitget, "T-BG", "BTC/USDT:USDT")

        bybit = eng._executor_for(UID, "bybit")
        assert bybit._venue.id == "bybit"
        assert bybit.open_positions == [], (
            "the bybit executor loaded the bitget position as its own")
        _open(bybit, "T-BY", "ETH/USDT:USDT")

        # A restart: fresh executors read what is on disk.
        eng2 = _engine(monkeypatch, _Store(active="bitget"))
        assert [p.trade_id for p in eng2._executor_for(UID, "bitget").open_positions] == ["T-BG"]
        assert [p.trade_id for p in eng2._executor_for(UID, "bybit").open_positions] == ["T-BY"]

    def test_each_book_lives_under_its_own_venue(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        _open(eng._executor_for(UID, "bitget"), "T-BG", "BTC/USDT:USDT")
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")
        assert set(_rows(_default_book(state))) == {"T-BG"}
        assert set(_rows(_bybit_book(state))) == {"T-BY"}

    def test_every_row_says_whose_it_is(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        _open(eng._executor_for(UID, "bitget"), "T-BG", "BTC/USDT:USDT")
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")
        assert _rows(_default_book(state))["T-BG"]["venue"] == "bitget"
        assert _rows(_bybit_book(state))["T-BY"]["venue"] == "bybit"

    def test_the_closed_record_is_split_too(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        by = eng._executor_for(UID, "bybit")
        pos = _position("T-BY", "ETH/USDT:USDT")
        pos.status = "closed"
        pos.close_price = 105.0
        pos.pnl_usd = 5.0
        by._closed_trades.append(pos)
        by._save_closed_trades()
        path = state / "venue" / "bybit" / f"closed_trades_{UID}.json"
        rows = _rows(path)
        assert [r["trade_id"] for r in rows] == ["T-BY"]
        assert rows[0]["venue"] == "bybit"
        assert not (state / f"closed_trades_{UID}.json").exists()


class TestAForeignRowIsKeptNotManaged:
    """A file can hold another venue's row only if something wrote it there by
    hand or an older build shared it; either way, loading it would hand this
    executor a position it cannot see on its own exchange."""

    def test_a_foreign_row_is_not_loaded_and_survives_the_next_save(
            self, state, per_user, monkeypatch):
        path = _default_book(state)
        path.write_text(json.dumps({
            "T-BG": {**_legacy_row("T-BG", "BTC/USDT:USDT"), "venue": "bitget"},
            "T-BY": {**_legacy_row("T-BY", "ETH/USDT:USDT"), "venue": "bybit"},
        }))
        eng = _engine(monkeypatch, _Store(active="bitget"))
        ex = eng._executor_for(UID, "bitget")
        assert [p.trade_id for p in ex.open_positions] == ["T-BG"]
        ex._save_positions()
        assert set(_rows(path)) == {"T-BG", "T-BY"}, (
            "saving this executor's book erased another venue's row")
        assert _rows(path)["T-BY"]["venue"] == "bybit"


class TestAnUnstampedBookIsClaimedByTheExecutorThatOwnsIt:

    def test_the_active_venues_executor_stamps_what_it_loaded(
            self, state, per_user, monkeypatch):
        path = _default_book(state)
        path.write_text(json.dumps({"T-OLD": _legacy_row("T-OLD", "BTC/USDT:USDT")}))
        eng = _engine(monkeypatch, _Store(active="bitget"))
        eng._executor_for(UID)
        assert _rows(path)["T-OLD"]["venue"] == "bitget"

    def test_a_reader_engine_claims_nothing(self, state, per_user, monkeypatch):
        path = _default_book(state)
        path.write_text(json.dumps({"T-OLD": _legacy_row("T-OLD", "BTC/USDT:USDT")}))
        before = path.read_bytes()
        eng = _engine(monkeypatch, _Store(active="bitget"))
        eng._state_persistence_detached = True
        ex = eng._executor_for(UID)
        assert [p.trade_id for p in ex.open_positions] == ["T-OLD"]
        assert path.read_bytes() == before, "a reader rewrote the bot's book"

    def test_a_claim_is_audited_by_name(self, state, per_user, monkeypatch):
        import bot.core.live_executor as le
        seen = []
        monkeypatch.setattr(le, "audit",
                            lambda log, msg, **kw: seen.append(kw.get("result")))
        _default_book(state).write_text(
            json.dumps({"T-OLD": _legacy_row("T-OLD", "BTC/USDT:USDT")}))
        _engine(monkeypatch, _Store(active="bitget"))._executor_for(UID)
        assert "CLAIMED" in seen

    def test_a_stamped_book_is_not_claimed_again(self, state, per_user, monkeypatch):
        import bot.core.live_executor as le
        seen = []
        monkeypatch.setattr(le, "audit",
                            lambda log, msg, **kw: seen.append(kw.get("result")))
        _default_book(state).write_text(json.dumps(
            {"T-BG": {**_legacy_row("T-BG", "BTC/USDT:USDT"), "venue": "bitget"}}))
        _engine(monkeypatch, _Store(active="bitget"))._executor_for(UID)
        assert "CLAIMED" not in seen

    def test_a_closed_record_that_did_not_read_is_not_rewritten(
            self, state, per_user, monkeypatch):
        closed = state / f"closed_trades_{UID}.json"
        closed.write_text("{not json")
        before = closed.read_bytes()
        _engine(monkeypatch, _Store(active="bitget"))._executor_for(UID)
        assert closed.read_bytes() == before, (
            "a claim-save over a closed record that could not be read would "
            "replace it with whatever part of it was read")


    def test_a_closed_record_with_an_unreadable_row_keeps_it_through_the_claim(
            self, state, per_user, monkeypatch):
        """The claim saves the record. A row this build cannot read is kept
        verbatim by that save (the closed-trade loader reads row by row and
        writes what it could not read back as it was), so the claim stamps
        what it read and loses nothing it did not."""
        closed = state / f"closed_trades_{UID}.json"
        good = {**_legacy_row("T-1", "BTC/USDT:USDT"), "status": "closed",
                "closed_at": "2026-09-21T00:00:00+00:00"}
        bad = {**good, "trade_id": "T-2", "opened_at": "not a time"}
        closed.write_text(json.dumps([good, bad]))
        _engine(monkeypatch, _Store(active="bitget"))._executor_for(UID)
        rows = json.loads(closed.read_text())
        assert bad in rows, "the claim erased a row it could not read"
        assert [r for r in rows if r.get("trade_id") == "T-1"][0]["venue"] == "bitget"

    def test_a_positions_file_read_only_in_part_is_not_rewritten(
            self, state, per_user, monkeypatch):
        path = _default_book(state)
        good = _legacy_row("T-1", "BTC/USDT:USDT")
        bad = {k: v for k, v in _legacy_row("T-2", "ETH/USDT:USDT").items()
               if k != "entry_price"}
        path.write_text(json.dumps({"T-1": good, "T-2": bad}))
        before = path.read_bytes()
        _engine(monkeypatch, _Store(active="bitget"))._executor_for(UID)
        assert path.read_bytes() == before


class TestTheLegacyBookMovesToTheActiveVenue:
    """The pre-split file is whichever executor saved last, and the only
    executor built without a venue named was the ACTIVE one. It moves there
    before any executor for this person is built, whichever is asked first."""

    def _legacy(self, state, with_bak=False):
        path = _default_book(state)
        path.write_text(json.dumps({"T-OLD": _legacy_row("T-OLD", "ETH/USDT:USDT")}))
        if with_bak:
            Path(str(path) + ".bak").write_text(path.read_text())
        return path

    def test_it_moves_before_the_default_venue_can_load_it(
            self, state, per_user, monkeypatch):
        self._legacy(state)
        eng = _engine(monkeypatch, _Store(active="bybit"))
        bitget = eng._executor_for(UID, "bitget")   # asked FIRST, and named
        assert bitget.open_positions == [], (
            "the default venue's executor took another exchange's position")
        bybit = eng._executor_for(UID, "bybit")
        assert [p.trade_id for p in bybit.open_positions] == ["T-OLD"]
        assert _rows(_bybit_book(state))["T-OLD"]["venue"] == "bybit"
        assert not _default_book(state).exists()

    def test_the_backup_travels_with_the_file(self, state, per_user, monkeypatch):
        path = self._legacy(state, with_bak=True)
        _engine(monkeypatch, _Store(active="bybit"))._executor_for(UID)
        assert not Path(str(path) + ".bak").exists(), (
            "a backup left behind is loaded by the default venue's fallback")
        assert Path(str(_bybit_book(state)) + ".bak").exists()

    def test_an_active_default_venue_moves_nothing(self, state, per_user, monkeypatch):
        path = self._legacy(state)
        _engine(monkeypatch, _Store(active="bitget"))._executor_for(UID)
        assert path.exists()
        assert not _bybit_book(state).exists()

    def test_a_reader_moves_nothing(self, state, per_user, monkeypatch):
        path = self._legacy(state)
        eng = _engine(monkeypatch, _Store(active="bybit"))
        eng._state_persistence_detached = True
        eng._executor_for(UID)
        assert path.exists() and not _bybit_book(state).exists()

    def test_the_move_is_audited(self, state, per_user, monkeypatch):
        seen = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda log, msg, **kw: seen.append(kw.get("result")))
        self._legacy(state)
        _engine(monkeypatch, _Store(active="bybit"))._executor_for(UID)
        assert "BOOK_MOVED" in seen


class TestTheMoveRefusesWhatItCannotBeSureOf:

    def _dst(self, state):
        return str(state / "venue" / "bybit")

    def test_an_existing_split_book_is_never_overwritten(self, state):
        src = _default_book(state)
        src.write_text(json.dumps({"T-OLD": _legacy_row("T-OLD", "X")}))
        dst = _bybit_book(state)
        dst.parent.mkdir(parents=True)
        dst.write_text(json.dumps({"T-NEW": {**_legacy_row("T-NEW", "Y"), "venue": "bybit"}}))
        assert move_legacy_user_book(UID, self._dst(state)) == []
        assert set(_rows(dst)) == {"T-NEW"} and src.exists()

    def test_an_unreadable_file_is_left_for_a_human(self, state):
        src = _default_book(state)
        src.write_text("{not json")
        assert move_legacy_user_book(UID, self._dst(state)) == []
        assert src.read_text() == "{not json"

    def test_an_empty_main_does_not_move_its_backup(self, state):
        src = _default_book(state)
        src.write_text("{}")
        Path(str(src) + ".bak").write_text(
            json.dumps({"T-OLD": _legacy_row("T-OLD", "X")}))
        assert move_legacy_user_book(UID, self._dst(state)) == []
        assert Path(str(src) + ".bak").exists()

    def test_a_stamped_file_stays_with_whoever_stamped_it(self, state):
        src = _default_book(state)
        src.write_text(json.dumps(
            {"T-BG": {**_legacy_row("T-BG", "X"), "venue": "bitget"}}))
        assert move_legacy_user_book(UID, self._dst(state)) == []
        assert src.exists()

    def test_the_closed_record_moves_on_the_same_rules(self, state):
        src = state / f"closed_trades_{UID}.json"
        src.write_text(json.dumps([_legacy_row("T-OLD", "X")]))
        moved = move_legacy_user_book(UID, self._dst(state))
        assert moved == [str(state / "venue" / "bybit" / f"closed_trades_{UID}.json")]
        assert not src.exists()


# ── the resolver ───────────────────────────────────────────────────────────


class TestANamedVenueIsTheVenue:

    def test_the_named_venue_is_built_whatever_is_active(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bybit"))
        assert eng._executor_for(UID, "bitget")._venue.id == "bitget"
        assert eng._executor_for(UID, "bybit")._venue.id == "bybit"
        assert set(eng._user_executors) == {UID, f"bybit/{UID}"}

    def test_the_unnamed_ask_is_still_the_active_venue(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bybit"))
        assert eng._executor_for(UID)._venue.id == "bybit"

    def test_a_named_venue_with_no_keys_is_none_not_the_operator(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget", creds={"bitget": BITGET}))
        assert eng._executor_for(UID, "bybit") is None

    def test_unusable_active_keys_never_route_a_named_venue_to_the_operator(
            self, state, per_user, monkeypatch):
        # The active venue's keys are gone; bybit was named and has none either.
        eng = _engine(monkeypatch, _Store(active="bitget", creds={}))
        assert eng._executor_for(UID, "bybit") is None
        assert eng._executor_for(UID, "bitget") is None

    def test_a_store_fault_on_a_named_venue_is_none(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(raises=True))
        assert eng._executor_for(UID, "bybit") is None

    def test_an_unknown_venue_is_none(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store())
        assert eng._executor_for(UID, "binance-typo") is None

    def test_the_unnamed_ask_keeps_its_fallback(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget", creds={}))
        assert eng._executor_for(UID) is eng.live_executor
        eng2 = _engine(monkeypatch, _Store(raises=True))
        assert eng2._executor_for(UID) is eng2.live_executor

    def test_with_per_user_off_every_ask_is_the_operator(
            self, state, per_user, monkeypatch):
        per_user(False)
        eng = _engine(monkeypatch, _Store())
        assert eng._executor_for(UID, "bybit") is eng.live_executor


class TestTheBalanceViewReadsTheVenuesBook:

    def test_a_split_active_venue_is_viewed_from_its_own_directory(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bybit"))
        _open(eng._executor_for(UID), "T-BY", "ETH/USDT:USDT")
        view = eng.balance_view_executor(UID)
        assert view is not eng._user_executors[f"bybit/{UID}"]
        assert [p.trade_id for p in view.open_positions] == ["T-BY"]


# ── restart ────────────────────────────────────────────────────────────────


class TestARestartMonitorsEveryBook:

    def test_a_non_active_venue_with_a_saved_book_is_rebuilt(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")

        eng2 = _engine(monkeypatch, _Store(active="bitget"))
        eng2._rehydrate_user_executors()
        assert set(eng2._user_executors) == {UID, f"bybit/{UID}"}
        assert [p.trade_id for p in eng2._user_executors[f"bybit/{UID}"].open_positions] == ["T-BY"]

    def test_a_non_active_venue_with_no_book_is_not_built(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        eng._rehydrate_user_executors()
        assert set(eng._user_executors) == {UID}

    def test_a_book_whose_executor_cannot_be_built_is_named(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")

        audits = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda log, msg, **kw: audits.append((msg, kw)))
        eng2 = _engine(monkeypatch, _Store(active="bitget", creds={"bitget": BITGET,
                                                                   "bybit": None}))
        eng2._rehydrate_user_executors()
        warn = [m for m, kw in audits if kw.get("result") == "WARNING"]
        assert warn and f"bybit/{UID}" in warn[0] and "NOT being monitored" in warn[0]
        assert [kw.get("level") for m, kw in audits
                if kw.get("result") == "WARNING"] == [logging.WARNING]

    def test_the_count_is_of_people_not_executors(self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")
        audits = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda log, msg, **kw: audits.append(msg))
        eng2 = _engine(monkeypatch, _Store(active="bitget"))
        eng2._rehydrate_user_executors()
        assert any("1 of 1" in m for m in audits), audits

    def test_the_saved_book_reading(self, state):
        d = executor_state_dir("bybit")
        assert saved_book_holds_positions(UID, d) is False
        p = _bybit_book(state)
        p.parent.mkdir(parents=True)
        p.write_text("{}")
        assert saved_book_holds_positions(UID, d) is False
        Path(str(p) + ".bak").write_text(json.dumps({"T": _legacy_row("T", "X")}))
        assert saved_book_holds_positions(UID, d) is True
        p.write_text("{not json")
        assert saved_book_holds_positions(UID, d) is True, (
            "unreadable is not empty: building the executor is what reports it")


# ── /venues ────────────────────────────────────────────────────────────────


def _venues_harness(monkeypatch, eng, store, selection_store):
    """Drive the real `_cmd_venues` with a store that records the open-position
    reading it is handed, and returns what the caller was sent."""
    from bot.skills import trading_commands as tc
    from bot.skills.telegram_handler import TelegramHandler

    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: store)
    monkeypatch.setattr("bot.core.venue_selection.get_venue_selection_store",
                        lambda: selection_store)
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = eng
    sent = []

    async def _send(update, text, **kw):
        sent.append(text)

    async def _guard(update, perm):
        return True

    h._send = _send
    h._guard = _guard
    h._get_tg_id = lambda update: UID
    h._is_admin = lambda update: False
    h.users = SimpleNamespace(permission_denial=lambda *a, **k: None,
                              is_authorized=lambda *a, **k: True)
    return h, sent, tc


class _Selection:
    def __init__(self):
        self.seen = []

    def set_selection(self, uid, wanted, *, connected, open_positions):
        try:
            counts = {v: open_positions(uid, v) for v in ("bitget", "bybit")}
        except Exception as exc:
            self.seen.append(("could-not-check", str(exc)))
            return False, "could not check"
        self.seen.append(("counts", counts))
        return True, ""

    def raw_selection(self, uid):
        return []


class TestVenuesCountsThisPersonsBook:

    def _run(self, monkeypatch, eng, store, args):
        import asyncio
        sel = _Selection()
        h, sent, tc = _venues_harness(monkeypatch, eng, store, sel)
        monkeypatch.setattr("bot.core.venue_selection.routing_decision",
                            lambda *a, **k: {"venues": [], "dropped": [], "mode": "off"})
        monkeypatch.setattr("bot.formatters.venue_card.venue_card",
                            lambda **k: "CARD")
        update = SimpleNamespace(effective_user=SimpleNamespace(id=int(UID)),
                                 effective_chat=SimpleNamespace(id=int(UID)),
                                 message=SimpleNamespace())
        ctx = SimpleNamespace(args=args)
        fn = tc.TradingCommands._cmd_venues
        asyncio.run(getattr(fn, "__wrapped__", fn)(h, update, ctx))
        return sel.seen

    def test_per_user_off_never_counts_the_operators_book(
            self, state, per_user, monkeypatch):
        per_user(False)
        eng = _engine(monkeypatch, _Store())
        eng.live_executor = SimpleNamespace(open_positions=[object(), object()])
        seen = self._run(monkeypatch, eng, _Store(), ["bitget"])
        assert seen == [("counts", {"bitget": 0, "bybit": 0})]

    def test_per_user_on_counts_each_venues_own_book(self, state, per_user, monkeypatch):
        store = _Store(active="bitget")
        eng = _engine(monkeypatch, store)
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")
        seen = self._run(monkeypatch, eng, store, ["bitget"])
        assert seen == [("counts", {"bitget": 0, "bybit": 1})]

    def test_a_saved_book_nobody_can_open_is_could_not_check(
            self, state, per_user, monkeypatch):
        eng = _engine(monkeypatch, _Store(active="bitget"))
        _open(eng._executor_for(UID, "bybit"), "T-BY", "ETH/USDT:USDT")
        store = _Store(active="bitget", creds={"bitget": BITGET, "bybit": None})
        eng2 = _engine(monkeypatch, store)
        seen = self._run(monkeypatch, eng2, store, ["bitget"])
        assert seen[0][0] == "could-not-check", seen

    def test_no_book_and_no_keys_is_none(self, state, per_user, monkeypatch):
        store = _Store(active="bitget", creds={"bitget": BITGET, "bybit": None})
        eng = _engine(monkeypatch, store)
        seen = self._run(monkeypatch, eng, store, ["bitget"])
        assert seen == [("counts", {"bitget": 0, "bybit": 0})]


def test_the_executor_state_dir_reading(state):
    assert executor_state_dir("") is None
    assert executor_state_dir("bitget") is None
    assert executor_state_dir("BITGET") is None
    assert executor_state_dir("bybit") == str(state / "venue" / "bybit")
    with pytest.raises(ValueError):
        executor_state_dir("../etc")
    with pytest.raises(ValueError):
        executor_state_dir("binance-typo")


def test_a_venue_executor_built_directly_still_uses_the_default_path(state):
    """The operator's executor and a caller that builds one with no state_dir
    keep the paths they always had: the split is the ENGINE's decision."""
    ex = LiveExecutor(user_id=UID, credentials=BYBIT, venue="bybit")
    assert Path(ex._positions_file) == _default_book(state)
