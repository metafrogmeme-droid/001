"""A live decision is recorded as live, and its confluence is the analyzer's.

Two fields of every learning row the engine wrote said something other than
what happened:

- **`mode`.** `DecisionMemory.mode` defaults to ``"paper"`` and no engine
  writer passed it, so every live entry, every live-mode risk rejection and
  every live close was written ``"paper"``; the vocabulary
  (``paper | demo | live-blocked``) had no ``"live"`` to write. A reader
  separating live evidence from paper evidence -- the live/backtest parity
  work, a calibrator that should not fit paper fills as live ones -- found
  none.
- **`confluence_score`.** Each engine row wrote ``idea.confidence`` there, a
  second copy of ``confidence``, while the analyzer's voter consensus sat
  unread on ``idea.confluence_score``.

`bot.core.engine.decision_mode` is the one reading: ``"paper"`` off the live
path; on it ``"demo"`` when the executor's own venue client trades in its
test environment, else ``"live"``. A live close reads the executor that
closed it, bound into its `on_position_closed` at wiring.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import time
from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.core.engine import decision_mode
from bot.learning.models import DecisionMemory
from bot.utils.models import Direction, TradeIdea

# The live confirm is driven for a caller the test config does not name as
# an operator; LIVE_ADMIN_ONLY would make it a paper fill (conftest).
pytestmark = pytest.mark.usefixtures("members_may_trade_live")

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT = ROOT / "bot"
WRITERS = ("log_decision", "record_trade_decision", "record_closed_outcome")


def _executor(venue_id):
    return SimpleNamespace(_venue=SimpleNamespace(id=venue_id))


def _sandbox(monkeypatch, answer):
    """Stand in for the venue's sandbox reading; record each venue asked."""
    asked: list = []

    def _read(venue, cfg=None):
        asked.append(venue)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("bot.core.exchange_credentials.venue_sandbox", _read)
    return asked


# ── the reading ─────────────────────────────────────────────────────────

class TestTheReading:
    def test_off_the_live_path_is_paper(self, monkeypatch):
        asked = _sandbox(monkeypatch, True)
        assert decision_mode(_executor("bybit"), live=False) == "paper"
        assert asked == []

    @pytest.mark.parametrize("sandbox, word", [(False, "live"), (True, "demo")])
    def test_on_it_the_executors_own_venue_decides(self, monkeypatch, sandbox, word):
        asked = _sandbox(monkeypatch, sandbox)
        assert decision_mode(_executor("bybit"), live=True) == word
        assert asked == ["bybit"]

    @pytest.mark.parametrize("executor", [None, SimpleNamespace(),
                                          SimpleNamespace(_venue=None)])
    def test_an_unread_venue_is_live_and_asks_about_no_venue(self, monkeypatch,
                                                             executor):
        """A default venue would answer for an account it may not be."""
        asked = _sandbox(monkeypatch, True)
        assert decision_mode(executor, live=True) == "live"
        assert asked == []

    def test_a_failed_sandbox_read_is_live(self, monkeypatch):
        _sandbox(monkeypatch, RuntimeError("venue registry unreadable"))
        assert decision_mode(_executor("bitget"), live=True) == "live"


# ── every writer states it ──────────────────────────────────────────────

def _writer_calls():
    for path in sorted(BOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in WRITERS):
                yield path.relative_to(ROOT).as_posix(), node


def _forwards(call):
    """A call whose only argument is ``**kwargs`` hands on its caller's."""
    return (not call.args and len(call.keywords) == 1
            and call.keywords[0].arg is None)


def test_every_learning_row_the_bot_writes_states_its_mode():
    calls = list(_writer_calls())
    found = Counter(c.func.attr for _, c in calls if not _forwards(c))
    assert found["log_decision"] >= 3 and found["record_closed_outcome"] >= 2, (
        f"the walk found fewer writers than exist: {dict(found)}")
    for path, call in calls:
        if _forwards(call):
            continue
        kw = {k.arg for k in call.keywords}
        assert "mode" in kw, (
            f"{path}:{call.lineno} writes a row with no mode, which reads "
            "\"paper\" whatever the trade was")


def test_the_only_forwarders_are_the_orchestrators():
    """Two-way: a new pass-through is named here, and a listed one that no
    longer exists fails, so the exemption above cannot grow in silence."""
    forwarders = sorted((p, c.func.attr) for p, c in _writer_calls() if _forwards(c))
    assert forwarders == [
        ("bot/learning/orchestrator.py", "record_closed_outcome"),
        ("bot/learning/orchestrator.py", "record_trade_decision"),
    ]


def test_every_engine_decision_carries_the_analyzers_confluence():
    engine = "bot/core/engine.py"
    calls = [c for p, c in _writer_calls()
             if p == engine and c.func.attr == "log_decision"]
    assert len(calls) >= 3
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        val = kw.get("confluence_score")
        assert (isinstance(val, ast.Attribute) and val.attr == "confluence_score"), (
            f"{engine}:{call.lineno} writes a confluence the analyzer did not make")


def test_the_row_keeps_both_fields_through_the_store(tmp_path):
    from bot.learning.experience import ExperienceMemory
    from bot.learning.store import LearningStore

    exp = ExperienceMemory(LearningStore(str(tmp_path)))
    exp.record_trade_decision(
        symbol="BTC/USDT", direction="LONG", confidence=0.7,
        confluence_score=None, entry_price=1.0, stop_loss=0.9,
        take_profit=1.2, risk_reward=2.0, position_size_usd=10.0,
        decision="TRADE_ACCEPTED_LIVE", paper_trade_id="T1", mode="live")
    exp.record_closed_outcome(symbol="BTC/USDT", direction="LONG",
                              pnl_result=-1.5, trade_id="T1", mode="demo")
    rows = LearningStore(str(tmp_path)).get_decisions(strict=True)
    assert [r.mode for r in rows] == ["live", "demo"]
    assert rows[0].confluence_score is None
    # A row written before the field became optional still reads.
    assert DecisionMemory.model_validate({"confluence_score": 0.5}).confluence_score == 0.5


# ── the live confirm ────────────────────────────────────────────────────

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def _no_website_sync(monkeypatch):
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda *a, **k: None)


def _confirming_engine(tmp_path):
    """The seal-failure suite's harness (`test_a_seal_failure_does_not_unplace
    _a_trade.py`): a real engine whose venue and compliance calls answer yes."""
    from bot.core.engine import RuneClawEngine
    from bot.utils.audit_chain import AuditChain
    engine = RuneClawEngine()
    engine.risk._state_file = "/dev/null"
    engine.risk._circuit_open = False
    engine.risk._consecutive_losses = 0
    engine.risk._last_loss_time = None
    engine._cooldown_until = 0.0
    if engine.macro_provider is not None:
        engine.macro_provider._calendar_stale = False
        engine.macro_provider._calendar_blind = False
    engine.portfolio.balance = 50000.0
    engine.portfolio._peak_equity = 50000.0
    engine._live_balance_cache = {"total": 50000.0, "free": 50000.0}
    engine._live_balance_cache_ts = time.monotonic()
    # confidence and confluence differ, so a row that wrote one where the
    # other belongs is told apart.
    idea = TradeIdea(
        id="TI-MODE1", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=65000, stop_loss=63050, take_profit=68120,
        confidence=1.0, confluence_score=0.62, reasoning="manual",
        signals_used=["manual"], source="manual", timestamp=datetime.now(UTC),
        order_type="market",
    )
    engine._pending_ideas[idea.id] = idea
    engine._pending_atr[idea.id] = 500.0
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": idea.entry_price})
    engine.scanner._get_exchange = AsyncMock(return_value=ex)
    engine.scanner._get_futures_exchange = AsyncMock(return_value=ex)
    engine.live_executor._positions = {}
    engine.live_executor.execute = AsyncMock(
        return_value="\U0001f7e2 LIVE LONG BTC/USDT opened @ $65,000.0000")
    engine.compliance.issue_approval_token = MagicMock(return_value="tok")
    engine.compliance.authorize = MagicMock(return_value=SimpleNamespace(
        granted=True, reasons=[], locks_failed=[], locks_passed=["L1"]))
    engine._live_execution_vetoed_by_simulation = lambda: False
    engine.learning.log_decision = MagicMock()
    engine._sync_flight_records = lambda: None
    engine.audit_chain = AuditChain(str(tmp_path / "chain.jsonl"))
    return engine, idea


@pytest.mark.usefixtures("_no_website_sync")
@pytest.mark.parametrize("sandbox, word", [(False, "live"), (True, "demo")])
def test_a_live_confirm_is_recorded_from_the_executor_that_placed_it(
        monkeypatch, tmp_path, sandbox, word):
    engine, idea = _confirming_engine(tmp_path)
    asked = _sandbox(monkeypatch, sandbox)
    with patch.object(type(CONFIG), "is_live", return_value=True), \
         patch("bot.core.engine.get_exchange_position_count",
               new=AsyncMock(return_value=0)), \
         patch("bot.core.engine.invalidate_position_count_cache"):
        _run(engine.confirm_trade(idea.id, user_id="123456"))
    engine.live_executor.execute.assert_awaited_once()
    engine.learning.log_decision.assert_called_once()
    kw = engine.learning.log_decision.call_args.kwargs
    assert kw["mode"] == word
    assert kw["confluence_score"] == 0.62 and kw["confidence"] == 1.0
    assert asked == [engine.live_executor._venue.id]


# ── the live close ──────────────────────────────────────────────────────

def _pos(pnl=-4.0):
    return SimpleNamespace(pnl_usd=pnl, symbol="BTC/USDT", direction="LONG",
                           close_reason="stop_loss", trade_id="T1")


def _closing_engine():
    """`test_per_user_live_breakers.py`'s minimal engine: the close handler's
    collaborators are fail-open, so only what it reads is set."""
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._invalidate_live_balance_cache = lambda: None
    eng.learning = Mock()
    eng._outcome_regime = lambda s: ""
    eng._auto_refit = Mock()
    eng._symbol_cooldowns = {}
    eng._symbol_cooldown_seconds = 60
    eng._symbol_loss_streaks = {}
    eng._sync_live_state_to_website = lambda: None
    eng.risk = Mock(name="operator_risk")
    eng.risk_for = lambda uid="": eng.risk
    return eng


def _close_mode(eng):
    eng.learning.record_closed_outcome.assert_called_once()
    return eng.learning.record_closed_outcome.call_args.kwargs["mode"]


@pytest.mark.parametrize("sandbox, word", [(False, "live"), (True, "demo")])
def test_a_live_close_reads_the_executor_that_closed_it(monkeypatch, sandbox, word):
    asked = _sandbox(monkeypatch, sandbox)
    eng = _closing_engine()
    eng._on_live_position_closed(_pos(), "alice", executor=_executor("bybit"))
    assert _close_mode(eng) == word
    assert asked == ["bybit"]


def test_a_close_with_no_executor_named_is_live_and_names_no_venue(monkeypatch):
    asked = _sandbox(monkeypatch, True)
    eng = _closing_engine()
    eng._on_live_position_closed(_pos(), "alice")
    assert _close_mode(eng) == "live"
    assert asked == []


def _recording_mode(monkeypatch):
    """Record which executor each close asked about."""
    import bot.core.engine as engine_mod
    seen: list = []
    real = engine_mod.decision_mode

    def _rec(executor=None, *, live):
        seen.append(executor)
        return real(executor, live=live)

    monkeypatch.setattr(engine_mod, "decision_mode", _rec)
    return seen


def test_the_operators_executor_is_bound_into_its_close(monkeypatch):
    seen = _recording_mode(monkeypatch)
    from bot.core.engine import RuneClawEngine
    engine = RuneClawEngine()
    engine.learning.record_closed_outcome = MagicMock()
    monkeypatch.setattr(engine, "_sync_live_state_to_website", lambda: None)
    # The REAL engine carries an audit chain, so its close starts the
    # flight-record sync on a daemon thread, whose POST can land while a later
    # test has pointed the website URL at a local server of its own: a
    # refused connect the network guard rightly reports against this test.
    monkeypatch.setattr(engine, "_sync_flight_records", lambda: None)
    engine.live_executor.on_position_closed(_pos(0.5))
    engine.learning.record_closed_outcome.assert_called_once()
    assert seen and seen[0] is engine.live_executor


def test_a_users_executor_is_bound_into_its_close(monkeypatch):
    seen = _recording_mode(monkeypatch)
    eng = _closing_engine()
    eng.ws_feed = None
    eng.slippage = None
    eng._user_executors = {}
    creds = {"api_key": "K" * 16, "api_secret": "S" * 16, "passphrase": "pp"}

    class _Store:
        def get(self, uid):
            return creds if str(uid) == "bob" else None

    cfg = patch("bot.core.engine.CONFIG")
    m = cfg.start()
    m.per_user_live_enabled = True
    store = patch("bot.core.exchange_credentials.get_credential_store",
                  return_value=_Store())
    store.start()
    try:
        ex = eng._executor_for("bob")
        assert ex is not None
        ex.on_position_closed(_pos())
    finally:
        store.stop()
        cfg.stop()
    assert seen == [ex]


def test_a_switched_venues_executor_is_bound_into_its_close(monkeypatch):
    import bot.core.engine as engine_mod
    from bot.core.venues import get_venue, set_venue_override

    class _FakeExec:
        def __init__(self):
            self._venue = get_venue()
            self.on_position_closed = None
            self.open_positions: list = []

        async def close(self):
            pass

    seen = _recording_mode(monkeypatch)
    monkeypatch.setattr(engine_mod, "LiveExecutor", _FakeExec)
    eng = _closing_engine()
    eng.live_executor = _FakeExec()
    eng.ws_feed = object()
    eng.slippage = object()
    eng._live_balance_cache = {}
    eng._live_balance_cache_ts = 0.0
    eng._user_live_balance_cache = {}
    eng._user_live_balance_cache_ts = {}
    try:
        assert asyncio.run(eng.switch_venue("hyperliquid")).startswith("switched")
        eng.live_executor.on_position_closed(_pos())
    finally:
        set_venue_override(None)
    assert seen == [eng.live_executor]
    assert eng.live_executor._venue.id == "hyperliquid"
