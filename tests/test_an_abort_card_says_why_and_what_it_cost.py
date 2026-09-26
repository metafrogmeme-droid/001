"""A limit-fill abort card names its cause, its reason and its net return.

The live card, 2026-09-26 08:04 UTC:

    ⚠️ ENTRY ABORTED: DOT/USDT filled but the stop-loss could not be placed —
    position CLOSED for safety.
    CLOSED LONG DOT/USDT (CLOSED (unknown))
    Entry: $1.2190 → Exit: $1.2170
    PnL: -$0.3182 (-0.82% margin / -0.16% notional, 5×) | Fees: $0.10 | Hold: 55m
    Fill source: exchange_fill_recent_local_pnl

Three things on it were wrong, each in code written to prevent exactly that:

- NO CAUSE. The market entry path's three abort cards carry the venue's
  refusal (`refusal_suffix(self._last_sltp_reason(...))`); the limit-fill
  ladder's three were never given it.
- "CLOSED (unknown)". A close the bot made, for `sl_placement_failed`, was
  booked with the reason the venue lookup GUESSED from where the exit sat,
  because the fix that made a bot close keep its own reason reached only the
  ticker branch. The parity report then counted the abort as a strategy trade.
- A GROSS PERCENT BESIDE NET DOLLARS. `-0.82% margin` is the price move times
  the leverage. With $0.10 of fees the return on margin was about -1.2%.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition, close_pnl_line
from bot.core.sltp_reason import refusal_line

UTC = timezone.utc
CAUSE = "bitget 40034: Parameter stopLossPrice verification failed"


def _pos(**kw):
    base = dict(trade_id="T-DOT", symbol="DOT/USDT", direction="LONG",
                entry_price=1.2190, quantity=109.0, cost_usd=26.57,
                stop_loss=1.19, take_profit=1.28, leverage=5, is_spot=False,
                status="open", order_type="limit",
                opened_at=datetime.now(UTC) - timedelta(minutes=55))
    base.update(kw)
    return LivePosition(**base)


def _ladder(tmp_path, close_msg):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _pos()
    ex._positions[pos.trade_id] = pos

    async def _refused(*_a, **_k):
        ex._note_sltp_error(pos.symbol, CAUSE)
        return None, None

    ex._place_sl_tp = _refused
    ex._guard_unprotected_grace = AsyncMock(return_value=None)
    ex.close_position = AsyncMock(return_value=close_msg)
    return ex, pos


class TestTheLimitFillAbortNamesItsCause:
    @pytest.mark.parametrize("close_msg, head", [
        ("CLOSED LONG DOT/USDT (sl_placement_failed)", "ENTRY ABORTED"),
        ("CLOSE FAILED for T-DOT: venue refused", "URGENT"),
        ("CLOSE NOT CONFIRMED for T-DOT: RESIDUAL REMAINS 40 of 109", "KEPT OPEN"),
    ])
    @pytest.mark.asyncio
    async def test_every_card_the_ladder_can_send(self, tmp_path, close_msg, head):
        ex, pos = _ladder(tmp_path, close_msg)
        _sl, _tp, msg = await ex._reattempt_post_fill_sl(
            AsyncMock(), pos, "LONG", pos.quantity, None, None, pos.trade_id)
        assert msg and head in msg, msg
        assert refusal_line(CAUSE) in msg, msg

    @pytest.mark.asyncio
    async def test_a_cause_nobody_recorded_is_said_so(self, tmp_path):
        ex, pos = _ladder(tmp_path, "CLOSED LONG DOT/USDT (sl_placement_failed)")

        async def _refused_silently(*_a, **_k):
            return None, None

        ex._place_sl_tp = _refused_silently
        _sl, _tp, msg = await ex._reattempt_post_fill_sl(
            AsyncMock(), pos, "LONG", pos.quantity, None, None, pos.trade_id)
        assert refusal_line("") in msg, msg


def _closed(tmp_path, close_data):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _pos()
    ex._positions[pos.trade_id] = pos
    ex._is_duplicate_close_booking = lambda p: False
    ex._fetch_bitget_close_data = AsyncMock(return_value=close_data)
    ex._get_exchange = AsyncMock(return_value=AsyncMock())
    return ex, pos


GUESSED = {"close_price": 1.2170, "pnl": None, "fees": 0.05,
           "reason": "CLOSED (unknown)", "reason_inferred": True,
           "source": "exchange_fill_recent_local_pnl", "pnl_is_net": False}


class TestABotCloseKeepsItsReason:
    @pytest.mark.asyncio
    async def test_a_guessed_venue_reason_does_not_overwrite_the_bots(self, tmp_path):
        ex, pos = _closed(tmp_path, dict(GUESSED))
        msg = await ex._handle_already_closed_position(
            pos, bot_reason="sl_placement_failed", bot_closed=True)
        assert ex._last_close_data["reason"] == "sl_placement_failed", msg
        assert "CLOSED (unknown)" not in msg
        assert ex._last_close_data["exit"] == pytest.approx(1.2170), (
            "the venue's price is still the price")

    @pytest.mark.asyncio
    async def test_a_mechanism_the_venue_named_still_wins(self, tmp_path):
        named = dict(GUESSED, reason="SL HIT (exchange)", reason_inferred=False)
        ex, pos = _closed(tmp_path, named)
        await ex._handle_already_closed_position(
            pos, bot_reason="sl_placement_failed", bot_closed=True)
        assert ex._last_close_data["reason"] == "SL HIT (exchange)"

    @pytest.mark.asyncio
    async def test_a_close_the_venue_made_first_keeps_the_guess(self, tmp_path):
        """The bot asked to close for a reason, and the venue had already
        closed the position: the bot's reason is not what closed it."""
        ex, pos = _closed(tmp_path, dict(GUESSED))
        await ex._handle_already_closed_position(
            pos, bot_reason="sl_placement_failed", bot_closed=False)
        assert ex._last_close_data["reason"] == "CLOSED (unknown)"

    @pytest.mark.asyncio
    async def test_a_close_the_bot_did_not_make_keeps_the_guess(self, tmp_path):
        ex, pos = _closed(tmp_path, dict(GUESSED))
        await ex._handle_already_closed_position(pos)
        assert ex._last_close_data["reason"] == "CLOSED (unknown)"


class TestTheStagesSayWhetherTheyGuessed:
    @pytest.mark.parametrize("close_type, inferred", [("", True), ("stop_loss", False)])
    @pytest.mark.asyncio
    async def test_the_history_row(self, tmp_path, close_type, inferred):
        """A bare closeType names no mechanism; the reason is then read off
        where the exit sits, which is a guess."""
        ex = LiveExecutor(state_dir=str(tmp_path))
        exchange = AsyncMock()
        exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(return_value={
            "data": {"list": [{"openAvgPrice": "1.219", "closeAvgPrice": "1.19",
                               "pnl": "-3.16", "netProfit": "-3.3",
                               "closeType": close_type}]}})
        out = await ex._close_from_history(_pos(), exchange, None, [])
        assert out and out["reason_inferred"] is inferred

    @pytest.mark.asyncio
    async def test_a_filled_stop_order_is_a_measurement(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        exchange = AsyncMock()
        exchange.fetch_closed_orders = AsyncMock(return_value=[
            {"id": "sl-1", "filled": 109.0, "status": "closed", "average": 1.19}])
        out = await ex._close_from_orders(
            _pos(sl_order_id="sl-1", tp_order_id="tp-1"), exchange, "DOT/USDT:USDT", [])
        assert out and out["reason_inferred"] is False

    def test_an_unmatched_close_side_fill_is_a_guess(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._hedge_mode = False
        pos = _pos(sl_order_id="sl-1", tp_order_id="tp-1")
        out, _why = ex._fill_by_close_side(
            pos, [{"side": "sell", "order": "o-9", "price": 1.2170,
                   "timestamp": None, "info": {}}], None)
        assert out and out["reason_inferred"] is True

    def test_our_stop_order_filling_is_a_measurement(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        pos = _pos(sl_order_id="sl-1", tp_order_id="tp-1")
        out, _why = ex._fill_by_order_id(
            pos, [{"order": "sl-1", "price": 1.19, "info": {"profit": "-3.3"}}])
        assert out and out["reason_inferred"] is False

    def test_a_stop_never_placed_is_not_matched_by_a_fill_with_no_order_id(self):
        """A position whose stop could not be placed has no stop id. A fill
        carrying no order id used to match that None and was booked as
        ``SL HIT (exchange)``, a measured reason, on a position with no stop."""
        ex = LiveExecutor.__new__(LiveExecutor)
        pos = _pos(sl_order_id=None, tp_order_id="tp-1")
        out, why = ex._fill_by_order_id(
            pos, [{"order": None, "price": 1.19, "info": {}}])
        assert out is None and why == "no fill carries the stop/target order id"

    def test_the_target_still_matches_when_the_stop_was_never_placed(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        pos = _pos(sl_order_id=None, tp_order_id="tp-1")
        out, _why = ex._fill_by_order_id(
            pos, [{"order": None, "price": 1.19, "info": {}},
                  {"order": "tp-1", "price": 1.28, "info": {"profit": "6.4"}}])
        assert out["reason"] == "TP HIT (exchange)"
        assert out["close_price"] == pytest.approx(1.28)


class TestTheMarginPercentIsNet:
    def test_the_dot_card(self):
        """$0.3182 net on $26.57 of margin is -1.20%; the move x 5 is -0.82%."""
        pnl, pct, fee = close_pnl_line(-0.3182, -0.164, 5, 0.10, margin_usd=26.57)
        assert pnl == "-$0.3182"
        assert pct == "-1.20% on margin after fees / -0.16% move, 5×"
        assert "-0.82%" not in pct

    def test_the_card_is_built_with_the_positions_margin(self, tmp_path):
        ex, pos = _closed(tmp_path, dict(GUESSED, pnl=-0.2182))
        msg = asyncio.run(ex._handle_already_closed_position(
            pos, bot_reason="sl_placement_failed", bot_closed=True))
        from bot.utils.leveraged_return import realized_margin_return_pct
        net = realized_margin_return_pct(pos.pnl_usd, 26.57)
        assert f"{net:+.2f}% on margin after fees" in msg, msg
        assert "margin /" not in msg.replace("on margin after fees /", "")

    def test_an_old_positional_call_fails_loudly(self):
        """The fifth positional argument used to be the commission; a call
        written for the old shape must raise, not read the fee as a margin."""
        with pytest.raises(TypeError):
            close_pnl_line(-0.2128, -0.5, -10.0, 20, 0.21)


def _card(path):
    """A priced 5x close through the bot's own close or the reconcile, with a
    limit entry placed 3h ago that filled 10 minutes ago."""
    from tests.test_an_unread_entry_is_an_unpriced_close import _close_fill, _executor
    now = datetime.now(UTC)
    fill = _close_fill("LONG", price=150.0, profit="100")
    # The borrowed fixture stamps its fill from ITS module's import-time clock
    # (`OPENED + 60s`), and this position opens three hours before the test
    # RUNS. In a 35-minute full run those two clocks are 35 minutes apart, the
    # fill predates the position, nothing matches, and the reconcile retries
    # instead of closing: a failure only a full run could produce, which the
    # flake filter then forgave. The fill is stamped from this card's clock.
    fill["timestamp"] = int((now - timedelta(minutes=1)).timestamp() * 1000)
    ex, _x = _executor(fills=[fill] if path == "reconcile" else ())
    pos = LivePosition(trade_id="T-5X", symbol="SOL/USDT:USDT", direction="LONG",
                       entry_price=140.0, quantity=10.0, cost_usd=280.0,
                       stop_loss=130.0, take_profit=160.0, leverage=5,
                       status="open", order_type="limit",
                       opened_at=now - timedelta(hours=3))
    setattr(pos, "filled_at", now - timedelta(minutes=10))
    ex._positions = {pos.trade_id: pos}
    if path == "reconcile":
        (msg,) = asyncio.run(ex.reconcile_positions())
    else:
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 150.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        ex._fetch_bitget_close_data = AsyncMock(return_value=None)
        msg = asyncio.run(ex.close_position(pos.trade_id, reason="TP"))
    return msg, pos


class TestEveryCloseCardPrintsTheNetReturn:
    @pytest.mark.parametrize("path", ["close", "reconcile"])
    def test_it_is_the_net_over_the_margin(self, path):
        from bot.utils.leveraged_return import realized_margin_return_pct
        msg, pos = _card(path)
        assert pos.pnl_usd is not None and pos.pnl_usd < 100.0
        net = realized_margin_return_pct(pos.pnl_usd, 280.0)
        assert f"{net:+.2f}% on margin after fees" in msg, msg
        assert f"{100.0 * 50.0 / 1400.0 * 5:+.2f}% on margin" not in msg, (
            "the price move times the leverage, which carries no fees")
