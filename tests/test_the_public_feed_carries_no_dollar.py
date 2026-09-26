"""The agent mind-stream is public, so a close is told in percent, never dollars.

Driven on 2026-09-26: every operator close was emitted to the public feed as
``Closed BTC/USDT -$41.20`` with ``data={"pnl": -41.2}``, under a comment and
a module docstring both saying realized P&L "is already public on the
track-record page". It is not. ``app/routes/track.js`` states that the public
track record is percent, ratio and count only, and indexes its curve to 100
so no account size escapes. The website stored the event as sent, broadcast
it on the unauthenticated ``/api/stream``, pushed it to every subscriber and
served it from ``GET /api/feed/recent`` and the MCP tool ``get_agent_feed``.

``agent_feed.close_event`` is the producer now: the return on margin, net of
fees, which is the same reading the public close line prints "on margin"; no
figure when the margin was never recorded; nothing when the close was never
priced. The receiver's backstop is ``app/lib/public_feed.js``
(``app/test/public_feed_carries_no_dollar.test.js``).
"""

from __future__ import annotations

import ast
import logging
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.agent_feed as agent_feed_mod
from bot.core.agent_feed import close_event

ROOT = Path(__file__).resolve().parents[1]
DOLLAR = re.compile(r"\$\s?[-+]?\d")


def _no_dollar(ev: dict) -> None:
    blob = repr(ev)
    assert not DOLLAR.search(blob), blob
    assert "pnl" not in ev["data"], ev["data"]


# ── the producer ──────────────────────────────────────────────────────────

def test_a_priced_close_with_a_margin_is_told_in_percent_of_margin():
    ev = close_event("BTC/USDT", -41.2, 200.0, "SL HIT")
    assert ev == {
        "title": "Closed BTC/USDT -20.60% on margin",
        "body": "Exit: SL HIT",
        "symbol": "BTC/USDT",
        "severity": "warning",
        "data": {"return_on_margin_pct": -20.6, "reason": "SL HIT"},
    }
    _no_dollar(ev)
    win = close_event("ETH/USDT", 12.5, 200.0, "TP HIT")
    assert win["title"] == "Closed ETH/USDT +6.25% on margin"
    assert win["severity"] == "success"
    _no_dollar(win)


def test_the_figure_is_the_one_the_public_close_line_prints():
    # One reading, two publishers: the channel prints `pnl_pct_margin_net`,
    # which the executor builds with realized_margin_return_pct.
    from bot.utils.leveraged_return import realized_margin_return_pct
    ev = close_event("SOL/USDT", 1.89, 7.44, "")
    assert ev["data"]["return_on_margin_pct"] == round(
        realized_margin_return_pct(1.89, 7.44), 2)
    assert ev["title"] == "Closed SOL/USDT +25.40% on margin"
    assert ev["body"] == "", "no reason, no body"


@pytest.mark.parametrize("margin", [None, 0.0, -5.0, float("nan"), "n/a"])
def test_a_margin_nobody_recorded_gets_no_figure_and_says_so(margin):
    ev = close_event("BTC/USDT", -41.2, margin, "SL HIT")
    assert ev["title"] == "Closed BTC/USDT"
    assert ev["body"] == "Exit: SL HIT · return on margin not recorded"
    assert ev["data"] == {"return_on_margin_pct": None, "reason": "SL HIT"}
    assert ev["severity"] == "warning", "the sign is read even when the size is not"
    _no_dollar(ev)
    bare = close_event("BTC/USDT", 3.0, margin, "")
    assert bare["body"] == "Return on margin not recorded"
    assert bare["severity"] == "success"


@pytest.mark.parametrize("pnl", [None, float("nan"), float("inf"), "n/a"])
def test_a_close_nobody_priced_is_not_announced(pnl):
    # Unchanged behaviour: an unpriced close was never emitted. Announcing it
    # on a public page is a decision about the page, not a wording fix.
    assert close_event("BTC/USDT", pnl, 200.0, "SL HIT") is None


def test_a_close_with_no_symbol_is_not_announced():
    assert close_event("", -1.0, 200.0, "SL HIT") is None
    assert close_event(None, -1.0, 200.0, "SL HIT") is None


def test_a_measured_break_even_is_neither_a_success_nor_a_loss():
    ev = close_event("BTC/USDT", 0.0, 200.0, "TIME STOP")
    assert ev["title"] == "Closed BTC/USDT +0.00% on margin"
    assert ev["severity"] == "info"


# ── the engine's close handler, driven ────────────────────────────────────

@pytest.fixture
def emitted(monkeypatch):
    got: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(agent_feed_mod.FEED, "emit",
                        lambda *a, **k: got.append((a, k)))
    return got


def _engine():
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._invalidate_live_balance_cache = lambda: None
    eng._symbol_cooldowns = {}
    eng._symbol_loss_streaks = {}
    eng._symbol_cooldown_seconds = 60
    eng._sync_live_state_to_website = lambda: None
    return eng


def _pos(pnl, margin):
    return SimpleNamespace(symbol="BTC/USDT", pnl_usd=pnl, close_reason="SL HIT",
                           cost_usd=margin, entry_price=63000.0, quantity=0.0158,
                           direction="LONG", trade_id="T1")


def test_the_operator_close_reaches_the_feed_in_percent(emitted):
    logging.disable(logging.CRITICAL)
    try:
        _engine()._on_live_position_closed(_pos(-41.2, 200.0), "")
    finally:
        logging.disable(logging.NOTSET)
    assert len(emitted) == 1
    args, kwargs = emitted[0]
    assert args == ("trade_close",)
    assert kwargs["title"] == "Closed BTC/USDT -20.60% on margin"
    assert kwargs["data"] == {"return_on_margin_pct": -20.6, "reason": "SL HIT"}
    assert not DOLLAR.search(repr(kwargs))


def test_the_margin_it_divides_by_is_the_recorded_one_not_the_notional(emitted):
    # cost_usd 0.0 is the orphan whose margin the venue never stated; the
    # notional (entry x quantity) is a different quantity and must not stand
    # in for it.
    logging.disable(logging.CRITICAL)
    try:
        _engine()._on_live_position_closed(_pos(-41.2, 0.0), "")
    finally:
        logging.disable(logging.NOTSET)
    [(_a, kwargs)] = emitted
    assert kwargs["title"] == "Closed BTC/USDT"
    assert kwargs["data"]["return_on_margin_pct"] is None


def test_a_per_user_close_and_an_unpriced_close_reach_no_feed(emitted):
    logging.disable(logging.CRITICAL)
    try:
        _engine()._on_live_position_closed(_pos(-41.2, 200.0), "7")
        _engine()._on_live_position_closed(_pos(None, 200.0), "")
    finally:
        logging.disable(logging.NOTSET)
    assert emitted == []


# ── every emit site's title, read off the source ──────────────────────────

def _emit_calls():
    """Every ``FEED.emit(...)`` call under ``bot/``, with its file."""
    for path in sorted((ROOT / "bot").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "emit"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "FEED"):
                yield path.relative_to(ROOT), node


def _literal_parts(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return []


def test_no_emit_site_spells_a_dollar_in_its_title():
    """A SCAN, stated as one: the emit sites sit inside long handlers (the
    engine's close handler, the executor's trailing-stop loop, the monitor's
    dispatch) and a title is decided by the literal text around its
    placeholders. No producer puts a PRICE in a title, so a `$` in one is an
    amount; the receiver refuses it too, and this says so at the source. A
    call that spreads its fields (`FEED.emit("trade_close", **ev)`) is the
    close event, which the drives above measure."""
    sites = list(_emit_calls())
    assert len(sites) >= 7, f"the walk found too few emit sites: {sites}"
    spread = [str(p) for p, c in sites if any(k.arg is None for k in c.keywords)]
    assert spread == ["bot/core/engine.py"], spread
    for path, call in sites:
        if len(call.args) < 2:
            continue
        for part in _literal_parts(call.args[1]):
            assert "$" not in part, f"{path}:{call.lineno} title spells a dollar: {part!r}"


def test_the_close_event_rounds_but_never_invents():
    ev = close_event("BTC/USDT", 1e-9, 1e9, "")
    assert ev["data"]["return_on_margin_pct"] == 0.0
    assert math.isfinite(ev["data"]["return_on_margin_pct"])
    assert ev["severity"] == "success", "a tiny measured gain is still a gain"
