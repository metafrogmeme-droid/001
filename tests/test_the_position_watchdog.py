"""The position watchdog reads what the venue holds and says what is missing.

`bot/position_watchdog.py` runs one pass from a timer: every open position
must have a stop-loss and a take-profit resting, and today's realized result
plus the open losses must be inside the daily stop. What it finds goes to the
operator's alert chat, once per window per finding.

Everything here drives ccxt's real Bitget client, built by the venue adapter
the bot uses, with the transport replaced. Nothing reaches a venue. The
transport fails the test on any request that is not a GET, and on any path it
was not told to expect.

The other half, that the watchdog cannot place, amend or cancel an order, is
`tests/test_the_position_watchdog_only_reads.py`.
"""
from __future__ import annotations

import asyncio
import calendar
import copy
import json
import logging
from urllib.parse import parse_qsl, urlparse

import ccxt.async_support as ccxt_async
import pytest

import bot.position_watchdog as w
from bot.config import CONFIG
from tests.test_a_classic_bitget_account_is_read_through_v2 import (
    _CFG,
    BITGET,
    MARKET,
    REFUSAL,
    _refuse,
)

NOW = float(calendar.timegm((2026, 11, 20, 12, 0, 0)))
TODAY = "2026-11-20"
AN_HOUR_AGO_MS = str(int((NOW - 3600) * 1000))
SYMBOL = "INJ/USDT:USDT"
#: A second market, so an order on it can be told from one on the first.
OTHER_MARKET = {**MARKET, "id": "SOLUSDT", "symbol": "SOL/USDT:USDT", "base": "SOL",
                "baseId": "SOL"}

_PROFIT_LOSS = {"pos_loss", "pos_profit", "loss_plan", "profit_plan", "moving_plan"}


def position(*, side="long", total="2", entry="10.0", mark="10.0", upl="0", opened=AN_HOUR_AGO_MS):
    """An open position as ``/api/v2/mix/position/all-position`` writes it."""
    return {"marginCoin": "USDT", "symbol": "INJUSDT", "holdSide": side,
            "openDelegateSize": "0", "marginSize": "4.0", "available": total,
            "locked": "0", "total": total, "leverage": "5", "achievedProfits": "0",
            "openPriceAvg": entry, "marginMode": "isolated", "posMode": "hedge_mode",
            "unrealizedPL": upl, "liquidationPrice": "0", "keepMarginRate": "0.01",
            "markPrice": mark, "breakEvenPrice": entry, "marginRatio": "0.01",
            "cTime": opened, "uTime": opened}


def plan(order_id, trigger, *, plan_type="normal_plan", side="sell", pos_side="long",
         trade_side="close"):
    """A resting plan order as ``/api/v2/mix/order/orders-plan-pending`` writes it."""
    row = {"planType": plan_type, "symbol": "INJUSDT", "size": "2", "orderId": order_id,
           "clientOid": f"c{order_id}", "price": "", "callbackRatio": "",
           "triggerPrice": trigger, "triggerType": "mark_price", "planStatus": "live",
           "side": side, "posSide": pos_side, "marginCoin": "USDT",
           "marginMode": "isolated", "enterPointSource": "API", "posMode": "hedge_mode",
           "orderType": "market", "stopSurplusTriggerPrice": "",
           "stopSurplusExecutePrice": "", "stopSurplusTriggerType": "fill_price",
           "stopLossTriggerPrice": "", "stopLossExecutePrice": "",
           "stopLossTriggerType": "fill_price", "cTime": "1", "uTime": "1"}
    if trade_side is not None:
        row["tradeSide"] = trade_side
    return row


STOP = plan("1", "9.0")
TARGET = plan("2", "12.0")


class Venue:
    """One Bitget account behind a replaced transport.

    Classic unless told otherwise: every ``/api/v3`` path is refused with
    40084 and ``/api/v2/mix`` answers. A request that is not a GET fails the
    test, as does a path nobody planned. ``down`` names paths that time out.
    """

    def __init__(self, *, positions=(), plans=(), equity="1000", classic=True,
                 unified_positions=(), unified_orders=(), down=()):
        self.positions, self.plans, self.equity = list(positions), list(plans), equity
        self.classic = classic
        self.unified_positions, self.unified_orders = list(unified_positions), list(unified_orders)
        self.down = set(down)
        self.calls: list[tuple[str, str]] = []
        self.closed = 0

    def paths(self):
        return [path for _, path in self.calls]

    def answer(self, ex, url, method):
        parsed = urlparse(url)
        path, query = parsed.path, dict(parse_qsl(parsed.query))
        self.calls.append((method, path))
        if method != "GET":
            raise AssertionError(f"a write reached the transport: {method} {path}")
        if path in self.down:
            raise ccxt_async.RequestTimeout(f"bitget GET {url}")
        ok = {"code": "00000", "msg": "success", "requestTime": 1}
        if path.startswith("/api/v3/"):
            if self.classic:
                _refuse(ex, url, REFUSAL)
            if path == "/api/v3/position/current-position":
                return {**ok, "data": {"list": copy.deepcopy(self.unified_positions)}}
            if path == "/api/v3/trade/unfilled-strategy-orders":
                # The strategy listing's `data` is the list itself, which is
                # how ccxt reads it.
                rows = self.unified_orders if query.get("planType") == "profit_loss" else []
                return {**ok, "data": copy.deepcopy(rows)}
            if path == "/api/v3/account/assets":
                return {**ok, "data": {"accountEquity": self.equity, "usdtEquity": self.equity,
                                       "assets": [{"coin": "USDT", "equity": self.equity,
                                                   "balance": self.equity,
                                                   "available": self.equity, "locked": "0"}]}}
            raise AssertionError(f"unplanned unified path {path}")
        if not self.classic:
            raise AssertionError(f"a unified account was asked the classic API: {path}")
        if path == "/api/v2/mix/position/all-position":
            return {**ok, "data": copy.deepcopy(self.positions)}
        if path == "/api/v2/mix/order/orders-plan-pending":
            wanted = query.get("planType")
            rows = [r for r in self.plans
                    if (r["planType"] in _PROFIT_LOSS) == (wanted == "profit_loss")]
            return {**ok, "data": {"entrustedList": copy.deepcopy(rows), "endId": ""}}
        if path == "/api/v2/mix/account/accounts":
            if self.equity is None:
                return {**ok, "data": []}
            return {**ok, "data": [{"marginCoin": "USDT", "locked": "0",
                                    "available": self.equity, "accountEquity": self.equity,
                                    "usdtEquity": self.equity, "unrealizedPL": "0"}]}
        raise AssertionError(f"unplanned classic path {path}")

    def client(self, key="watchdog-key"):
        ex = BITGET.create_exchange(_CFG, {"api_key": key, "api_secret": "s", "passphrase": "p"})
        ex.set_markets([ex.safe_market_structure(dict(m)) for m in (MARKET, OTHER_MARKET)])

        async def fetch(url, method="GET", headers=None, body=None):
            return self.answer(ex, url, method)

        async def load_markets(reload=False, params={}):
            return ex.markets

        async def close():
            self.closed += 1

        ex.fetch, ex.load_markets, ex.close = fetch, load_markets, close
        return ex


def bot_record(folder, *, pnl=0.0, day=TODAY):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "combined_state.json").write_text(
        json.dumps({"portfolio": {}, "risk": {"live_daily_pnl": pnl, "live_daily_day": day}}),
        encoding="utf-8")
    return folder


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(w.ENABLED_ENV, "true")
    monkeypatch.delenv("POSITION_WATCHDOG_REALERT_MIN", raising=False)
    monkeypatch.delenv("POSITION_WATCHDOG_GRACE_S", raising=False)


@pytest.fixture
def box(tmp_path, on):
    """A pass runner: ``box(venue)`` returns ``(status, messages)``."""
    state = tmp_path / "watchdog_state.json"
    record_dir = bot_record(tmp_path / "bot")
    sent: list[str] = []

    def run(venue, *, now=NOW, publisher=None, **extra):
        status = asyncio.run(w.run_once(
            exchange_factory=venue.client, publisher=publisher or sent.append, now=now,
            state_file=state, bot_state_dir=extra.pop("bot_state_dir", record_dir), **extra))
        return status, sent

    run.state, run.record_dir, run.sent = state, record_dir, sent
    return run


def read(venue):
    """The pass's reading of an account, through the guarded client."""
    async def go():
        client = w.read_only(venue.client())
        try:
            return await w.read_account(client, BITGET)
        finally:
            await client.close()
    return asyncio.run(go())


def held(venue):
    account = read(venue)
    assert account.unread == {}, account.unread
    (one,) = account.positions
    return w.protection(one, account.plans)


class TestAPositionsLegs:
    def test_a_stop_below_the_mark_and_a_target_above_it_are_both_found(self):
        venue = Venue(positions=[position()], plans=[STOP, TARGET])
        assert held(venue) == {"sl": w.PRESENT, "tp": w.PRESENT}
        assert {method for method, _ in venue.calls} == {"GET"}

    @pytest.mark.parametrize("plans,expected", [
        ([STOP], {"sl": w.PRESENT, "tp": w.ABSENT}),
        ([TARGET], {"sl": w.ABSENT, "tp": w.PRESENT}),
        ([], {"sl": w.ABSENT, "tp": w.ABSENT}),
    ])
    def test_a_missing_leg_is_absent_and_the_other_is_not(self, plans, expected):
        assert held(Venue(positions=[position()], plans=plans)) == expected

    def test_a_position_tp_sl_names_its_own_kind(self):
        """The plan type alone says which leg it is: these rows state neither
        that they close nor that they open, and each sits on the side of the
        mark the OTHER kind would."""
        venue = Venue(positions=[position()], plans=[
            plan("3", "12.0", plan_type="pos_loss", trade_side=None),
            plan("4", "9.0", plan_type="pos_profit", trade_side=None)])
        assert held(venue) == {"sl": w.PRESENT, "tp": w.PRESENT}
        for only, expected in (("pos_loss", {"sl": w.PRESENT, "tp": w.ABSENT}),
                               ("loss_plan", {"sl": w.PRESENT, "tp": w.ABSENT}),
                               ("pos_profit", {"sl": w.ABSENT, "tp": w.PRESENT}),
                               ("profit_plan", {"sl": w.ABSENT, "tp": w.PRESENT})):
            one = Venue(positions=[position()],
                        plans=[plan("5", "10.5", plan_type=only, trade_side=None)])
            assert held(one) == expected, only
        # A trailing plan is a stop, wherever it sits.
        trailing = Venue(positions=[position()], plans=[plan("5", "12.0", plan_type="moving_plan")])
        assert held(trailing) == {"sl": w.PRESENT, "tp": w.ABSENT}

    def test_an_entry_trigger_protects_nothing(self):
        """A resting order that OPENS, on this symbol and this side, at a price
        a stop would sit at. It is not a stop."""
        entry = plan("6", "9.0", side="buy", trade_side="open")
        assert held(Venue(positions=[position()], plans=[entry])) == {
            "sl": w.ABSENT, "tp": w.ABSENT}

    def test_a_stop_trailed_past_the_entry_is_still_a_stop(self):
        """Entry 10, mark 12, stop at 11, target at 13. Read against the entry,
        the stop at 11 would be a second target and the position would be
        reported with no stop."""
        venue = Venue(positions=[position(entry="10.0", mark="12.0")],
                      plans=[plan("1", "11.0"), plan("2", "13.0")])
        assert held(venue) == {"sl": w.PRESENT, "tp": w.PRESENT}

    def test_a_short_is_read_the_other_way_up(self):
        short = position(side="short")
        stop = plan("1", "11.0", side="buy", pos_side="short")
        target = plan("2", "8.0", side="buy", pos_side="short")
        assert held(Venue(positions=[short], plans=[stop, target])) == {
            "sl": w.PRESENT, "tp": w.PRESENT}
        assert held(Venue(positions=[short], plans=[stop])) == {"sl": w.PRESENT, "tp": w.ABSENT}
        assert held(Venue(positions=[short], plans=[target])) == {"sl": w.ABSENT, "tp": w.PRESENT}

    def test_the_other_sides_orders_are_not_this_positions(self):
        theirs = [plan("1", "9.0", side="buy", pos_side="short"),
                  plan("2", "12.0", side="buy", pos_side="short")]
        assert held(Venue(positions=[position()], plans=theirs)) == {
            "sl": w.ABSENT, "tp": w.ABSENT}

    def test_another_markets_orders_are_not_this_positions(self):
        elsewhere = [{**STOP, "symbol": "SOLUSDT"}, {**TARGET, "symbol": "SOLUSDT"}]
        assert held(Venue(positions=[position()], plans=elsewhere)) == {
            "sl": w.ABSENT, "tp": w.ABSENT}

    def test_a_row_of_size_zero_is_not_an_open_position(self):
        """The venue may list a market with nothing held on it. That is not a
        position without a stop."""
        account = read(Venue(positions=[position(total="0"), position()], plans=[STOP, TARGET]))
        assert [p.contracts for p in account.positions] == [2.0]
        assert read(Venue(positions=[position(total="0")])).positions == []

    def test_an_order_nobody_can_tell_apart_is_unread_not_absent(self):
        """A trigger that says neither that it closes nor that it opens may be
        the stop. The position's stop is then unread, in those words."""
        unstated = plan("7", "9.0", trade_side=None)
        assert held(Venue(positions=[position()], plans=[unstated])) == {
            "sl": w.UNREAD, "tp": w.UNREAD}
        # A leg that IS found stays found beside it.
        assert held(Venue(positions=[position()], plans=[unstated, TARGET])) == {
            "sl": w.UNREAD, "tp": w.PRESENT}

    def test_a_listing_that_fails_is_unread_and_says_why(self):
        venue = Venue(positions=[position()], plans=[STOP, TARGET],
                      down=["/api/v2/mix/order/orders-plan-pending"])
        account = read(venue)
        assert account.plans is None
        assert account.unread == {"plan orders": "RequestTimeout"}
        (one,) = account.positions
        assert w.protection(one, account.plans) == {"sl": w.UNREAD, "tp": w.UNREAD}

    def test_a_listing_a_full_page_long_may_be_cut_so_it_is_unread(self):
        """One query answers at most a page. A page-long answer may be missing
        the very order that is this position's stop."""
        elsewhere = [{**plan(str(100 + i), "9.0"), "symbol": "SOLUSDT"}
                     for i in range(w.LISTING_PAGE)]
        full = read(Venue(positions=[position()], plans=elsewhere))
        assert full.plans is None
        assert full.unread == {"plan orders": "a listing of 100 rows may be cut"}
        (one,) = full.positions
        assert w.protection(one, full.plans) == {"sl": w.UNREAD, "tp": w.UNREAD}
        # One row short of a page is a whole listing.
        short = read(Venue(positions=[position()], plans=elsewhere[1:]))
        assert short.unread == {} and len(short.plans) == w.LISTING_PAGE - 1
        (one,) = short.positions
        assert w.protection(one, short.plans) == {"sl": w.ABSENT, "tp": w.ABSENT}

    def test_a_unified_account_is_read_through_its_own_api(self):
        """The position row carries its stop; a strategy order states the target."""
        row = {"category": "USDT-FUTURES", "symbol": "INJUSDT", "marginCoin": "USDT",
               "holdMode": "hedge_mode", "posSide": "long", "marginMode": "crossed",
               "available": "2", "frozen": "0", "total": "2", "leverage": "5",
               "avgPrice": "10.0", "unrealisedPnl": "0", "markPrice": "10.0",
               "createdTime": AN_HOUR_AGO_MS, "updatedTime": AN_HOUR_AGO_MS,
               "stopLoss": "9.0", "stopLossId": "77", "takeProfit": "", "takeProfitId": ""}
        order = {"orderId": "8", "clientOid": "c8", "symbol": "INJUSDT",
                 "category": "USDT-FUTURES", "qty": "2", "posSide": "long",
                 "tpTriggerBy": "market", "slTriggerBy": "mark", "takeProfit": "12.0",
                 "stopLoss": "", "tpOrderType": "market", "slOrderType": "market",
                 "tpLimitPrice": "", "slLimitPrice": "", "createdTime": "1", "updatedTime": "1"}
        with_target = Venue(classic=False, unified_positions=[row], unified_orders=[order],
                            equity="147.46")
        assert held(with_target) == {"sl": w.PRESENT, "tp": w.PRESENT}
        assert all(path.startswith("/api/v3/") for path in with_target.paths())
        assert read(with_target).equity == 147.46
        without = Venue(classic=False, unified_positions=[row], equity="147.46")
        assert held(without) == {"sl": w.PRESENT, "tp": w.ABSENT}
        # The row's own target counts as the row's own stop does, with no
        # order resting beside it.
        both_on_the_row = {**row, "takeProfit": "12.0", "takeProfitId": "78"}
        assert held(Venue(classic=False, unified_positions=[both_on_the_row],
                          equity="147.46")) == {"sl": w.PRESENT, "tp": w.PRESENT}

    def test_the_target_reading_has_the_stops_three_values(self):
        assert w.take_profit_attached({"takeProfit": "12.0"}) is True
        assert w.take_profit_attached({"takeProfit": "", "takeProfitId": "9"}) is True
        assert w.take_profit_attached({"takeProfit": "", "takeProfitId": ""}) is False
        assert w.take_profit_attached({"takeProfit": "0"}) is False
        assert w.take_profit_attached({"stopLoss": "9"}) is None
        assert w.take_profit_attached(None) is None


class TestThePassReportsWhatIsMissing:
    def test_a_protected_position_sends_nothing(self, box):
        venue = Venue(positions=[position()], plans=[STOP, TARGET])
        status, sent = box(venue)
        assert (status, sent) == (w.EXIT_OK, [])
        assert venue.closed == 1

    def test_a_position_with_no_stop_is_named(self, box):
        status, sent = box(Venue(positions=[position()], plans=[TARGET]))
        assert status == w.EXIT_OK
        (text,) = sent
        assert f"{SYMBOL} long: no stop-loss is resting." in text
        assert "take-profit" not in text
        assert text.endswith("The watchdog only reads. It changed nothing.")

    def test_a_position_with_no_target_is_named(self, box):
        _, (text,) = box(Venue(positions=[position()], plans=[STOP]))
        assert f"{SYMBOL} long: no take-profit is resting." in text
        assert "stop-loss" not in text

    def test_unread_legs_are_reported_in_their_own_words(self, box):
        venue = Venue(positions=[position()], plans=[STOP, TARGET],
                      down=["/api/v2/mix/order/orders-plan-pending"])
        _, (text,) = box(venue)
        assert (f"{SYMBOL} long: stop-loss not confirmed, the plan orders could not be read "
                "(RequestTimeout).") in text
        assert "no stop-loss is resting" not in text

    def test_positions_that_cannot_be_read_are_a_finding_not_an_all_clear(self, box):
        venue = Venue(positions=[position()], down=["/api/v2/mix/position/all-position"])
        _, (text,) = box(venue)
        assert "Open positions could not be read (RequestTimeout)." in text

    def test_a_position_seconds_old_is_given_its_grace(self, box):
        """The bot places its stops after the entry. A pass that lands between
        the two must not report a stop that is seconds away."""
        young = position(opened=str(int((NOW - 30) * 1000)))
        assert box(Venue(positions=[young]))[1] == []
        assert len(box(Venue(positions=[young]), now=NOW + 300)[1]) == 1

    def test_the_grace_can_be_switched_off(self, box, monkeypatch):
        monkeypatch.setenv("POSITION_WATCHDOG_GRACE_S", "0")
        young = position(opened=str(int((NOW - 30) * 1000)))
        assert len(box(Venue(positions=[young]))[1]) == 1


class TestTheDailyStop:
    """Equity 1000 and the configured stop, in percent of it."""

    def limit(self):
        return 1000.0 * float(CONFIG.risk.max_daily_loss_pct) / 100.0

    def venue(self, *upls):
        return Venue(plans=[STOP, TARGET],
                     positions=[position(upl=str(u)) for u in upls])

    def test_realized_plus_open_losses_past_the_stop_is_reported(self, box):
        limit = self.limit()
        bot_record(box.record_dir, pnl=-0.6 * limit)
        _, (text,) = box(self.venue(-0.5 * limit))
        assert "Daily stop: realized" in text and "at or past the" in text
        assert f"{-0.6 * limit:+.2f}" in text and f"{-0.5 * limit:+.2f}" in text
        assert "1000.00" in text

    def test_inside_the_stop_nothing_is_sent(self, box):
        limit = self.limit()
        bot_record(box.record_dir, pnl=-0.6 * limit)
        assert box(self.venue(-0.3 * limit))[1] == []

    def test_the_stop_itself_is_a_breach(self):
        account = w.Account(positions=[], plans=[], equity=1000.0, unread={})
        at = w.daily_stop(account, -50.0, "b", stop_pct=5.0, floor_usd=0.0)
        under = w.daily_stop(account, -49.99, "b", stop_pct=5.0, floor_usd=0.0)
        assert (at.state, under.state) == ("breached", "within")

    def test_an_open_winner_does_not_pay_for_an_open_loser(self, box):
        """Open losses are the losing positions' losses. A winner beside them
        is not realized and offsets nothing."""
        limit = self.limit()
        bot_record(box.record_dir, pnl=-0.6 * limit)
        _, sent = box(self.venue(-0.5 * limit, 10 * limit))
        assert len(sent) == 1 and "Daily stop: realized" in sent[0]

    def test_a_realized_gain_does_offset_open_losses(self, box):
        limit = self.limit()
        bot_record(box.record_dir, pnl=0.8 * limit)
        assert box(self.venue(-1.1 * limit))[1] == []

    def test_the_dollar_floor_is_the_breakers_own(self):
        account = w.Account(positions=[], plans=[], equity=100.0, unread={})
        assert w.daily_stop(account, -6.0, "b", stop_pct=5.0, floor_usd=0.0).state == "breached"
        assert w.daily_stop(account, -6.0, "b", stop_pct=5.0, floor_usd=10.0).state == "within"

    def test_a_record_from_another_day_is_no_loss_today_and_says_so(self, box, tmp_path):
        bot_record(box.record_dir, pnl=-999.0, day="2026-11-19")
        assert w.realized_today(TODAY, box.record_dir) == (
            0.0, "the bot's last record is from 2026-11-19; nothing recorded today")
        assert box(self.venue(-1.0))[1] == []

    def test_no_record_is_unread_not_zero(self, box, tmp_path):
        empty = tmp_path / "nothing-here"
        empty.mkdir()
        assert w.realized_today(TODAY, empty) == (None, "the bot has saved no risk state")
        _, (text,) = box(self.venue(-1.0), bot_state_dir=empty)
        assert ("Daily stop could not be checked: not read: today's realized result "
                "(the bot has saved no risk state).") in text

    def test_a_record_that_will_not_read_is_unread_and_names_the_file(self, box):
        (box.record_dir / "combined_state.json").write_text("{not json", encoding="utf-8")
        realized, basis = w.realized_today(TODAY, box.record_dir)
        assert realized is None and basis.startswith("combined_state.json could not be read (")

    def test_the_older_risk_file_is_read_when_there_is_no_combined_one(self, tmp_path):
        (tmp_path / "risk_state.json").write_text(
            json.dumps({"live_daily_pnl": -12.5, "live_daily_day": TODAY}), encoding="utf-8")
        assert w.realized_today(TODAY, tmp_path) == (-12.5, "the bot's record of today")

    def test_equity_is_the_venues_marked_figure_before_the_wallets(self):
        marked = {"info": [{"usdtEquity": "101.25", "accountEquity": "99"}],
                  "USDT": {"total": 90.5}}
        assert w.equity_from_balance(marked, "USDT") == 101.25
        enveloped = {"info": {"data": {"accountEquity": "147.46"}}, "USDT": {"total": 1.0}}
        assert w.equity_from_balance(enveloped, "USDT") == 147.46
        wallet_only = {"info": {"data": []}, "USDT": {"total": 90.5}}
        assert w.equity_from_balance(wallet_only, "USDT") == 90.5
        # Nothing to divide by is not an equity of zero.
        for nothing in ({"info": [{"usdtEquity": "0"}], "USDT": {"total": 0.0}},
                        {"info": [{"usdtEquity": ""}], "USDT": {}}, {}, None):
            assert w.equity_from_balance(nothing, "USDT") is None

    def test_a_position_that_states_no_result_makes_the_sum_unread(self):
        """A total over rows that include an unread one is not the total."""
        rows = [w.OpenPosition("A", "long", 1.0, 1.0, 1.0, -5.0, None, {}),
                w.OpenPosition("B", "long", 1.0, 1.0, 1.0, None, None, {})]
        assert w.open_losses(rows) is None
        assert w.open_losses(rows[:1]) == -5.0
        assert w.open_losses([]) == 0.0
        assert w.open_losses(None) is None

    def test_an_unread_equity_matters_only_when_there_is_a_loss(self, box):
        no_equity = Venue(positions=[position(upl="0")], plans=[STOP, TARGET], equity=None)
        assert box(no_equity)[1] == []
        losing = Venue(positions=[position(upl="-1.0")], plans=[STOP, TARGET], equity=None)
        _, (text,) = box(losing)
        assert "Daily stop could not be checked: not read: equity." in text


class TestAFindingIsNotRepeatedEveryPass:
    def naked(self):
        return Venue(positions=[position()], plans=[TARGET])

    def test_the_second_pass_is_quiet_and_the_window_ends_it(self, box):
        assert len(box(self.naked())[1]) == 1
        assert len(box(self.naked(), now=NOW + 300)[1]) == 1
        assert len(box(self.naked(), now=NOW + 3599)[1]) == 1
        assert len(box(self.naked(), now=NOW + 3600)[1]) == 2

    def test_the_window_is_the_operators_to_set(self, box, monkeypatch):
        monkeypatch.setenv("POSITION_WATCHDOG_REALERT_MIN", "10")
        box(self.naked())
        assert len(box(self.naked(), now=NOW + 599)[1]) == 1
        assert len(box(self.naked(), now=NOW + 600)[1]) == 2

    def test_a_new_finding_is_sent_beside_a_quiet_one(self, box):
        box(self.naked())
        _, sent = box(Venue(positions=[position()], plans=[]), now=NOW + 300)
        assert len(sent) == 2
        assert "no take-profit is resting" in sent[1]
        assert "no stop-loss is resting" not in sent[1]
        assert "1 earlier finding(s) still stand and were not repeated." in sent[1]

    def test_a_finding_that_cleared_and_came_back_alerts_at_once(self, box):
        box(self.naked())
        assert len(box(Venue(positions=[position()], plans=[STOP, TARGET]), now=NOW + 300)[1]) == 1
        assert len(box(self.naked(), now=NOW + 600)[1]) == 2

    def test_an_alert_that_was_not_sent_stays_due(self, box):
        def refuses(_text):
            raise RuntimeError("telegram refused the message")

        status, sent = box(self.naked(), publisher=refuses)
        assert (status, sent) == (w.EXIT_FAILED, [])
        status, sent = box(self.naked(), now=NOW + 300)
        assert status == w.EXIT_OK and len(sent) == 1

    def test_a_record_that_will_not_read_does_not_silence_an_alert(self, box):
        box.state.write_text("{not json", encoding="utf-8")
        for step in (0, 300):
            box(self.naked(), now=NOW + step)
        assert len(box.sent) == 2
        assert all("The watchdog's own record could not be read" in text for text in box.sent)
        assert box.state.read_text(encoding="utf-8") == "{not json", "the file was written over"

    def test_the_text_goes_through_the_outbound_scrubber(self, box, monkeypatch):
        seen = []
        monkeypatch.setattr(w, "reply_safe", lambda text: seen.append(text) or "scrubbed")
        _, sent = box(self.naked())
        assert sent == ["scrubbed"] and len(seen) == 1


class TestThePass:
    def test_it_is_off_unless_switched_on(self, tmp_path, monkeypatch):
        monkeypatch.delenv(w.ENABLED_ENV, raising=False)

        def never():
            raise AssertionError("a client was built while the watchdog is off")

        sent: list[str] = []
        status = asyncio.run(w.run_once(exchange_factory=never, publisher=sent.append,
                                        state_file=tmp_path / "s.json"))
        assert (status, sent) == (w.EXIT_OK, [])
        assert not (tmp_path / "s.json").exists()
        assert w.switched_on() is False

    def test_switched_on_without_keys_it_says_so_and_reads_nothing(self, on, monkeypatch, caplog):
        monkeypatch.setattr(BITGET, "has_operator_credentials", lambda cfg: False)
        monkeypatch.setattr(BITGET, "create_exchange",
                            lambda *a, **k: pytest.fail("a client was built without keys"))
        with caplog.at_level(logging.ERROR, logger="position_watchdog"):
            assert asyncio.run(w.run_once(publisher=lambda text: None)) == w.EXIT_NOT_CONFIGURED
        assert "BITGET_API_KEY" in caplog.text

    def test_switched_on_without_an_alert_chat_it_says_so(self, on, monkeypatch, caplog):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)
        venue = Venue(positions=[position()])
        with caplog.at_level(logging.ERROR, logger="position_watchdog"):
            status = asyncio.run(w.run_once(exchange_factory=venue.client))
        assert status == w.EXIT_NOT_CONFIGURED
        assert venue.calls == [], "the venue was read with nowhere to send the result"
        assert "TELEGRAM_ALERT_CHAT_ID" in caplog.text

    def test_a_dry_run_prints_and_neither_sends_nor_records(self, box):
        printed: list[str] = []
        status, sent = box(Venue(positions=[position()], plans=[]), dry_run=True, out=printed.append)
        assert (status, sent) == (w.EXIT_OK, [])
        (text,) = printed
        assert "no stop-loss is resting" in text and "no take-profit is resting" in text
        assert not box.state.exists()
        printed.clear()
        box(Venue(positions=[position()], plans=[STOP, TARGET]), dry_run=True, out=printed.append)
        assert printed == ["RUNECLAW position watchdog: nothing to report."]

    def test_the_command_line_runs_a_pass(self, monkeypatch):
        monkeypatch.delenv(w.ENABLED_ENV, raising=False)
        assert w.main([]) == w.EXIT_OK
        assert w.main(["--dry-run"]) == w.EXIT_OK


class TestTheAlertChat:
    def test_it_needs_both_variables(self):
        assert w.telegram_publisher({}) is None
        assert w.telegram_publisher({"TELEGRAM_BOT_TOKEN": "x"}) is None
        assert w.telegram_publisher({"TELEGRAM_ALERT_CHAT_ID": "1"}) is None
        assert callable(w.telegram_publisher(
            {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALERT_CHAT_ID": "1"}))

    def test_it_posts_the_text_to_the_chat_and_refuses_a_refusal(self, monkeypatch, caplog):
        asked = []

        class Reply:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        answer = {"ok": True, "result": {"message_id": 41}}

        def urlopen(request, timeout=None):
            asked.append((request.full_url, dict(parse_qsl(request.data.decode()))))
            return Reply(answer)

        monkeypatch.setattr(w.urllib.request, "urlopen", urlopen)
        post = w.telegram_publisher({"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_ALERT_CHAT_ID": "7"})
        with caplog.at_level(logging.DEBUG):
            assert post("hello") == "41"
        assert asked == [("https://api.telegram.org/bottok/sendMessage",
                          {"chat_id": "7", "text": "hello"})]
        assert "tok" not in caplog.text
        answer["ok"] = False
        with pytest.raises(RuntimeError, match="telegram refused the message"):
            post("hello")
