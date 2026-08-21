"""A failed read of the drawdown backstop rendered as a bare heading.

`/drawdownlimit` decides how much real money the bot loses before it halts.
Its status block was built inline, and `drawdown_status()` is itself documented
"best-effort; returns empty on any error" — so two layers swallowed the same
fault and produced one outcome:

    📉 Live drawdown backstop
    <nothing>

CLAUDE.md's table gives two honest strategies for an unreadable source: GUARD
(throw, so the caller paints an error) or OMIT (leave the missing source out
entirely). This was neither. The section still announces itself and then says
nothing, which reads as the third thing — "nothing to report", i.e. no
drawdown worth naming, on the control that stops real-money losses.

WHERE IT LANDS. The block prints three times, and the worst is the
confirmation after an operator SETS a looser cap, immediately above

    "Real money is down — a looser cap means the bot tolerates MORE LOSS
     before halting."

The operator loosens the backstop, reads the confirmation, and the backstop
section is blank. Nothing tells them whether the override took.

THE SOURCE LABEL. `drawdown_status()` computes `drawdown_source` and carries a
comment recording that this reporter "used to return the paper number while its
own docstring promised 'the drawdown the breaker actually gates on' — so an
operator could read ~0% from a gate that was refusing trades at 9%". The engine
labels which number it is; the card dropped the label. "3.2%" means two very
different things depending on the answer.

THE RED HERRING, planted below: a genuinely flat equity curve — `0.0%` from a
real reading. It must still print 0.0%, because a measured break-even is a
measurement. A fix that renders every 0 as unknown is the mirror defect.
"""

from __future__ import annotations

import re

import pytest

from bot.formatters.drawdown_card import HEADING, render_drawdown_status

LIVE = {"drawdown_pct": 3.2, "drawdown_source": "live",
        "effective_limit_pct": 15.0, "config_live_limit_pct": 10.0,
        "override_pct": 15.0, "live_hardening": True}


def text(lines) -> str:
    return "\n".join(re.sub(r"<[^>]+>", "", ln) for ln in lines)


# ── the failed read ─────────────────────────────────────────────────────────

class TestAFailedReadIsNeverBlank:
    @pytest.mark.parametrize("st", [None, {}])
    def test_it_says_it_could_not_be_read(self, st):
        out = text(render_drawdown_status(st))
        assert "Could not be read" in out, out
        assert "unknown" in out

    @pytest.mark.parametrize("st", [None, {}])
    def test_it_is_more_than_a_heading(self, st):
        lines = render_drawdown_status(st)
        assert len(lines) > 1, (
            "the section announces itself and then says nothing, which reads "
            "as 'nothing to report' on the real-money backstop")
        assert lines[0] == HEADING

    def test_it_denies_the_reading_anyone_would_infer(self):
        """The whole failure mode is a reader concluding "no drawdown" from an
        empty block. Say the opposite in words."""
        out = text(render_drawdown_status({}))
        assert "not a flat equity curve" in out
        assert "does not mean the backstop is clear" in out

    def test_it_warns_that_an_override_may_not_be_in_force(self):
        # This block is printed directly under "override set to X%".
        out = text(render_drawdown_status({}))
        assert "may or may not be in force" in out

    def test_it_invents_no_numbers(self):
        out = text(render_drawdown_status({}))
        assert not re.search(r"\d+\.\d+%", out), f"a percentage appeared: {out}"
        assert "0.0%" not in out


# ── a real reading still reads ──────────────────────────────────────────────

class TestARealReadingIsUnchanged:
    def test_the_numbers_are_printed(self):
        out = text(render_drawdown_status(LIVE))
        assert "Current drawdown: 3.2%" in out
        assert "Limit in force: 15.0%" in out
        assert "Override: 15.0% (default 10.0%)" in out

    def test_a_genuinely_flat_curve_still_prints_zero(self):
        """THE RED HERRING. A measured break-even is a measurement."""
        out = text(render_drawdown_status(dict(LIVE, drawdown_pct=0.0)))
        assert "Current drawdown: 0.0%" in out
        assert "Could not be read" not in out

    def test_no_override_says_none_rather_than_a_number(self):
        out = text(render_drawdown_status(dict(LIVE, override_pct=None)))
        assert "Override: none (default 10.0%)" in out

    def test_a_zero_override_is_a_real_override(self):
        # `if ov is not None` rather than `if ov` — 0.0 is falsy and would
        # otherwise render as "none", hiding the tightest cap there is.
        out = text(render_drawdown_status(dict(LIVE, override_pct=0.0)))
        assert "Override: 0.0%" in out
        assert "Override: none" not in out


# ── which number is it ──────────────────────────────────────────────────────

class TestTheNumberIsAttributable:
    def test_a_live_reading_says_so(self):
        assert "live equity high-water mark" in text(render_drawdown_status(LIVE))

    def test_a_paper_reading_says_so(self):
        out = text(render_drawdown_status(dict(LIVE, drawdown_source="paper")))
        assert "paper snapshot" in out
        assert "live equity" not in out

    def test_the_two_render_differently(self):
        a = text(render_drawdown_status(LIVE))
        b = text(render_drawdown_status(dict(LIVE, drawdown_source="paper")))
        assert a != b, (
            "an enforced live figure and a paper snapshot render identically, "
            "which is the confusion drawdown_status was changed to prevent")

    def test_an_unlabelled_source_claims_nothing(self):
        st = dict(LIVE)
        st.pop("drawdown_source")
        out = text(render_drawdown_status(st))
        assert "Current drawdown: 3.2%" in out
        assert "snapshot" not in out and "high-water" not in out


# ── partial payloads ────────────────────────────────────────────────────────

class TestAPartialPayloadDoesNotManufactureAWarning:
    def test_absent_live_hardening_does_not_claim_it_is_off(self):
        st = dict(LIVE)
        st.pop("live_hardening")
        out = text(render_drawdown_status(st))
        assert "Live hardening OFF" not in out, (
            "a warning was manufactured from a key that was not present")

    def test_a_real_off_still_warns(self):
        out = text(render_drawdown_status(dict(LIVE, live_hardening=False)))
        assert "Live hardening OFF" in out

    @pytest.mark.parametrize("missing,expect", [
        ("drawdown_pct", "Current drawdown: —"),
        ("effective_limit_pct", "Limit in force: —"),
        ("config_live_limit_pct", "(default —)"),
    ])
    def test_a_missing_number_is_a_dash_not_a_zero(self, missing, expect):
        # Anchored to the field's own line. The first draft asserted
        # `"0.0%" not in out`, which matches inside "(default 10.0%)" — the
        # third too-loose absence assertion in this sweep. Asserting that a
        # SHORT string is absent from a longer body is the shape that keeps
        # misfiring; assert the positive rendering of the field instead.
        st = dict(LIVE)
        st.pop(missing)
        out = text(render_drawdown_status(st))
        assert expect in out, out


# ── the handler still uses it ───────────────────────────────────────────────

def test_the_command_routes_through_the_renderer():
    """A renderer nothing calls is indistinguishable from one that does not
    work, and the three print sites are why this block matters at all."""
    from tests.source_scan import code_only
    src = code_only(open("bot/skills/telegram_handler.py", encoding="utf-8").read())
    assert "render_drawdown_status(st)" in src
    assert 'lines = ["📉 <b>Live drawdown backstop</b>"]' not in src, (
        "the inline block is back")
    assert src.count("_status_lines()") >= 3, (
        "the status block is printed on show, on clear and on set — the last "
        "is the one directly under 'a looser cap tolerates more loss'")
