"""What the venue holds beyond the record is folded in or said, never dropped.

Two defects in squaring the book with the venue (the plan's D6):

1. THE DUPLICATE MERGE DROPPED A REAL FILL. `dedupe_duplicate_positions`
   closed every second record of one side as a phantom. When both were real
   fills, the dropped one's quantity stayed on the venue with nothing
   tracking it, and nothing adopts on a side the bot already holds. It reads
   the venue now: a venue holding both records folds the second into the
   first and re-places the stop for the whole where stops are sized; an
   unread venue merges nothing; anything else is the phantom it always was.
   And a merged record was booked at 0.0 under a reason no card knew, so
   every merge counted as a break-even trade.

2. A VENUE HOLDING MORE WAS A LOG LINE. Nothing writes that quantity (it can
   be a manual add, and a second record's stop would strip the first's), so
   `check_venue_excess` says it: twice running, once per reading, with what
   the bot's stop covers, and on /status until it is gone.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue
from bot.formatters.rich_cards import render_status_card, venue_excess_line
from bot.utils import trade_filter

UTC = timezone.utc


def _pos(trade_id, *, qty, entry, status="open", cost=10.0, sl=95.0, tp=110.0,
         age_min=60, direction="LONG", symbol="SOL/USDT:USDT", sl_id="sl-own",
         tp_id="tp-own"):
    return LivePosition(
        trade_id=trade_id, symbol=symbol, direction=direction, entry_price=entry,
        quantity=qty, cost_usd=cost, stop_loss=sl, take_profit=tp, leverage=5,
        status=status, opened_at=datetime.now(UTC) - timedelta(minutes=age_min),
        sl_order_id=sl_id, tp_order_id=tp_id)


class _Venue:
    """The executor's venue reads, recorded: the side's held size, and every
    stop placed."""

    def __init__(self, held):
        self.held = list(held) if isinstance(held, list) else [held]
        self.asked: list[tuple[str, str]] = []
        self.placed: list[tuple] = []
        self.place_result = ("sl-whole", "tp-whole")

    async def held_on_venue(self, exchange, symbol, direction):
        self.asked.append((symbol, str(direction)))
        return self.held.pop(0) if len(self.held) > 1 else self.held[0]

    async def place_sl_tp(self, exchange, symbol, direction, quantity, sl, tp):
        self.placed.append((symbol, str(getattr(direction, "value", direction)), quantity, sl, tp))
        return self.place_result


def _executor(tmp_path, venue, *rows, uta=False):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("bitget")
    ex._is_uta = uta
    ex._get_exchange = AsyncMock(return_value=object())
    ex._held_on_venue = venue.held_on_venue
    ex._place_sl_tp = venue.place_sl_tp
    for r in rows:
        ex._positions[r.trade_id] = r
    return ex


def _live():
    return patch.object(type(CONFIG), "is_live", return_value=True)


# ── the merge ───────────────────────────────────────────────────────────────


def test_a_venue_holding_both_records_folds_the_second_into_the_first(tmp_path):
    venue = _Venue(3.0)
    own = _pos("TI-own", qty=2.0, entry=100.0, cost=40.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, cost=21.2, age_min=5, sl_id="sl-second")
    ex = _executor(tmp_path, venue, own, second)

    msgs = asyncio.run(ex.dedupe_duplicate_positions())

    assert own.quantity == pytest.approx(3.0)
    assert own.entry_price == pytest.approx(102.0)        # (2*100 + 1*106) / 3
    assert own.cost_usd == pytest.approx(61.2)
    assert second.close_reason == "duplicate_merged" and "TI-second" not in ex._positions
    # The stop is re-placed for the whole, at the kept record's own levels.
    assert venue.placed == [("SOL/USDT:USDT", "LONG", 3.0, 95.0, 110.0)]
    assert own.sl_order_id == "sl-whole" and own.tp_order_id == "tp-whole"
    assert len(msgs) == 1 and msgs[0].startswith("SYNC: Folded")


def test_a_venue_holding_one_records_worth_drops_the_phantom(tmp_path):
    venue = _Venue(2.0)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    phantom = _pos("ORPHAN-77", qty=2.0, entry=100.0, age_min=1)
    ex = _executor(tmp_path, venue, own, phantom)

    msgs = asyncio.run(ex.dedupe_duplicate_positions())

    assert own.quantity == 2.0 and own.entry_price == 100.0
    assert phantom.close_reason == "duplicate_merged"
    assert venue.placed == [], "a phantom's drop places nothing"
    assert len(msgs) == 1 and msgs[0].startswith("SYNC: Merged")


def test_an_unread_venue_merges_nothing_this_pass(tmp_path):
    venue = _Venue(None)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second)

    assert asyncio.run(ex.dedupe_duplicate_positions()) == []
    assert own.quantity == 2.0
    assert second.status == "open" and "TI-second" in ex._positions


def test_a_unified_account_folds_and_places_nothing(tmp_path):
    # Its TP/SL is one order on the whole position: it already covers both.
    venue = _Venue(3.0)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second, uta=True)

    msgs = asyncio.run(ex.dedupe_duplicate_positions())

    assert own.quantity == pytest.approx(3.0)
    assert venue.placed == []
    assert [m for m in msgs if "stop" in m] == []


def test_an_unread_account_type_folds_and_says_the_stop_was_not_replaced(tmp_path):
    venue = _Venue(3.0)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second, uta=None)

    msgs = asyncio.run(ex.dedupe_duplicate_positions())

    assert venue.placed == []
    assert own.sl_order_id == "sl-own"
    assert any("was not re-placed" in m for m in msgs)


def test_a_refused_whole_stop_keeps_each_parts_own_and_says_so(tmp_path):
    venue = _Venue(3.0)
    venue.place_result = (None, None)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second)

    msgs = asyncio.run(ex.dedupe_duplicate_positions())

    assert (own.sl_order_id, own.tp_order_id) == ("sl-own", "tp-own"), \
        "a refused stop never replaces the ones resting"
    assert any("was not re-placed" in m for m in msgs)


def test_a_record_holding_one_leg_places_only_that_one_and_says_the_stop_is_not_whole(tmp_path):
    # The sanctioned path (`_place_missing_sltp`) places only a missing leg;
    # the held stop keeps its old size, and the second record's own stop
    # still rests, since nothing swept the side.
    venue = _Venue(3.0)
    own = _pos("TI-own", qty=2.0, entry=100.0, tp_id=None)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second)
    legs: list = []

    async def _one_leg(exchange, symbol, leg, direction, quantity, price):
        legs.append((leg, quantity, price))
        return "tp-whole"

    ex._place_classic_trigger = _one_leg
    msgs = asyncio.run(ex.dedupe_duplicate_positions())

    assert legs == [("tp", 3.0, 110.0)] and venue.placed == []
    assert (own.sl_order_id, own.tp_order_id) == ("sl-own", "tp-whole")
    assert any("was not re-placed" in m for m in msgs)


def test_switched_off_the_merge_drops_without_asking(tmp_path, monkeypatch):
    import dataclasses
    venue = _Venue(3.0)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second)
    off = dataclasses.replace(CONFIG.execution, duplicate_fold_enabled=False)
    saved = CONFIG.execution
    object.__setattr__(CONFIG, "execution", off)
    try:
        asyncio.run(ex.dedupe_duplicate_positions())
    finally:
        object.__setattr__(CONFIG, "execution", saved)
    assert venue.asked == [] and own.quantity == 2.0
    assert second.close_reason == "duplicate_merged"


def test_resting_orders_are_merged_without_a_position_read(tmp_path):
    venue = _Venue(3.0)
    a = _pos("TI-a", qty=1.0, entry=100.0, status="pending_fill")
    b = _pos("ORPHAN-b", qty=1.0, entry=100.0, status="pending_fill", age_min=1)
    ex = _executor(tmp_path, venue, a, b)

    asyncio.run(ex.dedupe_duplicate_positions())

    assert venue.asked == []
    assert b.close_reason == "duplicate_merged" and a.quantity == 1.0


def test_a_merged_record_is_not_a_trade_on_any_card():
    row = SimpleNamespace(trade_id="TI-x", close_reason="duplicate_merged",
                          pnl_usd=0.0, pnl=0.0)
    assert trade_filter.is_countable(row) is False


# ── the excess ──────────────────────────────────────────────────────────────


def _excess(ex):
    with _live():
        return asyncio.run(ex.check_venue_excess())


def test_an_excess_is_said_on_the_second_equal_reading_and_once(tmp_path):
    venue = _Venue(3.5)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    ex = _executor(tmp_path, venue, own)

    assert _excess(ex) == [], "one snapshot is not a reading"
    msgs = _excess(ex)
    assert len(msgs) == 1 and msgs[0].startswith("SYNC: SOL LONG")
    assert "1.5 more than the bot opened" in msgs[0]
    assert "no stop of the bot's" in msgs[0]
    assert own.quantity == 2.0 and venue.placed == [], "nothing is written or placed"
    [row] = ex.venue_excess()
    assert (row["venue"], row["records"], row["excess"], row["stops_sized"]) == (3.5, 2.0, 1.5, True)
    assert _excess(ex) == [], "the same reading is said once"


def test_a_changed_excess_is_confirmed_again_and_said_again(tmp_path):
    venue = _Venue([3.5, 3.5, 4.0, 4.0])
    ex = _executor(tmp_path, venue, _pos("TI-own", qty=2.0, entry=100.0))
    _excess(ex)
    assert len(_excess(ex)) == 1
    assert _excess(ex) == []                    # 4.0 seen once
    msgs = _excess(ex)
    assert len(msgs) == 1 and "2 more than the bot opened" in msgs[0]


def test_a_gone_excess_leaves_status(tmp_path):
    venue = _Venue([3.5, 3.5, 2.0])
    ex = _executor(tmp_path, venue, _pos("TI-own", qty=2.0, entry=100.0))
    _excess(ex)
    _excess(ex)
    assert ex.venue_excess()
    assert _excess(ex) == []
    assert ex.venue_excess() == []


def test_an_unread_venue_keeps_the_last_confirmed_reading(tmp_path):
    venue = _Venue([3.5, 3.5, None])
    ex = _executor(tmp_path, venue, _pos("TI-own", qty=2.0, entry=100.0))
    _excess(ex)
    _excess(ex)
    assert _excess(ex) == []
    assert ex.venue_excess(), "unread is not 'gone'"


def test_a_close_in_flight_is_not_read(tmp_path):
    venue = _Venue(3.5)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    ex = _executor(tmp_path, venue, own)

    async def go():
        lock = asyncio.Lock()
        ex._close_locks["TI-own"] = lock
        async with lock:
            return await ex.check_venue_excess()

    with _live():
        assert asyncio.run(go()) == []
    assert venue.asked == []


def test_the_sum_of_a_sides_records_is_what_the_venue_is_compared_with(tmp_path):
    # Two records on one side, the venue holding exactly both: no excess.
    venue = _Venue(3.0)
    ex = _executor(tmp_path, venue, _pos("TI-a", qty=2.0, entry=100.0),
                   _pos("TI-b", qty=1.0, entry=100.0, age_min=5))
    _excess(ex)
    assert _excess(ex) == [] and ex.venue_excess() == []


def test_under_one_tpsl_on_the_whole_position_the_extra_closes_with_it(tmp_path):
    venue = _Venue(3.5)
    ex = _executor(tmp_path, venue, _pos("TI-own", qty=2.0, entry=100.0), uta=True)
    _excess(ex)
    [msg] = _excess(ex)
    assert "covers the whole position" in msg


def test_paper_asks_nothing(tmp_path):
    venue = _Venue(3.5)
    ex = _executor(tmp_path, venue, _pos("TI-own", qty=2.0, entry=100.0))
    with patch.object(type(CONFIG), "is_live", return_value=False):
        assert asyncio.run(ex.check_venue_excess()) == []
    assert venue.asked == []


# ── the periodic sync carries both ──────────────────────────────────────────


def test_the_periodic_sync_says_a_fold_and_an_excess_and_still_adopts(tmp_path):
    venue = _Venue(3.0)
    own = _pos("TI-own", qty=2.0, entry=100.0)
    second = _pos("TI-second", qty=1.0, entry=106.0, age_min=5)
    ex = _executor(tmp_path, venue, own, second)
    ex.adopt_exchange_positions = AsyncMock(return_value=["ARB"])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex.check_venue_excess = AsyncMock(return_value=["SYNC: an excess"])
    ex._last_exchange_sync = 0.0

    with _live():
        msgs = asyncio.run(ex._periodic_exchange_sync())

    assert msgs[0].startswith("SYNC: Folded")
    assert "SYNC: an excess" in msgs
    assert "SYNC: Adopted untracked position ARB from exchange" in msgs
    # Inside the interval it asks nothing.
    with _live():
        assert asyncio.run(ex._periodic_exchange_sync()) == []


def test_every_sync_notice_routes_as_information_not_a_close():
    from bot.core.engine import RuneClawEngine
    for msg in ("SYNC: Folded a second record", "SYNC: Merged duplicate local record",
                "SYNC: SOL LONG -- the venue holds 3.5"):
        assert RuneClawEngine._is_sync_message(msg), msg


def test_the_engine_audits_a_size_notice_as_one_not_as_an_adoption(monkeypatch):
    from bot.core import engine as engine_mod
    from bot.core.engine import RuneClawEngine

    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()
    told: list = []

    async def _announce(ex, kind, msg):
        told.append(kind)

    eng._announce_executor_message = _announce
    results: list = []
    monkeypatch.setattr(engine_mod, "audit",
                        lambda log, msg, **kw: results.append(kw.get("result")))
    asyncio.run(eng._route_monitor_messages(object(), [
        "SYNC: SOL LONG -- the venue holds 3.5, the bot's record 2",
        "SYNC: Adopted untracked position ARB from exchange"]))
    assert results == ["SIZE", "ADOPTED"]
    assert told == ["sync", "sync"]


# ── /status ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sized,words", [
    (True, "the extra has no stop of the bot's"),
    (False, "the position's TP/SL covers it"),
    (None, "stop cover unread"),
])
def test_the_status_line_says_what_covers_the_extra(sized, words):
    line = venue_excess_line([{"symbol": "SOL", "direction": "LONG", "excess": 1.5,
                               "stops_sized": sized}])
    assert line == f"⚠️ Venue holds more than the record: SOL LONG +1.5 ({words})"


def test_no_confirmed_excess_is_no_line():
    assert venue_excess_line(None) == "" and venue_excess_line([]) == ""
    assert venue_excess_line([{"symbol": "SOL", "excess": None}]) == ""


def test_the_status_card_carries_the_line():
    out = render_status_card(
        mode="LIVE", active=True, equity=1000.0, open_positions=1, daily_pnl=0.5,
        drawdown=0.01, max_drawdown=0.1, market_bias="Neutral",
        position_watch={"outcome": "tick", "unwatched_streak": 0, "age_s": 9},
        venue_excess=[{"symbol": "SOL", "direction": "LONG", "excess": 1.5,
                       "stops_sized": True}])
    assert "- ⚠️ Venue holds more than the record: SOL LONG +1.5" in out


def test_status_reads_the_excess_off_the_callers_own_executor(monkeypatch):
    from bot.skills import start_commands as sc

    class _Rendered(Exception):
        pass

    seen: dict = {}

    def _render(**kw):
        seen.update(kw)
        raise _Rendered

    monkeypatch.setattr(sc, "render_status_card", _render)
    monkeypatch.setattr(sc, "entry_gate", lambda eng, uid: {"reasons": [], "blocked": False})
    monkeypatch.setattr(sc, "caller_risk", lambda eng, uid: None)
    rows = [{"symbol": "SOL", "direction": "LONG", "excess": 1.5, "stops_sized": True}]
    callers = SimpleNamespace(open_positions=[], closed_positions=[],
                              venue_excess=lambda: rows)
    operators = SimpleNamespace(open_positions=[], closed_positions=[],
                                venue_excess=lambda: [{"symbol": "NOT-MINE"}])
    snap = SimpleNamespace(equity_usd=1000.0, open_positions=0, daily_pnl=0.0,
                           max_drawdown_pct=0.0)

    async def _equity(uid):
        return 1000.0, "venue"

    engine = SimpleNamespace(
        user_portfolios={"u": SimpleNamespace(snapshot=lambda: snap)},
        live_view=lambda uid: {"executor": callers if uid == "u" else operators},
        live_executor=operators, resolve_display_equity=_equity, pending_ideas=[])
    handler = sc.StartCommands.__new__(sc.StartCommands)
    handler.engine = engine
    handler._status_market_bias = lambda: "Neutral"
    handler._tick_liveness = lambda e: (False, None)
    handler._tick_age_s = lambda e: None
    saved = CONFIG.simulation_mode
    object.__setattr__(CONFIG, "simulation_mode", False)    # a real account's book
    try:
        with pytest.raises(_Rendered):
            asyncio.run(handler.status_card_text("u"))
    finally:
        object.__setattr__(CONFIG, "simulation_mode", saved)
    assert seen["venue_excess"] == rows
