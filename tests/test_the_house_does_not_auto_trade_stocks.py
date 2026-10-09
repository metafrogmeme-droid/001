"""The engine does not AUTO-trade stock, ETF or pre-IPO perps by default.

Live `/parity`, 9 October (232 strategy exits): Stock 52 trades, 23% won, PF
0.19; ETF 11 trades, PF 0.03; Crypto 159 trades, 45% won, PF 0.73. No
benchmark has ever held an equity perp. The owner chose: the house strategy
does not auto-trade them by default, and every class stays scannable and
tradeable by hand.

So the switch is on the AUTONOMOUS confirm only. `STOCK_TRADING_ENABLED`
existed already and was read nowhere: a switch named for stock trading that
turned nothing off. It is read by `autonomous_class_refusal` now, which the
one auto-confirm suppression asks (`_auto_confirm_suppressed`, the reading the
tick and `/forcescan` share). The scan's class switches (`SCAN_CLASS_*`) are
untouched, so a stock is still scanned, analysed and offered with a Confirm
button, and the tap places it.

The class is `category_for_symbol`'s, the reading `/parity`'s asset-class rows
take, so the gate and the evidence that set it agree about what a stock is.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.config import CONFIG, StockTradingConfig
from bot.core import engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.stock_trading import EQUITY_PERP_CLASSES, autonomous_class_refusal
from bot.risk.quality_ladder import MEASURED_BASIS, confidence_basis
from bot.skills.engine_ops_commands import autoconfirm_placement_line
from bot.utils.models import Direction, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parents[1]

CRYPTO, STOCK, ETF = "BTC/USDT:USDT", "TSLA/USDT:USDT", "XLK/USDT:USDT"
PRE_IPO, COMMODITY, METAL = "OPENAI/USDT:USDT", "CL/USDT:USDT", "XAU/USDT:USDT"


@contextmanager
def _stocks(enabled: bool):
    was = CONFIG.stocks
    object.__setattr__(CONFIG, "stocks", dataclasses.replace(was, enabled=enabled))
    try:
        yield
    finally:
        object.__setattr__(CONFIG, "stocks", was)


def _idea(asset, confidence=0.95):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=106.0, confidence=confidence,
                     reasoning="x", source="unknown")


def _host():
    """A stand-in `self` carrying the book and the real methods."""
    host = SimpleNamespace(analyzer=None, _pending_ideas={}, _pending_atr={},
                           _pending_pyramid={}, _pending_timing={},
                           _engine_idea_ids=set())
    for name in ("_auto_confirm_batch", "_auto_confirm_gate_value",
                 "_auto_confirm_suppressed", "_engine_pending_ids",
                 "_register_engine_idea", "_drop_pending_idea"):
        setattr(host, name, getattr(RuneClawEngine, name).__get__(host))
    return host


# ── the default ─────────────────────────────────────────────────────────────

def test_it_is_off_by_default():
    assert StockTradingConfig.__dataclass_fields__["enabled"].default is False


def test_the_classes_are_the_equity_perps():
    assert EQUITY_PERP_CLASSES == ("Stock", "ETF", "Pre-IPO")


# ── the rule, both arms ─────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", [STOCK, ETF, PRE_IPO])
def test_an_equity_perp_is_refused_while_the_switch_is_off(asset):
    with _stocks(False):
        why = autonomous_class_refusal(asset)
    assert why and "STOCK_TRADING_ENABLED is off" in why


@pytest.mark.parametrize("asset", [CRYPTO, "BTC/USDT", COMMODITY, METAL])
def test_every_other_class_is_not(asset):
    with _stocks(False):
        assert autonomous_class_refusal(asset) is None


@pytest.mark.parametrize("asset", [STOCK, ETF, PRE_IPO, CRYPTO])
def test_the_switch_on_refuses_nothing(asset):
    with _stocks(True):
        assert autonomous_class_refusal(asset) is None


def test_an_unreadable_class_is_refused():
    with _stocks(False), patch("bot.core.market_scanner.category_for_symbol",
                               side_effect=ValueError("boom")):
        why = autonomous_class_refusal(CRYPTO)
    assert why == "its asset class could not be read (ValueError)"


# ── the batch the tick and /forcescan auto-confirm from ─────────────────────

def _batch(enabled: bool):
    host = _host()
    ideas = {a: _idea(a) for a in (CRYPTO, STOCK, ETF, COMMODITY)}
    for i in ideas.values():
        host._register_engine_idea(i)
    audits: list = []
    with _stocks(enabled), patch.object(engine_mod, "audit",
                                        lambda *a, **k: audits.append(k)):
        picked = {tidea.asset for _, tidea in host._auto_confirm_batch(0.85)}
    return picked, audits, host


def test_the_batch_leaves_stocks_and_etfs_for_a_tap():
    picked, audits, host = _batch(False)
    assert picked == {CRYPTO, COMMODITY}
    # Withheld, not dropped: each is still pending, its card still a door.
    assert {i.asset for i in host._pending_ideas.values()} >= {STOCK, ETF}
    withheld = [a for a in audits if a.get("result") == "SUPPRESSED_CLASS"]
    assert {a["data"]["why"].split(" ")[0] for a in withheld} == {"Stock", "ETF"}


def test_with_the_switch_on_the_batch_takes_them_all():
    picked, audits, _ = _batch(True)
    assert picked == {CRYPTO, STOCK, ETF, COMMODITY}
    assert not [a for a in audits if a.get("result") == "SUPPRESSED_CLASS"]


def test_a_hand_typed_ticket_is_still_refused_for_its_stamp_first():
    """The class rule sits BESIDE the stamp rule, not in place of it."""
    host = _host()
    from bot.skills.manual_trade import build_manual_idea
    ticket = build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0)
    host._register_engine_idea(ticket)
    audits: list = []
    with _stocks(True), patch.object(engine_mod, "audit",
                                     lambda *a, **k: audits.append(k)):
        assert host._auto_confirm_batch(0.85) == []
    assert [a["result"] for a in audits] == ["SUPPRESSED_UNMEASURED"]


# ── what it must not change ─────────────────────────────────────────────────

def test_the_learners_still_read_a_stocks_confidence_as_measured():
    with _stocks(False):
        assert confidence_basis(_idea(STOCK)) == MEASURED_BASIS


def test_only_the_autonomous_suppression_asks_the_rule():
    """A person's tap is not gated: the rule is asked from the auto-confirm
    suppression and nowhere on the confirm path. Callers are found by walking
    every module under bot/, so a new caller fails here by name."""
    callers = []
    for path in (ROOT / "bot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "autonomous_class_refusal"):
                    callers.append(f"{path.relative_to(ROOT)}::{fn.name}")
    assert sorted(set(callers)) == ["bot/core/engine.py::_auto_confirm_suppressed"]


def test_the_session_gate_reads_the_same_classes():
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    assert "_cat in EQUITY_PERP_CLASSES" in src
    assert '("Stock", "ETF", "Pre-IPO")' not in src


# ── the card that says what auto-confirm does ───────────────────────────────

def test_the_autoconfirm_card_names_the_exception_while_it_holds():
    engine = SimpleNamespace(_autonomous_live_refusal=lambda: None)
    with _stocks(False):
        off = autoconfirm_placement_line(engine, is_live=True)
    with _stocks(True):
        on = autoconfirm_placement_line(engine, is_live=True)
    assert off.startswith("Live: an idea that clears the bar is placed with no tap.")
    assert "Not a Stock, ETF or Pre-IPO perp" in off
    assert on == "Live: an idea that clears the bar is placed with no tap."
