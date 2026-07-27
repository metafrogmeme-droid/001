"""The kill switch must stop a position being OPENED, and must not stop an exit.

THE DEFECT

`bot/core/live_executor.py` had no halt awareness at all — grepping it for
`circuit_breaker|_halted|kill` returned nothing — while
`_execute_drift_market_fallback` opens a live position with a market order.

The engine's last-mile check ("halted before execute") guards the normal open
path. The drift fallback is reached from `_check_open_positions`, which
deliberately keeps running while halted so existing positions are still managed.
Correct for exits; wrong for an open.

Concrete sequence, all defaults, LIVE:

  10:00  a LONG rests as a limit order (default_order_type="limit", 4h expiry)
  11:30  two stop-outs trip the daily-loss breaker; the operator taps /halt
         — neither cancels resting limit orders
  11:45  price drifts 3%; the fallback converts the resting limit into a
         MARKET BUY, opening NEW exposure on a halted, breaker-open account

Only /emergency_stop removed that exposure. /halt, /pause and every automatic
breaker did not.

WHY THE HOOK IS MODULE-LEVEL

There are four `LiveExecutor()` construction sites. A per-instance callback would
be missed at the fifth, which is the same "guard not reached" defect this audit
has now found five times. `scripts/guard_lint.py` carries a `kill-switch` rule so
a new order-OPENING path cannot skip it.

WHAT MUST NOT HAPPEN

Reducing actions — `_place_sl_tp`, `_partial_close`, `_update_exchange_sl`,
`_close_position_inner` — must keep working while halted. Gating them would stop
a protective stop-loss from being placed during a kill switch, which is strictly
worse than the bug being fixed. The linter rule excludes them explicitly, and
the first draft of that rule flagged them, which is how the danger surfaced.
"""

import pytest

pytest.importorskip("ccxt", reason="live_executor imports ccxt")

from bot.core import live_executor as le  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_hook():
    yield
    le.set_halt_check(None)


def test_unwired_is_permissive_so_nothing_breaks():
    """An executor used standalone (tests, scripts) behaves exactly as before.

    Failing closed here would break every existing caller that never wired a
    halt check. Reachability is enforced by guard_lint instead, which is the
    right place for it — a structural check, not a runtime surprise.
    """
    le.set_halt_check(None)
    assert le.trading_halted() is False


def test_a_wired_halt_is_reported():
    le.set_halt_check(lambda: True)
    assert le.trading_halted() is True
    le.set_halt_check(lambda: False)
    assert le.trading_halted() is False


def test_an_unreadable_halt_state_fails_CLOSED():
    """If the halt state cannot be read, treat trading as halted.

    Refusing to open when the answer is unknown costs an opportunity. Opening
    costs money on an account somebody has already tried to stop.
    """
    def boom():
        raise RuntimeError("risk engine unavailable")
    le.set_halt_check(boom)
    assert le.trading_halted() is True


def test_the_engine_actually_wires_it():
    """The hook existing is worthless if nothing sets it.

    Source-level, because constructing a real engine here would need exchange
    credentials and a live venue.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "bot" / "core" / "engine.py").read_text()
    assert "set_halt_check(" in src, "the engine no longer wires the kill switch into the executor"
    assert "_halted" in src and "circuit_breaker_active" in src


def test_the_opening_path_consults_it_and_the_reducing_paths_do_not():
    """Pins both halves of the invariant.

    The second half matters as much as the first: a future change that gates
    `_place_sl_tp` on the kill switch would prevent a protective stop being
    placed during a halt.
    """
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "bot" / "core" / "live_executor.py").read_text()
    bodies = {n.name: (ast.get_source_segment(src, n) or "")
              for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    opener = bodies["_execute_drift_market_fallback"]
    assert "trading_halted()" in opener, (
        "_execute_drift_market_fallback opens a position and no longer checks the kill switch")
    # ...and checks it AFTER cancelling, because the cancel reduces exposure and
    # must happen even while halted.
    assert opener.index("cancel_order") < opener.index("trading_halted()"), (
        "the halt check moved above the cancel — a halted engine would now leave "
        "the resting limit order in place instead of cancelling it")

    for reducing in ("_place_sl_tp", "_partial_close", "_update_exchange_sl",
                     "_close_position_inner"):
        assert "trading_halted()" not in bodies[reducing], (
            f"{reducing} reduces or protects exposure and must keep working while "
            "halted; gating it would stop a stop-loss during a kill switch")
