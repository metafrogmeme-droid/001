"""/resume said CLEAR, and /start a second later said Paused.

2026-11-08, from the owner's phone: `/resume` printed "Trading ENABLED /
Circuit Breaker CLEAR", and `/start` straight after printed "🔴 Paused |
PAPER". Both were reading something real. While live is off every confirm the
operator makes, tapped or unattended, opens on the operator's PRACTICE book
(`confirm_is_practice`), and that book has its own RiskEngine
(`practice_risk_for`, its own state file) which the confirm re-checks and
/start's headline reads (`trade_gate.caller_risk`). /resume reset the SHARED
engine and read the gate off that engine alone, so a practice breaker still
refusing every entry was neither cleared nor said.

/resume now also resets the engine the caller's entries are gated on, when it
is another one, and reads both; the kill switch, which /resume does not
clear, is named. Driven through the real engine, the real `/resume` and the
real `entry_gate` /start renders, so the two cards are compared, not assumed.
"""
from __future__ import annotations

import asyncio

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.trade_gate import entry_gate
from tests.test_resume_card_does_not_claim_enabled_while_blocked import _host

OPERATOR = "5551234"


@pytest.fixture
def live_off(monkeypatch):
    monkeypatch.setattr(engine_mod, "_live_turned_off", lambda: True)


def _resume(eng) -> str:
    h, update, ctx, sent = _host(eng.risk)
    h.engine = eng
    h._get_tg_id = lambda u: OPERATOR
    asyncio.run(asyncio.wait_for(h._cmd_resume(update, ctx), timeout=15))
    return sent[-1]


def test_a_tripped_practice_breaker_is_cleared_and_both_cards_agree(live_off):
    eng = RuneClawEngine()
    practice = eng.practice_risk_for(OPERATOR)
    assert practice is not eng.risk, "the premise: the confirms gate on another engine"
    practice.emergency_halt("test")
    assert entry_gate(eng, OPERATOR)["blocked"], "the premise: /start reads Paused"
    card = _resume(eng)
    assert not practice.circuit_breaker_active, "the practice breaker was not reset"
    assert not entry_gate(eng, OPERATOR)["blocked"], "/start would still say Paused"
    assert "ENABLED" in card


def test_with_live_on_the_operator_confirms_live_and_only_the_shared_engine_is_reset(monkeypatch):
    """The other arm: no practice book in play, nothing beside the shared engine."""
    monkeypatch.setattr(engine_mod, "_live_turned_off", lambda: False)
    eng = RuneClawEngine()
    other = eng.practice_risk_for(OPERATOR)
    other.emergency_halt("test")
    eng.risk.emergency_halt("test")
    card = _resume(eng)
    assert not eng.risk.circuit_breaker_active
    assert other.circuit_breaker_active, "a book the operator's confirms do not land on was touched"
    assert "ENABLED" in card


def test_an_unreadable_entry_engine_is_unread_not_enabled(live_off, monkeypatch):
    eng = RuneClawEngine()

    def _raise(_uid):
        raise RuntimeError("state unreadable")
    monkeypatch.setattr(eng, "practice_risk_for", _raise)
    card = _resume(eng)
    assert "UNREAD" in card and "ENABLED" not in card


def test_the_kill_switch_is_named_and_resume_says_it_does_not_clear_it(live_off):
    eng = RuneClawEngine()
    eng._halted = True
    card = _resume(eng)
    assert "REFUSED" in card and "kill switch" in card and "/reset clears it" in card
    assert entry_gate(eng, OPERATOR)["blocked"], "and /start agrees"


def test_a_practice_gate_the_reset_does_not_clear_is_refused_on_the_card(live_off, monkeypatch):
    """The warning-rate breaker sits outside `reset_circuit_breaker`. On the
    practice book it still refuses entries after /resume, and the card says
    REFUSED with it named, as /start does."""
    eng = RuneClawEngine()
    practice = eng.practice_risk_for(OPERATOR)
    real = type(practice).trading_blocked_by.fget
    monkeypatch.setattr(type(practice), "trading_blocked_by", property(
        lambda self: "warning_rate:engine_tick_failure" if self is practice else real(self)))
    card = _resume(eng)
    assert "REFUSED" in card and "engine_tick_failure" in card, card
    assert entry_gate(eng, OPERATOR)["blocked"], "and /start agrees"


def _pause(eng) -> str:
    h, update, ctx, sent = _host(eng.risk)
    h.engine = eng
    h._get_tg_id = lambda u: OPERATOR
    asyncio.run(asyncio.wait_for(h._cmd_pause(update, ctx), timeout=15))
    return sent[-1]


def test_pause_halts_the_book_the_confirms_land_on_too(live_off):
    """A practice confirm is re-checked on the practice engine alone, so a
    pause that halted only the shared engine left paper entries opening."""
    eng = RuneClawEngine()
    practice = eng.practice_risk_for(OPERATOR)
    _pause(eng)
    assert eng.risk.circuit_breaker_active and practice.circuit_breaker_active
    _resume(eng)
    assert not practice.circuit_breaker_active and not entry_gate(eng, OPERATOR)["blocked"]


def test_with_live_on_pause_leaves_a_book_the_confirms_do_not_land_on(monkeypatch):
    monkeypatch.setattr(engine_mod, "_live_turned_off", lambda: False)
    eng = RuneClawEngine()
    practice = eng.practice_risk_for(OPERATOR)
    _pause(eng)
    assert eng.risk.circuit_breaker_active and not practice.circuit_breaker_active
