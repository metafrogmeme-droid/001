"""A confidence nobody could read was the MAXIMUM confidence.

Three parsers turned a model-supplied confidence into a number with the same
expression::

    max(0.0, min(1.0, float(conf)))

`min(1.0, x)` returns 1.0 for every `x` that does not compare less than it,
and NaN compares less than nothing -- so the clamp answered **1.0** for 2.5,
for 100, for JSON `true`, for a bare `NaN` or `Infinity` token (both of which
`json.loads` accepts by default), and for `1e400`. It answered **0.0** for
`-5`, for `false` and for an ABSENT key. Then the JSON branch set
``_parsed = True``, which is the flag whose own docstring says False means
"LLM output was malformed" -- so every one of those sailed past the C-07 guard
that blocks a trade on a reply that did not parse.

THE ORDINARY CASE IS NOT THE EXOTIC ONE. The plain-text branch's regex is
``(\\d+\\.\\d+|\\d+)``, so it takes a bare integer, and that branch is where the
ANTHROPIC path lands (`analyzer.py` sets ``use_json_format = sdk_type !=
"anthropic"``). ``CONFIDENCE: 85``, ``8/10`` and ``7 out of 10`` are ordinary
phrasings of moderate conviction, and every one of them became the maximum --
the transformation is monotonically wrong in the FLATTERING direction.

WHAT IT COST. The figure carries 60% of the blended confidence on a stock
deploy (`llm_weight` 0.6, and the uncalibrated cap did not apply because it
lifted on `confidence_calibration_enabled`, then default True), and the blend is what the
0.85 auto-confirm threshold is eventually tested against. It is CACHED, so one
bad reply is re-served. It is written to `data/learning/llm_calibration.jsonl`
as `llm_confidence_raw`, which `bot/backtest/recorded_llm.py` replays into
backtests and `scripts/llm_ab.py` scores models with.

THE READING ALREADY EXISTED. `bot/risk/quality_ladder.quality_reading` refuses
a bool, a NaN, an infinity and an out-of-range confidence BY NAME -- the exact
three-valued reading of this exact quantity -- and the clamp handed it a
laundered 1.0 so it could never fire. The fix is not a new reading; it is the
parser asking the one that is there. `confidence_on_record` is the value-level
half, and `quality_reading` now answers from it, so a second copy cannot drift.

0.0 IS KEPT, and that is the load-bearing half. A model saying 0.0 has said
something -- it is the confidence of the prompt's own no-trade contract,
``{"direction": null, "confidence": 0.0, ...}`` -- so absent must not collapse
onto it. The old default did exactly that.
"""
from __future__ import annotations

import ast
import json

import pytest

from bot.core.analyzer import Analyzer
from bot.core.token_optimizer import SmartBatcher
from bot.risk import quality_ladder
from bot.risk.quality_ladder import confidence_on_record, quality_reading
from tests.source_scan import code_only

P = Analyzer._parse_llm_response

ANALYZER_SRC = (
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "bot" / "core" / "analyzer.py"
).read_text(encoding="utf-8")


def _json_body(conf_literal: str, direction: str = '"LONG"') -> str:
    return ('{"direction":%s,"confidence":%s,"reasoning":"r"}'
            % (direction, conf_literal))


def _fn(name: str) -> ast.FunctionDef:
    """The ONE definition of `name`, from the raw source.

    `ast.parse` is given the raw text rather than `code_only`'s output because
    that helper blanks docstrings, and a class or function whose body is only a
    docstring stops parsing once it is blanked.
    """
    found = [n for n in ast.walk(ast.parse(ANALYZER_SRC))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == name]
    assert len(found) == 1, f"expected one {name}, found {len(found)}"
    return found[0]


# ══════════════════════════════════════════════════════════════════════════
# THE READING
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value", [0.0, 1.0, 0.85, 0.5, 0, 1, "0.85", "0", "1"])
def test_a_confidence_inside_the_declared_range_is_read(value):
    """Every real value in [0, 1] survives, INCLUDING both endpoints.

    A reading that refused 0.0 would replace a measured no-conviction with an
    absence, and one that refused 1.0 would refuse a legitimate maximum. The
    numeric STRINGS are here because a spelling is not an interpretation:
    `"0.85"` is the same number written differently, where `85` is not.
    """
    assert confidence_on_record(value) == float(value)


@pytest.mark.parametrize("value,why", [
    (2.5, "above the declared range"),
    (100, "above the declared range"),
    (85, "a percent, against a prompt that asked for 0.0-1.0"),
    (1.0001, "just above the range"),
    (-5, "below the declared range"),
    (-0.0001, "just below the range"),
    (True, "a bool is not full conviction"),
    (False, "a bool is not no conviction either"),
    (None, "absent"),
    ("high", "not a number"),
    ([0.85], "not a number"),
    ({"v": 0.85}, "not a number"),
    (float("nan"), "NaN compares less than nothing, so min() returned 1.0"),
    (float("inf"), "infinity"),
    (float("-inf"), "negative infinity"),
])
def test_a_confidence_the_record_does_not_hold_is_refused(value, why):
    assert confidence_on_record(value) is None, why


def test_the_reading_refuses_rather_than_rescaling():
    """`85` is not read as `0.85`.

    Rescaling would be a guess about what the model MEANT -- the trap
    `csf.market_is_perp` records, where an inference from an adjacent fact
    would have answered confidently and wrongly for every input. The model was
    told the range at every site that asks; a value outside it is the model not
    answering the question.
    """
    assert confidence_on_record(85) is None
    assert confidence_on_record(0.85) == 0.85


def test_zero_is_a_reading_and_absent_is_not():
    """The distinction the whole slice exists to make."""
    assert confidence_on_record(0.0) == 0.0
    assert confidence_on_record(None) is None


# ══════════════════════════════════════════════════════════════════════════
# ONE READING, NOT TWO -- proved by PLANTING, because a byte-identical copy
# agrees with every fixture and diverges on the first edit to either.
# ══════════════════════════════════════════════════════════════════════════

class _Idea:
    def __init__(self, confidence, source=""):
        self.confidence = confidence
        self.source = source


def test_quality_reading_answers_from_the_shared_reading(monkeypatch):
    monkeypatch.setattr(quality_ladder, "confidence_on_record",
                        lambda v: 0.4242 if v == "planted" else None)
    r = quality_reading(_Idea("planted"))
    assert r.measured is True and r.confidence == 0.4242, (
        "quality_reading carries its own copy of the reading: a planted "
        "answer did not reach it")


def test_quality_reading_keeps_a_three_valued_reason():
    """The VERDICT is two-valued and the REASON is three.

    "unread" and "outside [0, 1]" send an operator to different places -- one
    is a model that said nothing, the other a model that answered in the wrong
    units -- so consolidating the verdict must not consolidate the reason.
    """
    assert quality_reading(_Idea(None)).why == "confidence unread"
    assert "outside [0, 1]" in quality_reading(_Idea(85)).why
    assert quality_reading(_Idea(0.0)).measured is True


def test_the_json_branch_asks_the_shared_reading(monkeypatch):
    """The parser must not carry its own copy either."""
    import bot.core.analyzer as analyzer_mod
    monkeypatch.setattr(analyzer_mod, "confidence_on_record",
                        lambda v: 0.1234 if v == 0.85 else None)
    assert P(_json_body("0.85"))["confidence"] == 0.1234, (
        "the JSON branch does not go through confidence_on_record")


def test_the_plain_text_branch_asks_the_shared_reading(monkeypatch):
    import bot.core.analyzer as analyzer_mod
    monkeypatch.setattr(analyzer_mod, "confidence_on_record",
                        lambda v: 0.1234 if v == "0.85" else None)
    r = P("DIRECTION: LONG\nCONFIDENCE: 0.85\nREASONING: r")
    assert r["confidence"] == 0.1234, (
        "the plain-text branch does not go through confidence_on_record")


# ══════════════════════════════════════════════════════════════════════════
# THE JSON BRANCH
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("literal,was", [
    ("2.5", 1.0), ("100", 1.0), ("85", 1.0), ("1e400", 1.0),
    ("true", 1.0), ("NaN", 1.0), ("Infinity", 1.0),
    ("-5", 0.0), ("false", 0.0), ("-Infinity", 0.0),
])
def test_an_unreadable_json_confidence_no_longer_parses(literal, was):
    """`was` is what the clamp answered -- driven, before the fix."""
    r = P(_json_body(literal))
    assert r["_parsed"] is False, (
        f'"confidence": {literal} used to parse as {was}')
    assert r.get("_parse_fail") == "confidence"


def test_an_absent_json_confidence_is_not_a_measured_zero():
    r = P('{"direction":"LONG","reasoning":"r"}')
    assert r["_parsed"] is False and r.get("_parse_fail") == "confidence"


def test_an_empty_json_body_does_not_report_as_parsed():
    """`P('{}')` returned every default with the flag flipped to True."""
    assert P("{}")["_parsed"] is False


@pytest.mark.parametrize("literal", ['0.0', '1.0', '0.85', '0', '1', '"0.85"'])
def test_a_readable_json_confidence_still_parses(literal):
    r = P(_json_body(literal))
    assert r["_parsed"] is True and r.get("_parse_fail") is None
    assert r["confidence"] == float(literal.strip('"'))


def test_the_no_trade_contract_is_untouched():
    """`{"direction": null, "confidence": 0.0, ...}` is the prompt's OWN
    no-trade answer, and THESIS_JSON_SCHEMA admits a null direction for
    exactly that reason. A fix that refused it would turn every no-setup
    reply into a parse failure."""
    r = P('{"direction": null, "confidence": 0.0, "reasoning": "no setup"}')
    assert r["_parsed"] is True
    assert r["direction"] is None and r["confidence"] == 0.0


# ══════════════════════════════════════════════════════════════════════════
# THE PLAIN-TEXT BRANCH -- the Anthropic path's parse
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("line,was", [
    ("CONFIDENCE: 85", 1.0),
    ("CONFIDENCE: 8/10", 1.0),
    ("CONFIDENCE: 7 out of 10", 1.0),
    ("CONFIDENCE: high", 0.0),
])
def test_an_unreadable_plain_text_confidence_no_longer_parses(line, was):
    r = P(f"DIRECTION: LONG\n{line}\nREASONING: r")
    assert r["_parsed"] is False, f"{line!r} used to parse as {was}"
    assert r.get("_parse_fail") == "confidence"


def test_a_plain_text_reply_with_no_confidence_line_does_not_parse():
    """DIRECTION + REASONING reached `parsed_fields >= 2` and was reported as
    parsed, carrying the 0.0 the result was initialised with."""
    r = P("DIRECTION: LONG\nREASONING: r")
    assert r["_parsed"] is False and r.get("_parse_fail") == "confidence"


@pytest.mark.parametrize("line,expected", [
    ("CONFIDENCE: 0.85", 0.85),
    # A percent is a SPELLING: the old clamp read these as 1.0 too, but
    # the answer is to read the unit, not to refuse a stated one.
    ("CONFIDENCE: 85%", 0.85),
    ("CONFIDENCE: 100%", 1.0),
    ("CONFIDENCE: 0", 0.0),
    ("CONFIDENCE: 1", 1.0),
    ("CONFIDENCE 0.7", 0.7),
])
def test_a_readable_plain_text_confidence_still_parses(line, expected):
    r = P(f"DIRECTION: LONG\n{line}\nREASONING: r")
    assert r["_parsed"] is True and r["confidence"] == expected


def test_a_plain_text_reply_with_no_direction_still_fails():
    """Unchanged: the plain-text branch has no no-trade contract."""
    assert P("CONFIDENCE: 0.85\nREASONING: r")["_parsed"] is False


# ══════════════════════════════════════════════════════════════════════════
# THE BATCH PARSER -- dark code, fixed before it is wired
# ══════════════════════════════════════════════════════════════════════════

def _batch(*items) -> str:
    return json.dumps(list(items))


def test_the_batcher_omits_a_symbol_whose_confidence_it_cannot_read():
    """Per-symbol fail-closed, which its docstring already promised."""
    out = SmartBatcher.parse_batch_response(
        _batch({"symbol": "BTC/USDT", "direction": "LONG", "confidence": 85,
                "reasoning": "r"},
               {"symbol": "ETH/USDT", "direction": "LONG", "confidence": 0.6,
                "reasoning": "r"}),
        ["BTC/USDT", "ETH/USDT"])
    assert "BTC/USDT" not in out
    assert out["ETH/USDT"]["confidence"] == 0.6


def test_the_batcher_does_not_invent_a_direction():
    """`item.get("direction", "LONG")` made an ABSENT direction a LONG, and
    the `else "LONG"` did the same for a word it cannot place."""
    out = SmartBatcher.parse_batch_response(
        _batch({"symbol": "BTC/USDT", "confidence": 0.6, "reasoning": "r"},
               {"symbol": "ETH/USDT", "direction": "SIDEWAYS",
                "confidence": 0.6, "reasoning": "r"}),
        ["BTC/USDT", "ETH/USDT"])
    assert out == {}, "a row naming no placeable direction became a LONG"


def test_the_batcher_still_reads_a_good_row():
    out = SmartBatcher.parse_batch_response(
        _batch({"symbol": "BTC/USDT", "direction": "SHORT", "confidence": 0.0,
                "reasoning": "r"}),
        ["BTC/USDT"])
    assert out["BTC/USDT"]["direction"] == "SHORT"
    assert out["BTC/USDT"]["confidence"] == 0.0, "a measured 0.0 is a reading"


# ══════════════════════════════════════════════════════════════════════════
# WIRING -- the drives above prove the behaviour; these say WHERE
# ══════════════════════════════════════════════════════════════════════════

def test_neither_parse_site_clamps_a_supplied_confidence():
    """Bounded to the function, and read with comments BLANKED.

    Both are load-bearing. `max(0.0, min(1.0, ...))` is legitimate elsewhere in
    this module -- it bounds values the code itself computed -- so a whole-file
    scan would accuse correct code. And the fix's own comment QUOTES the
    expression it removes, so a scan over raw text would match the explanation
    and pass over a restored defect: a comment that quotes the string it
    forbids, from the author's side.
    """
    body = code_only(ast.get_source_segment(ANALYZER_SRC, _fn("_parse_llm_response")))
    assert "min(1.0" not in body and "min(1.0," not in body, (
        "the clamp is back in _parse_llm_response")
    assert body.count("confidence_on_record(") == 2, (
        "both branches must ask the shared reading, once each")


@pytest.mark.parametrize("fn_name", ["_llm_thesis", "_try_llm_fallback"])
def test_both_audits_name_the_field_that_could_not_be_read(fn_name):
    """"Could not be parsed" over a reply whose direction and reasoning were
    fine sends an operator to look at the wrong thing.

    The VALUE is asserted, not the key. `'"parse_fail"' in body` is satisfied
    by the key alone, so `"parse_fail": ""` and `result.get("_parse_reason",
    "")` -- a key nothing writes -- both passed it while the audit stopped
    naming the field; the mutation round found exactly that. The fallback
    audit is additionally DRIVEN below; this is a scan and says so, because
    the primary one sits most of the way through a 500-line async method
    behind an exchange client, and standing that up to read one dict entry
    would be a fixture with more moving parts than the claim.
    """
    found = []
    for node in ast.walk(_fn(fn_name)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "audit"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        res = kw.get("result")
        if not (isinstance(res, ast.Constant) and res.value == "LLM_PARSE_FAIL"):
            continue
        data = kw.get("data")
        assert isinstance(data, ast.Dict), f"{fn_name}: audit data is not a literal dict"
        for k, v in zip(data.keys, data.values):
            if isinstance(k, ast.Constant) and k.value == "parse_fail":
                assert (isinstance(v, ast.Call)
                        and isinstance(v.func, ast.Attribute)
                        and v.func.attr == "get"
                        and isinstance(v.func.value, ast.Name)
                        and v.func.value.id == "result"
                        and v.args and isinstance(v.args[0], ast.Constant)
                        and v.args[0].value == "_parse_fail"), (
                    f"{fn_name}: parse_fail is not read from the parser's own "
                    f"_parse_fail key -- it is {ast.dump(v)[:120]}")
                found.append(fn_name)
                break
        else:
            raise AssertionError(
                f"{fn_name}'s LLM_PARSE_FAIL audit carries no parse_fail entry")
    assert found, f"{fn_name} makes no LLM_PARSE_FAIL audit at all"


# ══════════════════════════════════════════════════════════════════════════
# A PERCENT IS A SPELLING -- the in-house model's only format
#
# The first draft of this slice refused `Confidence: 72%`, and the adversarial
# round found what that costs: `ollama/Modelfile`'s SYSTEM prompt SPECIFIES
# that format, the in-house corpus carries 7,342 of them across 47 distinct
# spellings and ZERO instances of the JSON thesis key, and `LLMProvider.RUNECLAW`
# is keyless, zero-cost and `/setllm`-selectable. So refusing it is the
# 2026-07-21 "trades can not open" shape.
#
# The old clamp read every one of those 47 spellings as the SAME value, 1.0 --
# a fine-tuned model's whole confidence vocabulary flattened to the maximum at
# the parser -- so the answer is neither the clamp nor a blanket refusal. A
# percent sign STATES ITS OWN DENOMINATOR, which is the line this reading
# already draws: `"0.85"` reads because it is a spelling and a bare `85` does
# not because choosing a denominator for it would be a guess.
# ══════════════════════════════════════════════════════════════════════════

MODELFILE = (
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "ollama" / "Modelfile"
)


@pytest.mark.parametrize("spelled,expected", [
    ("72%", 0.72), ("68%", 0.68), ("65%", 0.65), ("60%", 0.60),
    ("100%", 1.0), ("0%", 0.0), ("62%", 0.62), ("68 %", 0.68),
])
def test_a_percent_reads_as_the_fraction_it_spells(spelled, expected):
    assert confidence_on_record(spelled) == pytest.approx(expected)


@pytest.mark.parametrize("spelled", ["150%", "-5%", "%", "abc%", "1e400%"])
def test_a_percent_outside_the_range_is_still_refused(spelled):
    assert confidence_on_record(spelled) is None


def test_the_format_the_modelfile_documents_is_one_the_parser_accepts():
    """A document that names a format is claiming that typing it works.

    Every `Confidence: NN%` the shipped SYSTEM prompt shows is fed back
    through the real parser, the way `trade_help`'s examples are. Eyeballing
    an example is how the old clamp survived however long it did.
    """
    examples = __import__("re").findall(
        r"Confidence:\s*(\d+)%", MODELFILE.read_text(encoding="utf-8"))
    assert examples, "the Modelfile no longer documents a percent confidence"
    for pct in examples:
        r = P(f"DIRECTION: LONG\nCONFIDENCE: {pct}%\nREASONING: r")
        assert r["_parsed"] is True, f"Confidence: {pct}% does not parse"
        assert r["confidence"] == pytest.approx(int(pct) / 100.0)


@pytest.mark.parametrize("word", ["HIGH", "MEDIUM", "LOW", "MODERATE"])
def test_a_word_valued_confidence_is_refused_rather_than_scored(word):
    """3,016 lines of the shipped corpus say a WORD, and the old code recorded
    every one as a measured 0.0. Mapping HIGH to a number would invent a scale
    nobody stated -- the `85 -> 0.85` guess at larger scale -- so it is
    refused, and the audit says which field."""
    r = P(f"DIRECTION: LONG\nCONFIDENCE: {word}\nREASONING: r")
    assert r["_parsed"] is False and r.get("_parse_fail") == "confidence"


# ══════════════════════════════════════════════════════════════════════════
# THE READING IS ASKED ABOUT THE WHOLE REMAINDER
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("line,was", [
    ("CONFIDENCE: 1 of 5 confluence signals aligned, 0.2", 1.0),
    ("CONFIDENCE: 0 of 3 filters passed", 0.0),
    ("CONFIDENCE: -0.85", 0.85),
    ("CONFIDENCE: 0.85 (high)", 0.85),
])
def test_the_regex_no_longer_chooses_the_token(line, was):
    """`re.search` for a digit-run is unanchored and sign-blind, so it PICKED
    the token and the reading only validated what it was handed -- a guard one
    layer too late. `was` is what the old pair produced."""
    r = P(f"DIRECTION: LONG\n{line}\nREASONING: r")
    assert r["_parsed"] is False, f"{line!r} still books {was}"


def test_a_readable_confidence_with_an_unplaceable_direction_blames_neither():
    """`DIRECTION: NEUTRAL` is the plain-text way of declining a setup. The
    confidence READ fine, so the audit must not say the confidence was what
    could not be read."""
    r = P("DIRECTION: NEUTRAL\nCONFIDENCE: 0.30\nREASONING: chop")
    assert r["_parsed"] is False
    assert r.get("_parse_fail") is None, (
        "the audit blames the confidence for a direction failure")
    r2 = P("CONFIDENCE: 0.85\nREASONING: r")
    assert r2["_parsed"] is False and r2.get("_parse_fail") is None


# ══════════════════════════════════════════════════════════════════════════
# REASONING IS A STRING OR IT IS ABSENT
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("literal", ["null", "123", '{"a":1}', "[1]", "true"])
def test_a_non_string_reasoning_is_an_absence_not_the_word_None(literal):
    """`str(None)` is the four-character string "None", which is TRUTHY, so it
    displaced the honest fallback sentence one caller up and reached a person
    as the reason the bot declined to trade."""
    r = P('{"direction":"LONG","confidence":0.7,"reasoning":%s}' % literal)
    assert r["_parsed"] is True
    assert r["reasoning"] == "", f"reasoning became {r['reasoning']!r}"


def test_the_batcher_does_not_invent_a_reasoning_either():
    out = SmartBatcher.parse_batch_response(
        _batch({"symbol": "BTC/USDT", "direction": "LONG", "confidence": 0.7,
                "reasoning": None}),
        ["BTC/USDT"])
    assert out["BTC/USDT"]["reasoning"] == ""


# ══════════════════════════════════════════════════════════════════════════
# THE BATCHER'S REMAINING REFUSALS
# ══════════════════════════════════════════════════════════════════════════

def test_the_batcher_refuses_a_row_with_no_confidence_key():
    """The gap the adversarial round found: no fixture omitted the confidence
    key on a row whose direction was good, so restoring
    `item.get("confidence", 0.0)` survived every assertion."""
    out = SmartBatcher.parse_batch_response(
        _batch({"symbol": "BTC/USDT", "direction": "LONG", "reasoning": "r"}),
        ["BTC/USDT"])
    assert out == {}, "an absent confidence became a measured 0.0"


@pytest.mark.parametrize("body", ['[1,2,3]', '["BTC/USDT"]', '[null]', '[[]]'])
def test_a_non_dict_batch_row_drops_rather_than_raising(body):
    """`item.get` on a str/int/list raises AttributeError, which is NOT in the
    caught tuple -- so one malformed element took the whole call down, in a
    function whose docstring promises fail-closed PER SYMBOL."""
    assert SmartBatcher.parse_batch_response(body, ["BTC/USDT"]) == {}


# ══════════════════════════════════════════════════════════════════════════
# THE FOURTH COPY -- the backtest replay's read-back
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw,was", [
    (85, 1.0), (2.5, 1.0), (True, 1.0), (float("nan"), 1.0), (-5, 0.0),
])
def test_the_replay_drops_a_row_it_cannot_read(raw, was):
    """`recorded_llm.py` carried the clamp byte-for-byte on the READ-BACK of
    the file the live path writes `llm_confidence_raw` into. The first draft
    of this slice called it "the backstop for rows written before the fix",
    which was a claim nobody drove: a clamp that answers 1.0 for an unreadable
    row is the defect, not the guard against it."""
    from bot.backtest.recorded_llm import RecordedLLM
    rec = RecordedLLM([{"symbol": "BTC/USDT", "llm_direction": "LONG",
                        "llm_confidence_raw": raw,
                        "ts": "2026-01-01T00:00:00+00:00"}])
    assert rec._by_symbol == {}, f"the replay still books {was}"


def test_the_replay_keeps_a_readable_row_and_a_junk_string_does_not_raise():
    from bot.backtest.recorded_llm import RecordedLLM
    rec = RecordedLLM([
        {"symbol": "BTC/USDT", "llm_direction": "LONG",
         "llm_confidence_raw": "high", "ts": "2026-01-01T00:00:00+00:00"},
        {"symbol": "BTC/USDT", "llm_direction": "LONG",
         "llm_confidence_raw": 0.72, "ts": "2026-01-02T00:00:00+00:00"},
    ])
    kept = [t["confidence"] for _ts, rows in rec._by_symbol.values() for t in rows]
    assert kept == [0.72], "the string 'high' used to raise out of from_jsonl"


# ══════════════════════════════════════════════════════════════════════════
# THE AUDIT IS DRIVEN, NOT SCANNED
#
# `assert '"parse_fail"' in body` is satisfied by the KEY alone, so
# `"parse_fail": ""` and `result.get("_parse_reason", "")` -- a key nothing
# writes -- both pass while the audit stops naming the failing field. The
# scan above says WHERE; this says the value arrives.
# ══════════════════════════════════════════════════════════════════════════

class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Msg(content)


class _Completions:
    def __init__(self, answer): self._a = answer

    async def create(self, **kw):
        class _R:
            choices = [_Choice(self._a)]
            usage = None
        _R.choices = [_Choice(self._a)]
        return _R()


class _Chat:
    def __init__(self, answer): self.completions = _Completions(answer)


class _FakeClient:
    def __init__(self, answer): self.chat = _Chat(answer)


def test_the_fallback_audit_carries_the_field_that_could_not_be_read(monkeypatch):
    import asyncio

    import bot.core.analyzer as am
    from bot.llm.provider import fallback_chain

    provider, key_env, _model = list(fallback_chain("analysis"))[0]
    monkeypatch.setenv(key_env, "k" * 20)
    monkeypatch.setattr(am, "create_llm_client", lambda cfg: _FakeClient("<junk>"))

    async def _complete(*a, **kw):
        return "<junk>"
    monkeypatch.setattr(am, "llm_complete", _complete)

    seen = []
    real_audit = am.audit
    monkeypatch.setattr(am, "audit", lambda *a, **kw: (seen.append(kw), None)[1])

    a = am.Analyzer.__new__(am.Analyzer)
    a._llm_calls_today = 0
    a._cost = None
    a._llm_config = None
    a._llm_last_chain_walk = []
    a._estimate_tokens = lambda t: len(t) // 4
    a._parse_llm_response = lambda raw: {"_parsed": False,
                                         "_parse_fail": "confidence"}

    asyncio.run(a._try_llm_fallback("p", None, False,
                                    failed_provider=None, is_admin=False))
    assert real_audit is not None
    fails = [kw for kw in seen if kw.get("result") == "LLM_PARSE_FAIL"]
    assert fails, f"no LLM_PARSE_FAIL audit was written; saw {[k.get('result') for k in seen]}"
    assert fails[0]["data"].get("parse_fail") == "confidence", (
        "the audit does not carry the field the parser could not read: "
        f"{fails[0]['data']}")
