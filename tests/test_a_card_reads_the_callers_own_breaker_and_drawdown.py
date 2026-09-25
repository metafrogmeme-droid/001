"""A risk card reads the caller's own breaker and drawdown, not the operator's.

Under per-user live a linked user has their own `RiskEngine` (`risk_for`), and
`engine.risk` is the operator's. `/risk` (the `check_risk` skill) printed
"Circuit Breaker: CLEAR" off the OPERATOR's engine directly above
"Gate: fail-closed — blocking new entries" off the caller's, and a drawdown
gauge off the operator's live high-water mark beside the caller's own equity.
Driven, one card read `8.7% / 10%` for an account at 0.4%, and CLEAR for an
account whose own breaker had tripped on the daily loss.

`skill_registry.entry_gate` read `engine.risk` alone; the pre-execute gate
refuses on EITHER engine (`trade_gate.risk_engines`). And five cards read
`engine.risk.drawdown_status()` for a caller: /risk twice over, /portfolio,
/daily_report and the status card. The fixture is ASYMMETRIC on purpose: two
engines that agree cannot tell a card which one it read.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import re
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from bot.config import CONFIG
from bot.core import trade_gate
from bot.skills import skill_registry as SR

ROOT = pathlib.Path(__file__).resolve().parents[1]
CALLER = "777"


class _Risk:
    def __init__(self, dd, blocked=""):
        self.dd = dd
        self.trading_blocked_by = blocked
        self.circuit_breaker_active = bool(blocked)
        self.consecutive_losses = 0

    def drawdown_status(self):
        return {"drawdown_pct": self.dd, "drawdown_source": "live",
                "live_drawdown_pct": self.dd, "effective_limit_pct": 10.0}


class _Raises:
    @property
    def trading_blocked_by(self):
        raise RuntimeError("state unreadable")


def _engine(op, own, *, raises=False):
    def risk_for(uid):
        if raises:
            raise RuntimeError("store down")
        return own if uid == CALLER else op
    return NS(risk=op, risk_for=risk_for)


# ── the registry's breaker reading ──────────────────────────────────────────


def test_the_callers_own_trip_is_reported_under_a_clear_shared_engine():
    eng = _engine(_Risk(8.7), _Risk(0.4, "daily_loss"))
    assert SR.entry_gate(eng, CALLER) == "daily_loss"


def test_the_shared_engines_trip_still_refuses_the_caller():
    """The pre-execute gate refuses on either engine, so the card does too."""
    eng = _engine(_Risk(8.7, "drawdown"), _Risk(0.4))
    assert SR.entry_gate(eng, CALLER) == "drawdown"


def test_both_clear_is_clear():
    assert SR.entry_gate(_engine(_Risk(1.0), _Risk(0.4)), CALLER) == ""


def test_an_own_engine_nobody_could_resolve_is_unread_not_clear():
    eng = _engine(_Risk(8.7), _Risk(0.4), raises=True)
    assert SR.entry_gate(eng, CALLER) is None


def test_an_unreadable_engine_is_unread_unless_the_other_one_refuses():
    assert SR.entry_gate(_engine(_Risk(1.0), _Raises()), CALLER) is None
    assert SR.entry_gate(_engine(_Risk(1.0, "streak"), _Raises()), CALLER) == "streak"


def test_the_unscoped_call_is_the_shared_engine_alone():
    """Per-user off, and every caller with no id, is the shared engine, read once."""
    op = _Risk(1.0, "manual")
    eng = NS(risk=op, risk_for=lambda uid: op)
    assert SR.entry_gate(eng) == "manual"


# ── the gate helper's own walk ──────────────────────────────────────────────


def test_a_raised_risk_for_makes_the_gate_unknown():
    """The walk used to END in silence when `risk_for` raised, so the answer
    read as complete: clear, about an account nobody looked at."""
    eng = _engine(_Risk(1.0), _Risk(0.4), raises=True)
    eng._halted = False
    g = trade_gate.entry_gate(eng, CALLER, live=False)
    assert g["blocked"] is False and g["unknown"] is True


def test_an_unreadable_shared_engine_makes_the_gate_unknown():
    """The shared engine is read through `_read`, so an attribute that raises
    is a failed read. The caller's own engine answering clear does not make
    the shared one clear."""
    class _Eng:
        _halted = False

        @property
        def risk(self):
            raise RuntimeError("engine half-built")

        def risk_for(self, uid):
            return _Risk(0.4)
    g = trade_gate.entry_gate(_Eng(), CALLER, live=False)
    assert g["blocked"] is False and g["unknown"] is True
    assert SR.entry_gate(_Eng(), CALLER) is None


def test_caller_risk_never_answers_the_shared_engine_for_a_failed_read():
    op, own = _Risk(8.7), _Risk(0.4)
    assert trade_gate.caller_risk(_engine(op, own), CALLER) is own
    assert trade_gate.caller_risk(_engine(op, own, raises=True), CALLER) is None


def test_an_engine_with_no_per_user_seam_is_the_shared_one():
    op = _Risk(1.0)
    assert trade_gate.caller_risk(NS(risk=op), CALLER) is op


# ── the /risk card, driven ──────────────────────────────────────────────────


def _risk_card(op, own):
    state = NS(max_drawdown_pct=0.0, equity_usd=10000.0, open_positions=0,
               total_trades=0, daily_pnl=0.0, win_rate=None)
    pf = NS(snapshot=lambda: state, open_positions=[])
    caller_ex = NS(open_positions=[], closed_positions=[])

    async def eq(uid):
        return 250.0

    engine = NS(risk=op, risk_for=lambda uid: own if uid == CALLER else op,
                user_portfolios=NS(get=lambda uid, *a: pf),
                cost=NS(snapshot=lambda: NS(llm_cost_usd=0.0, infra_cost_usd=0.0)),
                viewer_executor=lambda uid: caller_ex if uid == CALLER else None,
                get_effective_equity_async=eq, _halted=False,
                live_auth_healthy=lambda uid: True, _live_auth_detail={})
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
        out = asyncio.run(SR.CheckRiskSkill().execute(engine, user_id=CALLER))
    return re.sub(r"<[^>]+>", "", out)


def _line(card, label):
    for ln in card.splitlines():
        if label in ln:
            return ln
    raise AssertionError(f"no {label!r} line in:\n{card}")


def test_the_risk_card_prints_the_callers_breaker():
    card = _risk_card(_Risk(8.7), _Risk(0.4, "daily_loss"))
    breaker = _line(card, "Circuit Breaker")
    assert "TRIPPED (daily_loss)" in breaker, breaker
    assert "CLEAR" not in breaker, breaker
    assert "blocking new entries" in _line(card, "Gate:")


def test_the_risk_card_prints_the_callers_drawdown():
    card = _risk_card(_Risk(8.7), _Risk(0.4))
    dd = _line(card, "Drawdown")
    assert "0.4%" in dd, dd
    assert "8.7" not in dd, dd


def test_the_playbook_prints_the_callers_breaker():
    op, own = _Risk(8.7), _Risk(0.4, "daily_loss")
    state = NS(equity_usd=10000.0, open_positions=0, total_trades=0,
               daily_pnl=0.0, win_rate=None, max_drawdown_pct=0.0)
    pf = NS(snapshot=lambda: state, open_positions=[], closed_trades=[],
            trade_history=[], _positions={})

    async def scan():
        return []
    engine = NS(risk=op, risk_for=lambda uid: own if uid == CALLER else op,
                user_portfolios=NS(get=lambda uid, *a: pf), scanner=NS(scan=scan),
                _pending_ideas={})
    with mock.patch.object(type(CONFIG), "is_live", lambda self: False):
        out = asyncio.run(SR.PlaybookSkill().execute(engine, user_id=CALLER))
    breaker = _line(re.sub(r"<[^>]+>", "", out), "Circuit Breaker")
    assert "TRIPPED (daily_loss)" in breaker, breaker


# ── the status card, driven ─────────────────────────────────────────────────


def test_the_status_card_prints_the_callers_drawdown():
    from tests.test_the_status_question_is_answered_by_the_status_card import CALLER as S_CALLER
    from tests.test_the_status_question_is_answered_by_the_status_card import _card, _Engine
    eng = _Engine(per_user=True)
    op, own = _Risk(8.7), _Risk(0.4)
    op.streak_state = own.streak_state = lambda: {"latched": False}
    eng.risk = op
    eng.risk_for = lambda uid: own if uid == S_CALLER else op
    card = _card(eng, S_CALLER, live=True)
    dd = next(ln for ln in card.splitlines() if "Drawdown" in ln)
    assert "0.4" in dd and "8.7" not in dd, dd


# ── the reader every other card asks ────────────────────────────────────────


def test_the_portfolio_cards_read_the_callers_engine():
    from bot.skills.portfolio_commands import _caller_dd_status
    op, own = _Risk(8.7), _Risk(0.4)
    assert _caller_dd_status(_engine(op, own), CALLER)["drawdown_pct"] == 0.4
    assert _caller_dd_status(_engine(op, own, raises=True), CALLER) == {}, (
        "{} is drawdown_status's own word for unreadable; the shared engine "
        "in its place is the operator's book")


# ── nothing reads the shared engine's drawdown for a caller ─────────────────

#: (file, function) that may read `engine.risk.drawdown_status()`, with why.
_OPERATOR_READS = {
    ("bot/skills/engine_ops_commands.py", "_status_lines"):
        "/drawdownlimit is admin-only and sets the OPERATOR's live cap",
}


def _shared_drawdown_reads(src: str):
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "drawdown_status"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "risk"):
            fn = node
            while fn in parents and not isinstance(
                    fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = parents[fn]
            yield getattr(fn, "name", "<module>"), node.lineno


def test_no_card_reads_the_shared_engines_drawdown_for_a_caller():
    found = set()
    for path in sorted((ROOT / "bot" / "skills").glob("*.py")):
        rel = str(path.relative_to(ROOT))
        for fn, line in _shared_drawdown_reads(path.read_text()):
            found.add((rel, fn))
            assert (rel, fn) in _OPERATOR_READS, (
                f"{rel}:{line} in {fn} reads engine.risk.drawdown_status(), the "
                "OPERATOR's drawdown; a caller's card reads "
                "trade_gate.caller_risk(engine, user_id)")
    stale = set(_OPERATOR_READS) - found
    assert not stale, f"exemptions whose read is gone: {stale}"


@pytest.mark.parametrize("src, expected", [
    ("def f(self):\n    return self.engine.risk.drawdown_status()\n", [("f", 2)]),
    ("def f(engine):\n    return engine.risk.drawdown_status()\n", [("f", 2)]),
    ("def f(risk):\n    return risk.drawdown_status()\n", []),
    ("def f(self):\n    def g():\n        return self.engine.risk.drawdown_status()\n",
     [("g", 3)]),
])
def test_the_rule_reads_the_shapes_it_names(src, expected):
    assert list(_shared_drawdown_reads(src)) == expected


# ── the drawdown the gate enforces includes the person's ───────────────────


def _engine_with_person(tmp_path, person_dd):
    import os

    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    eng = RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "r.json"))
    eng._live_equity_peak = 100.0
    eng._last_live_equity = 98.0          # this venue: 2.0% below its peak
    eng._person_drawdown_pct = lambda: (person_dd, "across 2 venue(s)")
    return eng


def test_the_status_reports_the_persons_drawdown_when_the_gate_would(tmp_path):
    st = _engine_with_person(tmp_path, 7.5).drawdown_status()
    assert st["drawdown_pct"] == 7.5 and st["drawdown_source"] == "person"
    assert st["live_drawdown_pct"] == pytest.approx(2.0)


def test_a_smaller_person_drawdown_leaves_the_venues_figure(tmp_path):
    """Tighten-only, as the gate is: the larger of the two is enforced."""
    st = _engine_with_person(tmp_path, 1.0).drawdown_status()
    assert st["drawdown_pct"] == pytest.approx(2.0) and st["drawdown_source"] == "live"


def test_no_person_reading_is_the_venues_figure(tmp_path):
    st = _engine_with_person(tmp_path, None).drawdown_status()
    assert st["drawdown_source"] == "live" and st["person_drawdown_pct"] is None


def test_a_card_prints_the_person_figure_and_says_whose_peak_it_is():
    from bot.formatters.drawdown_card import (
        drawdown_source_note,
        resolve_display_drawdown,
    )
    st = {"drawdown_pct": 7.5, "drawdown_source": "person", "effective_limit_pct": 10.0}
    pct, src, limit = resolve_display_drawdown(0.0, st, 10.0)
    assert (pct, src, limit) == (7.5, "person", 10.0)
    assert "every venue" in drawdown_source_note(src)


def test_an_equal_person_drawdown_is_attributed_to_the_venue(tmp_path):
    """The gate names the person only when theirs is strictly the larger."""
    st = _engine_with_person(tmp_path, 2.0).drawdown_status()
    assert st["drawdown_source"] == "live"


def test_an_engine_with_no_per_user_seam_is_read_not_unread():
    """No `risk_for` means one account, the shared engine, which was read.
    The first draft of the walk called that a failed read and turned every
    such engine's clear gate into UNREAD; the full suite said so."""
    op = _Risk(1.0)
    assert SR.entry_gate(NS(risk=op), CALLER) == ""
    g = trade_gate.entry_gate(NS(risk=op, _halted=False), CALLER, live=False)
    assert g["unknown"] is False and g["blocked"] is False
