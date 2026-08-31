"""A reply may not claim a tool ran. Nothing ran.

Asked "Doji BTC" on 2026-08-31, v12 wrote its own `[analyze_asset] result:`
block and a `[PENDING] scanning...`. It had copied the format
`bot/nlp/skill_memory.py` uses to record REAL tool output into the history —
the memory-grounding fix, working exactly as designed, being imitated.

The failure mode is the one this repository keeps naming from the other
direction. Elsewhere an absent reading gets rendered as a measurement; here an
absent EXECUTION gets rendered as one, in the same prefix and layout a real
one would use. And it is a regression in honesty terms even though it looks
like an improvement: the empty reply it replaced was a failure the bot could
see and report.

TWO LAYERS, TESTED SEPARATELY, because they fail differently. The prompt asks
the model not to do it and lowers the rate; `_chat_ret` refuses the ones that
do it anyway. This exact class of defect already survived a training
generation aimed squarely at it (`bot/nlp/rr_honesty.py` records that), which
is the argument for not stopping at the prompt.
"""

from __future__ import annotations

import types

import pytest

from bot.nlp.fabricated_tool_calls import (
    REFUSAL,
    find_fabricated_marker,
    strip_fabricated_tool_results,
)

# ── the detector ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    "[analyze_asset] result:\nRSI 48",
    "[get_portfolio] result:\nequity 1000",
    "[PENDING] scanning...",
    "  [scan_symbol] result:\nnothing",
    "[macro_calendar] NO OUTPUT — the tool returned nothing.",
    "[check_risk] FAILED — the tool raised an error",
    "[learning] UNAVAILABLE",
    "Sure.\n\n[analyze_asset] result:\nRSI 48",
])
def test_every_recorded_marker_shape_is_caught(reply):
    """The whole vocabulary skill_memory writes, plus the invented [PENDING].

    Parametrised rather than one assertion per shape because the shapes are
    the specification: a new record type in skill_memory that this list does
    not carry is a hole, and the list is where you notice.
    """
    assert find_fabricated_marker(reply) is not None


@pytest.mark.parametrize("reply", [
    "BTC is consolidating. No trade here.",
    "The doji closed just under resistance.",
    "You asked what [analyze_asset] result: blocks are — they're tool output.",
    "Run a scan and I'll read the result.",
    "Nothing pending right now.",
    "",
])
def test_ordinary_replies_are_left_alone(reply):
    """Including a user's phrase quoted back MID-SENTENCE.

    The marker is anchored to the start of a line because that is where
    skill_memory writes it. Without the anchor, explaining the feature to
    someone would trip the guard that exists to catch faking it — and a guard
    that fires on a true statement gets turned off.
    """
    assert find_fabricated_marker(reply) is None
    assert strip_fabricated_tool_results(reply) == (reply, 0)


# ── what it does about it ─────────────────────────────────────────────────

def test_the_grounded_prefix_survives_and_the_claim_does_not():
    out, n = strip_fabricated_tool_results(
        "BTC looks range-bound near resistance.\n\n"
        "[analyze_asset] result:\nRSI 48, doji at 101k, entry 101200")
    assert n == 1
    assert out == "BTC looks range-bound near resistance."


def test_the_invented_body_goes_too_not_just_its_label():
    """Excising only the marker line would be the worse outcome.

    It leaves the fabricated numbers behind as ordinary prose — the invention
    stripped of the one label that made it recognisable as a tool transcript.
    """
    out, _ = strip_fabricated_tool_results(
        "Here you go.\n[analyze_asset] result:\nentry 101200 / SL 99800")
    assert "101200" not in out
    assert "99800" not in out
    assert out == "Here you go."


def test_a_reply_that_is_nothing_but_the_claim_becomes_a_refusal():
    """Not an empty string. An empty reply is its own defect on this surface."""
    out, n = strip_fabricated_tool_results("[PENDING] scanning...")
    assert n == 1
    assert out == REFUSAL
    assert out.strip()


def test_the_refusal_says_nothing_ran():
    """The point of the whole change, asserted positively.

    A refusal that merely declines is still an absence the reader fills in.
    This one has to state the fact: no tool executed.
    """
    assert "nothing ran" in REFUSAL.lower()


# ── the seam: both surfaces return through _chat_ret ───────────────────────

def _chat_ret():
    from bot.skills.telegram_handler import _chat_ret as f
    return f


def test_the_seam_cleans_a_fabricated_reply():
    """Drives the real function, not the module it calls.

    A unit-tested helper that no return path reaches is #999. `_chat_ret` is
    what every caller on both the Telegram and web surfaces funnels through,
    so this is the assertion that the guard is REACHED.
    """
    assert _chat_ret()("Sure.\n[analyze_asset] result:\nRSI 48", None, False) == "Sure."


def test_the_seam_leaves_an_honest_reply_untouched():
    text = "BTC is chopping around 101k. I'd wait."
    assert _chat_ret()(text, None, False) == text


def test_the_seam_still_returns_meta_when_asked():
    """The guard must not change the return SHAPE the web gateway depends on."""
    cfg = types.SimpleNamespace(
        provider=types.SimpleNamespace(value="ollama"), model="v12")
    out, meta = _chat_ret()("[PENDING] scanning...", cfg, True)
    assert out == REFUSAL
    assert meta == {"provider": "ollama", "model": "v12"}


_FABRICATED_WITH_A_RATIO = (
    "Here's the setup.\n"
    "[analyze_asset] result:\n"
    "Direction: LONG Entry: 100 Stop Loss: 90 Take Profit: 111.7 "
    "Risk:Reward: 2.50"
)


def test_a_fabricated_block_carrying_a_ratio_is_dropped_whole():
    out = _chat_ret()(_FABRICATED_WITH_A_RATIO, None, False)
    assert out == "Here's the setup."
    assert "2.5" not in out
    assert "1.1" not in out, "a corrected ratio would mean the block survived"


def test_no_risk_reward_correction_is_LOGGED_for_a_discarded_block(monkeypatch):
    """The one thing the two checks' ORDER actually changes.

    The first version of this test asserted on the returned text and claimed
    to pin the ordering. It did not: a mutation swapping the two blocks passed
    all 22 tests, because the truncation removes whatever the correction did
    and the reply is identical either way.

    The real difference is the RECORD. Run the risk:reward correction first
    and it fires an `rr_corrected` audit event for a ratio inside a block
    about to be thrown away — the bot logging that it corrected a number
    nobody was ever shown. On the surface built to be audited, that is a false
    account of what happened, and it is the only observable the ordering
    moves. So this asserts on the audit stream, which is where the claim lives.
    """
    from bot.skills import telegram_handler as th

    events = []
    monkeypatch.setattr(
        th, "audit",
        lambda _log, msg, **kw: events.append((kw.get("action"), msg)))

    out = th._chat_ret(_FABRICATED_WITH_A_RATIO, None, False)

    assert out == "Here's the setup."
    actions = [a for a, _ in events]
    assert "fabricated_tool_result" in actions, "the refusal must be recorded"
    assert "rr_corrected" not in actions, (
        "a risk:reward correction was logged for a block that was discarded"
    )
