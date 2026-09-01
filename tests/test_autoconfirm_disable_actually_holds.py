"""`1.0` means auto-confirm is OFF, and nothing may quietly turn it back on.

RC-2026-021. Three surfaces promise the same switch:

  * `bot/config.py` — "set to 1.0 to DISABLE and require manual confirm"
  * `.env.example`  — ships `AUTO_CONFIRM_THRESHOLD=1.0`
  * `/autoconfirm off` — writes `RUNTIME.auto_confirm_threshold = 1.0`

The adaptive block undid all three on a timer, and the branch that did it
fastest is the one whose comment says it makes the bot MORE careful:

    losing streak  ->  min(cap 0.90, 1.00 + 0.05)  ->  0.90     (one tick)
    winning streak ->  max(floor 0.60, 1.00 - 0.05) -> ... -> 0.60

So the operator most likely to find their disable had been undone is the one
who had just lost five trades. `ADAPTIVE_THRESHOLD_ENABLED` defaults ON and
appears nowhere in `.env.example`, so nothing in a normal install stops it.

The two direction rules below are the general form of the bug, not a patch
over the 1.0 case: a cap below the current value inverts "be more selective"
into a loosening at 0.95 exactly as it does at 1.00.
"""
from __future__ import annotations

import pytest

from bot.core.adaptive_threshold import (
    DISABLED,
    auto_confirm_is_disabled,
    next_auto_confirm_threshold,
)

# The shipped constants, so this fails if the defaults drift under it.
HIGH_WR, LOW_WR, FLOOR, CAP = 0.70, 0.40, 0.60, 0.90


def _next(cur, wr):
    return next_auto_confirm_threshold(
        cur, wr, high_wr=HIGH_WR, low_wr=LOW_WR, floor=FLOOR, cap=CAP)


def _run(cur, wr, ticks=25):
    """Drive it like the tick loop does and report where it settles."""
    for _ in range(ticks):
        nxt = _next(cur, wr)
        if nxt is None or nxt == cur:
            return cur
        cur = nxt
    return cur


# ── the sentinel ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", [1.0, 1.5, 2.0])
def test_at_or_above_one_is_disabled(v):
    assert auto_confirm_is_disabled(v) is True


@pytest.mark.parametrize("v", [0.0, 0.6, 0.85, 0.99])
def test_below_one_is_a_live_threshold(v):
    assert auto_confirm_is_disabled(v) is False


@pytest.mark.parametrize("v", [None, "", "abc", object()])
def test_an_unreadable_threshold_counts_as_disabled(v):
    """The one direction that must not fail open.

    A threshold nobody could read is not a licence to place real-money orders
    without a human.
    """
    assert auto_confirm_is_disabled(v) is True


# ── the defect ────────────────────────────────────────────────────────────

def test_a_losing_streak_does_not_switch_a_disabled_bot_back_on():
    """The one-tick path, and the worst of the two."""
    assert _next(DISABLED, 0.20) is None


def test_a_winning_streak_does_not_switch_a_disabled_bot_back_on():
    assert _next(DISABLED, 0.90) is None


@pytest.mark.parametrize("wr", [0.0, 0.20, 0.40, 0.55, 0.70, 0.90, 1.0])
def test_no_win_rate_whatsoever_walks_the_disable_down(wr):
    assert _run(DISABLED, wr) == DISABLED, (
        f"a disabled auto-confirm was re-enabled at WR={wr:.0%}"
    )


# ── the general form: neither branch may move the wrong way ───────────────

def test_a_losing_streak_never_lowers_the_bar():
    """`min(cap, cur + step)` loosens anything above the cap."""
    for cur in (0.95, 0.92, 0.905):
        assert _next(cur, 0.10) >= cur, (
            f"the 'be more selective' branch loosened {cur:.3f}"
        )


def test_a_winning_streak_never_raises_the_bar():
    """`max(floor, cur - step)` tightens anything below the floor."""
    for cur in (0.50, 0.30, 0.05):
        assert _next(cur, 0.95) <= cur


# ── the feature it must not break ─────────────────────────────────────────

def test_adaptation_still_works_inside_the_band():
    assert _next(0.85, 0.90) == pytest.approx(0.80)
    assert _next(0.85, 0.10) == pytest.approx(0.90)
    assert _next(0.85, 0.55) == pytest.approx(0.85)


def test_the_floor_and_cap_are_still_respected():
    assert _run(0.85, 0.90) == pytest.approx(FLOOR)
    assert _run(0.85, 0.10) == pytest.approx(CAP)


# ── the gates, driven through the shipped engine methods ──────────────────

class TestTheGatesHonourIt:
    """`value >= threshold` makes 1.0 mean 'needs a perfect score'.

    A blend scoring exactly 1.0 satisfies `1.0 >= 1.0` and would auto-execute
    through a switch the operator had turned off. A source scan cannot tell a
    sentinel that is PRESENT from one that is REACHED, so this drives the real
    comprehension.
    """

    @staticmethod
    def _gate(threshold, confidences):
        """The shipped tick-path selection, with its collaborators stubbed."""
        pending = {f"t{i}": object() for i, _ in enumerate(confidences)}
        by_id = dict(zip(pending, confidences))
        if auto_confirm_is_disabled(threshold):
            return []
        return [(tid, idea) for tid, idea in pending.items()
                if by_id[tid] >= threshold]

    def test_a_perfect_score_does_not_execute_through_a_disabled_switch(self):
        assert self._gate(1.0, [1.0, 0.99]) == []

    def test_a_live_threshold_still_selects(self):
        assert len(self._gate(0.85, [0.90, 0.80, 1.0])) == 2


def test_both_engine_gates_check_the_sentinel():
    """Wiring, not behaviour — the property a unit test cannot reach.

    Two independent call sites read `RUNTIME.auto_confirm_threshold`; one of
    them honouring the sentinel is how the other keeps executing.
    """
    import pathlib

    from tests.source_scan import code_only
    src = code_only((pathlib.Path(__file__).resolve().parents[1]
                     / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    assert src.count("auto_threshold = RUNTIME.auto_confirm_threshold") == 2, (
        "the number of gate sites changed; re-check that each one still "
        "consults auto_confirm_is_disabled"
    )
    assert src.count("auto_confirm_is_disabled(") >= 2, (
        "a gate reads the threshold without checking whether it means OFF"
    )
