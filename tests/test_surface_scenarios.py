"""Scored surface scenarios: plant a known state, assert what the card SAYS.

Every diagnosis defect fixed on 2026-07-30 — nineteen PRs — was found by the
operator hitting it live, not by a test. The reason is structural. Our ~5,400
tests verify that code RUNS. Almost none verify that a surface TELLS THE
TRUTH when the system is in a particular broken state.

The idea is borrowed from OpenSRE's "scored synthetic RCA suites that check
root-cause accuracy, required evidence, and adversarial red herrings". Three
axes, and the third is the one that matters here:

  MUST_SAY      the fact the operator needs
  MUST_NOT_SAY  the wrong conclusion the state invites
  RED HERRING   a true-but-misleading signal planted alongside

The red herring is not decoration. `_scan_timeout_hint` carries a comment
recording that a GREEN LLM health check was read as "the exchange is slow"
while analyses hung badly enough to blow the tick cap thirty-seven times. A
green sub-check that rules ONE cause out is exactly an adversarial signal,
and no test had ever planted one.

Two of tonight's four defects would have failed here on the first run:
#999's adoption detail (rendered nothing, forever) and the unlabelled
lifetime PnL. Both were caught by screenshots instead.
"""
from __future__ import annotations

import time
from types import SimpleNamespace as NS

import pytest

from bot.formatters.rich_cards import render_adoption_card, render_open_positions
from bot.skills.telegram_handler import _scan_timeout_hint


def _pos(**over):
    base = dict(origin="adopted", symbol="UNI/USDT", status="open",
                stop_loss=6.51, take_profit=7.89, unprotected=False)
    base.update(over)
    return NS(**base)


# ── Scenario table ────────────────────────────────────────────────────────
# (id, render callable, must_say, must_not_say, why this state is a trap)

ADOPTION_SCENARIOS = [
    pytest.param(
        lambda: render_adoption_card(["UNI/USDT"], [_pos()]),
        ["UNI/USDT", "SL", "6.51", "active"],
        [],
        id="protected-position-names-its-levels",
    ),
    pytest.param(
        lambda: render_adoption_card(["UNI/USDT"], [_pos(unprotected=True)]),
        ["UNPROTECTED", "Set one NOW"],
        ["active"],
        id="unprotected-is-loud-not-buried",
    ),
    pytest.param(
        # THE #999 TRAP: the card is handed a symbol it cannot match. It must
        # still name the position — dropping an adoption notice is worse than
        # rendering one without levels — but must NOT imply protection.
        lambda: render_adoption_card(["UNI/USDT"], []),
        ["UNI/USDT"],
        ["active", "UNPROTECTED"],
        id="unmatched-symbol-renders-bare-never-invents",
    ),
    pytest.param(
        # A non-adopted position with the same symbol must not lend it levels.
        lambda: render_adoption_card(["UNI/USDT"], [_pos(origin="bot")]),
        ["UNI/USDT"],
        ["active"],
        id="a-bot-opened-twin-does-not-lend-its-stops",
    ),
    pytest.param(
        # A CLOSED adopted twin likewise.
        lambda: render_adoption_card(["UNI/USDT"], [_pos(status="closed")]),
        ["UNI/USDT"],
        ["active"],
        id="a-closed-twin-does-not-lend-its-stops",
    ),
]


@pytest.mark.parametrize("render,must_say,must_not_say", ADOPTION_SCENARIOS)
def test_adoption_card_scenarios(render, must_say, must_not_say):
    out = render()
    for phrase in must_say:
        assert phrase in out, f"card omitted {phrase!r}\n---\n{out}"
    for phrase in must_not_say:
        assert phrase not in out, f"card wrongly claimed {phrase!r}\n---\n{out}"


# ── The red herring that cost thirty-seven ticks ──────────────────────────

def _analyzer(degraded_streak: int):
    return NS(llm_health=lambda: {"degraded_streak": degraded_streak})


class TestGreenSubCheckIsNotAVerdict:
    """A healthy LLM rules ONE cause out. It names none."""

    def test_green_llm_never_concludes_the_exchange_is_at_fault(self):
        eng = NS(_last_analysis_timeout=None, _analyze_progress=None)
        out = _scan_timeout_hint(_analyzer(0), eng)
        # The exact wording that shipped and misled, and anything like it.
        assert "not the AI" not in out
        assert "likely exchange" not in out
        assert "does not identify the cause" in out

    def test_measured_progress_outranks_the_green_check(self):
        eng = NS(_last_analysis_timeout=None,
                 _analyze_progress={"of": 40, "done": 12,
                                    "started": time.monotonic() - 25.0, "seq": 1})
        out = _scan_timeout_hint(_analyzer(0), eng)
        assert "12 of 40" in out
        assert "slow, not hung" in out

    def test_zero_progress_is_not_reported_as_slow(self):
        # "Slow" and "blocked" call for different next moves.
        eng = NS(_last_analysis_timeout=None,
                 _analyze_progress={"of": 40, "done": 0,
                                    "started": time.monotonic() - 30.0, "seq": 1})
        out = _scan_timeout_hint(_analyzer(0), eng)
        assert "blocked dependency" in out
        assert "slow, not hung" not in out

    def test_a_degraded_brain_is_named_over_everything_else(self):
        eng = NS(_last_analysis_timeout=None,
                 _analyze_progress={"of": 40, "done": 12,
                                    "started": time.monotonic() - 25.0, "seq": 1})
        out = _scan_timeout_hint(_analyzer(5), eng)
        assert "LLM brain degraded" in out
        assert "slow, not hung" not in out


# ── A total whose window is not stated ────────────────────────────────────

class TestNumbersCarryTheirWindow:
    def test_open_positions_card_never_implies_a_pnl_window(self):
        # The positions card shows per-position PnL only; a lifetime total
        # appearing here unlabelled is the /portfolio trap one surface over.
        out = render_open_positions([{
            "pair": "INJUSDT", "direction": "LONG", "entry": 10.0,
            "current": 10.5, "pnl_pct": 5.0, "pnl_usd": 2.5,
            "sl": 9.5, "tp": 11.0, "size_usd": 51.18, "hold_hours": 1.0,
        }])
        assert "INJ" in out
        assert "Realized PnL" not in out
