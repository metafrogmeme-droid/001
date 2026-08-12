"""The evening digest booked unpriced closes as $0 break-even non-wins.

`proactive_monitor._digest_body` summed the recent live book with

    net  = sum(float(getattr(t, "pnl_usd", 0) or 0) for t in closed)
    wins = sum(1 for t in closed if float(getattr(t, "pnl_usd", 0) or 0) > 0)

`LivePosition.pnl_usd` is `Optional[float] = None`, so `or 0` made every close
the record could not price into a measured break-even — added to the net, and
counted as a non-win against `len(closed)`. Two banned shapes in three lines,
on a card whose own docstring says *"a field we can't read is omitted, never
invented"*.

`bot/utils/win_rate.py` exists to be the single answer to both, and six other
sites already call it. This digest went its own way, which is the failure the
module's docstring names: fixing the LINE leaves the surface half-cured, so the
fix has to follow the VALUE.

The card also printed `net $+0.00` — with the sign — which reads as a measured
flat night rather than as nothing having been readable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.core.live_executor import LivePosition
from bot.core.proactive_monitor import ProactiveMonitor


def _pos(pnl_usd, *, symbol="BTC/USDT:USDT", tid="t1"):
    return LivePosition(
        trade_id=tid, symbol=symbol, direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=90.0, take_profit=120.0,
        leverage=1, is_spot=False,
        opened_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        close_price=110.0, pnl_usd=pnl_usd, status="closed",
        strategy_type="breakout", close_reason="tp",
    )


class _Executor:
    def __init__(self, closed):
        self.closed_positions = closed
        self.open_positions = []


class _Engine:
    def __init__(self, closed):
        self.live_executor = _Executor(closed)
        self.state = "RUNNING"
        self.portfolio = None
        self._live_balance_cache = {}


def _digest(closed) -> str:
    monitor = ProactiveMonitor.__new__(ProactiveMonitor)
    monitor.engine = _Engine(closed)
    return ProactiveMonitor._digest_body(monitor, "evening")


def test_a_real_book_reads_correctly():
    """3 wins, 2 losses, +$200."""
    body = _digest([_pos(150.0, tid="a"), _pos(120.0, tid="b"), _pos(30.0, tid="c"),
                    _pos(-40.0, tid="d"), _pos(-60.0, tid="e")])
    assert "Recent closes: <b>5</b>" in body
    assert "<b>3</b> wins" in body
    assert "$+200.00" in body
    assert "unpriced" not in body, "no caveat is due on a complete window"


def test_an_unpriced_close_is_neither_a_zero_nor_a_non_win():
    body = _digest([_pos(100.0, tid="a"), _pos(-40.0, tid="b"), _pos(None, tid="c")])
    assert "Recent closes: <b>3</b>" in body, "the COUNT of closes is still 3"
    assert "<b>1</b> wins of 2 priced" in body, (
        "the unpriced close was scored as a non-win against the full count")
    assert "$+60.00" in body, "the unpriced close was summed as a break-even"
    assert "1 unpriced" in body, "the window the figures cover was not disclosed"


def test_a_book_nothing_can_price_reports_no_net():
    body = _digest([_pos(None, tid="a"), _pos(None, tid="b")])
    assert "net <code>—</code>" in body, (
        "`net $+0.00` reads as a measured flat night, not as an unreadable one")
    assert "$+0.00" not in body
    assert "<b>0</b> wins of 0 priced" in body


def test_a_measured_flat_book_still_prints_its_zero():
    """The other half of the rule. Two closes that both broke even is a
    result, and hiding it is the same defect facing the other way."""
    body = _digest([_pos(0.0, tid="a"), _pos(0.0, tid="b")])
    assert "$+0.00" in body, "a measured break-even was suppressed"
    assert "<b>0</b> wins of 2 priced" in body
    assert "unpriced" not in body


def test_an_empty_book_says_nothing_at_all():
    """No closes means the line is omitted — not a zero line."""
    body = _digest([])
    assert "Recent closes" not in body


def test_the_digest_uses_the_shared_reader():
    """Wiring. The six other sites route through bot/utils/win_rate.py; this
    one computing its own answer again is how the surfaces come apart."""
    import inspect
    src = inspect.getsource(ProactiveMonitor._digest_body)
    # Comments are stripped so the note explaining the fix — which quotes the
    # expression it forbids — cannot fail the scan that the fix passes.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "win_stats" in code and "pnl_stats" in code
    assert 'getattr(t, "pnl_usd", 0)' not in code, "back to the banned shape"


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_nonsense_pnl_is_absent_rather_than_extreme(bad):
    """NaN and inf reach a float field through bad arithmetic upstream. The
    shared reader drops them; a local `float(x or 0)` would have propagated
    them into the total and printed `$+nan`."""
    body = _digest([_pos(50.0, tid="a"), _pos(bad, tid="b")])
    assert "$+50.00" in body
    assert "nan" not in body.lower() and "inf" not in body.lower()
