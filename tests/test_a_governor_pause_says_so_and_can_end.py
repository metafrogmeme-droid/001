"""A paused live-performance governor is named everywhere, and it can end.

On 2026-09-25 a scan card rejected NEAR/USDT LONG with "LIVE_PERF_GOVERNOR:
trading paused", and six minutes later /start answered "Active | LIVE". Two
defects, one under the other:

1. The governor's PAUSE refuses every idea whatever it is, and it was not in
   `RiskEngine.trading_blocked_by`, the reading every status surface asks
   through `trade_gate.entry_gate` (/start, /status, /risk, /resume, the chat
   prompt, the website chip). So all of them said entries were open.
2. The pause could not end. The governor scores the last 20 live closes; a
   pause opens nothing, so on a flat book the window never changes, and since
   the window is seeded from the closed-trade record at boot a restart does
   not lift it either. The loss-streak latch in the same gate had the same
   trap and a probe to get out of it; the governor had none. Operator
   decision: after `LIVE_PERF_PROBE_HOURS` (24) since the last close, with no
   position open, ONE entry goes through at the reduce size.
"""
from __future__ import annotations

import dataclasses
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.config import CONFIG as REAL
from bot.core import live_executor
from bot.core.trade_gate import entry_gate, gate_label, gate_sentence
from bot.risk.live_perf_gate import pause_reason, probe_clause
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine, RiskVerdict
from bot.utils.models import Direction, TradeIdea

HOUR = 3600.0


class _Cfg:
    def __init__(self, **kw):
        self.risk = dataclasses.replace(REAL.risk, **kw)

    def __getattr__(self, name):
        return getattr(REAL, name)


_GOV = dict(live_performance_governor_enabled=True, live_perf_window=20,
            live_perf_min_samples=5, live_perf_pause_winrate=0.25,
            live_perf_reduce_winrate=0.40, live_perf_reduce_mult=0.5,
            live_perf_probe_hours=24.0)


@pytest.fixture
def cfg():
    c = _Cfg(**_GOV)
    with patch("bot.risk.risk_engine.CONFIG", c):
        yield c


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=95.0, take_profit=115.0, confidence=0.75,
                     reasoning="t", source="test")


def _engine(tmp_path, hours_since_close=None, window=None):
    e = RiskEngine(PortfolioTracker(initial_balance=10_000),
                   state_file=os.path.join(str(tmp_path), "r.json"))
    # 4 of 20 won, net negative: PAUSE (<= 25% and < 0).
    e._realized_pnl_window.extend([1.0] * 4 + [-10.0] * 16 if window is None else window)
    e._last_close_time = (None if hours_since_close is None
                          else time.time() - hours_since_close * HOUR)
    return e


class _Bot:
    """The engine shape `entry_gate` reads: one risk engine, no kill switch."""
    def __init__(self, risk):
        self.risk = risk
        self._halted = False


def _governor_lines(rc):
    return [c for c in rc.checks_failed + rc.checks_passed if "LIVE_PERF_GOVERNOR" in c]


# ── 1. The pause is named where every status surface looks ─────────────────

def test_a_pause_is_named_by_the_gate_every_card_reads(tmp_path, cfg):
    e = _engine(tmp_path, hours_since_close=23)
    blocked = e.trading_blocked_by
    assert blocked.startswith("live_perf_pause: 4 of the last 20 closes won, net negative")
    gate = entry_gate(_Bot(e), "", live=False)
    # /start's headline is gate_label(entry_gate(...)): the screenshot's
    # "Active" over a paused governor, now "Paused".
    assert gate["blocked"] is True and gate_label(gate) == "Paused"
    assert "probe entry is allowed in 60m" in gate_sentence(gate)


def test_the_reason_carries_counts_and_no_account_money(tmp_path, cfg):
    # It reaches the unauthenticated /health payload and the website chip.
    e = _engine(tmp_path, hours_since_close=3, window=[5.0] * 3 + [-400.0] * 17)
    assert "$" not in e.trading_blocked_by
    assert "3 of the last 20" in e.trading_blocked_by


def test_a_healthy_or_reduced_window_names_no_pause(tmp_path, cfg):
    reduced = _engine(tmp_path, hours_since_close=1, window=[1.0] * 7 + [-2.0] * 13)
    assert reduced.live_performance_state()["status"] == "REDUCE"
    assert reduced.trading_blocked_by == ""
    healthy = _engine(tmp_path, hours_since_close=1, window=[5.0] * 12 + [-1.0] * 8)
    assert healthy.trading_blocked_by == ""


def test_a_disabled_governor_names_no_pause(tmp_path):
    with patch("bot.risk.risk_engine.CONFIG",
               _Cfg(**{**_GOV, "live_performance_governor_enabled": False})):
        e = _engine(tmp_path, hours_since_close=1)
        assert e.trading_blocked_by == ""
        rc = e.evaluate(_idea(), atr=1.0, live_open_count=0)
        assert not _governor_lines(rc)


# ── 2. The probe ─────────────────────────────────────────────────────────

def test_before_the_probe_period_every_entry_is_refused_and_says_when(tmp_path, cfg):
    rc = _engine(tmp_path, hours_since_close=23).evaluate(_idea(), atr=1.0, live_open_count=0)
    assert rc.verdict == RiskVerdict.REJECTED
    assert _governor_lines(rc) == [
        "LIVE_PERF_GOVERNOR: trading paused — realized win rate and net PnL below "
        "floor over recent window; a probe entry is allowed in 60m"]


def test_after_the_probe_period_one_entry_goes_through_at_the_reduce_size(tmp_path, cfg):
    e = _engine(tmp_path, hours_since_close=25)
    rc = e.evaluate(_idea(), atr=1.0, live_open_count=0)
    assert rc.verdict == RiskVerdict.APPROVED
    assert _governor_lines(rc) == [
        "LIVE_PERF_GOVERNOR: paused — probe entry allowed 25.0h after the last close, "
        "sized x0.50"]
    assert e.trading_blocked_by == ""        # conditionally open, like the loss streak
    # The probe is a REDUCED entry: never larger than the same idea unpaused.
    full = _engine(tmp_path, hours_since_close=25, window=[]).evaluate(
        _idea(), atr=1.0, live_open_count=0)
    assert 0 < rc.position_size_usd <= full.position_size_usd
    assert "live-performance governor probe x0.50" in " ".join(rc.size_path)


def test_the_boundary_is_inclusive(tmp_path, cfg):
    e = _engine(tmp_path)
    e._last_close_time = 1_000_000.0
    with patch.object(RiskEngine, "_now", lambda self: 1_000_000.0 + 24 * HOUR):
        assert e.evaluate(_idea(), atr=1.0, live_open_count=0).verdict == RiskVerdict.APPROVED
    with patch.object(RiskEngine, "_now", lambda self: 1_000_000.0 + 24 * HOUR - 1):
        assert e.evaluate(_idea(), atr=1.0, live_open_count=0).verdict == RiskVerdict.REJECTED


def test_a_probe_waits_for_the_book_to_be_flat(tmp_path, cfg):
    rc = _engine(tmp_path, hours_since_close=30).evaluate(_idea(), atr=1.0, live_open_count=1)
    assert rc.verdict == RiskVerdict.REJECTED
    assert _governor_lines(rc)[0].endswith("; a probe entry waits until no position is open")


def test_no_close_on_record_is_no_probe(tmp_path, cfg):
    e = _engine(tmp_path, hours_since_close=None)
    rc = e.evaluate(_idea(), atr=1.0, live_open_count=0)
    assert rc.verdict == RiskVerdict.REJECTED
    assert _governor_lines(rc)[0].endswith("; no close is on record to time a probe entry from")
    assert e.trading_blocked_by.endswith("; no close is on record to time a probe entry from")
    assert e.live_performance_state()["probe_in_seconds"] is None


def test_probing_switched_off_says_so(tmp_path):
    with patch("bot.risk.risk_engine.CONFIG", _Cfg(**{**_GOV, "live_perf_probe_hours": 0.0})):
        e = _engine(tmp_path, hours_since_close=500)
        rc = e.evaluate(_idea(), atr=1.0, live_open_count=0)
        assert rc.verdict == RiskVerdict.REJECTED
        assert "LIVE_PERF_PROBE_HOURS is 0" in _governor_lines(rc)[0]
        assert "LIVE_PERF_PROBE_HOURS is 0" in e.trading_blocked_by


def test_a_losing_probe_re_arms_the_wait(tmp_path, cfg):
    e = _engine(tmp_path, hours_since_close=25)
    e.record_trade_result(-3.0)
    assert e.trading_blocked_by.endswith("a probe entry is allowed in 24.0h with no position open")
    assert e.evaluate(_idea(), atr=1.0, live_open_count=0).verdict == RiskVerdict.REJECTED


def test_a_probe_that_closed_unpriced_re_arms_the_wait_too(tmp_path, cfg):
    e = _engine(tmp_path, hours_since_close=25)
    e.note_unpriced_close()
    assert e.governor_probe_in_seconds() > 23.9 * HOUR
    assert len(e._realized_pnl_window) == 20       # the window learned nothing


def test_a_window_that_recovers_leaves_pause_on_its_own(tmp_path, cfg):
    e = _engine(tmp_path, hours_since_close=25)
    for _ in range(12):
        e.record_trade_result(5.0)
    assert e.live_performance_state()["status"] == "REDUCE"
    assert e.live_performance_state()["probe_in_seconds"] is None
    assert e.trading_blocked_by == ""


def test_the_state_reports_the_probe_only_while_paused(tmp_path, cfg):
    paused = _engine(tmp_path, hours_since_close=20).live_performance_state()
    assert paused["status"] == "PAUSE"
    assert 3.9 * HOUR < paused["probe_in_seconds"] <= 4 * HOUR
    assert _engine(tmp_path, hours_since_close=20, window=[]).live_performance_state()[
        "probe_in_seconds"] is None


def test_the_loss_streak_probe_still_uses_its_own_clock(tmp_path, cfg):
    # One reading (`_probe_cooled`) for both latches; each keeps its clock.
    e = _engine(tmp_path, hours_since_close=25, window=[])
    e._consecutive_losses = 10
    e._last_loss_time = time.time() - 2 * HOUR
    rc = e.evaluate(_idea(), atr=1.0, live_open_count=0)
    assert any(c.startswith("LOSS_STREAK: 10 consecutive") for c in rc.checks_failed)


# ── 3. A restart does not restart the wait ─────────────────────────────────

T0 = datetime(2026, 9, 20, tzinfo=UTC)


def _close(pnl, hours, reason="SL HIT"):
    return SimpleNamespace(pnl_usd=pnl, close_reason=reason,
                           closed_at=None if hours is None else T0 + timedelta(hours=hours))


def test_the_record_says_when_the_newest_filled_close_happened():
    record = [_close(-2.0, 3), _close(None, 9),            # unpriced: still a close
              _close(0.0, 12, reason="stale_pending"),     # never filled: not one
              _close(1.0, None)]                           # undated: cannot place it
    assert live_executor.realized_close_last_at(record) == (T0 + timedelta(hours=9)).timestamp()
    assert live_executor.realized_close_last_at([]) is None
    assert live_executor.realized_close_last_at([_close(1.0, None)]) is None


def test_the_boot_seed_sets_the_clock_and_never_moves_it_back(tmp_path):
    e = RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "r.json"))
    e.seed_realized_window([-1.0] * 5, last_close_at=1234.0)
    assert e._last_close_time == 1234.0
    e._last_close_time = 9999.0                 # a close already seen this process
    e.seed_realized_window([-1.0], last_close_at=1234.0)
    assert e._last_close_time == 9999.0


def test_the_engine_hands_the_seed_the_records_clock():
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine.__init__)
    seed = src[src.index("seed_realized_window("):]
    assert "last_close_at=_live_executor_mod.realized_close_last_at(_closed_record)" in \
        seed[:seed.index(")\n")]


# ── 4. The words ───────────────────────────────────────────────────────────

def test_the_clause_has_four_outcomes():
    assert "LIVE_PERF_PROBE_HOURS is 0" in probe_clause(None, 0.0, 0)
    assert probe_clause(None, 24.0, 0) == "; no close is on record to time a probe entry from"
    assert probe_clause(10.0, 24.0, 2) == "; a probe entry waits until no position is open"
    assert probe_clause(13.2 * HOUR, 24.0, 0) == "; a probe entry is allowed in 13.2h"
    assert probe_clause(13.2 * HOUR, 24.0, None) == \
        "; a probe entry is allowed in 13.2h with no position open"


def test_the_wait_rounds_up_so_a_card_never_calls_a_probe_early():
    assert probe_clause(59.0, 24.0, 0) == "; a probe entry is allowed in 1m"
    assert probe_clause(13.21 * HOUR, 24.0, 0) == "; a probe entry is allowed in 13.3h"


def test_pause_reason_counts_wins_off_the_window():
    assert pause_reason(20, 0.2, 5 * HOUR, 24.0).startswith(
        "live_perf_pause: 4 of the last 20 closes won, net negative; ")


def test_the_resume_card_names_the_governor_not_the_circuit_breaker():
    from bot.warroom.warroom_bot import resume_gate_line
    line = resume_gate_line(
        "live_perf_pause: 4 of the last 20 closes won, net negative; a probe entry "
        "is allowed in 13.2h with no position open")
    assert "live-performance governor paused (4 of the last 20" in line
    # /resume clears a PAUSE before it reads the gate
    # (`test_resume_and_reset_clear_the_governor_pause.py`), so a pause the
    # card still sees is a clear that failed, and the line says that.
    assert "circuit breaker" not in line and "/resume tried to clear it and could not" in line
    assert "equity-curve breaker" in resume_gate_line("equity_curve_pause")


def test_the_bridge_says_it_cannot_see_the_governor():
    from bot.core.persisted_breaker import UNSAVED_GATES
    assert "live-performance governor" in UNSAVED_GATES


# ── 5. The scan card does not offer a door the gate will refuse ──────────

_SETUP = [{"sym": "NEAR/USDT", "dir": "LONG", "price": 4.985}]


def test_a_blocked_gate_offers_no_buttons_and_says_why():
    from bot.skills.scan_skill import scan_action_rows
    text, rows = scan_action_rows(_SETUP, {"blocked": True, "unknown": False,
                                           "reasons": ["live_perf_pause: 4 of the last 20"]})
    assert rows == []
    assert "tap to execute" not in text
    assert "New entries are refused: live_perf_pause: 4 of the last 20" in text


def test_an_open_or_unread_gate_offers_the_buttons():
    from bot.skills.scan_skill import scan_action_rows
    for gate in ({"blocked": False, "unknown": False, "reasons": []},
                 {"blocked": False, "unknown": True, "reasons": []}):
        text, rows = scan_action_rows(_SETUP, gate)
        assert "tap to execute" in text
        assert [d for _l, d in rows[0]] == ["scan_confirm:NEAR/USDT:LONG:4.985",
                                            "scan_limit:NEAR/USDT:LONG:4.985",
                                            "scan_reject:NEAR/USDT"]


def test_the_refusal_escapes_what_it_quotes():
    from bot.skills.scan_skill import scan_action_rows
    text, _ = scan_action_rows(_SETUP, {"blocked": True, "reasons": ["a<b"]})
    assert "a&lt;b" in text and "a<b" not in text


# ── 6. The scan handler itself, both send paths ────────────────────────────

_NEAR = {"sym": "NEAR/USDT", "price": 4.985, "dir": "LONG", "score": 0.8, "rsi": 48.0,
         "atr": 0.1, "vol_ratio": 1.6, "sma20": 4.9, "change_pct": 1.2,
         "regime": "LONG", "patterns": []}


def _drive_scan(monkeypatch, risk, card_renders: bool):
    import asyncio
    from unittest.mock import AsyncMock

    from bot.skills import scan_skill

    async def _one(_exchange, symbol, _analyzer=None):
        return dict(_NEAR) if symbol == "NEAR/USDT" else None

    monkeypatch.setattr(scan_skill, "_scan_symbol", _one)
    monkeypatch.setattr(scan_skill, "_push_scan_to_dashboard", lambda *a, **k: None)
    import bot.formatters.signal_card as signal_card

    def _render(*_a, **_k):
        if not card_renders:
            raise RuntimeError("no Pillow here")
        return b"\x89PNG"
    monkeypatch.setattr(signal_card, "render_scan_results_card", _render)

    msg = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=msg)),
                             effective_user=SimpleNamespace(id=7),
                             effective_chat=SimpleNamespace(id=7))
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    context = SimpleNamespace(bot=bot, bot_data={})
    engine = SimpleNamespace(risk=risk, _halted=False, analyzer=None,
                             scanner=SimpleNamespace(_get_exchange=AsyncMock()))
    assert not REAL.is_live()        # the gate's venue-auth half is live-only
    asyncio.run(scan_skill._scan_batch(update, context, engine, top_n=10,
                                       patterns=False, ai=False))
    return msg, bot


def _sent(bot):
    return [(c.kwargs.get("text", ""), c.kwargs.get("reply_markup"))
            for c in bot.send_message.await_args_list]


def test_the_card_path_sends_the_refusal_and_no_buttons(tmp_path, cfg, monkeypatch):
    msg, bot = _drive_scan(monkeypatch, _engine(tmp_path, hours_since_close=3), True)
    assert bot.send_photo.await_count == 1
    sent = _sent(bot)
    assert len(sent) == 1
    text, kb = sent[0]
    assert kb is None
    assert "New entries are refused: live_perf_pause: 4 of the last 20" in text
    assert "tap to execute" not in text


def test_the_card_path_offers_buttons_when_the_gate_is_open(tmp_path, cfg, monkeypatch):
    healthy = _engine(tmp_path, hours_since_close=3, window=[5.0] * 12 + [-1.0] * 8)
    _msg, bot = _drive_scan(monkeypatch, healthy, True)
    (text, kb), = _sent(bot)
    assert "tap to execute" in text
    assert kb.inline_keyboard[0][0].callback_data == "scan_confirm:NEAR/USDT:LONG:4.985"


def test_the_text_path_carries_the_refusal_and_no_buttons(tmp_path, cfg, monkeypatch):
    msg, bot = _drive_scan(monkeypatch, _engine(tmp_path, hours_since_close=3), False)
    final = msg.edit_text.await_args_list[-1]
    assert final.kwargs.get("reply_markup") is None
    assert "New entries are refused: live_perf_pause" in final.args[0]
    assert bot.send_message.await_count == 0


# ── 7. Fixtures the mutation round asked for ───────────────────────────────

def test_the_equity_curve_pause_is_named_too(tmp_path, cfg):
    e = _engine(tmp_path, window=[])
    e._equity_curve_paused = True
    assert e.trading_blocked_by == "equity_curve_pause"
    assert gate_label(entry_gate(_Bot(e), "", live=False)) == "Paused"


def test_the_probe_halves_a_size_the_cap_does_not_bind(tmp_path, cfg):
    # A 20% stop sizes UNDER the notional cap, so the x0.50 is not taken back;
    # on a stop the cap binds on, the cap would, as for any REDUCE entry
    # (PRE_CAP_TIGHTENS_CAP ships empty).
    wide = dict(stop_loss=80.0, take_profit=150.0)
    probe = _engine(tmp_path, hours_since_close=25).evaluate(
        _idea().model_copy(update=wide), atr=1.0, live_open_count=0)
    full = _engine(tmp_path, hours_since_close=25, window=[]).evaluate(
        _idea().model_copy(update=wide), atr=1.0, live_open_count=0)
    # The verdict here is MARGIN_RISK's (a 20% stop at 5x); the size is still
    # computed, and the probe's step is on it.
    assert "live-performance governor probe x0.50" in " ".join(probe.size_path)
    assert probe.position_size_usd == pytest.approx(full.position_size_usd * 0.5)
