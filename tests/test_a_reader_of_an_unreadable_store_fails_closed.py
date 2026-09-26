"""What each surface answers when the store under it will not read.

The stores refuse to write over a file they cannot read (the sibling suite,
``test_an_unreadable_store_is_not_an_empty_one``). That is half of it: every
reader used to get ``{}`` for a failed read and turned it into the reassuring
answer one layer up. Driven on the unfixed code:

* the engine bound a per-user executor to ``None`` -- the operator default,
  the LOOSEST a reduce-only leverage preference resolves to -- for a person
  who may have pinned 1x, and the executor kept it for its whole life;
* a confirm skipped the person's chosen strategy veto, because a selections
  file that did not read read as "no strategy chosen";
* the web answered ``selected: null`` ("no strategy armed") and
  ``bound: false`` ("no authority envelope") about files nobody read;
* the account purge reported ``none`` -- "nothing was stored" -- for a
  leverage, strategy, recall or profile file it could not read, so a deletion
  the person asked for was reported as having found nothing to delete.

Each of those answers now names the fact: the leverage is sized at the
tightest a preference can be until the file reads, the confirm is refused,
the web answers 503 with a sentence, and the purge says ``error``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.engine as engine_mod
import bot.core.live_executor as le
from bot.core import user_leverage_store as lev
from bot.core import user_strategy_store as strat
from bot.core.engine import RuneClawEngine
from bot.guardian import user_authority_store as uas
from bot.utils.json_store import StoreUnreadable
from bot.web import user_gateway as ug
from tests.test_one_user_one_venue_one_book import BITGET, UID, _engine, _Store

CORRUPT = b'{"7": 2, "8": "conservative", "sk-live-FILE-TEXT'


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def per_user():
    original = engine_mod.CONFIG.per_user_live_enabled
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", True)
    yield
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


def _corrupt(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CORRUPT)


def _run(coro):
    return asyncio.run(coro)


# ── the leverage an order is sized at ────────────────────────────────────

class TestAnUnreadLeveragePreference:
    def test_the_bind_records_unread_not_the_operator_default(self, state, per_user,
                                                                monkeypatch):
        _corrupt(state / "user_leverage.json")
        eng = _engine(monkeypatch, _Store(creds={"bitget": BITGET}))
        ex = eng._executor_for(UID)
        assert ex is not None and ex is not eng.live_executor
        assert ex._user_leverage_pref is lev.UNREAD, (
            "an unreadable preferences file was bound as no preference")

    def test_the_order_is_sized_at_the_tightest_until_the_file_reads(
            self, state, per_user, monkeypatch):
        from bot.config import RUNTIME
        path = state / "user_leverage.json"
        _corrupt(path)
        monkeypatch.setattr(RUNTIME, "_leverage_override", 5)
        said = []
        monkeypatch.setattr(le, "audit",
                            lambda *a, **k: said.append(k.get("result")))
        eng = _engine(monkeypatch, _Store(creds={"bitget": BITGET}))
        ex = eng._executor_for(UID)
        assert ex._standard_leverage("BTC/USDT") == lev.TIGHTEST_PREF == 1
        assert ex._standard_leverage("BTC/USDT") == 1
        assert said.count("UNREAD") == 1, "said on every order, or never"
        # The file reads again: the person's own preference, not the tightest.
        path.write_text(json.dumps({UID: 2}))
        assert ex._standard_leverage("BTC/USDT") == 2
        assert ex._user_leverage_pref == 2

    def test_a_readable_file_with_no_preference_is_the_operator_standard(
            self, state, per_user, monkeypatch):
        from bot.config import RUNTIME
        monkeypatch.setattr(RUNTIME, "_leverage_override", 5)
        eng = _engine(monkeypatch, _Store(creds={"bitget": BITGET}))
        ex = eng._executor_for(UID)
        assert ex._user_leverage_pref is None
        assert ex._standard_leverage("BTC/USDT") == 5

    def test_the_command_says_it_was_not_saved(self, state, monkeypatch):
        """``/leverage set 3`` over a file that will not read answered "saved"
        and erased everybody else's preference."""
        from bot.skills.engine_ops_commands import EngineOpsCommands
        path = state / "user_leverage.json"
        _corrupt(path)
        replies = []

        async def _reply(update, text, **k):
            replies.append(text)

        host = SimpleNamespace(_is_admin=lambda u: False, _get_tg_id=lambda u: UID,
                               _reply=_reply,
                               engine=SimpleNamespace(_user_executors={}))
        for args in (["set", "3"], ["reset"]):
            replies.clear()
            _run(EngineOpsCommands._cmd_leverage.__wrapped__(
                host, SimpleNamespace(), SimpleNamespace(args=args)))
            assert replies == [lev.UNREAD_SENTENCE], args
        assert path.read_bytes() == CORRUPT


# ── the strategy veto on a confirm ───────────────────────────────────────

class _PastTheGate(Exception):
    """Raised by the first thing the confirm reads after the strategy gate,
    so a test can tell "the gate let it through" from "the gate refused"."""


def _confirming_engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._pending_ideas = {"T1": SimpleNamespace(asset="BTC/USDT", confidence=0.4,
                                                direction="LONG")}
    eng._pending_atr = SimpleNamespace(
        get=lambda *a: (_ for _ in ()).throw(_PastTheGate()))
    return eng


class TestAnUnreadStrategySelection:
    def test_a_confirm_is_refused_and_says_nothing_was_placed(self, state, monkeypatch):
        from bot.core.confirm_result import placed_nothing
        _corrupt(state / "user_strategy.json")
        seen = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda *a, **k: seen.append(k.get("result")))
        out = _run(_confirming_engine()._confirm_trade_inner("T1", user_id=UID))
        assert out.startswith("\U0001f6e1") and "could not be read" in out
        assert "Nothing was placed" in out
        assert placed_nothing(out) is True
        assert "UNREAD" in seen

    def test_a_missing_file_is_no_selection_and_the_confirm_goes_on(self, state):
        with pytest.raises(_PastTheGate):
            _run(_confirming_engine()._confirm_trade_inner("T1", user_id=UID))

    def test_the_operator_loop_is_not_gated_by_it(self, state):
        """The veto is the person's, on their own confirms; the auto loop has
        its own stance and never read this file."""
        _corrupt(state / "user_strategy.json")
        with pytest.raises(_PastTheGate):
            _run(_confirming_engine()._confirm_trade_inner("T1", user_id="auto"))

    def test_the_command_says_it_could_not_read_it(self, state):
        from bot.skills.trading_commands import TradingCommands
        _corrupt(state / "user_strategy.json")
        replies = []

        async def _reply(update, text, **k):
            replies.append(text)

        host = SimpleNamespace(_get_tg_id=lambda u: UID, _reply=_reply)
        for args in ([], ["off"], ["safe", "scalper"]):
            replies.clear()
            _run(TradingCommands._cmd_mystrategy.__wrapped__(
                host, SimpleNamespace(), SimpleNamespace(args=args)))
            text = "\n".join(replies)
            assert strat.UNREAD_SENTENCE in text, args
            assert "None selected" not in text and "cleared" not in text.lower()
        assert (state / "user_strategy.json").read_bytes() == CORRUPT


# ── the web ──────────────────────────────────────────────────────────────

def _req(query=None, body=None):
    async def _json():
        return body or {}

    return SimpleNamespace(app={"tg_handler": SimpleNamespace(), "engine": None},
                           query=query or {}, json=_json)


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **k: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda *a, **k: True)


class TestTheWebSaysItCouldNotRead:
    def test_the_strategy_routes_answer_503_not_no_strategy(self, state, web):
        _corrupt(state / "user_strategy.json")
        resp = _run(ug.handle_user_strategy_get(_req(query={"telegram_id": UID})))
        assert resp.status == 503
        assert _body(resp) == {"error": "strategy_store_unreadable",
                               "detail": strat.UNREAD_SENTENCE}
        for strategy in ("off", "safe scalper"):
            resp = _run(ug.handle_user_strategy_set(
                _req(body={"telegram_id": UID, "strategy": strategy})))
            assert resp.status == 503, strategy
            assert _body(resp)["error"] == "strategy_store_unreadable"
        resp = _run(ug.handle_user_strategy_set(_req(body={
            "telegram_id": UID, "strategy": "x", "kind": "community",
            "slug": "longonly", "label": "Long only",
            "gates": {"direction": "long_only"}})))
        assert resp.status == 503
        assert (state / "user_strategy.json").read_bytes() == CORRUPT

    def test_the_authority_routes_answer_503_not_nothing_bound(
            self, tmp_path, web, monkeypatch):
        path = tmp_path / "ua.json"
        _corrupt(path)
        monkeypatch.setattr(uas, "_STORE", uas.UserAuthorityStore(str(path)))
        calls = [
            (ug.handle_authority_status, {"telegram_id": UID}, None),
            (ug.handle_authority_revoke, None, {"telegram_id": UID}),
            (ug.handle_authority_mode, None, {"telegram_id": UID, "mode": "off"}),
            (ug.handle_authority_apply, None,
             {"telegram_id": UID, "text": "max $50 per trade", "mode": "shadow"}),
            (ug.handle_guardian_review_tighten, None,
             {"telegram_id": UID, "target_user": UID,
              "tighten": {"max_notional_per_trade_usd": 10}}),
        ]
        for handler, query, body in calls:
            resp = _run(handler(_req(query=query, body=body)))
            assert resp.status == 503, handler.__name__
            got = _body(resp)
            assert got["error"] == "authority_store_unreadable", handler.__name__
            assert got["detail"] == uas.UNREAD_SENTENCE
            assert "sk-live" not in resp.body.decode("utf-8")
        assert path.read_bytes() == CORRUPT

    def test_the_readers_behind_the_live_gate_read_it_as_not_enforcing(
            self, tmp_path, monkeypatch):
        """The fail-CLOSED readers were already closed; a raise keeps them so."""
        from bot.web import web_live_admin
        path = tmp_path / "ua.json"
        _corrupt(path)
        monkeypatch.setattr(uas, "_STORE", uas.UserAuthorityStore(str(path)))
        assert ug._web_envelope_enforcing(None, UID) is False
        assert web_live_admin._envelope_enforcing(UID) is False

    def test_the_purge_says_error_not_none(self, state, tmp_path, monkeypatch, web):
        """A deletion the person asked for, over a file nobody could read, is an
        error: ``none`` says nothing was stored for them."""
        from bot.core import user_memory_store, user_profile_store
        from tests.test_chat_guards_say_what_ran import _conversations, _gateway_handler, _purge, _stub_the_other_stores
        real = {m: m.clear for m in (lev, strat, user_memory_store)}
        _stub_the_other_stores(monkeypatch, tmp_path)
        for mod, fn in real.items():            # the four stores under test are real
            monkeypatch.setattr(mod, "clear", fn)
        monkeypatch.setattr(ug, "_profile_store", user_profile_store)
        mem, prof = tmp_path / "mem.json", tmp_path / "prof.json"
        monkeypatch.setenv("RUNECLAW_USER_MEMORY_FILE", str(mem))
        monkeypatch.setenv("RUNECLAW_USER_PROFILE_FILE", str(prof))
        for p in (state / "user_leverage.json", state / "user_strategy.json", mem, prof):
            _corrupt(p)
        h = _gateway_handler(tmp_path)
        h.conversations = _conversations(tmp_path)
        h.users = SimpleNamespace(forget=lambda uid: False)
        _status, body = _purge(h, UID)
        for key in ("leverage_preference", "strategy_preference",
                    "agent_memory", "agent_profile"):
            assert body["stores"][key] == "error", key
        assert body["purged"] is not True
        for p in (state / "user_leverage.json", state / "user_strategy.json", mem, prof):
            assert p.read_bytes() == CORRUPT


# ── the venue selection ──────────────────────────────────────────────────

class TestAnUnreadVenueSelection:
    def test_the_card_says_it_could_not_be_read(self):
        from bot.formatters.venue_card import venue_card
        out = venue_card(connected=["bitget", "bybit"], selected=None, dropped=(),
                         mode="enforce", enforce_available=True)
        assert "could not be read" in out
        assert "You have not chosen any venues" not in out

    def test_the_command_hands_the_card_an_unread_selection(self, tmp_path,
                                                            monkeypatch):
        """Driven through ``/venues`` itself: the card is only as honest as
        what the handler hands it, and ``[]`` would be "you chose nothing"."""
        from bot.core import exchange_credentials, venue_selection
        from bot.skills.trading_commands import TradingCommands
        path = tmp_path / "sel.json"
        _corrupt(path)
        store = venue_selection.VenueSelectionStore(str(path))
        monkeypatch.setattr(venue_selection, "get_venue_selection_store",
                            lambda: store)
        monkeypatch.setattr(
            exchange_credentials, "get_credential_store",
            lambda: SimpleNamespace(list_venues=lambda uid: ["bitget", "bybit"]))
        sends = []

        async def _send(update, text, **k):
            sends.append(text)

        host = SimpleNamespace(_send=_send, _get_tg_id=lambda u: UID,
                               engine=SimpleNamespace())
        _run(TradingCommands._cmd_venues.__wrapped__(
            host, SimpleNamespace(), SimpleNamespace(args=[])))
        assert len(sends) == 1
        assert "could not be read" in sends[0]
        assert "You have not chosen any venues" not in sends[0]
        assert path.read_bytes() == CORRUPT

    def test_the_web_ack_is_null_not_cleared(self, tmp_path, monkeypatch):
        """The website writes a null ``venues`` as "we have not been told";
        ``''`` would tell it the bot had cleared the selection."""
        from bot.core import venue_selection
        from bot.utils import control_pull
        path = tmp_path / "sel.json"
        _corrupt(path)
        store = venue_selection.VenueSelectionStore(str(path))
        monkeypatch.setattr(venue_selection, "get_venue_selection_store", lambda: store)
        assert control_pull._apply_venue_selection(UID, None) is None
        assert control_pull._apply_venue_selection(UID, "bitget,bybit") is None
        assert path.read_bytes() == CORRUPT


# ── the public board ─────────────────────────────────────────────────────

def test_the_leaderboard_command_says_it_failed_not_an_empty_board(tmp_path, monkeypatch):
    from bot.proofofpnl import leaderboard as lb
    from bot.skills.start_commands import StartCommands
    path = tmp_path / "board.json"
    _corrupt(path)
    monkeypatch.setattr(lb, "get_leaderboard_registry",
                        lambda: lb.LeaderboardRegistry(str(path)))
    errors, sends = [], []

    async def _send_error(update, what, exc):
        errors.append((what, type(exc).__name__))

    async def _send(update, text, **k):
        sends.append(text)

    host = SimpleNamespace(_send=_send, _send_error=_send_error)
    _run(StartCommands._cmd_leaderboard.__wrapped__(
        host, SimpleNamespace(), SimpleNamespace(args=[])))
    assert sends == [] and errors == [("the leaderboard", "StoreUnreadable")]


def test_every_raise_is_the_class_the_callers_catch():
    """Every store raises ONE class, so a caller cannot catch one store's
    unreadable file and let another's through."""
    assert lev.StoreUnreadable is strat.StoreUnreadable is StoreUnreadable
    assert uas.StoreUnreadable is StoreUnreadable
    assert issubclass(StoreUnreadable, RuntimeError)
    assert not issubclass(StoreUnreadable, OSError), (
        "a caller catching a write that did not land would swallow an "
        "unreadable file as the same fact")
    logging.getLogger(__name__).debug("ok")
