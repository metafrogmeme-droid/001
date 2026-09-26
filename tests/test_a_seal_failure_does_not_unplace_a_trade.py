"""A live order the venue filled was reported as a failed execution when the
audit record written after it could not be sealed.

`_confirm_trade_inner` calls `executor.execute()`, which places the order, and
only THEN seals the decision to the audit chain -- with no guard around the
seal. So anything that made the seal raise (a write cut short at the end of the
chain, which made every later append raise JSONDecodeError; a full disk) raised
out of `confirm_trade` over a position that was open on the venue:

  * the Confirm button caught it and showed "Trade execution failed:
    Unterminated string ..." over the open position;
  * auto-confirm logged "Auto-confirm failed" and skipped the notification,
    so a position was opened and nobody was told;
  * `learning.log_decision` and the transition back to IDLE, written after
    the seal, never ran.

Three more records on the confirm path had the same shape and each turned a
decision into a different one: the re-check REJECTION's seal raised out as an
error, the compliance denial's AUTH_DENIED append did the same, and the
critique HALT's append sat inside the critique's own `try` -- whose handler
treats any exception as "the critique could not complete", which in paper
mode PROCEEDS with the trade the critique had just halted.

The trade happened, or it was refused; a record that could not be written
changes neither. Each seal is written through one helper that says so --
logged at ERROR, and a sentence appended to the answer the confirmer reads --
and the decision stands.

The chain half of the incident (a torn tail no longer breaks appends) is
`test_a_torn_audit_line_does_not_break_the_chain.py`; the end-to-end drive
below plants that exact tail against the real chain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.core.confirm_result import placed_nothing
from bot.utils.audit_chain import TORN_TAIL_EVENT, AuditChain
from bot.utils.models import AgentState, Direction, RiskCheck, RiskVerdict, TradeIdea

FILLED = "\U0001f7e2 LIVE LONG BTC/USDT opened @ $65,000.0000"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _engine(tmp_path):
    from bot.core.engine import RuneClawEngine
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
    idea = TradeIdea(
        id="TI-SEAL1", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=65000, stop_loss=63050, take_profit=68120,
        confidence=1.0, reasoning="manual", signals_used=["manual"],
        source="manual", timestamp=datetime.now(UTC), order_type="market",
    )
    engine._pending_ideas[idea.id] = idea
    engine._pending_atr[idea.id] = 500.0
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": idea.entry_price})
    engine.scanner._get_exchange = AsyncMock(return_value=ex)
    engine.scanner._get_futures_exchange = AsyncMock(return_value=ex)
    engine.live_executor._positions = {}
    engine.live_executor.execute = AsyncMock(return_value=FILLED)
    engine.compliance.issue_approval_token = MagicMock(return_value="tok")
    engine.compliance.authorize = MagicMock(return_value=SimpleNamespace(
        granted=True, reasons=[], locks_failed=[], locks_passed=["L1"]))
    engine._live_execution_vetoed_by_simulation = lambda: False
    engine.learning.log_decision = MagicMock()
    engine._sync_flight_records = lambda: None
    engine.audit_chain = AuditChain(str(tmp_path / "chain.jsonl"))
    return engine, idea


def _confirm(engine, idea):
    with patch.object(type(CONFIG), "is_live", return_value=True), \
         patch("bot.core.engine.get_exchange_position_count", new=AsyncMock(return_value=0)), \
         patch("bot.core.engine.invalidate_position_count_cache"):
        return _run(engine.confirm_trade(idea.id, user_id="123456"))


def _full_disk(*_a, **_k):
    raise OSError(28, "No space left on device")


@pytest.fixture(autouse=True)
def _no_website_sync(monkeypatch):
    """A fill syncs the portfolio to the website on a background thread;
    that is not this suite's subject, and it would reach the network."""
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda *a, **k: None)


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def trade_records():
    """The trade channel does not propagate to the root logger, so caplog
    cannot see it; a handler on the channel itself can."""
    from bot.utils.logger import trade_log
    h = _Records()
    trade_log.addHandler(h)
    try:
        yield h.records
    finally:
        trade_log.removeHandler(h)


def _seal_errors(records):
    return [r for r in records
            if getattr(r, "result", "") == "SEAL_FAILED" and r.levelno >= logging.ERROR]


class TestAFilledTradeStaysFilled:
    def test_the_answer_still_reports_the_fill(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.seal_decision = _full_disk
        result = _confirm(engine, idea)
        engine.live_executor.execute.assert_awaited_once()
        assert result.startswith(FILLED)
        assert placed_nothing(result) is False, (
            "a placed trade whose record did not seal was read as nothing placed")

    def test_the_answer_says_the_record_was_not_sealed(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.seal_decision = _full_disk
        result = _confirm(engine, idea)
        assert "NOT written to the audit chain" in result
        assert "OSError" in result
        # The class, never the driver's text: it reaches a chat.
        assert "No space left" not in result

    def test_the_failure_is_logged_at_error(self, tmp_path, trade_records):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.seal_decision = _full_disk
        _confirm(engine, idea)
        errs = _seal_errors(trade_records)
        assert len(errs) == 1
        assert idea.id in errs[0].getMessage()
        assert errs[0].data["record"] == "DECISION"

    def test_the_log_line_is_scrubbed(self, tmp_path, trade_records):
        """The operator's log gets the driver's text, scrubbed: a write error
        that echoes a credential-shaped value must not carry it into a log."""
        engine, idea = _engine(tmp_path)

        def _leaky(*_a, **_k):
            raise OSError("write failed api_key=sk-live-0123456789abcdefghij")
        engine.audit_chain.seal_decision = _leaky
        _confirm(engine, idea)
        msg = _seal_errors(trade_records)[0].getMessage()
        assert "write failed" in msg
        assert "sk-live-0123456789abcdefghij" not in msg

    def test_a_refused_order_whose_seal_fails_still_reads_as_refused(self, tmp_path):
        """The note is appended to the executor's answer either way; it must
        not turn a refusal into something the Confirm button reads as a fill."""
        engine, idea = _engine(tmp_path)
        engine.live_executor.execute = AsyncMock(
            return_value="❌ EXECUTION FAILED: venue refused the order")
        engine.audit_chain.seal_decision = _full_disk
        result = _confirm(engine, idea)
        assert result.startswith("❌ EXECUTION FAILED")
        assert "NOT written to the audit chain" in result
        assert placed_nothing(result) is True

    def test_what_ran_after_the_seal_still_runs(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.seal_decision = _full_disk
        _confirm(engine, idea)
        engine.learning.log_decision.assert_called_once()
        assert engine.learning.log_decision.call_args.kwargs["decision"] == "TRADE_ACCEPTED_LIVE"
        assert engine.state == AgentState.IDLE

    def test_a_seal_that_worked_adds_nothing(self, tmp_path, trade_records):
        engine, idea = _engine(tmp_path)
        result = _confirm(engine, idea)
        assert result == FILLED
        assert _seal_errors(trade_records) == []
        assert engine.audit_chain.get_entries()[-1].payload["decision_id"] == idea.id


class TestTheTornChainEndToEnd:
    def test_a_torn_tail_no_longer_reaches_the_confirm(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.append("DECISION", {"a": 1})
        engine.audit_chain.append("DECISION", {"a": 2})
        with engine.audit_chain._path.open("a", encoding="utf-8") as fh:
            fh.write('{"sequence": 2, "event_type": "DECI')
        result = _confirm(engine, idea)
        assert result == FILLED
        tail = [json.loads(ln) for ln in
                engine.audit_chain._path.read_text(encoding="utf-8").splitlines()[3:]]
        assert tail[0]["event_type"] == TORN_TAIL_EVENT
        assert tail[1]["event_type"] == "DECISION"
        assert tail[1]["payload"]["decision_id"] == idea.id
        assert tail[1]["payload"]["outcome"] == "EXECUTED_LIVE"


class TestARefusalStaysARefusal:
    def test_a_recheck_rejection_whose_seal_fails(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.seal_decision = _full_disk
        rejected = RiskCheck(trade_id=idea.id, verdict=RiskVerdict.REJECTED,
                             reason="DAILY_LOSS: limit reached")
        engine.risk_for = lambda _uid: SimpleNamespace(
            evaluate=lambda *a, **k: rejected)
        engine._apply_regime_to = lambda *a, **k: None
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED on re-check: DAILY_LOSS")
        assert "NOT written to the audit chain" in result
        engine.live_executor.execute.assert_not_awaited()

    def test_a_compliance_denial_whose_record_fails(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.compliance.authorize = MagicMock(return_value=SimpleNamespace(
            granted=False, reasons=["Lock 5: no human approval"],
            locks_failed=["L5"], locks_passed=[]))
        engine.audit_chain.append = _full_disk
        result = _confirm(engine, idea)
        assert result.startswith("Execution denied: Lock 5: no human approval")
        assert "NOT written to the audit chain" in result
        engine.live_executor.execute.assert_not_awaited()
        assert engine.state == AgentState.IDLE

    def _halting(self, engine):
        from bot.core import critique as _crit
        halt = SimpleNamespace(verdict="HALT", bear_case="funding flipped",
                               concerns=["crowded long"], confidence_adjustment=0.0)
        return patch.object(_crit.TradeCritique, "evaluate", lambda *a, **k: halt)

    def test_a_critique_halt_whose_record_fails_is_still_a_halt(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.audit_chain.append = _full_disk
        with self._halting(engine):
            result = _confirm(engine, idea)
        assert result.startswith("Trade HALTED by adversarial review: funding flipped")
        assert "could not complete" not in result
        assert "NOT written to the audit chain" in result
        engine.live_executor.execute.assert_not_awaited()

    def test_in_paper_mode_the_halt_does_not_proceed(self, tmp_path):
        """The critique's own handler fails OPEN in paper mode. Before the
        fix, a HALT whose record could not be written raised into it, and the
        practice fill the critique had just halted went ahead."""
        engine, idea = _engine(tmp_path)
        engine.audit_chain.append = _full_disk
        engine._simulate_paper_fill = AsyncMock(return_value="✅ [PAPER] filled")
        engine._user_store = SimpleNamespace(sim_opt_in=lambda _uid: True)
        was = CONFIG.paper_sim_opt_in_enabled
        # CONFIG is frozen; this is the one door, restored in the finally.
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", True)
        try:
            with self._halting(engine), \
                 patch.object(type(CONFIG), "is_live", return_value=False):
                result = _run(engine.confirm_trade(idea.id, user_id="123456"))
        finally:
            object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was)
        assert result.startswith("Trade HALTED by adversarial review")
        engine._simulate_paper_fill.assert_not_awaited()
