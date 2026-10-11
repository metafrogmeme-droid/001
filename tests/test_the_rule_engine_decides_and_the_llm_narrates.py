"""The rule engine decides a trade; the LLM narrates beside it, until measured.

The LLM chose every live trade's direction and carried up to 40% of its score,
and nothing had measured whether that helped (improvement plan C2). The
operator's decision, 11 October: the rule engine's thesis decides -- the one
the frozen backtest and every card measure -- and the LLM is asked where it
was asked before, as a shadow. Its direction and confidence go to the
calibration log for C2, and its reasoning rides on the idea marked as not used
to decide. `LLM_DECIDES=on` restores the old path.

Driven on the real `Analyzer._llm_thesis`; only the LLM's own call (the
shadow) and the rule engine's answer are stand-ins.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock

from bot.config import CONFIG
from bot.core import analyzer as an
from bot.core.analyzer import Analyzer

LLM_CALL = {"direction": "SHORT", "confidence": 0.95, "reasoning": "bearish divergence on 4h",
            "source": "LLM_TIER3", "model_used": "m-large", "prompt_hash": "abc"}
RULE_CALL = {"direction": "LONG", "confidence": 0.62, "reasoning": "confluence 0.61, RSI 48"}


@contextmanager
def _decides(value: bool):
    prev = CONFIG.analyzer.llm_decides
    object.__setattr__(CONFIG.analyzer, "llm_decides", value)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.analyzer, "llm_decides", prev)


def _analyzer(monkeypatch, *, rule=RULE_CALL, llm=LLM_CALL):
    """A bare Analyzer whose LLM is present; the shadow call answers ``llm``
    and records what it was asked; the rule engine answers ``rule``."""
    a = Analyzer.__new__(Analyzer)
    a._llm = MagicMock()
    asked: list[dict] = []
    real = Analyzer._llm_thesis

    async def thesis(self, signal, indicators, **kw):
        if kw.get("_shadow"):
            asked.append(kw)
            return dict(llm) if llm is not None else None
        return await real(self, signal, indicators, **kw)

    monkeypatch.setattr(Analyzer, "_llm_thesis", thesis)
    monkeypatch.setattr(a, "_rule_based_thesis",
                        lambda s, i: dict(rule) if rule is not None else None, raising=False)
    return a, asked


def _run(a, **kw):
    sig = MagicMock(symbol="BTC/USDT:USDT")
    return asyncio.run(Analyzer._llm_thesis(a, sig, {}, **kw))


def test_by_default_the_rule_engine_decides_and_the_llm_is_a_shadow(monkeypatch):
    a, asked = _analyzer(monkeypatch)
    with _decides(False):
        out = _run(a, user_id=7, background=False)
    assert out["direction"] == "LONG" and out["confidence"] == 0.62
    assert out["source"] == "RULE_ENGINE_DECIDES"
    assert out["shadow_llm"]["direction"] == "SHORT" and out["shadow_llm"]["confidence"] == 0.95
    # The LLM was asked once, with the same identity the old path used.
    assert len(asked) == 1 and asked[0]["user_id"] == 7 and asked[0]["_shadow"] is True


def test_with_llm_decides_on_the_old_path_runs_and_no_shadow_is_asked(monkeypatch):
    a, asked = _analyzer(monkeypatch)
    # Past the decision branch the method continues into the LLM pipeline;
    # stand that pipeline in by its first call, the semantic cache.
    a._llm_cache = MagicMock()
    a._llm_cache.get.return_value = dict(LLM_CALL)
    a._llm_cache_scope = MagicMock(return_value="t")
    with _decides(True):
        out = _run(a, user_id=7)
    assert asked == [], "a shadow was asked while the LLM decides"
    assert out["direction"] == "SHORT", "the LLM's own call decides when LLM_DECIDES is on"


def test_a_neutral_rule_engine_asks_the_llm_nothing(monkeypatch):
    a, asked = _analyzer(monkeypatch, rule=None)
    with _decides(False):
        assert _run(a, user_id=7) is None
    assert asked == []


def test_a_shadow_the_pipeline_sent_to_the_rules_is_not_the_llms_view(monkeypatch):
    a, _ = _analyzer(monkeypatch, llm=dict(LLM_CALL, source="RULE_ENGINE_BUDGET"))
    with _decides(False):
        out = _run(a, user_id=7)
    assert "shadow_llm" not in out
    a2, _ = _analyzer(monkeypatch, llm=None)
    with _decides(False):
        assert "shadow_llm" not in _run(a2, user_id=7)


# ── what the idea and the calibration log carry ─────────────────────────────

def _decided(shadow=LLM_CALL):
    t = dict(RULE_CALL, source="RULE_ENGINE_DECIDES")
    if shadow is not None:
        t["shadow_llm"] = dict(shadow)
    return t


def test_the_idea_names_the_models_read_as_not_used_and_says_whether_it_agrees():
    text = an._shadow_narration(_decided())
    assert text == (" | Model's read (SHORT, disagrees; not used to decide): "
                    "bearish divergence on 4h")
    agree = an._shadow_narration(_decided(dict(LLM_CALL, direction="LONG")))
    assert "(LONG, agrees; not used to decide)" in agree
    assert an._shadow_narration(_decided(None)) == ""
    assert an._shadow_narration(dict(LLM_CALL)) == "", "a deciding LLM's reasoning is the thesis"


def test_the_ideas_llm_confidence_is_the_llms_or_none():
    assert an._llm_confidence_on_record(_decided()) == 0.95
    assert an._llm_confidence_on_record(_decided(None)) is None, \
        "the rule engine's confidence filed as the LLM's"
    assert an._llm_confidence_on_record(dict(LLM_CALL)) == 0.95
    assert an._llm_confidence_on_record(dict(RULE_CALL, source="RULE_ENGINE")) == 0.62


def test_the_calibration_row_records_the_llms_own_call_and_who_decided():
    row = an._calibration_llm_fields(_decided())
    assert (row["llm_direction"], row["llm_confidence_raw"], row["llm_source"]) == \
        ("SHORT", 0.95, "LLM_TIER3")
    assert (row["decided_by"], row["decision_direction"]) == ("RULE_ENGINE_DECIDES", "LONG")
    assert row["prompt_hash"] == "abc"
    # A row the LLM decided, as before.
    old = an._calibration_llm_fields(dict(LLM_CALL))
    assert (old["llm_direction"], old["decided_by"]) == ("SHORT", "LLM_TIER3")


def test_the_default_is_off():
    import inspect
    src = inspect.getsource(type(CONFIG.analyzer))
    assert '_env_switch("LLM_DECIDES", False)' in src


def test_the_idea_and_the_row_are_built_from_these_readings():
    """The idea is built inside a 900-line analysis no test runs end to end;
    its two fields and the calibration row must call the readings above."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(an.__file__).read_text(encoding="utf-8"))
    ideas = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "TradeIdea"]
    built = [n for n in ideas if any(k.arg == "llm_confidence" for k in n.keywords)]
    assert len(built) == 1, "one analyzer idea carries an LLM confidence"
    kw = {k.arg: k.value for k in built[0].keywords}
    def calls(node):
        return {c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "_shadow_narration" in calls(kw["reasoning"])
    assert "_llm_confidence_on_record" in calls(kw["llm_confidence"])
    rows = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)
            and any(isinstance(k, ast.Constant) and k.value == "confluence_score" for k in n.keys)]
    assert len(rows) == 1
    assert any(k is None and isinstance(v, ast.Call) and v.func.id == "_calibration_llm_fields"
               for k, v in zip(rows[0].keys, rows[0].values))
