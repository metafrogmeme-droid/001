"""A close nobody could price is not a $0.00 break-even, and not a win.

`LivePosition.pnl_usd` is `Optional[float]`, and `live_executor` writes `None`
deliberately -- twice, at two different close paths::

    pos.pnl_usd = None if net_pnl is None else round(net_pnl, 4)

That is the honest half, and it was already there. So is the far end: the
website's `winStats` counts an unpriced close as its own fourth outcome and
reports `rate: null` rather than scoring it, with a comment naming
`losses = total - wins` as the shape it refuses. `trades.pnl` is
`DECIMAL(14,2)` -- nullable -- and `sync.js` inserts `t.pnl` with no `|| 0`.

Three lines in the middle threw all of that away, and each one was a different
spelling of the same coercion:

    bot/core/engine.py       `d["pnl"] = pos.pnl_usd or 0`      (the producer)
    bot/utils/website_sync.py `float(_attr(t, "pnl", 0))`       (the wire, x2)
    bot/core/engine.py       `(t.pnl_usd or 0) < 0`             (the CONTROL)

The third is the expensive one. It is the filter behind
`cooldown_after_loss_seconds` -- the pause that stops the bot re-entering
straight after a live loss -- and `0 < 0` is False, so the one close the bot
understood least was the one it declined to slow down for. CLAUDE.md's table
lists that exact shape and what it silently asserts: **unreadable LOST** for
`(x or 0) > 0`, and here, inverted, unreadable *did not lose*.

All three were found by `scripts/honesty_gate.py` on its first run, and the
wire was found only after the gate learned that `_attr(t, "pnl", 0)` is the
same shape as `getattr(o, "pnl", 0)` -- a project-local accessor it had no
name for. A gate that knows two spellings finds the shape in two places.

`test_unpriced_close_is_not_break_even.py` is the same rule one layer down:
there `close_pnl_line` stopped printing `-0.00% margin` for a close it could
not price, and pinned `pos.pnl_usd = None if net_pnl is None else ...` as the
producer. This file is what happens to that None afterwards -- the control
that reads it and the two wires that carry it -- which is the corollary that
file did not take: **ask which OTHER surface makes the same claim.**
"""

import types
from datetime import datetime, timedelta, timezone

import bot.utils.website_sync as ws
from bot.core.engine import COOLDOWN_LOOKBACK_SECONDS, RuneClawEngine, loss_cooldown_reason

UTC = timezone.utc
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _closed(symbol, pnl, *, ago_s=10, tz=UTC):
    at = NOW - timedelta(seconds=ago_s)
    return types.SimpleNamespace(
        symbol=symbol, pnl_usd=pnl,
        closed_at=at if tz else at.replace(tzinfo=None))


class TestTheCooldownReadsThreeOutcomes:
    def test_a_priced_loss_still_cools_down_and_names_itself(self):
        reason = loss_cooldown_reason([_closed("ENA/USDT", -4.2)], NOW, 300)
        assert reason and "ENA/USDT" in reason and "-4.2" in reason
        assert "cooling down 300s" in reason

    def test_an_unpriced_close_cools_down_too(self):
        reason = loss_cooldown_reason([_closed("RAVE/USDT", None)], NOW, 300)
        assert reason is not None, (
            "`(pnl or 0) < 0` is False for an unreadable close, so the bot "
            "went straight back to sizing the next entry after the one trade "
            "it could not evaluate")

    def test_the_unpriced_reason_claims_no_number(self):
        reason = loss_cooldown_reason([_closed("RAVE/USDT", None)], NOW, 300)
        assert "could not be priced" in reason
        assert "PnL=$" not in reason, (
            "a dollar figure on a close nobody read is the fabrication one "
            "layer up from the one being fixed")
        assert "RAVE/USDT" in reason

    def test_a_win_alone_does_not_cool_down(self):
        assert loss_cooldown_reason([_closed("BTC/USDT", 12.0)], NOW, 300) is None

    def test_break_even_is_not_a_loss(self):
        # 0.0 is falsy and 0.0 is a real, measured, flat close. `is None` is
        # the test; truthiness is not.
        assert loss_cooldown_reason([_closed("BTC/USDT", 0.0)], NOW, 300) is None

    def test_an_empty_ledger_does_not_cool_down(self):
        assert loss_cooldown_reason([], NOW, 300) is None
        assert loss_cooldown_reason(None, NOW, 300) is None

    def test_a_real_loss_is_named_ahead_of_an_unpriced_one(self):
        # Both cool down; the priced one is the more informative message and
        # the one an operator can act on.
        reason = loss_cooldown_reason(
            [_closed("RAVE/USDT", None), _closed("ENA/USDT", -4.2)], NOW, 300)
        assert "ENA/USDT" in reason and "could not be priced" not in reason

    def test_the_worst_of_several_losses_is_the_one_named(self):
        reason = loss_cooldown_reason(
            [_closed("A/USDT", -1.0), _closed("B/USDT", -9.0),
             _closed("C/USDT", -3.0)], NOW, 300)
        assert "B/USDT" in reason

    def test_an_unpriced_loss_does_not_win_the_min_over_priced_ones(self):
        # `min(..., key=lambda t: t.pnl_usd or 0)` would rank None as 0, which
        # is above every loss — so the worst-loss line could name a trade that
        # was never scored at all.
        reason = loss_cooldown_reason(
            [_closed("UNPRICED", None), _closed("REAL", -9.0)], NOW, 300)
        assert "REAL" in reason and "UNPRICED" not in reason

    def test_every_unpriced_symbol_is_listed(self):
        reason = loss_cooldown_reason(
            [_closed("A/USDT", None), _closed("B/USDT", None)], NOW, 300)
        assert "A/USDT" in reason and "B/USDT" in reason

    def test_a_close_outside_the_window_is_not_this_tick(self):
        old = _closed("ENA/USDT", -4.2, ago_s=COOLDOWN_LOOKBACK_SECONDS + 1)
        assert loss_cooldown_reason([old], NOW, 300) is None
        stale_unpriced = _closed("ENA/USDT", None,
                                 ago_s=COOLDOWN_LOOKBACK_SECONDS + 1)
        assert loss_cooldown_reason([stale_unpriced], NOW, 300) is None

    def test_a_naive_timestamp_is_read_as_utc_not_dropped(self):
        naive = _closed("ENA/USDT", -4.2, tz=None)
        assert loss_cooldown_reason([naive], NOW, 300) is not None

    def test_a_trade_that_never_closed_is_not_in_the_window(self):
        never = types.SimpleNamespace(symbol="X/USDT", pnl_usd=None,
                                      closed_at=None)
        assert loss_cooldown_reason([never], NOW, 300) is None


class TestTheDashboardPayloadCarriesTheUnknown:
    """`_closed_dict` feeds the live user's dashboard, one field at a time."""

    @staticmethod
    def _capture(monkeypatch, closed_pos):
        captured = {}
        monkeypatch.setattr(
            ws, "sync_in_background",
            lambda user_id, equity, positions, closed_trades:
                captured.update(closed_trades=closed_trades))
        executor = types.SimpleNamespace(
            open_positions=[], closed_positions=[closed_pos], user_id=7)
        stub = types.SimpleNamespace(
            live_executor=executor,
            resolve_display_equity_sync=lambda: (100.0, "live"))
        RuneClawEngine._sync_live_state_to_website(stub)
        return captured["closed_trades"][0]

    @staticmethod
    def _pos(pnl, close_price):
        return types.SimpleNamespace(
            trade_id="TI-1", symbol="RAVE/USDT:USDT", direction="LONG",
            entry_price=100.0, quantity=1.5, commission=0.3,
            signal_type="momentum", stop_loss=95.0, take_profit=110.0,
            opened_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
            closed_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
            close_price=close_price, pnl_usd=pnl, status="closed")

    def test_an_unpriced_close_travels_as_none(self, monkeypatch):
        row = self._capture(monkeypatch, self._pos(None, None))
        assert row["pnl"] is None, (
            "the dashboard shows a live user $0.00 on a trade nobody priced, "
            "and the web reader that counts unpriced closes never sees one")
        assert row["exit_price"] is None

    def test_a_measured_break_even_still_travels_as_zero(self, monkeypatch):
        # The distinction the whole change is about: 0.0 is a reading.
        row = self._capture(monkeypatch, self._pos(0.0, 63.6))
        assert row["pnl"] == 0.0 and row["pnl"] is not None
        assert row["exit_price"] == 63.6

    def test_a_priced_loss_is_unchanged(self, monkeypatch):
        row = self._capture(monkeypatch, self._pos(-4.25, 63.6))
        assert row["pnl"] == -4.25


class TestBothWiresMakeTheSameClaim:
    """`sync_portfolio` and `sync_trade_event` are two paths to one column."""

    @staticmethod
    def _closed_trade(pnl, exit_price):
        return types.SimpleNamespace(
            asset="RAVE/USDT:USDT", direction="LONG", entry_price=100.0,
            quantity=1.5, exit_price=exit_price, pnl=pnl, commission=0.3,
            pattern="momentum", opened_at="2026-09-08T11:00:00+00:00",
            closed_at="2026-09-08T12:00:00+00:00", venue="bitget",
            close_reason="tp_hit", trade_id="TI-1")

    def _posted(self, monkeypatch, fn, *args, **kw):
        sent = {}
        monkeypatch.setattr(ws, "_post",
                            lambda path, body, **k: sent.update(body=body)
                            or {"ok": True})
        fn(*args, **kw)
        return sent["body"]

    def test_the_bulk_wire_sends_none(self, monkeypatch):
        body = self._posted(monkeypatch, ws.sync_portfolio, 7, 100.0, [],
                            [self._closed_trade(None, None)])
        assert body["closed_trades"], "the countable filter dropped the row"
        assert body["closed_trades"][0]["pnl"] is None
        assert body["closed_trades"][0]["exit_price"] is None

    def test_the_single_event_wire_sends_none(self, monkeypatch):
        body = self._posted(monkeypatch, ws.sync_trade_event, 7, "close",
                            self._closed_trade(None, None), 100.0)
        assert body["trade"]["pnl"] is None
        assert body["trade"]["exit_price"] is None

    def test_both_wires_still_send_a_measured_zero(self, monkeypatch):
        bulk = self._posted(monkeypatch, ws.sync_portfolio, 7, 100.0, [],
                            [self._closed_trade(0.0, 63.6)])
        assert bulk["closed_trades"][0]["pnl"] == 0.0
        single = self._posted(monkeypatch, ws.sync_trade_event, 7, "close",
                              self._closed_trade(0.0, 63.6), 100.0)
        assert single["trade"]["pnl"] == 0.0

    def test_an_unusable_value_is_unknown_rather_than_an_exception(self):
        # `float("")` raises, and one unreadable field is not a reason to drop
        # the other nine off the sync.
        assert ws._opt_num(types.SimpleNamespace(pnl=""), "pnl") is None
        assert ws._opt_num(types.SimpleNamespace(pnl="x"), "pnl") is None
        assert ws._opt_num(types.SimpleNamespace(pnl="1.5"), "pnl") == 1.5
        assert ws._opt_num(types.SimpleNamespace(), "pnl") is None
        assert ws._opt_num({"pnl": None}, "pnl") is None
        assert ws._opt_num({"pnl": -2.5}, "pnl") == -2.5
