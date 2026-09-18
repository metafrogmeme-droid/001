"""The nightly card must not endorse a configuration it has just accused.

`costliest_gate_line` reports a gate the shadow book has ESTABLISHED as eating
edge — a gate whose whole 95% per-trade interval clears zero, the strongest
statement that record can make that a risk gate is costing money. Directly
beneath it, `no_change_verdict` printed:

    No changes proposed — the evidence supports the current configuration.
    (An empty audit is a pass, not a failure.)

Two claims, one card, opposite directions, and the reassuring one had read
less: the verdict consulted `window_reading` (the live P&L window) and never
the shadow evidence sitting beside it.

IT IS THE ORDINARY CASE, NOT A CORNER. Driven, `RiskEngine` can charge a
refusal to 32 distinct gate names and `self_audit.ALLOWED_FLAGS` holds 12
knobs, so for most gates the model has nothing it is permitted to propose.
"Proposed nothing" is then a fact about the allow-list, not about the gate —
which is exactly the argument the `losing` branch of this same function
already makes for its own case, one line up.

NO KNOB-BY-KNOB CLAIM IS MADE, and that is deliberate. There is no
gate -> flag map in the module; inventing one to say "no knob reaches this
gate" would be the ten-of-eleven shape (the row added tomorrow is the one
missing from the hand-written map). The sentence says only what is true and
sufficient: the audit reaches N allowlisted flags, so finding nothing to turn
among them is not a finding about that gate.
"""
import inspect
from unittest.mock import patch

import pytest

from bot.core import self_audit
from bot.core.self_audit import (
    ALLOWED_FLAGS,
    SelfAudit,
    costliest_gate_line,
    established_gate,
    no_change_verdict,
)

# A gate the record HAS established: `eating_edge` with a bound behind it.
ESTABLISHED = {
    "TAKER_3BAR": {"n": 84, "net_r": 31.2, "avg_r": 0.371, "wins": 52,
                   "losses": 32, "verdict": "eating_edge",
                   "lower_r": 0.18, "upper_r": 0.56,
                   "sole_n": 61, "sole_net_r": 22.4, "sole_avg_r": 0.367},
}
# The shape the LIVE book actually holds today: one gate, net_r NEGATIVE (it
# saved money), no verdict. Re-driven rather than remembered — an earlier
# session carried a TAKER_3BAR figure forward through a check-in prompt and it
# does not reproduce against `data/shadow_book.json`.
CLEAN = {
    "CONFIDENCE": {"n": 120, "net_r": -2.193, "verdict": None,
                   "sole_n": 8, "lower_r": -1.2839},
}
SOUND_WINDOW = {"n": 40, "scored": 40, "pf": 1.8, "net_pnl": 412.0}

ENDORSEMENT = "supports the current configuration"


def _card(gates, results=None):
    """The whole card, as an operator reads it."""
    return SelfAudit.render_report(
        {"summary": SOUND_WINDOW, "shadow_gates": gates},
        results or [], {}, "dataset", parsed=[])


class TestTheCardDoesNotContradictItself:
    def test_an_established_gate_withdraws_the_endorsement(self):
        card = _card(ESTABLISHED)
        assert "TAKER_3BAR" in card, "the gate line must still name the gate"
        assert ENDORSEMENT not in card, (
            "the card names a gate its own shadow book has established as "
            "eating edge, and then endorses the configuration:\n" + card)
        assert "not an endorsement" in card

    def test_the_verdict_names_the_gate_the_line_above_named(self):
        # Not merely "some caveat appeared": the reader has to be told WHICH
        # gate makes the endorsement unsayable, or the sentence is a hedge.
        verdict = no_change_verdict(SOUND_WINDOW, [], ESTABLISHED)
        assert "TAKER_3BAR" in verdict
        assert "+0.18R/trade" in verdict, "quote the bound the verdict turns on"

    def test_the_allowlist_size_is_derived_not_written(self):
        # A number in prose is the part that rots first. If a knob is added to
        # ALLOWED_FLAGS tomorrow the sentence must move with it.
        #
        # ASSERTING THE CURRENT NUMBER CANNOT SAY THAT. `len(ALLOWED_FLAGS)` is
        # 12 today, so `"12 allowlisted flags"` matches a hard-coded 12 exactly
        # as well as a derived one — an assertion that passes under the
        # mutation it names. The table is patched so the count has to move.
        verdict = no_change_verdict(SOUND_WINDOW, [], ESTABLISHED)
        assert f"{len(ALLOWED_FLAGS)} allowlisted flags" in verdict

        wider = dict(ALLOWED_FLAGS)
        wider["A_KNOB_ADDED_TOMORROW"] = {"type": "bool"}
        with patch.object(self_audit, "ALLOWED_FLAGS", wider):
            moved = no_change_verdict(SOUND_WINDOW, [], ESTABLISHED)
        assert f"{len(wider)} allowlisted flags" in moved
        assert len(wider) != len(ALLOWED_FLAGS)

    def test_it_makes_no_knob_by_knob_claim(self):
        # There is no gate -> flag map in the module, so the sentence must not
        # assert one. Inventing it is the ten-of-eleven shape.
        verdict = no_change_verdict(SOUND_WINDOW, [], ESTABLISHED)
        for overclaim in ("no knob", "cannot be tuned", "no flag reaches"):
            assert overclaim not in verdict.lower(), overclaim


class TestTheOrdinaryCardIsUnchanged:
    def test_nothing_established_still_endorses(self):
        # A caveat that fires when nothing is wrong is how operators learn to
        # skip the next one. This is the shape the LIVE book has today.
        assert ENDORSEMENT in no_change_verdict(SOUND_WINDOW, [], CLEAN)

    def test_a_caller_that_gathered_no_gates_is_unchanged(self):
        # An older build, or a render that does not carry the evidence. Not a
        # failed read, and not a reason to withdraw an endorsement whose basis
        # (the live window) is unchanged.
        assert ENDORSEMENT in no_change_verdict(SOUND_WINDOW, [])

    def test_an_empty_ledger_endorses(self):
        assert ENDORSEMENT in no_change_verdict(SOUND_WINDOW, [], {})


class TestUnreadableIsNotClean:
    def test_a_scoreboard_that_could_not_be_read_withholds_the_endorsement(self):
        verdict = no_change_verdict(SOUND_WINDOW, [], None)
        assert ENDORSEMENT not in verdict
        assert "could not be read" in verdict

    def test_unreadable_gathered_and_clean_are_three_different_sentences(self):
        unreadable = no_change_verdict(SOUND_WINDOW, [], None)
        not_gathered = no_change_verdict(SOUND_WINDOW, [])
        clean = no_change_verdict(SOUND_WINDOW, [], CLEAN)
        assert unreadable != not_gathered
        # `clean` and `not_gathered` deliberately agree: both endorse on the
        # window, and neither has a shadow finding to withhold it. Stated so a
        # later reader does not "fix" it into a third sentence.
        assert clean == not_gathered


class TestOneReadingTwoReaders:
    def test_both_readers_answer_what_the_reading_said(self):
        # A byte-identical second copy agrees with every fixture and diverges
        # on the first edit to either, so equality proves nothing. Patch the
        # reading and see whether each reader really consults it.
        #
        # The planted row is NOT established by the real rule (its verdict is
        # `undistinguished`), so only the patch can make either reader treat
        # it as established.
        row = {"n": 9, "net_r": 4.0, "verdict": "undistinguished",
               "lower_r": -0.1, "upper_r": 0.9, "sole_n": 5,
               "sole_net_r": 3.0, "sole_avg_r": 0.6}
        gates = {"PLANTED": row}
        assert established_gate(gates) is None, "fixture must not self-establish"

        with patch.object(self_audit, "established_gate",
                          return_value=("PLANTED", row)):
            line = costliest_gate_line(gates)
            verdict = no_change_verdict(SOUND_WINDOW, [], gates)

        # "The line mentions the gate" is NOT the assertion. With an inline
        # second copy of the reading the patch reaches only the verdict, the
        # planted row falls through to the `undistinguished` branch — which
        # names the gate too — and the mutation survives a green suite. It
        # did, on the first round. The claim is that the line came from the
        # ESTABLISHED branch, which only the patched reading can select.
        assert line is not None, "the line must be built"
        assert "is the costliest gate" in line and "95% lower bound" in line, (
            "costliest_gate_line must answer from `established_gate`, not from "
            "its own inline copy of the rule:\n  " + line)
        assert "not established" not in line
        assert "PLANTED" in verdict and ENDORSEMENT not in verdict

    def test_the_reading_requires_a_bound_not_just_the_word(self):
        # A row asserting `eating_edge` with no bound behind it is
        # contradictory input; every caller quotes the bound.
        assert established_gate({"X": {"verdict": "eating_edge"}}) is None
        assert established_gate(
            {"X": {"verdict": "eating_edge", "lower_r": 0.2}}) is not None

    def test_a_junk_ledger_is_not_a_finding(self):
        assert established_gate(None) is None
        assert established_gate({}) is None
        assert established_gate("not a dict") is None


class TestTheBoundIsQuotedOnlyWhenItReads:
    def test_a_non_numeric_bound_still_withdraws_the_endorsement(self):
        # `established_gate` requires `lower_r is not None`; a non-None value
        # is still not necessarily a number (a JSON round-trip, or a cache
        # written by an older build). The gate is named either way; the FIGURE
        # is omitted rather than interpolated as junk.
        gates = {"ODD": {"verdict": "eating_edge", "lower_r": "n/a",
                         "sole_n": 4, "sole_net_r": 1.0}}
        verdict = no_change_verdict(SOUND_WINDOW, [], gates)
        assert "ODD" in verdict and ENDORSEMENT not in verdict
        assert "n/a" not in verdict, "never interpolate a bound that is not a number"


class TestTheGatesReachTheVerdict:
    def test_the_render_block_hands_the_gates_over(self):
        # The wiring, not the leaf: a verdict that reads gates it is never
        # given is the fix landing in the assessor and not the renderer, which
        # is the shape this repo already records once.
        src = inspect.getsource(SelfAudit.render_report)
        assert "no_change_verdict(" in src
        # Driven above by `_card`; this pins that the value travels rather
        # than the verdict being called with two arguments again.
        assert _card(ESTABLISHED) != _card(CLEAN)

    @pytest.mark.parametrize("gates,must_say", [
        (ESTABLISHED, "not an endorsement"),
        (CLEAN, ENDORSEMENT),
        (None, "could not be read"),
    ])
    def test_the_card_says_the_right_thing_for_each_input(self, gates, must_say):
        assert must_say in _card(gates)
