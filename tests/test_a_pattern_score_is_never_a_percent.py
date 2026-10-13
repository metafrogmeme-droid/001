"""A chart pattern's figure is the detector's score, never a percent.

Each detector in `bot.core.chart_patterns` writes a hand-set constant or a
clamp (``0.70 if broke else 0.55``), and nothing records how a pattern's calls
turned out. Every surface printed that figure as ``72%``, which reads as a win
probability. It is printed as ``detector score 72 (unmeasured)``
(`signal_confidence.pattern_score_text`), the website's twin is pinned to the
same words, and an unread figure is a dash, never a zero.

Driven on every bot surface that prints one: `/deepscan` (`DeepScanSkill`),
its PNG card, the scan's detail line and the single-symbol deep scan's two
branches. The plan's F4.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.core import chart_patterns
from bot.core.signal_confidence import (
    PATTERN_SCORE_WORD,
    UNMEASURED_WORD,
    pattern_score_text,
)
from bot.formatters.signal_card import render_patterns_card
from tests.png_text import strings

PATS = [
    {"name": "Double Top", "signal": "bearish", "confidence": 0.71,
     "description": "two peaks"},
    {"name": "No Score", "signal": "bullish", "description": "nothing read"},
]
SAID = "detector score 71 (unmeasured)"
#: The figure as a percent, anywhere: what this file exists to refuse.
PERCENT = re.compile(r"71\s*%")


# ── the wording ─────────────────────────────────────────────────────────────


def test_the_wording_says_whose_figure_it_is_and_that_nothing_measured_it():
    assert (PATTERN_SCORE_WORD, UNMEASURED_WORD) == ("detector score", "unmeasured")
    assert pattern_score_text(0.71) == SAID
    assert pattern_score_text(0) == "detector score 0 (unmeasured)", "a read zero is a reading"


@pytest.mark.parametrize("unread", [None, True, "x", float("nan"), float("inf")])
def test_an_unread_figure_is_a_dash_not_a_zero(unread):
    assert pattern_score_text(unread) == "—"


# ── /deepscan ───────────────────────────────────────────────────────────────

_CANDLES = ([[i * 60_000, 1.0, 1.01, 0.99, 1.0 + (i % 3) * 0.001, 10.0]
             for i in range(59)]
            + [[59 * 60_000, 1.0, 1.06, 1.0, 1.05, 40.0]])


def test_deepscan_prints_the_detectors_score(monkeypatch):
    import bot.utils.website_sync as ws
    from bot.skills.skill_registry import DeepScanSkill

    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            return [list(c) for c in _CANDLES]

    async def _ex():
        return _Ex()

    engine = SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_ex, _get_futures_exchange=_ex),
        risk=SimpleNamespace(circuit_breaker_active=False),
        _last_deepscan_hits=None)
    monkeypatch.setattr(ws, "sync_scan_in_background", lambda p: None)
    monkeypatch.setattr(chart_patterns, "scan_all_chart_patterns",
                        lambda *a, **k: [dict(p) for p in PATS])

    text = asyncio.run(DeepScanSkill().execute(engine, timeframe="4h"))

    assert engine._last_deepscan_hits, "no hits: the fixture measures nothing"
    assert f"<code>{SAID}</code>" in text
    assert not PERCENT.search(text), text
    # The unread figure: a dash, and no bar drawn for it.
    [line] = [ln for ln in text.splitlines() if "No Score" in ln][:1]
    assert line.endswith("<code>—</code>") and "░" not in line, line


# ── the PNG ─────────────────────────────────────────────────────────────────


def test_the_card_prints_the_score_and_names_it_once():
    hits = [{"symbol": "ETH/USDT", "price": 1582.5, "chg": -0.04, "rsi": 51,
             "vol_spike": False, "chart_patterns": [dict(p) for p in PATS],
             "candle_patterns": {}}]
    drawn = strings(render_patterns_card, hits, scan_label="DEEP SCAN")
    i = drawn.index("Double Top")
    assert drawn[i + 1] == "71", drawn
    j = drawn.index("No Score")
    assert drawn[j + 1] == "—", drawn
    assert not any(PERCENT.search(s) for s in drawn), drawn
    assert any(f"{PATTERN_SCORE_WORD} 0-100, {UNMEASURED_WORD}" in s for s in drawn), drawn


# ── the scan's lines ────────────────────────────────────────────────────────


def _row():
    return {"sym": "SOL/USDT", "dir": "LONG", "price": 122.4, "rsi": 55, "atr": 1.2,
            "vol_ratio": 1.4, "sma20": 120.0, "score": 0.6, "change_pct": 1.2,
            "patterns": [dict(p) for p in PATS]}


def test_the_scan_detail_line_prints_the_score():
    from bot.skills.scan_skill import _fmt_detail

    text = _fmt_detail(_row())
    assert f"Double Top (bearish, {SAID})" in text
    assert "No Score (bullish, —)" in text
    assert not PERCENT.search(text), text


@pytest.mark.parametrize("rich", [True, False])
def test_the_single_symbol_deep_scan_prints_the_score(monkeypatch, rich):
    import bot.skills.scan_skill as ss

    async def _data(*a, **k):
        return {"change_pct": 1.2, "volume_24h_usd": 1e6} if rich else None

    async def _scanned(*a, **k):
        return _row()

    monkeypatch.setattr(ss, "fetch_analysis_data", _data)
    monkeypatch.setattr(ss, "_scan_symbol", _scanned)
    monkeypatch.setattr(ss, "render_analysis_card", lambda data, idea: "CARD")

    async def _no_idea(*a, **k):
        return None

    engine = SimpleNamespace(scanner=SimpleNamespace(_get_exchange=AsyncMock(return_value=object())),
                             analyzer=None, _analyze_signal=_no_idea,
                             _pending_ideas={}, _pending_atr={})
    msg = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=msg)),
                             effective_user=SimpleNamespace(id=7))
    asyncio.run(ss._scan_single(update, SimpleNamespace(), engine, "SOL/USDT"))
    text = msg.edit_text.await_args_list[-1].args[0]

    assert ("CARD" in text) is rich, "the fixture reached the branch it names"
    assert "<b>Double Top</b>" in text and f"({SAID})" in text
    assert "(—)" in text, "the unread figure is a dash"
    assert not PERCENT.search(text), text
