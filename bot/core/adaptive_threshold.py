"""The auto-confirm threshold's disable sentinel, and how adaptation may move it.

RC-2026-021. `config.py` documents the switch in as many words —

    set to 1.0 to DISABLE and require manual confirm

— `.env.example` ships `AUTO_CONFIRM_THRESHOLD=1.0`, and `/autoconfirm off`
writes exactly that value. All three promise the same thing, and the adaptive
block undid it on a timer.

WHAT IT DID, executed rather than argued. The block adjusts by ±0.05 per tick
against a floor of 0.60 and a cap of 0.90:

    winning streak (WR >= 70%)   1.00 -> 0.95 -> 0.90 -> ... -> 0.60
    losing  streak (WR <= 40%)   1.00 -> 0.90                    (ONE tick)

The losing-streak line is the one to read twice. Its own comment says "raise
threshold to be selective", and it LOWERS it, because the branch is
`min(cap, current + step)` and the cap sits below a disabled threshold. So the
operator most likely to discover that their disable had been undone is the one
who had just lost money — auto-confirm switches itself back on after five
losing trades.

Neither knob that would have stopped it is reachable from a normal install:
`ADAPTIVE_THRESHOLD_ENABLED` defaults ON and does not appear in
`.env.example` at all.

THREE RULES, and the second and third are the general form of the bug rather
than a patch over the 1.0 case:

  * a threshold at or above DISABLED is not a number to tune — leave it;
  * a winning streak may only LOWER the bar;
  * a losing streak may only RAISE it.

A cap or floor on the wrong side of the current value inverted both directions,
and would still do so from any hand-set value outside [floor, cap] — 0.95 with
a 0.90 cap is loosened by the "be more selective" branch just as 1.00 is.
"""
from __future__ import annotations

#: The documented sentinel. At or above this, auto-confirm is OFF — not
#: "requires 100% confidence", which is a different and reachable claim: the
#: gate is `value >= threshold`, so a blend that scored exactly 1.0 would have
#: auto-executed through a switch the operator had turned off.
DISABLED = 1.0


def auto_confirm_is_disabled(threshold: float | None) -> bool:
    """True when the operator has switched auto-confirm off.

    None counts as disabled. A threshold nobody could read is not a licence to
    place real-money orders without a human, and this is the one direction in
    which an unreadable value must not fail open.
    """
    if threshold is None:
        return True
    try:
        return float(threshold) >= DISABLED
    except (TypeError, ValueError):
        return True


def next_auto_confirm_threshold(
    current: float, recent_wr: float, *,
    high_wr: float, low_wr: float, floor: float, cap: float,
    step: float = 0.05,
) -> float | None:
    """The adaptive block's next threshold, or None to leave it untouched.

    None is returned for a DISABLED threshold and is a real answer: the caller
    must not write it back, because writing anything at all is what undid the
    operator's switch.
    """
    if auto_confirm_is_disabled(current):
        return None
    if recent_wr >= high_wr:
        # Only downward. `max(floor, ...)` alone RAISES a threshold that
        # already sits below the floor.
        return min(current, max(floor, current - step))
    if recent_wr <= low_wr:
        # Only upward. `min(cap, ...)` alone LOWERS a threshold that already
        # sits above the cap — which is the defect, in its general form.
        return max(current, min(cap, current + step))
    return current
