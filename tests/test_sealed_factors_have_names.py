"""59.3% of a decision, attributed to nothing, sealed into the hash chain.

`FactorAttribution` has a field called ``factor``. `_explain_slice` read
``f.get("name", "")``. There is no ``name`` field and never was, so every
factor attribution the Guardian Flight Recorder sealed looked like this:

    {"name": "", "contribution_pct": 59.3, "direction": "bullish"}

A percentage and a direction attached to nothing — written into a hash-chained
record whose stated purpose is that "every executed decision is fully
auditable", on every executed trade, permanently.

WHY IT SURVIVED. Nothing renders `factors`. The record LOOKED complete — three
keys, a plausible number, a direction — and the single field carrying the
identity was the one no surface could show. This repository's rule is that a
module nothing calls is indistinguishable from one that does not work; this is
the same thing one level down, a FIELD nothing reads, and it is worse because
the output is sealed and cannot be corrected in place.

AND THE DEFAULT IS WHAT ALLOWED IT. `f.get("name", "")` cannot fail. A plain
`f["name"]` would have raised KeyError the first time it ran, in the analyzer,
in front of whoever wrote it. The default turned a loud programming error into
a quiet permanent one — which is the same trade CLAUDE.md's table names in its
first row, applied to a string instead of a number.

The test that matters most here is not the one that checks today's key. It is
`test_the_slice_only_reads_fields_the_model_actually_has`, which reads the keys
out of `_explain_slice` itself and requires each to exist on the model, and so
catches the NEXT one of these before it is ever sealed.
"""

from __future__ import annotations

import pathlib
import re


from bot.core.explainability import (ExplainabilityEngine, ExplainabilityReport,
                                     FactorAttribution)
from bot.guardian.flight_recorder import _explain_slice, decision_idea_payload

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _real_report() -> dict:
    """A report from the real engine, driven the way the analyzer drives it."""
    return ExplainabilityEngine().explain(
        trade_id="TI-1", symbol="BTC/USDT", direction="LONG",
        indicators={"rsi": 61, "regime": "TREND_UP"}, regime="TREND_UP",
        confluence=0.68, confidence=0.72,
        votes=[0.8, -0.3, 0.5], weights=[1.0, 0.5, 0.8],
        labels=["rsi", "macd", "mtf_alignment"],
    ).model_dump(mode="json")


# ── the sealed record names its factors ─────────────────────────────────────

def test_a_sealed_factor_carries_the_name_of_the_factor():
    sealed = _explain_slice(_real_report())
    assert sealed["factors"], "the slice sealed no factors at all"
    names = [f["name"] for f in sealed["factors"]]
    assert all(names), (
        f"a factor was sealed with no name: {sealed['factors']} — a "
        "contribution percentage attributed to nothing")
    assert set(names) == {"rsi", "macd", "mtf_alignment"}, names


def test_the_biggest_contributor_is_identifiable():
    """The whole point of an attribution. 59.3% of a decision has to be 59.3%
    OF something."""
    sealed = _explain_slice(_real_report())
    top = max(sealed["factors"], key=lambda f: f["contribution_pct"])
    assert top["name"] == "rsi"
    assert top["contribution_pct"] > 50


# ── the guard that generalises ──────────────────────────────────────────────

def test_the_slice_only_reads_fields_the_model_actually_has():
    """THE ONE THAT WOULD HAVE CAUGHT THIS, and catches the next one.

    Reads every `f.get("...")` out of the factor loop in `_explain_slice` and
    requires each key to exist on `FactorAttribution`. Checking today's spelling
    only proves today; checking the RELATION between the two sides proves that
    a rename on either side fails here rather than being sealed silently.
    """
    from tests.source_scan import code_only
    # COMMENTS STRIPPED FIRST. The comment beside the fix quotes the broken
    # `f.get("name", "")` it replaced, and to a regex that is indistinguishable
    # from the code doing it — this test failed on its own explanation before it
    # failed on anything real. CLAUDE.md names this as having caused four false
    # failures already; this is the fifth.
    src = code_only((ROOT / "bot" / "guardian" / "flight_recorder.py")
                    .read_text(encoding="utf-8"))
    i = src.index("slim_factors.append({")
    block = src[i:src.index("})", i)]
    keys = set(re.findall(r'f\.get\(\s*"([^"]+)"', block))
    assert keys, "the factor slice no longer reads its fields the way this test does"
    fields = set(FactorAttribution.model_fields)
    unknown = keys - fields
    assert not unknown, (
        f"_explain_slice reads {sorted(unknown)} off a FactorAttribution, which "
        f"has {sorted(fields)} — every one of those seals as the default, "
        "permanently, and no test of today's output would notice")


def test_the_report_slice_reads_report_fields_that_exist():
    """The same check for the outer report, where the same mistake fits."""
    from tests.source_scan import code_only
    src = code_only((ROOT / "bot" / "guardian" / "flight_recorder.py")
                    .read_text(encoding="utf-8"))
    block = src[src.index("def _explain_slice"):src.index("def decision_risk_payload")]
    keys = set(re.findall(r'report\.get\(\s*"([^"]+)"', block))
    unknown = keys - set(ExplainabilityReport.model_fields)
    assert not unknown, (
        f"_explain_slice reads {sorted(unknown)} off an ExplainabilityReport "
        "that does not have them")


# ── it still fails soft ─────────────────────────────────────────────────────

def test_a_malformed_factor_does_not_take_the_seal_down():
    """The slice is best-effort by design — a recorder failure must never cost
    a trade. Naming the field must not turn a tolerated shape into a crash."""
    assert _explain_slice({"factors": ["not a dict", 42, None],
                           "top_bullish": [], "top_bearish": []})["factors"] == []
    # An empty report is NO report, and says so rather than returning a
    # skeleton that reads as "explained, with nothing in it".
    assert _explain_slice(None) is None
    assert _explain_slice({}) is None


def test_a_factor_with_no_name_seals_as_empty_not_as_a_crash():
    """A factor genuinely missing its identity is still sealed — with an empty
    name, which is honest — rather than raising inside the recorder."""
    out = _explain_slice({"factors": [{"contribution_pct": 10.0, "direction": "bullish"}]})
    assert out["factors"][0]["name"] == ""


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_slice_is_reached_from_the_decision_payload():
    """Every test above calls `_explain_slice` directly. This drives the
    function the engine actually calls for each executed decision."""
    class _Idea:
        id = "TI-1"
        direction = "LONG"
        entry_price, stop_loss, take_profit = 61000.0, 60000.0, 63000.0
        confidence = 0.72
        risk_reward_ratio = 2.0

    idea = _Idea()
    idea._explain_report = _real_report()
    payload = decision_idea_payload(idea)
    names = [f["name"] for f in (payload.get("explain") or {}).get("factors", [])]
    assert names and all(names), (
        f"the decision payload seals nameless factors: {names}")


# ── the derivation, not only the conclusion ─────────────────────────────────

class TestTheReasoningChainIsSealed:
    """`ExplainabilityReport` carries a step-by-step trace — what data went in,
    what came out, which way it pushed — and `_explain_slice` kept the
    CONCLUSIONS (top_bullish, factors, summary) while discarding the derivation.

    A sealed record that states "bullish, 0.68 confluence" and cannot show the
    path to it asks the reader to trust the arithmetic, which is the one thing
    a hash chain exists to remove.

    Adding it is forward-only. The chain hashes
    `sha256(f"{seq}|{type}|{json(payload)}")` over the payload AS GIVEN, so
    every historical entry keeps hashing its own stored bytes — no migration,
    no reseal, the same property the v4 call seal has.
    """

    def test_the_chain_is_in_the_seal(self):
        sealed = _explain_slice(_real_report())
        chain = sealed["reasoning_chain"]
        assert chain, "the reasoning chain is being dropped again"
        stages = [st["stage"] for st in chain]
        assert "regime_detection" in stages and "confluence_scoring" in stages, stages
        for st in chain:
            assert set(st) == {"stage", "input", "output", "impact"}, st

    def test_the_true_step_count_rides_beside_the_capped_list(self):
        """A reader who sees twelve steps has no way to know whether that was
        all of them. A trace that silently stops is a partial presented as a
        derivation — the defect this field exists to remove, one level in."""
        from bot.guardian.flight_recorder import _MAX_STEPS

        many = {"reasoning_chain": [
            {"stage": f"s{i}", "input_summary": "i", "output_summary": "o", "impact": "neutral"}
            for i in range(_MAX_STEPS + 7)]}
        sealed = _explain_slice(many)
        assert len(sealed["reasoning_chain"]) == _MAX_STEPS
        assert sealed["reasoning_steps_total"] == _MAX_STEPS + 7, (
            "the seal records only what it kept, so a truncated trace is "
            "indistinguishable from a complete one")

    def test_an_absent_chain_is_an_empty_list_and_a_zero(self):
        sealed = _explain_slice({"top_bullish": ["x"]})
        assert sealed["reasoning_chain"] == []
        assert sealed["reasoning_steps_total"] == 0

    def test_malformed_steps_are_skipped_not_crashed_on(self):
        sealed = _explain_slice({"reasoning_chain": ["nope", 7, None,
                                                    {"stage": "ok"}]})
        assert [st["stage"] for st in sealed["reasoning_chain"]] == ["ok"]
        assert sealed["reasoning_steps_total"] == 1, (
            "the total counts entries that could not be read, so it overstates "
            "what was sealed")

    def test_a_step_missing_its_summaries_seals_empty_strings(self):
        sealed = _explain_slice({"reasoning_chain": [{"stage": "regime_detection"}]})
        st = sealed["reasoning_chain"][0]
        assert st["stage"] == "regime_detection"
        assert st["input"] == "" and st["output"] == "" and st["impact"] == ""

    def test_the_chain_is_reached_from_the_decision_payload(self):
        class _Idea:
            id, direction = "TI-1", "LONG"
            entry_price, stop_loss, take_profit = 1.0, 0.9, 1.2
            confidence, risk_reward_ratio = 0.7, 2.0

        idea = _Idea()
        idea._explain_report = _real_report()
        explain = decision_idea_payload(idea).get("explain") or {}
        assert explain.get("reasoning_chain"), (
            "the decision payload seals no reasoning chain")
