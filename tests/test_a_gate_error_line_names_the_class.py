"""A risk-gate check line that reports an evaluation error names the
exception's CLASS, never its text.

Twenty-six `except Exception as exc: failed.append(f"...: evaluation error
({exc})")` sites in the gate, one per check, printed the exception's text on
a check line -- which reaches the operator's card, the chat model's evidence
and the audit chain. The rule everywhere else on the money path is the class
alone, because a venue's rejection echoes the request (and its signature)
and a store's names a path. Driven: a margin-risk reading that raised
`RuntimeError("apiKey=SECRETVALUE")` put the key on the MARGIN_RISK line.
"""
from __future__ import annotations

import inspect
import os
import re
import tempfile
from datetime import datetime

import bot.risk.risk_engine as rem
from bot.compat import UTC
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-cls-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=0.72,
                     reasoning="cls", source="scan", timestamp=datetime.now(UTC))


def test_a_raising_reading_puts_its_class_and_not_its_text_on_the_line(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("apiKey=SECRETVALUE https://venue/x?sign=abc")

    monkeypatch.setattr(rem, "margin_risk_verdict", _boom)
    check = _engine().evaluate(_idea(), atr=2.0)
    line = next(ln for ln in check.checks_failed if ln.startswith("MARGIN_RISK"))
    assert line == "MARGIN_RISK: evaluation error (RuntimeError)"
    assert "SECRETVALUE" not in " ".join(check.checks_failed + check.checks_passed)


def test_no_gate_line_interpolates_the_exception_itself():
    src = code_only(inspect.getsource(rem))
    assert not re.search(r"evaluation error \(\{exc\}\)", src)
    assert len(re.findall(r"evaluation error \(\{type\(exc\)\.__name__\}\)", src)) >= 26
