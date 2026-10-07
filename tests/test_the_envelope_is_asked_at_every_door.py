"""The Authority Envelope is asked at every door, not only the web confirm.

A person binds an envelope on the website ("only majors, max $500 a trade,
$2,000 a day, only on bitget") and three surfaces say it "caps and
authorizes every live order". It was asked at ONE door,
`bot/web/user_gateway.py`'s confirm handler. Driven on the unfixed tree with
per-user live on, a person with their own keys and an enforce envelope bound
(BTC only, $50 a trade, $100 a day):

    /trade LONG SOL, $500 margin, confirmed on TELEGRAM
      -> "LIVE LONG SOL/USDT opened", execute awaited once, ledger $0.00

The symbol list, both caps and the day's record were all skipped, and every
web order after it was checked against a day the Telegram orders were
missing from. `bot/guardian/order_authority.py` is the one leaf both doors
ask; the engine asks it right before `executor.execute` for every human
confirm that runs on a person's own account under an enforce envelope, so a
door added tomorrow inherits it.
"""
from __future__ import annotations

import ast
import inspect
import logging
import time
import types
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG
from bot.core.confirm_result import placed_nothing
from bot.core.engine import RuneClawEngine
from bot.guardian import authority_ledger as al
from bot.guardian import order_authority as oa
from bot.guardian import user_authority_store as uas
from bot.guardian.authority import compile_envelope
from bot.guardian.authority_ledger import AuthoritySpendLedger
from bot.skills.manual_trade import build_manual_idea, register_manual_idea
from bot.web import user_gateway as ug
from tests.source_scan import code_only
from tests.test_a_seal_failure_does_not_unplace_a_trade import _engine as _seal_engine
from tests.test_a_seal_failure_does_not_unplace_a_trade import _no_website_sync as _seal_no_website_sync
from tests.test_a_seal_failure_does_not_unplace_a_trade import _run
from tests.test_an_unverified_submission_is_neither_a_fill_nor_a_failure import UNVERIFIED_CARD

_no_website_sync = _seal_no_website_sync

UID = "123456"
FILLED = "\U0001f7e2 LIVE LONG SOL/USDT opened @ $150.0000"
REFUSED = "⚠️ EXECUTION FAILED: Bitget requires >= $60.00 notional"
TOO_BIG = "SOL" and "$500"


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """One store, ONE ledger for the web door and the engine, a credential
    store that says the person's keys read, and per-user live ON."""
    store = uas.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(uas, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(al, "_USER_LEDGER", ledger)
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    cred = types.SimpleNamespace(
        credential_state=lambda u: "readable", get_venue=lambda u: "bitget",
        get=lambda u: {"api_key": "k", "api_secret": "s"}, has=lambda u: True,
        list_venues=lambda u: ["bitget"])
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", lambda: cred)
    was = CONFIG.per_user_live_enabled
    object.__setattr__(CONFIG, "per_user_live_enabled", True)
    try:
        yield store, ledger
    finally:
        object.__setattr__(CONFIG, "per_user_live_enabled", was)


def _bind(store, *, per_trade=100, daily=1000, symbols=("BTC",), venues=("bitget",),
          mode="enforce"):
    store.bind(UID, compile_envelope({
        "mode": mode, "label": "t", "allowed_venues": list(venues),
        "symbol_allowlist": list(symbols), "max_notional_per_trade_usd": per_trade,
        "max_notional_daily_usd": daily}))


def _own(answer=FILLED, lev=5, venue=None):
    """The person's OWN executor: answers `answer`, places at `lev`."""
    ex = types.SimpleNamespace(
        execute=AsyncMock(return_value=answer),
        _compute_target_leverage=lambda s, i=None: lev,
        fetch_balance=AsyncMock(return_value={"total": 50000.0, "free": 50000.0}),
        _positions={}, open_positions=[], closed_positions=[], user_id=UID)
    if venue is not None:
        ex._venue = types.SimpleNamespace(id=venue)
    return ex


def _engine(tmp_path, own, *, symbol="SOL", margin=500.0):
    """The seal suite's engine with a typed ticket, routed to `own`."""
    from bot.core.engine import _LiveRecheck
    engine, _seed = _seal_engine(tmp_path)
    engine._pending_ideas.clear()
    engine._pending_atr.clear()
    price = 150.0 if symbol == "SOL" else 65000.0
    idea = build_manual_idea("LONG", symbol, price, price * 0.98, price * 1.04,
                             order_type="market")
    register_manual_idea(engine, idea, margin)
    engine._pending_atr[idea.id] = price * 0.013
    engine.scanner._get_exchange.return_value.fetch_ticker = AsyncMock(
        return_value={"last": price})
    engine._executor_for = lambda uid, venue=None: own
    engine._live_recheck_context = AsyncMock(
        return_value=_LiveRecheck(50000.0, 0, 50000.0, [], "bitget"))
    # `get` answers the record a vouched trader has: the engine reads the
    # admission before a live fill and refuses a store it cannot ask
    # (`practice_fill.admission_unread`).
    engine._user_store = types.SimpleNamespace(
        get=lambda u: {"role": "trader", "admitted_by": "999"},
        can_trade_live=lambda u: True, max_margin=lambda u: None,
        live_trading_revoked=lambda u: False)
    return engine, idea


def _confirm(engine, idea, user_id=UID):
    with patch.object(type(CONFIG), "is_live", return_value=True), \
         patch("bot.core.engine.get_exchange_position_count", new=AsyncMock(return_value=0)), \
         patch("bot.core.engine.invalidate_position_count_cache"):
        return _run(engine.confirm_trade(idea.id, user_id=user_id))


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def audits():
    from bot.utils.logger import system_log, trade_log
    h = _Records()
    trade_log.addHandler(h)
    system_log.addHandler(h)
    try:
        yield h.records
    finally:
        trade_log.removeHandler(h)
        system_log.removeHandler(h)


def _results(records, action):
    return [getattr(r, "result", "") for r in records if getattr(r, "action", "") == action]


# ── the Telegram door ────────────────────────────────────────────────────────


class TestATelegramConfirmIsBoundByTheEnvelope:

    def test_a_ticket_outside_the_envelope_is_refused_and_records_nothing(self, wired, tmp_path, audits):
        """The drive: BTC-only, $50 a trade; a $500 SOL ticket at 5x."""
        store, ledger = wired
        _bind(store, per_trade=50, daily=100, symbols=("BTC",))
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED by your Authority Envelope: "), result
        assert "SOL" in result and "per-trade cap $50.00" in result and "Nothing was placed" in result
        assert placed_nothing(result) is True
        own.execute.assert_not_awaited()
        assert ledger.spent(UID, time.time()) == 0.0
        assert "DENY" in _results(audits, "order_authority")
        assert idea.id in engine._pending_ideas, "a refused ticket stays for the person to retry"

    def test_a_ticket_within_the_envelope_places_and_records_its_notional(self, wired, tmp_path):
        store, ledger = wired
        _bind(store, per_trade=100, daily=1000, symbols=("BTC",))
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=10.0)
        result = _confirm(engine, idea)
        assert result.startswith("\U0001f7e2 LIVE"), result
        own.execute.assert_awaited_once()
        assert ledger.spent(UID, time.time()) == 50.0, "$10 at 5x"
        # The stamp is on the idea the EXECUTOR is handed -- the object the
        # round-up bound reads -- which the confirm path may have copied from
        # the pending one.
        handed = own.execute.await_args.args[0]
        assert getattr(handed, "authorized_notional_usd", None) == 50.0, (
            "the round-up bound reads the stamp")

    def test_the_leverage_is_the_executors_own(self, wired, tmp_path):
        store, ledger = wired
        _bind(store, per_trade=100, daily=1000, symbols=("BTC",))
        own = _own(lev=3)
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=10.0)
        _confirm(engine, idea)
        assert ledger.spent(UID, time.time()) == 30.0

    def test_the_final_size_is_what_is_asked_about(self, wired, tmp_path):
        """A $500 ticket under a $20 per-user margin cap is a $100 order at
        5x, and that is what the envelope hears and records -- not the typed
        $500, which the web door's pre-ask reads as a floor-safe figure."""
        store, ledger = wired
        _bind(store, per_trade=100, daily=1000, symbols=("BTC",))
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=500.0)
        engine._user_store.max_margin = lambda u: 20.0
        result = _confirm(engine, idea)
        assert result.startswith("\U0001f7e2 LIVE"), result
        assert own.execute.await_args.args[1] == 20.0
        assert ledger.spent(UID, time.time()) == 100.0

    def test_the_executors_own_venue_is_the_one_asked_about(self, wired, tmp_path):
        """An order routed to a bybit executor is a bybit order, whatever the
        person's ACTIVE venue in the credential store says."""
        store, ledger = wired
        _bind(store, per_trade=100, daily=1000, symbols=("BTC",), venues=("bitget",))
        own = _own(venue="bybit")
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=10.0)
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED by your Authority Envelope: "), result
        assert "bybit" in result
        own.execute.assert_not_awaited()

    def test_a_venue_refusal_after_the_ask_takes_the_spend_back(self, wired, tmp_path, audits):
        store, ledger = wired
        _bind(store, per_trade=100, daily=1000, symbols=("BTC",))
        own = _own(answer=REFUSED)
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=10.0)
        result = _confirm(engine, idea)
        assert placed_nothing(result) is True
        assert ledger.spent(UID, time.time()) == 0.0
        assert "RELEASED" in _results(audits, "web_live_spend")

    def test_an_unverified_outcome_keeps_the_spend(self, wired, tmp_path, audits):
        """The venue may hold that order; the day counts it."""
        store, ledger = wired
        _bind(store, per_trade=100, daily=1000, symbols=("BTC",))
        own = _own(answer=UNVERIFIED_CARD)
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=10.0)
        _confirm(engine, idea)
        assert ledger.spent(UID, time.time()) == 50.0
        assert _results(audits, "web_live_spend") == []


class TestWhoIsNotAsked:

    def test_a_person_with_no_envelope_places_as_before(self, wired, tmp_path):
        """Telegram per-user live never required an envelope; requiring one
        is a product decision, stated in the leaf, not made here."""
        store, ledger = wired
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)
        result = _confirm(engine, idea)
        assert result.startswith("\U0001f7e2 LIVE"), result
        assert ledger.spent(UID, time.time()) == 0.0

    def test_a_shadow_envelope_is_not_asked(self, wired, tmp_path):
        store, ledger = wired
        _bind(store, per_trade=50, symbols=("BTC",), mode="shadow")
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)
        assert _confirm(engine, idea).startswith("\U0001f7e2 LIVE")
        assert ledger.spent(UID, time.time()) == 0.0

    def test_the_operators_shared_executor_is_never_asked(self, wired, tmp_path):
        """With per-user live OFF every confirm runs on the shared operator
        executor, and an envelope bound for the person is a grant over
        THEIR account, not the operator's."""
        store, ledger = wired
        object.__setattr__(CONFIG, "per_user_live_enabled", False)
        _bind(store, per_trade=50, symbols=("BTC",))
        engine, idea = _engine(tmp_path, _own(), symbol="SOL", margin=500.0)
        engine._executor_for = lambda uid, venue=None: engine.live_executor
        engine.live_executor.execute = AsyncMock(return_value=FILLED)
        result = _confirm(engine, idea)
        assert result.startswith("\U0001f7e2 LIVE"), result
        engine.live_executor.execute.assert_awaited_once()
        assert ledger.spent(UID, time.time()) == 0.0

    def test_an_auto_confirm_runs_on_the_shared_executor_which_is_never_asked(self, wired, tmp_path):
        """No clause about the CALLER is written into the ask, because none
        is needed: the property that makes it so is `_executor_for`'s first
        rule, driven here on the real resolver with per-user live ON -- an
        auto or unattended confirm resolves to the shared executor, named
        venue or not, and the shared executor is refused by identity."""
        store, ledger = wired
        _bind(store, per_trade=50, symbols=("BTC",))
        engine, idea = _engine(tmp_path, _own(), symbol="SOL", margin=500.0)
        real = RuneClawEngine._executor_for
        assert real(engine, "auto") is engine.live_executor
        assert real(engine, "") is engine.live_executor
        assert real(engine, "auto", "bybit") is engine.live_executor
        assert engine._own_account_order_authority(
            "auto", idea.id, idea, 500.0, engine.live_executor) is None
        assert engine._own_account_order_authority(
            UID, idea.id, idea, 500.0, engine.live_executor) is None

    def test_a_store_that_cannot_be_read_refuses_by_class(self, wired, tmp_path, monkeypatch):
        """In doubt, deny -- the envelope's own rule -- and the exception's
        CLASS is all that reaches the person."""
        store, ledger = wired
        _bind(store, per_trade=50, symbols=("BTC",))

        def _boom(self, uid):
            raise PermissionError("/secret/path/ua.json")

        monkeypatch.setattr(uas.UserAuthorityStore, "is_enforcing", _boom)
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=10.0)
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED by your Authority Envelope: ")
        assert "could not be read (PermissionError)" in result and "/secret" not in result
        own.execute.assert_not_awaited()


# ── the web door and the engine ask ONCE ────────────────────────────────────


class TestTheWebPreAskAndTheEnginesAskAreOneRecording:

    def test_one_row_and_no_double_count_under_the_daily_cap(self, wired, tmp_path):
        """A $50 ticket at 5x under a $300 day: the web door records $250,
        the engine asks again on the same ref, reads the day WITHOUT that
        row, allows, and records nothing new."""
        store, ledger = wired
        _bind(store, per_trade=300, daily=300, symbols=("BTC",))
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="BTC", margin=50.0)
        pre = ug._authorize_web_live_trade({}, engine, UID, idea.id, executor=own)
        assert pre.ok is True and pre.recorded is True
        assert ledger.spent(UID, time.time()) == 250.0
        result = _confirm(engine, idea)
        assert result.startswith("\U0001f7e2 LIVE"), result
        assert ledger.spent(UID, time.time()) == 250.0, "one row for one order"
        assert len(ledger._book[UID]) == 1

    def test_the_ledger_leaves_out_the_ref_being_asked_about(self, tmp_path):
        ledger = AuthoritySpendLedger(state_file=str(tmp_path / "l.json"))
        now = time.time()
        assert ledger.record(UID, 250.0, now, ref="T1") is True
        assert ledger.record(UID, 40.0, now, ref="T2") is True
        assert ledger.spent(UID, now) == 290.0
        assert ledger.spent(UID, now, excluding_ref="T1") == 40.0
        assert ledger.spent(UID, now, excluding_ref="T9") == 290.0

    def test_the_web_door_hands_the_leafs_answer_on(self, wired, tmp_path, monkeypatch):
        store, ledger = wired
        _bind(store)
        engine, idea = _engine(tmp_path, _own(), symbol="BTC", margin=10.0)
        planted = oa.OrderAuthorization(False, ["PLANTED"], False, 1.0)
        monkeypatch.setattr(oa, "authorize_order", lambda *a, **k: planted)
        auth = ug._authorize_web_live_trade({}, engine, UID, idea.id, executor=_own())
        assert auth == (False, ["PLANTED"], False)


# ── the leaf's own words ─────────────────────────────────────────────────────


class TestTheLeaf:

    def test_no_envelope_and_no_idea(self, wired, tmp_path):
        store, ledger = wired
        assert oa.authorize_order(UID, "T1", object(), margin=10, executor=_own(),
                                  venue="bitget", ledger=ledger).reasons == [oa.NO_ENVELOPE]
        _bind(store)
        assert oa.authorize_order(UID, "T1", None, margin=10, executor=_own(),
                                  venue="bitget", ledger=ledger).reasons == [oa.NOT_PENDING]

    def test_a_leverage_under_one_is_not_a_reading(self, wired, tmp_path):
        store, ledger = wired
        _bind(store)
        auth = oa.authorize_order(UID, "T1", types.SimpleNamespace(asset="BTC/USDT"), margin=10,
                                  executor=_own(lev=0), venue="bitget", ledger=ledger)
        assert auth.ok is False and auth.reasons == [oa.LEVERAGE_UNREAD]
        assert ledger.spent(UID, time.time()) == 0.0

    def test_no_envelope_denies_before_any_account_is_resolved_at_the_web_door(self, wired, tmp_path, monkeypatch):
        """The leaf answers NO_ENVELOPE; the door resolved an account for a
        typed margin first, which reads nothing and places nothing."""
        store, ledger = wired
        engine, idea = _engine(tmp_path, _own(), symbol="BTC", margin=10.0)
        auth = ug._authorize_web_live_trade({}, engine, UID, idea.id, executor=_own())
        assert auth == (False, [oa.NO_ENVELOPE], False)

    def test_an_unreadable_leverage_denies_by_class(self, wired, tmp_path):
        store, ledger = wired
        _bind(store)
        idea = types.SimpleNamespace(asset="BTC/USDT")

        def _raise(s, i=None):
            raise ValueError("apiKey=SECRETVALUE")

        auth = oa.authorize_order(UID, "T1", idea, margin=10,
                                  executor=types.SimpleNamespace(_compute_target_leverage=_raise),
                                  venue="bitget", ledger=ledger)
        assert auth.ok is False and auth.reasons == [f"{oa.LEVERAGE_UNREAD} (ValueError)"]
        assert ledger.spent(UID, time.time()) == 0.0

    def test_a_stamp_that_cannot_be_written_denies(self, wired, tmp_path):
        store, ledger = wired
        _bind(store)

        class _Sealed:
            __slots__ = ("asset",)

            def __init__(self):
                self.asset = "BTC/USDT"

        auth = oa.authorize_order(UID, "T1", _Sealed(), margin=10, executor=_own(),
                                  venue="bitget", ledger=ledger)
        assert auth.ok is False and auth.reasons == [oa.STAMP_FAILED]
        assert ledger.spent(UID, time.time()) == 0.0

    def test_no_margin_is_an_unknown_notional_and_reads_no_leverage(self, wired, tmp_path):
        store, ledger = wired
        _bind(store)
        asked = []
        ex = types.SimpleNamespace(_compute_target_leverage=lambda s, i=None: asked.append(1) or 5)
        auth = oa.authorize_order(UID, "T1", types.SimpleNamespace(asset="BTC/USDT"), margin=None,
                                  executor=ex, venue="bitget", ledger=ledger)
        assert auth.ok is False and any("notional is unknown" in r for r in auth.reasons)
        assert asked == []

    def test_a_denied_answer_still_carries_the_notional_it_computed(self, wired, tmp_path):
        store, ledger = wired
        _bind(store, per_trade=10)
        auth = oa.authorize_order(UID, "T1", types.SimpleNamespace(asset="BTC/USDT"), margin=10,
                                  executor=_own(), venue="bitget", ledger=ledger)
        assert auth.ok is False and auth.notional == 50.0 and auth.recorded is False


# ── the shape, stated as scans beside the drives ────────────────────────────


def _calls(fn_src, name):
    tree = ast.parse(fn_src)
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and (getattr(n.func, "attr", "") == name or getattr(n.func, "id", "") == name)]


def test_the_engine_asks_right_before_the_order_and_releases_after_it():
    """The ask sits AFTER every other refusal (it is the last statement
    before `executor.execute`) and the release after the order reads both
    words: a refusal releases, an unverified outcome keeps."""
    import textwrap
    src = code_only(textwrap.dedent(inspect.getsource(RuneClawEngine._confirm_trade_inner)))
    asks = _calls(src, "_own_account_order_authority")
    execs = [n for n in _calls(src, "execute") if ast.unparse(n.func) == "executor.execute"]
    assert len(asks) == 1 and len(execs) == 1
    assert asks[0].lineno < execs[0].lineno
    between = src.splitlines()[asks[0].lineno:execs[0].lineno - 1]
    assert not any(line.strip().startswith("return ") and "Authority Envelope" not in line
                   for line in between), between
    rel = _calls(src, "release_order_spend")
    assert len(rel) == 1 and rel[0].lineno > execs[0].lineno
    tail = "\n".join(src.splitlines()[execs[0].lineno:rel[0].lineno])
    assert "outcome_unverified(result)" in tail and "placed_nothing(result)" in tail


def test_the_web_door_keeps_no_copy_of_the_ask():
    door = code_only(inspect.getsource(ug._authorize_web_live_trade))
    assert "authorize_order(" in door
    for spelling in ("authorize(env", "ledger.spent(", "ledger.record(", "setattr(idea"):
        assert spelling not in door, spelling
    rel = code_only(inspect.getsource(ug._release_web_live_spend))
    assert "release_order_spend(" in rel and ".release(" not in rel


def test_the_three_surfaces_still_make_the_claim_the_engine_now_keeps():
    """The claim this slice makes true; a reworded claim moves this pin."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    assert "caps and authorizes every live order" in (root / "docs/authority_nl.md").read_text(encoding="utf-8")
    dashboard = (root / "app/public/js/dashboard.js").read_text(encoding="utf-8")
    assert "caps and authorizes</b> every live order" in dashboard
    assert "authorizing every order" in (root / "app/lib/guardian_readiness.js").read_text(encoding="utf-8")
