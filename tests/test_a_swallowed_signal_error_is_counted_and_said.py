"""An error the signal path swallows is counted and said, and still swallowed.

Thirty-four enrichments on the analysis path (`RuneClawEngine` batch and
`_analyze_signal`, `Analyzer.analyze` and its helpers) failed open with
``except Exception: pass``, so one that broke on every call looked exactly
like one that worked. Each now calls `note_signal_path_error` with a site
name: the failure is counted per site, logged at WARNING the first time
(DEBUG after) by exception CLASS only, and printed by `/gates`. The
behaviour is unchanged -- the analysis carries on without that input, as
before -- which the first test holds by comparing against a clean run.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.session_aware as session_aware
from bot.core import signal_path_errors as spe
from bot.core.analyzer import Analyzer
from bot.skills.engine_ops_commands import EngineOpsCommands, signal_path_error_lines
from bot.utils.models import MarketSignal
from tests.candle_clock import anchored

ROOT = Path(__file__).resolve().parents[1]
NOW_MS = int(datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)

#: The functions whose silent handlers were triaged. Derived names, so a new
#: silent handler in any of them fails the guard below by name.
TRIAGED = {
    "bot/core/engine.py": {"_stage_enter_guarded", "_mtf_note", "_stage_profile_record",
                           "_analyze_signals_batched", "_one", "_analyze_signal"},
    "bot/core/analyzer.py": {"analyze", "_compute_indicators", "_llm_thesis", "scan_read",
                             "_record_no_trade", "_apply_mtf_elliott_alignment",
                             "mtf_candle_map"},
}


@pytest.fixture(autouse=True)
def _fresh_counts(monkeypatch):
    monkeypatch.setattr(spe, "_COUNTS", {})
    monkeypatch.setattr(spe, "_LAST_CLASS", {})


def _rows() -> list:
    out, p = [], 100.0
    for i in range(150):
        o = p
        p = p + 0.15 + 0.4 * math.sin(i / 3.0)
        out.append([i * 3_600_000, o, max(o, p) + 0.3, min(o, p) - 0.3, p, 1000.0 + 10 * i])
    return anchored(out, "1h", now_ms=NOW_MS)


async def _no_fetch(*_a, **_k):
    return None


def _analyze():
    a = Analyzer()
    a._sentiment.refresh_fear_greed = _no_fetch

    async def _thesis(*_a, **_k):
        return {"direction": "LONG", "confidence": 0.95, "reasoning": "planted",
                "source": "LLM_TEST"}
    a._llm_thesis = _thesis
    a._regime_hard_gate_reason = lambda *_x: None
    a._direction_guard_action = lambda *_x: None
    rows = _rows()
    sig = MarketSignal(symbol="BTC/USDT", price=rows[-1][4], change_pct_24h=1.0,
                       volume_usd_24h=1e8)
    as_of = datetime.fromtimestamp(NOW_MS / 1000 + 60, tz=timezone.utc)
    return asyncio.run(a.analyze(sig, rows, as_of=as_of, timeframe="1h")), a


def _decision(idea):
    keys = ("direction", "entry_price", "stop_loss", "take_profit", "confluence_score")
    return None if idea is None else {k: getattr(idea, k) for k in keys}


def test_a_failing_enrichment_is_counted_once_per_failure_and_the_analysis_goes_on(
        monkeypatch, caplog):
    def _boom(*_a, **_k):
        raise RuntimeError("venue said apiKey=SECRET")
    # A session reading that adds nothing, so the clean run and the failing
    # run make the same decision when the failure is swallowed.
    monkeypatch.setattr(session_aware, "get_current_session",
                        lambda now=None: SimpleNamespace(confidence_adjustment=0))
    clean, _ = _analyze()
    assert clean is not None, "the series must reach the enrichment under test"
    monkeypatch.setattr(session_aware, "get_current_session", _boom)
    caplog.set_level(logging.DEBUG, logger="runeclaw.signal_path")
    first, _ = _analyze()
    second, _ = _analyze()
    assert _decision(first) == _decision(clean) == _decision(second), "behaviour unchanged"
    assert spe.signal_path_error_counts()["analyzer.session_adjustment"] == (2, "RuntimeError")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "WARNING the first time, DEBUG after"
    assert "analyzer.session_adjustment" in warnings[0].getMessage()
    assert "RuntimeError" in warnings[0].getMessage()
    assert "SECRET" not in caplog.text


def test_an_engine_helper_failure_is_counted_under_its_own_name():
    from bot.core.engine import _stage_enter_guarded

    def _raise(*_a, **_k):
        raise KeyError("x")
    _stage_enter_guarded(SimpleNamespace(_stage_enter=_raise), "BTC/USDT", "fetch")
    assert spe.signal_path_error_counts() == {"engine.stage_enter": (1, "KeyError")}


def test_the_counter_itself_never_raises(monkeypatch):
    class _BadLock:
        def __enter__(self):
            raise RuntimeError("lock broke")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(spe, "_LOCK", _BadLock())
    spe.note_signal_path_error("anywhere", ValueError("x"))     # no raise


# ── /gates says it ───────────────────────────────────────────────────────────

def test_the_section_lists_each_site_by_count_then_name():
    spe.note_signal_path_error("b.site", ValueError())
    spe.note_signal_path_error("a.site", KeyError())
    spe.note_signal_path_error("b.site", TypeError())
    lines = signal_path_error_lines()
    assert lines[1:] == ["<code>b.site</code>: 2× (TypeError)",
                         "<code>a.site</code>: 1× (KeyError)"]


def test_an_empty_counter_is_said_as_a_reading_not_as_all_clear():
    assert signal_path_error_lines()[1] == "None recorded this session."


def test_gates_prints_the_section_even_before_any_gate_has_run():
    sent: list[str] = []

    async def _send(_update, text, **_k):
        sent.append(text)
    host = SimpleNamespace(engine=SimpleNamespace(risk=SimpleNamespace(gate_stats=lambda: {})),
                           _is_admin=lambda _u: True, _send=_send)
    spe.note_signal_path_error("analyze_signal.funding_seed", AttributeError())
    asyncio.run(EngineOpsCommands._cmd_gates(host, object(), None))
    assert len(sent) == 1
    assert "No gate evaluations recorded yet this session." in sent[0]
    assert "<code>analyze_signal.funding_seed</code>: 1× (AttributeError)" in sent[0]


# ── no silent handler is left in the triaged functions ───────────────────────

def _silent_handlers(src: str, names: set[str]) -> list[str]:
    tree = ast.parse(src)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    out = []
    for h in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        if not (isinstance(h.type, ast.Name) and h.type.id == "Exception" and len(h.body) == 1):
            continue
        b = h.body[0]
        if not (isinstance(b, (ast.Pass, ast.Continue))
                or (isinstance(b, ast.Return) and (b.value is None or (
                    isinstance(b.value, ast.Constant) and b.value.value is None)))):
            continue
        cur = h
        while cur in parents and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and cur.name in names:
            out.append(f"{cur.name}:{h.lineno}")
    return out


@pytest.mark.parametrize("path", sorted(TRIAGED))
def test_no_silent_handler_is_left_in_the_triaged_functions(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    assert _silent_handlers(src, TRIAGED[path]) == []


def test_the_guard_sees_a_planted_silent_handler():
    planted = ("def analyze():\n    try:\n        x()\n    except Exception:\n        pass\n"
               "def other():\n    try:\n        x()\n    except Exception:\n        pass\n")
    assert _silent_handlers(planted, {"analyze"}) == ["analyze:4"]
