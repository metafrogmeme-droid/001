"""A refused web-live confirm takes its notional back off the 24h ledger.

`_authorize_web_live_trade` records an order's notional against the day BEFORE
`confirm_trade` runs -- the allow is what lets the confirm proceed, and a
recorder that waited for the fill would let two confirms race past one cap.
Every refusal `confirm_trade` writes after that allow (the risk re-check, the
strategy gate, a price drift, an order the venue refused) then left the
day counting an order that never happened. Driven on the unfixed tree through
the real handler, a real ledger and the real authorization:

    refused ("Your chosen strategy ...")  -> placed False, spent_after 250.0
    a retry of the same trade id          -> spent 250.0 (idempotent by ref)
    placed                                -> spent 500.0

Four refusals at the per-trade cap were a day locked out, and CLAUDE.md had
filed it as "Recorded, not changed". The handler releases the spend THIS
attempt recorded when the confirm refuses, never when the venue confirmed the
order neither way (`placed is None`: that order may be filled), and never a
row an earlier attempt recorded (`WebLiveAuthorization.recorded` is the
recorder's own word for "this call added it"; a ledger asked afterwards would
read an earlier attempt's row as this one's). `AuthoritySpendLedger.release`
takes the row out of memory AND out of the file inside the merge `_save`
makes, because that merge adds back every row memory lacks.
"""
from __future__ import annotations

import ast
import inspect
import os
import time
from types import SimpleNamespace

import pytest

from bot.core.live_executor import EXECUTION_UNVERIFIED_TOKEN
from bot.guardian.authority import compile_envelope
from bot.guardian.authority_ledger import AuthoritySpendLedger
from bot.utils import json_store
from bot.utils.json_store import StoreUnreadable
from bot.web import user_gateway as ug
from tests.source_scan import code_only
from tests.test_a_refused_web_confirm_is_not_a_confirmed_trade import _confirm_audits, _WebHandler
from tests.test_web_gateway import HDRS, SECRET, FakeEngine, _propose, gateway_client


@pytest.fixture
def audits():
    """Every audit record the system channel writes during the test (the
    sibling suite's fixture; a fixture imported by name is a parameter
    shadowing an import to the strict lint gate)."""
    import logging

    from bot.utils.logger import system_log
    seen: list = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record)

    h = _H(level=logging.DEBUG)
    system_log.addHandler(h)
    try:
        yield seen
    finally:
        system_log.removeHandler(h)

NOW = 1_000_000
UID = "web:5"
REFUSAL = "\U0001f6e1 Your chosen strategy 'safe' only trades BTC and ETH."
PLACED = "✅ LIVE LONG SOL/USDT opened"
UNVERIFIED = f"⚠️ {EXECUTION_UNVERIFIED_TOKEN}: the venue answered neither way"


# ── the ledger's release ─────────────────────────────────────────────────────


def _ledger(tmp_path, name="ledger.json"):
    return AuthoritySpendLedger(state_file=str(tmp_path / name))


class TestTheLedgerRelease:

    def test_a_released_row_leaves_memory_and_the_file(self, tmp_path):
        led = _ledger(tmp_path)
        led.record("k", 250, NOW, ref="T1")
        led.record("k", 100, NOW, ref="T2")
        assert led.release("k", "T1", NOW) is True
        assert led.spent("k", NOW) == 100.0
        assert _ledger(tmp_path).spent("k", NOW) == 100.0, "a fresh reader of the file agrees"

    def test_a_ref_nobody_holds_releases_nothing_and_writes_nothing(self, tmp_path):
        led = _ledger(tmp_path)
        led.record("k", 250, NOW, ref="T1")
        before = os.stat(tmp_path / "ledger.json").st_mtime_ns
        assert led.release("k", "T9", NOW) is False
        assert led.release("other", "T1", NOW) is False, "a ref is released under its own key"
        assert os.stat(tmp_path / "ledger.json").st_mtime_ns == before
        assert led.spent("k", NOW) == 250.0

    @pytest.mark.parametrize("on_disk", [True, False], ids=["file", "memory"])
    def test_a_released_ref_records_again(self, tmp_path, on_disk):
        """The dedup set forgets the ref, so a retried trade id is a new spend.
        Driven on a file-backed ledger AND an in-memory one: a save re-reads
        the file's refs, so on disk the discard is redundant with the adopt
        that follows, and only the ledger with no file measures it."""
        led = _ledger(tmp_path) if on_disk else AuthoritySpendLedger()
        assert led.record("k", 250, NOW, ref="T1") is True
        assert led.release("k", "T1", NOW) is True
        assert led.record("k", 250, NOW, ref="T1") is True
        assert led.spent("k", NOW) == 250.0
        assert led.release("k", "T1", NOW) is True and led.release("k", "T1", NOW) is False

    def test_only_that_ref_goes(self, tmp_path):
        """Another key's rows, a row with no ref, and a row whose ref is the
        WORD "None" each stay: `str(None)` is a name a trade could carry."""
        led = _ledger(tmp_path)
        led.record("k", 250, NOW, ref="T1")
        led.record("k", 10, NOW, ref=None)
        led.record("k", 20, NOW, ref="None")
        led.record("j", 30, NOW, ref="T1")
        assert led.release("k", "T1", NOW) is True
        assert led.spent("k", NOW) == 30.0
        assert led.spent("j", NOW) == 30.0
        fresh = _ledger(tmp_path)
        assert fresh.spent("k", NOW) == 30.0 and fresh.spent("j", NOW) == 30.0
        # Releasing the WORD "None" takes the row named that and leaves the
        # row with no ref: the decoy only bites when it is the ref released.
        assert led.release("k", "None", NOW) is True
        assert led.spent("k", NOW) == 10.0 and _ledger(tmp_path).spent("k", NOW) == 10.0

    def test_the_file_side_drop_keeps_what_another_process_recorded(self, tmp_path):
        """Two ledgers on one file. B recorded T3 after A loaded, so A's memory
        lacks T3; A's release of T1 must drop T1 and leave T3, which the merge
        would otherwise have read straight back in beside a T1 it re-added."""
        a = _ledger(tmp_path)
        a.record("k", 250, NOW, ref="T1")
        b = _ledger(tmp_path)
        b.record("k", 40, NOW, ref="T3")
        assert a.spent("k", NOW) == 250.0, "A has not seen T3 yet"
        assert a.release("k", "T1", NOW) is True
        assert a.spent("k", NOW) == 40.0, "the merge adopted T3 and dropped T1"
        assert _ledger(tmp_path).spent("k", NOW) == 40.0

    def test_an_unreadable_file_refuses_and_changes_nothing(self, tmp_path):
        led = _ledger(tmp_path)
        led.record("k", 250, NOW, ref="T1")
        (tmp_path / "ledger.json").write_text("{ not json", encoding="utf-8")
        led2 = _ledger(tmp_path)
        with pytest.raises(StoreUnreadable):
            led2.release("k", "T1", NOW)
        assert (tmp_path / "ledger.json").read_text(encoding="utf-8") == "{ not json"

    def test_a_release_that_did_not_land_keeps_counting(self, tmp_path, monkeypatch):
        """The write raises: the row is put back in memory, because a day read
        as emptier than the file says is the loose direction here (a record
        that did not land keeps its row for the opposite reason)."""
        led = _ledger(tmp_path)
        led.record("k", 250, NOW, ref="T1")

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(json_store, "atomic_write_json", _boom)
        with pytest.raises(OSError):
            led.release("k", "T1", NOW)
        assert led.spent("k", NOW) == 250.0, "memory still counts the row"
        assert led.record("k", 250, NOW + 1, ref="T1") is False, "and still knows the ref"
        monkeypatch.undo()
        assert led.release("k", "T1", NOW) is True, "a later release lands"
        assert _ledger(tmp_path).spent("k", NOW) == 0.0


# ── the authorization says whether THIS call recorded ──────────────────────


class _Idea:
    def __init__(self, asset="SOL/USDT"):
        self.asset = asset


class _Own:
    def _compute_target_leverage(self, symbol, idea=None):
        return 5


class _AuthEngine:
    def __init__(self, ideas, margins):
        self._pending_ideas = ideas
        self._manual_margin_override = margins
        self.live_executor = object()

    def _executor_for(self, tg_id, venue=None):
        return _Own()


@pytest.fixture
def envelope(monkeypatch, tmp_path):
    """A real authority store with an enforce envelope bound for UID, a real
    ledger on a tmp file wired as the gateway's, and the credential store
    answering bitget."""
    import bot.core.exchange_credentials as xc
    import bot.guardian.user_authority_store as uas
    store = uas.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(uas, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    monkeypatch.setattr(xc, "get_credential_store", lambda: SimpleNamespace(
        credential_state=lambda tg: "readable", get_venue=lambda tg: "bitget"))

    def bind(per_trade=500, daily=2000):
        store.bind(UID, compile_envelope({
            "mode": "enforce", "label": "t", "allowed_venues": ["bitget"],
            "symbol_allowlist": ["SOL", "BTC"],
            "max_notional_per_trade_usd": per_trade, "max_notional_daily_usd": daily}))

    bind()
    return SimpleNamespace(store=store, ledger=ledger, bind=bind)


class TestTheAuthorizationNamesWhatItRecorded:

    def test_an_allow_that_added_the_row_says_so(self, envelope):
        eng = _AuthEngine({"T1": _Idea()}, {"T1": 50})
        auth = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert auth == (True, [], True)
        assert auth.recorded is True
        assert envelope.ledger.spent(UID, time.time()) == 250.0

    def test_a_duplicate_ref_is_an_allow_that_recorded_nothing(self, envelope):
        """The second attempt at one trade id: the ledger already holds the
        row, so this call did not add it, and must not later release it."""
        eng = _AuthEngine({"T1": _Idea()}, {"T1": 50})
        assert ug._authorize_web_live_trade({}, eng, UID, "T1").recorded is True
        second = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert second.ok is True and second.recorded is False
        assert envelope.ledger.spent(UID, time.time()) == 250.0

    def test_a_deny_records_nothing_and_says_so(self, envelope):
        envelope.bind(per_trade=100)
        eng = _AuthEngine({"T1": _Idea()}, {"T1": 50})
        auth = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert auth.ok is False and auth.recorded is False
        assert envelope.ledger.spent(UID, time.time()) == 0.0

    def test_a_store_that_raises_is_a_deny_naming_the_class_only(self, envelope, audits):
        """The authorization's own `except` logs the exception's class: a
        store's text names a path, a venue's echoes the request. (The system
        channel does not propagate, so the handler fixture reads it, not
        caplog.)"""
        def _boom(tg):
            raise RuntimeError("apiKey=SECRETVALUE at /secret/path")

        envelope.store.get = _boom
        eng = _AuthEngine({"T1": _Idea()}, {"T1": 50})
        auth = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert auth == (False, ["authorization check failed"], False)
        said = [r.getMessage() for r in audits if "authorization error" in r.getMessage()]
        assert said and "RuntimeError" in said[0]
        assert "SECRETVALUE" not in " ".join(said) and "/secret/path" not in " ".join(said)

    def test_an_allow_with_no_notional_recorded_nothing(self, envelope):
        """An auto-sized order under an envelope with no ceiling is allowed
        and adds no row: nothing to release later."""
        envelope.store.bind(UID, compile_envelope({
            "mode": "enforce", "label": "t", "allowed_venues": ["bitget"],
            "symbol_allowlist": ["SOL", "BTC"]}))
        eng = _AuthEngine({"T1": _Idea()}, {})
        auth = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert auth.ok is True and auth.recorded is False
        assert envelope.ledger.spent(UID, time.time()) == 0.0


# ── the handler, end to end ──────────────────────────────────────────────────


class _LiveEngine(FakeEngine):
    """`confirm_trade` answers a planted sentence; `drops_idea` says whether
    that answer is one the real engine gives after the idea left the book."""

    def __init__(self, answers):
        super().__init__()
        self.live_executor = object()
        self.answers = list(answers)

    def _executor_for(self, user_id="", venue=""):
        return _Own()

    async def confirm_trade(self, trade_id, user_id=""):
        self.confirm_calls.append((trade_id, user_id))
        answer = self.answers.pop(0)
        if answer == PLACED:
            self._pending_ideas.pop(trade_id, None)
        return answer


@pytest.fixture
def live(monkeypatch, envelope):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_LIVE_TRADING_ENABLED", "1")
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
        telegram=SimpleNamespace(admin_ids=""), is_live=lambda: True,
        per_user_live_enabled=True, exchange=SimpleNamespace(default_leverage=5)))
    return envelope


async def _confirm(c, tid):
    r = await c.post("/trade/confirm", json={"telegram_id": UID, "trade_id": tid}, headers=HDRS)
    return r.status, await r.json()


def _spend_audits(seen):
    return [(r.result, dict(r.data)) for r in _confirm_audits(seen, "web_live_spend")]


class TestTheHandler:

    async def _drive(self, answers, live, audits, margin=50):
        engine = _LiveEngine(answers)
        async with gateway_client(engine, _WebHandler(users={})) as c:
            ug_live = ug.CONFIG
            ug.CONFIG = SimpleNamespace(telegram=SimpleNamespace(admin_ids=""),
                                        is_live=lambda: False, per_user_live_enabled=True,
                                        exchange=SimpleNamespace(default_leverage=5))
            try:
                tid = await _propose(c, tg=UID)
            finally:
                ug.CONFIG = ug_live
            engine._manual_margin_override[tid] = margin
            results = []
            for _ in answers:
                results.append(await _confirm(c, tid))
            return engine, tid, results

    async def test_a_refusal_releases_the_spend_it_recorded(self, live, audits):
        engine, tid, [(status, body)] = await self._drive([REFUSAL], live, audits)
        assert status == 200 and body["placed"] is False
        assert live.ledger.spent(UID, time.time()) == 0.0
        assert _spend_audits(audits) == [("RELEASED", {"user": UID})]
        assert AuthoritySpendLedger(state_file=live.ledger._path).spent(UID, time.time()) == 0.0, (
            "released on disk too, not only in this process")

    async def test_a_placement_keeps_the_spend(self, live, audits):
        engine, tid, [(status, body)] = await self._drive([PLACED], live, audits)
        assert status == 200 and body["placed"] is True
        assert live.ledger.spent(UID, time.time()) == 250.0
        assert _spend_audits(audits) == []

    async def test_an_unverified_outcome_keeps_the_spend(self, live, audits):
        """The venue may hold that order; a release here is the loose direction."""
        engine, tid, [(status, body)] = await self._drive([UNVERIFIED], live, audits)
        assert status == 200 and body["placed"] is None
        assert live.ledger.spent(UID, time.time()) == 250.0
        assert _spend_audits(audits) == []

    async def test_a_retry_after_a_refusal_records_again_and_a_fill_counts_once(self, live, audits):
        engine, tid, results = await self._drive([REFUSAL, PLACED], live, audits)
        assert [b["placed"] for _, b in results] == [False, True]
        assert live.ledger.spent(UID, time.time()) == 250.0
        assert [r for r, _ in _spend_audits(audits)] == ["RELEASED"]

    async def test_a_refusal_after_an_unverified_attempt_keeps_that_attempts_spend(self, live, audits):
        """Attempt 1 submitted an order the venue confirmed neither way (the
        spend stands, the idea stays pending). Attempt 2 is refused -- and its
        authorization recorded NOTHING (the ledger held the ref), so the
        refusal releases nothing: the venue may still hold attempt 1's order."""
        engine, tid, results = await self._drive([UNVERIFIED, REFUSAL], live, audits)
        assert [b["placed"] for _, b in results] == [None, False]
        assert live.ledger.spent(UID, time.time()) == 250.0
        assert _spend_audits(audits) == []

    async def test_the_day_is_not_locked_out_by_refusals(self, live, audits):
        """The money-facing claim: under a $300 day, a refused $250 order no
        longer denies the next $250 order."""
        live.bind(per_trade=500, daily=300)
        engine = _LiveEngine([REFUSAL, PLACED])
        async with gateway_client(engine, _WebHandler(users={})) as c:
            ug_live = ug.CONFIG
            ug.CONFIG = SimpleNamespace(telegram=SimpleNamespace(admin_ids=""),
                                        is_live=lambda: False, per_user_live_enabled=True,
                                        exchange=SimpleNamespace(default_leverage=5))
            try:
                t1 = await _propose(c, tg=UID)
                t2 = await _propose(c, tg=UID)
            finally:
                ug.CONFIG = ug_live
            engine._manual_margin_override[t1] = 50
            engine._manual_margin_override[t2] = 50
            s1, b1 = await _confirm(c, t1)
            s2, b2 = await _confirm(c, t2)
        assert (s1, b1["placed"]) == (200, False)
        assert (s2, b2["placed"]) == (200, True), b2
        assert live.ledger.spent(UID, time.time()) == 250.0

    async def test_a_release_that_cannot_land_is_said_and_the_spend_stays(self, live, audits, monkeypatch):
        """The refusal is still answered as a refusal (never a 500 over an
        accounting fault), the audit names the class and never the text, and
        the day keeps counting the order."""
        real = json_store.atomic_write_json
        writes: list = []

        def _second_write_fails(*a, **k):
            # The record's write (the first) lands; the release's (the
            # second) does not. Failing every write would fail the RECORD
            # and deny at the envelope, which is a different test.
            writes.append(a)
            if len(writes) == 2:
                raise OSError("disk full at /secret/path")
            return real(*a, **k)

        monkeypatch.setattr(json_store, "atomic_write_json", _second_write_fails)
        engine, tid, [(status, body)] = await self._drive([REFUSAL], live, audits)
        assert len(writes) == 2, "the record's write and the release's"
        assert status == 200 and body["placed"] is False
        assert live.ledger.spent(UID, time.time()) == 250.0
        assert _spend_audits(audits) == [("KEPT", {"user": UID, "error": "OSError"})]
        rows = [r.getMessage() for r in audits if getattr(r, "action", "") == "web_live_spend"]
        assert not any("/secret/path" in m for m in rows)

    async def test_a_refusal_before_the_envelope_records_releases_nothing(self, live, audits):
        """The envelope DENIES (per-trade cap), so nothing was recorded and
        the handler never reaches a confirm: no release, no NOT_HELD row."""
        live.bind(per_trade=100)
        engine, tid, [(status, body)] = await self._drive([REFUSAL], live, audits)
        assert status == 403 and body["error"] == "authority_denied"
        assert engine.confirm_calls == []
        assert _spend_audits(audits) == []


# ── the shape the drives cannot see from one run ────────────────────────────


def test_the_release_is_keyed_on_this_attempts_record_and_a_false_placed():
    """`_release_web_live_spend` is reached under exactly `placed is False`
    with the trade id THIS attempt recorded -- a scan, stated as one, beside
    the drives above: the unverified drive proves `is None` is not `is False`,
    and this pins that no second call site reaches the release by another
    condition."""
    src = code_only(inspect.getsource(ug.handle_trade_confirm))
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_release_web_live_spend"]
    assert len(calls) == 1
    guards = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and any(c is calls[0] for c in ast.walk(n))]
    inner = min(guards, key=lambda n: n.end_lineno - n.lineno)
    cond = ast.unparse(inner.test)
    assert "placed is False" in cond and "web_live_recorded is not None" in cond, cond
    assert "recorded" in src and "web_live_recorded = trade_id" in src


def test_the_recorder_is_the_one_that_says_recorded():
    """`recorded` is the ledger's own answer to `record` (True for an added
    row), not a flag set on allow: a duplicate ref must read False."""
    src = code_only(inspect.getsource(ug._authorize_web_live_trade))
    assert "recorded = bool(ledger.record(tg_id, notional, now, ref=trade_id))" in src
    assert "WebLiveAuthorization(True, [], recorded)" in src
