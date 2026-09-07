"""close_position answers by RETURN VALUE, and the post-fill guards read it.

A venue-side failure inside `_close_position_inner` is caught, the position's
status is restored to open, and the caller receives `"CLOSE FAILED for …"`.
An unconfirmed or partial close comes back as a "kept OPEN" message with
whatever remains re-protected inside close_position. Nothing raises. The file
says so beside `_reattempt_post_fill_sl` ("close_position signals failure by
RETURN VALUE … not by raising — a failed breach-close must not read as
closed") and honours it there.

The three guards that flatten after a fill did not. Each read "the coroutine
returned" as "the position is closed", and each had a handler for a failed
close that only a raise could reach — so on the one failure they were written
for, the overshoot guard rested the symbol, skipped the stop and headed its
card CLOSED over an open, over-levered position; the slippage guard announced
"CLOSED for safety"; and the fill-path guard told the operator the stops had
been cancelled and the position closed, when the cancel had happened and the
close had not.

`flatten_outcome` is the one reading, tested here as a pure function against
the strings close_position actually returns, and the wiring pin below is what
keeps a fourth guard from inferring a close from a return.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.core.live_executor import _CLOSE_KEPT_OPEN_MARKERS, flatten_outcome
from tests.source_scan import code_only

SRC = (Path(__file__).resolve().parent.parent
       / "bot" / "core" / "live_executor.py").read_text(encoding="utf-8")
CODE = code_only(SRC)


def _method(name: str) -> str:
    body = CODE[CODE.index(f"async def {name}("):]
    nxt = re.search(r"\n    (?:async )?def ", body[10:])
    return body[:nxt.start() + 10] if nxt else body


# ── the three answers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer, expected", [
    ("CLOSE FAILED for t1: bitget {\"code\":\"40762\"}", "failed"),
    ("⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nThe position is kept OPEN and re-protected.",
     "kept_open"),
    ("⚠️ PARTIAL CLOSE — RESIDUAL REMAINS: LONG BTC/USDT\nPosition kept OPEN.", "kept_open"),
    ("Position t1 not found or already closed/closing.", "kept_open"),
    ("✅ CLOSED LONG BTC/USDT @ $100,000.00 (leverage_overshoot)", "closed"),
    ("CLOSED (unknown)", "closed"),
])
def test_each_answer_reads_as_itself(answer, expected):
    assert flatten_outcome(answer) == expected


@pytest.mark.parametrize("unreadable", [None, "", 42, {"status": "closed"}])
def test_an_unreadable_answer_is_never_a_close(unreadable):
    """close_position is typed -> str. Anything else is a reading nobody made,
    and a guard that treated it as a close would stand down on nothing."""
    assert flatten_outcome(unreadable) == "failed"


def test_a_failed_close_is_not_softened_by_what_surrounds_it():
    """The failure marker wins even inside a longer message."""
    assert flatten_outcome("Attempted flatten.\nCLOSE FAILED for t1: timeout\nReview.") == "failed"


# ── the markers are close_position's own words ───────────────────────────────

def test_the_markers_are_the_strings_close_position_returns():
    """A marker that drifts from close_position's wording is a reader that
    silently files every kept-open answer under 'closed'. Anchored to the
    method that returns them, not to prose about it."""
    inner = _method("_close_position_inner")
    assert 'return f"CLOSE FAILED for {trade_id}: {exc}"' in inner, (
        "the failure answer changed shape; flatten_outcome no longer recognises it")
    for marker in _CLOSE_KEPT_OPEN_MARKERS:
        assert marker in inner, (
            f"{marker!r} is no longer a string _close_position_inner returns")


# ── every post-fill flatten reads the verdict ────────────────────────────────

@pytest.mark.parametrize("guard", [
    "_leverage_overshoot_guard",
    "_post_fill_slippage_guard",
    "_guard_fill_leverage",
])
def test_the_guard_reads_the_answer_before_it_says_anything(guard):
    """Wiring. The behaviour of each arm is driven in the guards' own suites;
    this pins that the answer is READ, after the close and before any card is
    built, so the defect cannot come back as 'the coroutine returned'."""
    body = _method(guard)
    close = body.index("await self.close_position(")
    read = body.index("flatten_outcome(close_msg)")
    assert close < read, f"{guard} reads the verdict before it closes"
    assert 'if _outcome == "failed":' in body, f"{guard} has no failed arm"
    assert 'if _outcome == "kept_open":' in body, f"{guard} has no kept-open arm"
    # The card that says "closed" must sit after both arms have returned.
    closed_claim = next(i for i in (body.find("was CLOSED"), body.find("POSITION CLOSED"),
                                    body.find("CLOSED for safety")) if i != -1)
    assert body.index('if _outcome == "kept_open":') < closed_claim, (
        f"{guard} announces a close before it has ruled out a kept-open answer")
