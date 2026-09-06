"""A model that gave no reason was rendering as a model that reasoned.

`analyzer.analyze()` stamps a machine provenance tag onto every idea before
storing its reasoning::

    f"[{source}|{regime}|{mode}|{strategy}|C={confluence:.2f}{mtf}{sm}] "
    f"{thesis.get('reasoning', '')}"

and `_parse_llm_response` returns ``reasoning=""`` for two response shapes the
bot accepts as good: JSON carrying ``direction`` and ``confidence`` with no
``reasoning`` key, and plain text with DIRECTION and CONFIDENCE lines and no
REASONING line. Both set ``_parsed=True``; both clear the C-07 invalid-direction
guard. What is stored on those calls is::

    "[gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up] "

— truthy, non-empty, and containing no reasoning whatsoever.

SEVEN SURFACES PRINTED IT AS THE RATIONALE. The idea card's blockquote, the
verdict card's blockquote, ``/explain`` (a card whose entire subject is the
reason), the scan push to the website dashboard, the signal text fallback,
Guardian's ``Thesis:`` line inside a module whose docstring promises it invents
nothing, and the Reasoning row on the sealed public receipt — the page built
specifically so a reader would not have to take the reason on trust.

Three of those had a guard. All three were spelled ``if idea.reasoning:``, and
all three passed on every idea ever generated. This is the repository's oldest
rule wearing different clothes: absent is never a measurement. An unreadable
price must not print as ``0.00%``; a call with no stated reason must not print
a reason.

WHAT THIS FILE PINS, and in what order of value:

  1. the defect is REACHABLE — `_parse_llm_response` really does return a
     valid direction with empty reasoning, so this is a live path and not a
     hypothesis about one;
  2. the stripper's behaviour, including the cases where it must NOT strip;
  3. the SHAPE the stripper depends on is still the shape the analyzer emits —
     the two halves checked against each other rather than each against a
     literal;
  4. the call sites are reached, exercised where a card can be built and
     source-checked where it cannot.
"""

from __future__ import annotations

import pathlib
import re
import types

import pytest

from bot.formatters.thesis_text import provenance_tag, thesis_prose

ROOT = pathlib.Path(__file__).resolve().parent.parent

TAG = "[gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up]"


# ── 1. the defect is reachable, not hypothetical ─────────────────────────────

def test_the_bot_really_does_accept_a_thesis_with_no_reasoning():
    """THE REACHABILITY PROOF, and the reason this file exists at all.

    A stripper for a string nothing produces is a refactor. Drive the real
    parser with the two response shapes that produce it, and confirm each is
    accepted — valid direction, `_parsed` true — with an empty reasoning.
    """
    from bot.core.analyzer import Analyzer

    js = Analyzer._parse_llm_response('{"direction": "LONG", "confidence": 0.7}')
    assert js["direction"] == "LONG" and js["_parsed"] is True, (
        "the parser no longer accepts JSON without a reasoning key — if that is "
        "deliberate, this whole class of empty thesis is gone and the guards "
        "below are belt-and-braces rather than live")
    assert js["reasoning"] == ""

    txt = Analyzer._parse_llm_response("DIRECTION: LONG\nCONFIDENCE: 0.7")
    assert txt["direction"] == "LONG" and txt["_parsed"] is True
    assert txt["reasoning"] == ""


def test_the_stored_string_for_that_call_is_truthy_and_says_nothing():
    """Which is why every `if idea.reasoning:` guard passed."""
    stored = f"{TAG} " + ""      # exactly what analyze() concatenates
    assert bool(stored) is True, "the premise of the whole defect"
    assert thesis_prose(stored) is None


# ── 2. the stripper ──────────────────────────────────────────────────────────

def test_tag_only_is_none_not_empty_string():
    """`None`, not `""`. A caller must be able to tell "no reason recorded"
    apart from a reason that renders empty, and here the two collapse only by
    accident of the return value. Test `is None`."""
    assert thesis_prose(f"{TAG} ") is None
    assert thesis_prose(TAG) is None


def test_the_prose_survives_and_the_tag_goes():
    got = thesis_prose(f"{TAG} 4H RSI at 61 with MACD crossing up.")
    assert got == "4H RSI at 61 with MACD crossing up."


def test_reasoning_with_no_tag_is_returned_untouched():
    """Manual, scan and SDK ideas set a plain `reasoning=` with no tag, and the
    rule-based thesis is a measurement dump. None of them are defects and none
    of them may be altered on the way to a card."""
    for plain in ("Manual trade placed by user",
                  "GetClaw SDK signal",
                  "Regime=TREND_UP, RSI=61.2, MACD_hist=0.0031, confluence=0.68"):
        assert thesis_prose(plain) == plain


def test_a_bracketed_opening_without_a_pipe_is_left_alone():
    """The strip is keyed on the tag's SHAPE — a pipe inside the brackets — so
    a model that opens its reasoning with an aside keeps every word of it. A
    looser regex would silently eat the first sentence of a real thesis, which
    is a worse failure than the one being fixed."""
    s = "[worth noting] the daily trend is still up, so this is a fade."
    assert thesis_prose(s) == s


def test_none_and_whitespace_are_none():
    assert thesis_prose(None) is None
    assert thesis_prose("") is None
    assert thesis_prose("   ") is None
    assert thesis_prose(f"{TAG}    \n  ") is None


def test_only_the_leading_tag_is_stripped():
    """A tag-shaped bracket later in the prose is the model's text, not ours."""
    got = thesis_prose(f"{TAG} see [a|b] below")
    assert got == "see [a|b] below"


def test_provenance_is_kept_not_discarded():
    """The tag is TRUE. It is only the label that lied, so it stays available
    for surfaces that want to show it under an honest one."""
    assert provenance_tag(f"{TAG} anything") == "gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up"
    assert provenance_tag("Manual trade placed by user") is None
    assert provenance_tag(None) is None


# ── 3. the two halves, checked against each other ────────────────────────────

def test_the_stripper_matches_the_tag_the_analyzer_actually_builds():
    """Not a literal-vs-literal check. Lift the f-string analyze() concatenates
    out of the source, fill it with plausible values, and require the stripper
    to reduce it to nothing. Adding a segment to the tag — the one change that
    would silently defeat the strip — fails here.
    """
    src = (ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8")
    m = re.search(r'reasoning=\(\s*\n\s*f"(\[[^"]*)"\s*\n\s*f"([^"]*)"', src)
    assert m, ("analyze() no longer builds `reasoning=` the way this test reads "
               "it — re-derive the tag shape before trusting the strip")
    template = m.group(1) + m.group(2)
    assert template.startswith("["), "the provenance tag is no longer a leading bracket"
    assert "|" in template, (
        "the tag lost its pipe separators, which is the shape thesis_text.py "
        "keys on — a model's own bracketed aside is now indistinguishable "
        "from our stamp")

    # Split the template where the tag closes, fill its {placeholders}, and
    # check both halves: the tag alone must reduce to nothing, and the tag with
    # the model's slot filled must reduce to exactly the model's words. The
    # second half is what stops a fix that simply hides every rationale.
    head, sep, _rest = template.partition("] ")
    assert sep, "the provenance tag no longer closes with '] '"
    tag = re.sub(r"\{[^{}]*\}", "x", head + sep)
    assert thesis_prose(tag) is None, (
        f"the tag as analyze() now builds it survives the strip: {tag!r}")
    assert thesis_prose(tag + "the model said this") == "the model said this"


# ── 4. the call sites are reached ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explain_says_no_rationale_rather_than_quoting_the_tag():
    """The whole card, built for real. `/explain` is the one surface where
    silence would be its own defect — a card headed EXPLANATION that explains
    nothing and does not say so is worse than one that admits it."""
    from bot.skills.skill_registry import ExplainTradeSkill

    idea = types.SimpleNamespace(
        id="TI-deadbeef", asset="XLM/USDT",
        direction=types.SimpleNamespace(value="LONG"),
        confidence=0.71, signals_used=["rsi", "macd"],
        reasoning=f"{TAG} ",
    )
    out = await ExplainTradeSkill().execute(
        types.SimpleNamespace(pending_ideas=[idea]), trade_id="TI-deadbeef")

    assert "<blockquote>" not in out, (
        "the EXPLANATION card is quoting something as the rationale, and there "
        "is no rationale to quote")
    assert "No written rationale was recorded" in out, (
        "the card went silent instead of saying the reason is missing")
    # Provenance is disclosure, not explanation. It stays — under its own
    # label, and ONLY there: the tag appearing anywhere else on this card is
    # the defect wearing a different hat.
    assert "- Provenance: <code>gpt-4o|TREND_UP" in out
    assert out.count("gpt-4o") == 1


@pytest.mark.asyncio
async def test_explain_still_quotes_a_real_rationale():
    """THE CONTROL. A fix that hides every rationale would pass every
    assertion above."""
    from bot.skills.skill_registry import ExplainTradeSkill

    idea = types.SimpleNamespace(
        id="TI-cafe", asset="XLM/USDT",
        direction=types.SimpleNamespace(value="LONG"),
        confidence=0.71, signals_used=["rsi"],
        reasoning=f"{TAG} Momentum turned on the 4H and volume confirmed.",
    )
    out = await ExplainTradeSkill().execute(
        types.SimpleNamespace(pending_ideas=[idea]), trade_id="TI-cafe")
    assert "Momentum turned on the 4H and volume confirmed." in out
    assert "<blockquote>" in out
    assert "No written rationale" not in out


def test_the_shared_blockquote_helper_omits_rather_than_quotes():
    from bot.skills.skill_registry import _thesis_bq

    assert _thesis_bq(f"{TAG} ", 250, tail="\n\n") == ""
    assert _thesis_bq(f"{TAG} because momentum", 250) == (
        "<blockquote>because momentum</blockquote>")
    # the tail rides along only when there is something to attach it to
    assert _thesis_bq(f"{TAG} x", 250, tail="\n\n").endswith("\n\n")


def test_guardian_omits_the_thesis_line_it_cannot_support():
    """`explain_fill`'s docstring promises it narrates the record "inventing
    nothing". A Thesis: line built from a provenance tag is an invention."""
    from bot.guardian.explain_fill import explain

    def _narrative(reasoning):
        out = explain({
            "symbol": "XLM/USDT", "outcome": "taken", "is_paper": True,
            "idea": {"direction": "LONG", "confidence": 0.71, "rr": 2.0,
                     "entry": 0.31, "sl": 0.30, "tp": 0.34,
                     "reasoning": reasoning},
        })
        return " ".join(out.get("why") or [])

    assert "Thesis:" not in _narrative(f"{TAG} ")
    assert "gpt-4o" not in _narrative(f"{TAG} ")
    assert "Thesis: because momentum" in _narrative(f"{TAG} because momentum")


def test_every_display_site_goes_through_the_seam():
    """THE WIRING, WHICH EVERY TEST ABOVE MISSES for the sites that need a live
    engine to render. Two of the six call sites sit inside 190-line async
    methods that a unit test cannot drive, and a correct stripper reached by
    nothing is this repository's signature failure. Checked from the CALLER,
    which is the only place it is visible.
    """
    from tests.source_scan import code_only

    for rel, needles in (
        ("bot/skills/skill_registry.py",
         ("_thesis_bq(idea.reasoning, 250", "_thesis_bq(idea.reasoning, 200",
          "provenance_tag(idea.reasoning)", "thesis_prose(idea.reasoning) or \"\"")),
        # /latest_signal's card: in the trading mixin since the handler split.
        ("bot/skills/trading_commands.py", ("thesis_prose(idea.reasoning)",)),
        ("bot/guardian/explain_fill.py", ('thesis_prose(idea.get("reasoning"))',)),
    ):
        src = code_only((ROOT / rel).read_text(encoding="utf-8"))
        for needle in needles:
            assert needle in src, f"{rel} no longer routes its reasoning through the seam ({needle})"
        # and the raw field must not go straight to a renderer again
        assert "_esc(idea.reasoning" not in src, (
            f"{rel} escapes idea.reasoning directly into a card again")
        assert "html.escape(idea.reasoning" not in src, (
            f"{rel} escapes idea.reasoning directly into a card again")
