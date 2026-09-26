"""
Regression: a blocked live execution must never be announced as "EXECUTED".

Real incident: /fullscan -> tap-to-confirm on a signal while the WebSocket was
disconnected. live_executor.execute() correctly refused (returning
"EXECUTION BLOCKED: system is in degraded mode (paused) -- WebSocket
disconnected") BEFORE placing any order. But scan_skill.callback_confirm_reject
classified success/failure with its own local prefix list that only checked
for a bare "BLOCKED:" prefix -- "EXECUTION BLOCKED:" never matched it, so the
handler rendered "(check) DYDX/USDT LONG EXECUTED" followed by the block
reason, telling the user a live position was opened when none was.

The scan card's tap is `confirm:<id>` on the card's own registered idea now
(test_a_scan_button_places_what_the_card_shows), so the answer is read by the
one door every trade button uses -- `_handle_callback`'s confirm branch, which
asks `confirm_result.placed_nothing` -- and these drive THAT door, tapped the
way a scan card taps it.
"""

from tests.test_every_confirm_trade_door_is_gated import _confirm_tap


def test_degraded_mode_block_is_not_announced_as_executed():
    engine, _h, said = _confirm_tap(
        admin=True, live_ok=True, live=True,
        answer=("EXECUTION BLOCKED: real-time price feed disconnected (degraded mode, "
                "paused) — market orders are held so none fires into a stale price. This "
                "auto-resumes once the feed reconnects (usually within ~60s). To trade "
                "right now, resend as a LIMIT order — limit orders don't need the live "
                "feed and are not blocked."))
    assert engine.confirm_trade.await_count == 1
    assert "Trade executed" not in said
    assert "didn't go through" in said
    assert "degraded mode" in said


def test_genuine_fill_is_still_announced_as_executed():
    _e, _h, said = _confirm_tap(admin=True, live_ok=True, live=True,
                                answer="Filled at $0.20462, qty 48.7")
    assert "Trade executed" in said


def test_confirm_trade_exception_does_not_leak_raw_text():
    """Audit F-15: a caught exception (e.g. a ccxt/auth error whose str()
    can contain the raw API key) must never reach the user verbatim."""
    _e, _h, said = _confirm_tap(
        admin=True, live_ok=True, live=True,
        answer=RuntimeError("auth failed: api_key=SUPER_SECRET_TOKEN_123"))
    assert "SUPER_SECRET_TOKEN_123" not in said
    assert "execution failed" in said.lower()
