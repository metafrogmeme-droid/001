"""The memory said "executed successfully" and the model filled in the rest.

Both chat surfaces recorded the same assistant turn no matter what a skill
returned:

    self.conversations.append(tg_id, "assistant",
                              f"[{intent.skill}] executed successfully", ...)

The Telegram call site's own comment read ``# Store skill result as assistant
message (truncated)``. It stored no result. The comment described the intent,
the code stored a placeholder, and nothing between them ever disagreed out loud.

That string is the assistant's turn in the history handed to the chat model.
Ask a follow-up and the model is shown a question, the words "executed
successfully", and nothing else — told an answer exists and not what it was.
That is the prompt shape most reliably completed with invention, and it is
where the UNIVERSE/USDT reply and the four-RSI-values-for-one-pair reply came
from: not an unreliable model, a model handed a gap exactly where the evidence
belonged.

A skill that finds nothing says so. A skill that fails says so. Recording
"executed successfully" over both is absent-rendered-as-a-measurement moved
into the memory layer, where it appears on no screen and surfaces several turns
later as fiction.

AND THE FAILURE PATH RECORDED NOTHING AT ALL. Both surfaces caught the
exception, apologised to the user and returned, leaving the history with a
question and no answer — the same gap, dug a different way.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.nlp.skill_memory import (MEMORY_CAP, skill_failure_memory,
                                  skill_result_memory)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── three outcomes, three records ───────────────────────────────────────────

def test_a_real_result_is_what_gets_recorded():
    """The whole point. What the tool said is what the model reads."""
    card = "SUI SHORT confidence 48% TP 0.625 SL 0.67725"
    assert card in skill_result_memory("scan_market", card)


def test_no_output_is_recorded_as_no_output():
    for empty in (None, "", "   ", "\n\n"):
        got = skill_result_memory("scan_market", empty)
        assert "NO OUTPUT" in got, f"{empty!r} -> {got!r}"
        assert "success" not in got.lower(), (
            "a skill that returned nothing is still being described as having "
            "succeeded — the exact string this file exists to remove")


def test_a_failure_is_recorded_rather_than_left_blank():
    got = skill_failure_memory("scan_market")
    assert "FAILED" in got and "Nothing was measured" in got
    assert "success" not in got.lower()


def test_every_record_is_non_empty_so_the_store_cannot_drop_it():
    """ConversationStore.append() returns early on falsy content. A record that
    rendered as "" would be silently discarded and restore the very gap being
    fixed — the failure would be invisible AND unrecorded."""
    for value in (None, "", "   ", "ok", "x" * 5000):
        assert skill_result_memory("s", value).strip()
    assert skill_failure_memory("s").strip()


# ── truncation is announced, never silent ───────────────────────────────────

def test_a_long_result_says_it_was_cut_and_by_how_much():
    """A scan card cut at a fixed length and presented whole is a partial
    printed as a total: the model reads seven of twelve rows as the complete
    set and describes twelve."""
    body = "\n".join(f"PAIR{i} SHORT rsi {i}" for i in range(400))
    got = skill_result_memory("scan_market", body)
    assert "TRUNCATED" in got
    assert str(len(body)) in got, "the record does not say how much was omitted"
    assert str(MEMORY_CAP) in got, "the record does not say how much is present"


def test_a_result_that_fits_is_not_marked_truncated():
    got = skill_result_memory("scan_market", "short and complete")
    assert "TRUNCATED" not in got


def test_the_cap_stays_inside_the_stores_own_persistence_cap():
    """`ConversationStore` writes `content[:2000]` to disk, so a record longer
    than that is shortened AGAIN, silently, and a restart quietly changes what
    the model remembers. Pinned as a relation against the store's own constant
    rather than as two numbers that can drift apart."""
    src = (ROOT / "bot" / "nlp" / "conversation_store.py").read_text(encoding="utf-8")
    assert "msg.content[:2000]" in src, (
        "the store's persistence cap moved — re-derive this bound")
    longest = skill_result_memory("a_rather_long_skill_name", "x" * (MEMORY_CAP * 2))
    assert len(longest) <= 2000, (
        f"the longest record this module can emit is {len(longest)} chars and "
        "the store truncates at 2000 — it would be cut a second time, silently")


# ── the text the model reads is the text the tool emitted ───────────────────

def test_markup_becomes_a_space_not_nothing():
    """`<b>LONG</b>XLM` collapsing to `LONGXLM` invents a token the tool never
    emitted, and a model reading it will repeat the invention."""
    got = skill_result_memory("analyze_asset", "<b>LONG</b>XLM")
    assert "LONG XLM" in got and "LONGXLM" not in got


def test_entities_are_unescaped():
    got = skill_result_memory("analyze_asset", "RSI 61 &amp; MACD up &lt;fast&gt;")
    assert "RSI 61 & MACD up <fast>" in got


def test_line_structure_survives():
    """A scan card's newlines carry one row per symbol. Flattening them is how
    four RSI values end up attached to one pair."""
    got = skill_result_memory("scan_market", "SUI rsi 48\nFIL rsi 49\nATOM rsi 53")
    assert "SUI rsi 48\nFIL rsi 49\nATOM rsi 53" in got


def test_the_failure_record_carries_no_driver_detail():
    """Memory feeds the model and the model writes to a user. An exception's
    text can carry a URL, a host or a config value, which is why /readyz answers
    with a coarse reason from a fixed vocabulary."""
    got = skill_failure_memory("scan_market")
    for leak in ("Traceback", "File \"", "http", "Error(", "secret", "token"):
        assert leak.lower() not in got.lower(), f"{leak!r} reached the record"


# ── the wiring, which none of the above can see ─────────────────────────────

@pytest.mark.parametrize("rel", ["bot/skills/telegram_handler.py",
                                 "bot/web/user_gateway.py"])
def test_neither_surface_still_claims_success(rel):
    """THE ORIGINAL STRING, on both surfaces. Every test above exercises the
    module and none of them prove anything calls it — and a correct recorder
    reached by nothing is this repository's signature failure."""
    from tests.source_scan import code_only

    src = code_only((ROOT / rel).read_text(encoding="utf-8"))
    assert "executed successfully" not in src, (
        f"{rel} still records a placeholder instead of the tool's output")
    assert "skill_result_memory(" in src, f"{rel} does not record the result"
    assert "skill_failure_memory(" in src, (
        f"{rel} still returns its apology without recording that the tool failed")


@pytest.mark.parametrize("rel", ["bot/skills/telegram_handler.py",
                                 "bot/web/user_gateway.py"])
def test_the_failure_record_is_inside_the_except(rel):
    """Placement, not presence. `skill_failure_memory` called anywhere in the
    file satisfies the test above; it has to be reached when the skill raises,
    which is a property of where it sits."""
    from tests.source_scan import code_only

    src = code_only((ROOT / rel).read_text(encoding="utf-8"))
    i = src.index("skill_failure_memory(")
    before = src[max(0, i - 600):i]
    assert "except" in before, (
        f"{rel} calls skill_failure_memory outside any except block — it would "
        "not run on the failure it exists to record")
