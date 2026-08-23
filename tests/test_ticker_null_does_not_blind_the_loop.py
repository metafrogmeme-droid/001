"""One unreadable ticker must not stop stop-loss monitoring for the rest.

`check_positions` fetches each open symbol's ticker, then walks every position
comparing price against its SL/TP. The fetch itself is already careful — a
failed `fetch_ticker` is caught per symbol and that symbol is simply left out
of the dict, which is the "omit" half of the CLAUDE.md table and correct.

The hole is a fetch that SUCCEEDS and returns a null price:

    price = float(tickers.get(pos.symbol, {}).get("last", 0))

`.get("last", 0)` returns the default only when the key is ABSENT. An explicit
`{"last": None}` — which ccxt returns for a market with no trades — returns
`None`, and `float(None)` raises TypeError. That raise lands in the `try` at
the top of `check_positions`, which wraps the ENTIRE position loop, so every
position after the unreadable one is skipped for that tick. A stop-loss that
should have fired does not, and the only trace is one `Position check error`
line.

Two tests, because there are two separate properties:

  * the null price itself must not raise — that symbol is unknown, so it is
    skipped, exactly as a failed fetch is;
  * ANY per-position fault must be contained to that position. The first test
    would pass with the price coerced and the loop still fragile; only the
    second pins the isolation, and the isolation is the part that survives the
    next unforeseen exception.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


def _pos(trade_id, symbol, sl, entry=100.0):
    return LivePosition(
        trade_id=trade_id, symbol=symbol, direction="LONG",
        entry_price=entry, quantity=1.0, cost_usd=100.0,
        stop_loss=sl, take_profit=entry * 2, leverage=5, status="open",
        opened_at=datetime.now(UTC) - timedelta(hours=2),
    )


def _executor(tickers_by_symbol):
    ex = LiveExecutor()
    mock = AsyncMock()

    async def _fetch_ticker(sym):
        return tickers_by_symbol[sym]

    mock.fetch_ticker = AsyncMock(side_effect=_fetch_ticker)
    ex._exchange = mock
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = __import__("time").time()
    ex.close_position = AsyncMock(return_value="CLOSED")
    return ex


class TestAnUnreadablePriceIsSkippedNotFatal:
    @pytest.mark.asyncio
    async def test_a_null_last_does_not_stop_the_next_position_closing(self):
        # THIN is first in iteration order and its ticker is a successful fetch
        # carrying a null price. BTC is second and is 10 below its stop.
        ex = _executor({
            "THIN/USDT:USDT": {"last": None},
            "BTC/USDT:USDT": {"last": 90.0},
        })
        ex._positions["TI-thin"] = _pos("TI-thin", "THIN/USDT:USDT", sl=95.0)
        ex._positions["TI-btc"] = _pos("TI-btc", "BTC/USDT:USDT", sl=95.0)

        await ex.check_positions()

        assert ex.close_position.await_count == 1, (
            "the BTC stop-loss did not fire — an unreadable price on an "
            "earlier symbol blinded the rest of the loop")
        assert ex.close_position.await_args.args[0] == "TI-btc"

    @pytest.mark.asyncio
    async def test_the_unreadable_position_itself_is_left_alone(self):
        # Skipped, never closed at a guessed price. `float(None)` coerced to
        # 0.0 would read as "price 0", which for a LONG is below every stop —
        # unreadable rendered as a catastrophic quote.
        ex = _executor({"THIN/USDT:USDT": {"last": None}})
        ex._positions["TI-thin"] = _pos("TI-thin", "THIN/USDT:USDT", sl=95.0)

        await ex.check_positions()

        ex.close_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_missing_last_key_behaves_the_same(self):
        ex = _executor({
            "THIN/USDT:USDT": {},
            "BTC/USDT:USDT": {"last": 90.0},
        })
        ex._positions["TI-thin"] = _pos("TI-thin", "THIN/USDT:USDT", sl=95.0)
        ex._positions["TI-btc"] = _pos("TI-btc", "BTC/USDT:USDT", sl=95.0)

        await ex.check_positions()

        assert ex.close_position.await_count == 1


class TestOnePositionsFaultIsContainedToIt:
    @pytest.mark.asyncio
    async def test_an_arbitrary_per_position_error_does_not_blind_the_others(self):
        """The property that outlives this particular bug.

        Coercing the price fixes the null. It does not fix the next unforeseen
        exception in a 400-line loop body, which would blind every position
        after it in exactly the same way. So the isolation is pinned
        separately: a position whose own processing raises must cost only
        itself.
        """
        ex = _executor({
            "BAD/USDT:USDT": {"last": 90.0},
            "BTC/USDT:USDT": {"last": 90.0},
        })

        class _Exploding(LivePosition):
            @property
            def stop_loss(self):          # read early in the loop body
                raise RuntimeError("planted per-position fault")

            @stop_loss.setter
            def stop_loss(self, v):
                pass

        bad = _pos("TI-bad", "BAD/USDT:USDT", sl=95.0)
        bad.__class__ = _Exploding
        ex._positions["TI-bad"] = bad
        ex._positions["TI-btc"] = _pos("TI-btc", "BTC/USDT:USDT", sl=95.0)

        await ex.check_positions()

        assert ex.close_position.await_count == 1, (
            "one position's fault stopped the loop — every position after it "
            "went unmonitored for this tick")
        assert ex.close_position.await_args.args[0] == "TI-btc"


class TestTheNullIsSkippedCleanlyNotCaught:
    """The isolation and the coercion do different jobs, and a mutation proved
    the second was unpinned: reverting the safe price read failed no test,
    because the per-position `except` swallowed the TypeError and the loop
    carried on regardless.

    That is not the same outcome. Caught, the symbol logs `position check
    failed` at ERROR every single tick — an operator watching a thin market
    sees a stream of errors naming a fault that is really just a market with
    no trades. Skipped, it is what it is: an unknown price, handled exactly
    like a failed fetch, silently.
    """

    @pytest.mark.asyncio
    async def test_a_null_price_never_reaches_the_exception_handler(self, monkeypatch):
        import bot.core.live_executor as le

        errors = []
        monkeypatch.setattr(le.trade_log, "error",
                            lambda *a, **k: errors.append(a[0] if a else ""),
                            raising=False)

        ex = _executor({"THIN/USDT:USDT": {"last": None}})
        ex._positions["TI-thin"] = _pos("TI-thin", "THIN/USDT:USDT", sl=95.0)

        await ex.check_positions()

        assert not any("position check failed" in str(e) for e in errors), (
            "an unreadable price was handled by catching an exception rather "
            "than by skipping the symbol — correct outcome, wrong reason, and "
            "it reports a fault on every tick for a market that simply has no "
            "trades")
