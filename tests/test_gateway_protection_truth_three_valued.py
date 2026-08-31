"""A stop nobody could read is not a stop that exists.

RC-2026-016. `_live_position_row` in `bot/web/user_gateway.py` computed::

    sl = float(getattr(pos, "stop_loss", 0) or 0)
    unprotected = (not sl_protected and sl > 0) or bool(getattr(pos, "unprotected", False))

`sl` is `0.0` whenever nobody read a stop -- an absent field, a venue that
answered nothing, an adoption whose order-book read raised. And `sl > 0` is
then False, so the whole expression collapses to False: **unprotected: false**,
which the operator's dashboard reads as "this position has a stop".

The same zero flowed into `sl_dist_pct: 0.0` -- "the stop sits 0% away" -- and
into `sl_order: "manual"`, meaning "there is a stop and you placed it by hand".
Three separate reassurances, all manufactured from a read that never happened.

Downstream, `app/public/js/dashboard.js` chips on exactly those fields, so the
row rendered **🤖 bot-managed** for a position whose protection is unknown.

This is the audit's central rule on the most expensive surface it has: the
operator consults this list *because they do not know what is out there*.
There are three states, not two -- a price, no stop, and nobody looked.
"""
import pytest

pytest.importorskip("aiohttp")

from bot.web.user_gateway import _live_position_row  # noqa: E402


class _Pos:
    """A LivePosition with only the attributes the row reads."""

    def __init__(self, **kw):
        self.symbol = kw.get("symbol", "BTC/USDT:USDT")
        self.direction = kw.get("direction", "long")
        self.entry_price = kw.get("entry_price", 100.0)
        self.stop_loss = kw.get("stop_loss", 0)
        self.take_profit = kw.get("take_profit", 0)
        self.quantity = kw.get("quantity", 1.0)
        self.cost_usd = kw.get("cost_usd", 100.0)
        self.leverage = kw.get("leverage", 1.0)
        self.sl_order_id = kw.get("sl_order_id")
        self.tp_order_id = kw.get("tp_order_id")
        self.strategy_type = ""
        self.opened_at = None
        if "unprotected" in kw:
            self.unprotected = kw["unprotected"]


def test_a_stop_nobody_read_is_not_reported_as_protected():
    """The defect, stated as the operator experiences it."""
    row = _live_position_row(_Pos(stop_loss=0))
    assert row["unprotected"] is not False, (
        "a position whose stop could not be read reports unprotected=False, "
        "which the dashboard renders as a position that has a stop"
    )


def test_an_unread_stop_does_not_claim_a_distance():
    """`sl_dist_pct: 0.0` says the stop sits exactly at entry. Nobody measured it."""
    row = _live_position_row(_Pos(stop_loss=0))
    assert row["sl_dist_pct"] is None, (
        f"sl_dist_pct is {row['sl_dist_pct']!r} for an unread stop -- 0.0 is a "
        "real distance (a stop AT entry) and must not double as 'unknown'"
    )


def test_an_unread_stop_does_not_claim_a_price():
    row = _live_position_row(_Pos(stop_loss=0))
    assert row["stop_loss"] is None, (
        f"stop_loss is {row['stop_loss']!r}; 0 is a price, not an absence"
    )


def test_the_order_state_has_a_third_value():
    """exchange / manual / unknown. 'manual' asserts a stop was placed by hand."""
    assert _live_position_row(_Pos(stop_loss=0))["sl_order"] == "unknown"
    assert _live_position_row(_Pos(stop_loss=95.0))["sl_order"] == "manual"
    assert _live_position_row(
        _Pos(stop_loss=95.0, sl_order_id="x1")
    )["sl_order"] == "exchange"


def test_a_real_stop_still_reports_normally():
    """The honest path must not eat the measured one.

    A stop that IS known and NOT on the exchange is the original alarm this
    payload was built for -- it must keep firing.
    """
    row = _live_position_row(_Pos(stop_loss=95.0))
    assert row["unprotected"] is True
    assert row["sl_order"] == "manual"
    assert row["stop_loss"] == 95.0
    assert row["sl_dist_pct"] == 5.0


def test_a_stop_on_the_exchange_is_protected():
    row = _live_position_row(_Pos(stop_loss=95.0, sl_order_id="abc"))
    assert row["unprotected"] is False
    assert row["sl_protected"] is True


def test_an_explicitly_marked_position_stays_marked():
    """`pos.unprotected` is set by the executor; it must survive the rewrite."""
    row = _live_position_row(_Pos(stop_loss=95.0, sl_order_id="abc", unprotected=True))
    assert row["unprotected"] is True


def test_the_unknown_case_is_distinguishable_from_the_unprotected_one():
    """Downstream must be able to tell 'no stop' from 'no reading'.

    Without a dedicated field the UI cannot: both are "not protected", and the
    chip that fires for one would fire for the other, which turns a real alarm
    into noise or hides it.
    """
    unread = _live_position_row(_Pos(stop_loss=0))
    known_naked = _live_position_row(_Pos(stop_loss=95.0))
    assert unread["sl_unknown"] is True
    assert known_naked["sl_unknown"] is False


def test_zero_is_not_special_cased_into_a_reading_for_take_profit_either():
    """The same expression built tp_dist_pct. Same rule."""
    row = _live_position_row(_Pos(take_profit=0))
    assert row["tp_dist_pct"] is None
    assert row["take_profit"] is None
    assert row["tp_order"] == "unknown"
