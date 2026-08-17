"""`execute` was not the only way a position gets filled.

The 2026-08-17 APT incident — a 5x target filled at 20x on Bitget's sticky
per-symbol leverage — was fixed in `execute`. Adversarial review of that fix
then found THREE other paths reaching the same state, none of which had any
verdict at all:

    _check_pending_limit            a limit order fills
    _adopt_partial_fill             a cancelled limit had partly filled
    _execute_drift_market_fallback  a drifted limit becomes a market order

Each computes `cost_usd` from `pos.leverage` — the INTENDED value — and places
SL/TP. The limit path then calls `sync_positions_from_exchange`, which silently
rewrites `pos.leverage` and `pos.cost_usd` and audits a `leverage_sync` line.
That is a log entry, not a decision: the position keeps running, sized and
displayed for 5x while the venue has it at 20x, with a liquidation distance a
quarter of the approved one. The other two never look at all.

This is the repo's own recurring lesson — "ask which OTHER surface makes the
same claim" — arriving one level up from where it usually does. The claim here
is not a number on a card; it is "this position is running at the approved
leverage", made by four code paths and evidenced by one.

THE DIFFERENCE THAT MADE A SHARED HELPER NECESSARY

In `execute` the guard runs BEFORE `_place_sl_tp`, so flattening cannot orphan
anything — that ordering is pinned by its own test. On these three paths the
stop and take-profit are ALREADY LIVE by the time the leverage is known.
Closing must cancel them first, and `close_position` already does exactly that
(`_close_position_inner` cancels SL/TP before the reduceOnly close, to stop a
trigger firing between close-fill and cancel and opening an opposite position).
Reusing it is what makes this safe; a hand-rolled close would reintroduce that
race. `test_the_flatten_goes_through_close_position` is that property.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor
from tests.source_scan import code_only

SRC = Path("bot/core/live_executor.py").read_text(encoding="utf-8")
CODE = code_only(SRC)


def _run(coro):
    return asyncio.run(coro)


def _executor(tmp_path, *, actual_leverage: int, close_ok: bool = True):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._verify_position_exists = AsyncMock(return_value={
        "confirmed": True, "leverage": actual_leverage,
        "exchange_entry": 100.0, "exchange_qty": 1.0,
    })
    if close_ok:
        ex.close_position = AsyncMock(return_value="closed at $100.00")
    else:
        ex.close_position = AsyncMock(side_effect=RuntimeError("venue rejected"))
    return ex


def _pos(symbol="APT/USDT:USDT", leverage=5):
    return SimpleNamespace(symbol=symbol, direction="SHORT", leverage=leverage,
                           sl_order_id="sl-1", tp_order_id="tp-1")


# ── the verdict, on the paths that had none ──────────────────────────────────

def test_a_4x_overshoot_on_a_filled_position_is_closed(tmp_path):
    ex = _executor(tmp_path, actual_leverage=20)
    msg = _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill"))
    assert msg is not None, "a 5x target filled at 20x must not be kept"
    assert "20x" in msg and "5x" in msg
    ex.close_position.assert_awaited_once()


def test_venue_rounding_is_kept(tmp_path):
    ex = _executor(tmp_path, actual_leverage=6)
    assert _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill")) is None
    ex.close_position.assert_not_awaited()


def test_an_undershoot_is_never_closed(tmp_path):
    """Less risk than approved is not more. Closing here would be the guard
    causing the loss it exists to prevent."""
    ex = _executor(tmp_path, actual_leverage=3)
    assert _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill")) is None
    ex.close_position.assert_not_awaited()


def test_an_unreadable_leverage_does_not_close_and_does_not_claim_it_was_fine(tmp_path):
    """`unknown` is not `keep` — the position stays either way, but a leverage
    nobody could read must not be recorded as one that checked out."""
    ex = _executor(tmp_path, actual_leverage=0)
    assert _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill")) is None
    ex.close_position.assert_not_awaited()


# ── the flatten is safe on THESE paths specifically ──────────────────────────

def test_the_flatten_goes_through_close_position(tmp_path):
    """THE PROPERTY THAT MAKES THIS SAFE HERE.

    SL and TP are already live on the venue by this point. `close_position`
    cancels them before sending the reduceOnly close; a hand-rolled
    create_order would leave two triggers armed against a position that no
    longer exists, and either could fill and open an opposite one.
    """
    ex = _executor(tmp_path, actual_leverage=20)
    _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill"))
    ex.close_position.assert_awaited_once()
    assert ex.close_position.await_args.kwargs.get("reason") == "leverage_overshoot"

    guard = CODE[CODE.index("async def _guard_fill_leverage"):]
    guard = guard[:guard.index("async def _verify_position_exists")]
    assert "create_order" not in guard, (
        "the flatten must reuse close_position, which cancels the live SL/TP "
        "first — a direct order here reintroduces the trigger race")


def test_a_failed_close_says_the_position_is_still_open_and_still_protected(tmp_path):
    """The honest failure. The close is what would have cancelled SL/TP, so a
    failed close leaves them in place: over-levered, but not naked. Saying
    'unprotected' would send the operator to the wrong emergency."""
    ex = _executor(tmp_path, actual_leverage=20, close_ok=False)
    msg = _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill"))
    assert msg is not None
    assert "still OPEN" in msg
    assert "still in place" in msg, (
        "the operator must be told the stop survived, or they will assume the "
        "position is naked")
    assert "MANUALLY" in msg


def test_a_flatten_rests_the_symbol(tmp_path):
    """Sticky leverage does not heal because we closed. Without the rest the
    engine re-signals and the cycle repeats at a fee per round."""
    ex = _executor(tmp_path, actual_leverage=20)
    _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill"))
    assert "APT/USDT:USDT" in ex._leverage_blocked_until
    assert ex._preflight_check(10.0, symbol="APT/USDT:USDT") is not None


def test_the_guard_never_raises_into_a_filled_position(tmp_path):
    """It runs after capital is committed and the stop is on the venue. A guard
    that raises must not become the reason a filled position goes unmanaged."""
    ex = _executor(tmp_path, actual_leverage=20)
    ex._verify_position_exists = AsyncMock(side_effect=RuntimeError("venue down"))
    assert _run(ex._guard_fill_leverage(object(), "t1", _pos(), 5, "limit fill")) is None


# ── reachability: all three paths actually call it ───────────────────────────

def test_every_fill_path_is_guarded():
    """A helper nothing calls leaves all three paths exactly as they were."""
    calls = CODE.count("self._guard_fill_leverage(")
    assert calls >= 3, (
        f"expected a call from each of the three fill paths, found {calls}")


@pytest.mark.parametrize("fn", [
    "_check_pending_limit",
    "_adopt_partial_fill",
    "_execute_drift_market_fallback",
])
def test_the_named_path_calls_the_guard(fn):
    body = CODE[CODE.index(f"async def {fn}("):]
    nxt = body.find("\n    async def ", 10)
    body = body[:nxt if nxt != -1 else len(body)]
    assert "_guard_fill_leverage(" in body, (
        f"{fn} fills a position and never checks the venue's applied leverage")


def test_the_limit_path_captures_the_target_before_the_sync_overwrites_it():
    """THE TRAP THIS PATH SETS.

    `sync_positions_from_exchange` overwrites `pos.leverage` with the venue's
    value — that is its job. Reading the target after it runs compares the
    actual against itself, so the verdict is always "keep" and the guard is
    dead code that greps fine.
    """
    body = CODE[CODE.index("async def _check_pending_limit("):]
    nxt = body.find("\n    async def ", 10)
    body = body[:nxt if nxt != -1 else len(body)]
    capture = body.find("_intended_lev =")
    sync = body.find("await self.sync_positions_from_exchange()")
    guard = body.find("_guard_fill_leverage(")
    assert -1 not in (capture, sync, guard)
    assert capture < sync < guard, (
        "the intended leverage must be captured BEFORE the sync overwrites it, "
        "and the guard must run after")
