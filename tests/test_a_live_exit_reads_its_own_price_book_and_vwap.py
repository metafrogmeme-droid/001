"""The engine's live exits read the price, the book and the VWAP that belong
to the position they close.

Four defects on the exit path, each driven before it was fixed:

- THE SMART EXITS HAD A PRICE FOR THREE SYMBOLS. `_evaluate_live_smart_exits`
  priced a position off the WS feed alone, and the feed carries only what
  somebody subscribed: BTC, ETH and SOL at start, and paper positions. A live
  PENDLE swing held 60h flat was left open where the same BTC position was
  closed, so the time and hold exits the position card promises never ran on
  most of the book. The executor's own monitor pass reads a ticker for every
  open symbol just before, and threw it away; it keeps the price now
  (`pass_mark`), and the smart exits read it.
- IN LIVE MODE A STALE PAPER POSITION FED THE LIVE ENGINE. Nothing writes the
  shared paper book in live mode, so it holds what paper trading left. Checked
  against live prices, one stop-out took the live loss streak from 0 to 1, put
  a paper close in the live governor window, paused the engine for every
  account, and pushed the paper book to the website as the agent's record.
- ONE VWAP PER SYMBOL, IN MEMORY. The VWAP-reversion exit read an engine dict
  keyed by symbol: another account's confirm on the symbol replaced it, a
  restart emptied it, and a missing VWAP was stood in by the entry price. It
  is the position's own field now, saved with its row.
- A DRIFT RE-OFFER DROPPED WHAT KIND OF TRADE IT WAS. A scalp on a volume
  spike came back a swing on momentum: its time stop went from 2h to 48h. Its
  levels sat on a six-decimal grid, so a sub-cent re-offer's stop was 2.08%
  away where the card says 3%, and 0.0 at 4.9e-07.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

import bot.core.engine as engine_mod
from bot.compat import UTC
from bot.config import CONFIG
from bot.core import live_executor as le
from bot.core.engine import AgentState, RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.formatters.drift_offer import STOP_PCT, TARGET_PCT, reanalyzed_idea
from bot.utils.models import Direction, TradeIdea
from bot.utils.trailing import make_trailing_state
from tests.source_scan import code_only
from tests.test_a_close_reaches_whoever_holds_the_position import (  # noqa: F401
    _Ex,
    live_multi_user,  # a fixture, used by name
)
from tests.test_a_trail_stop_the_price_has_crossed_closes_the_position import (
    _executor as _trail_executor,
)
from tests.test_live_smart_exit_autoclose import _cfg, _pos
from tests.test_live_smart_exit_autoclose import _engine as _exit_engine
from tests.test_live_smart_exit_autoclose import _Executor as _ExitExecutor


def _now():
    return engine_mod.datetime.now(UTC)


# ── the executor keeps the mark its pass read ────────────────────────────

def _open_row(ex, symbol, *, hours=0.5, entry=100.0, sl=98.0, tp=110.0):
    p = LivePosition(
        trade_id=f"TI-{symbol[:4]}", symbol=symbol, direction="LONG",
        entry_price=entry, quantity=1.0, cost_usd=20.0, stop_loss=sl,
        take_profit=tp, leverage=5, opened_at=_now() - timedelta(hours=hours),
        status="open", atr_at_entry=1.0)
    p.strategy_type = "swing"
    p.signal_type = "momentum_confluence"
    p.filled_at = p.opened_at
    p.sl_order_id = "S1"
    p.tp_order_id = "T1"
    p.trailing_state = make_trailing_state(entry, "LONG", abs(entry - sl), 1.0)
    p.partial_tp_state = {"ladder": "off", "reason": "isolate the exit"}
    ex._positions[p.trade_id] = p
    return p


class TestThePassKeepsItsMark:
    def test_the_pass_keeps_the_price_each_ticker_stated(self, monkeypatch):
        ex, box, _moves, _closes = _trail_executor(monkeypatch)
        _open_row(ex, "ETH/USDT")
        box["p"] = 100.5
        asyncio.run(ex.check_positions())
        assert ex.pass_mark("ETH/USDT") == 100.5

    def test_a_ticker_that_states_no_price_leaves_no_mark(self, monkeypatch):
        ex, box, _m, _c = _trail_executor(monkeypatch)
        _open_row(ex, "ETH/USDT")
        box["p"] = None
        asyncio.run(ex.check_positions())
        assert ex.pass_mark("ETH/USDT") is None

    def test_a_stale_ticker_leaves_no_mark(self, monkeypatch):
        # The stop check may run on a stale price for an unprotected position;
        # a rule that closes by choice must not act on one.
        ex, box, _m, _c = _trail_executor(monkeypatch, stale=True)
        _open_row(ex, "ETH/USDT")
        box["p"] = 100.5
        asyncio.run(ex.check_positions())
        assert ex.pass_mark("ETH/USDT") is None

    def test_a_symbol_not_read_this_pass_has_no_mark(self, monkeypatch):
        ex, box, _m, _c = _trail_executor(monkeypatch)
        _open_row(ex, "ETH/USDT")
        box["p"] = 100.5
        asyncio.run(ex.check_positions())
        assert ex.pass_mark("ETH/USDT") == 100.5
        ex._exchange.fetch_ticker = AsyncMock(side_effect=RuntimeError("venue down"))
        asyncio.run(ex.check_positions())
        assert ex.pass_mark("ETH/USDT") is None

    def test_a_mark_older_than_the_ticker_bound_is_not_read(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        read_at = 1_000_000.0
        ex._pass_marks = {"ETH/USDT": (100.5, read_at)}
        bound = CONFIG.execution.live_ticker_max_age_sec
        assert ex.pass_mark("ETH/USDT", now_sec=read_at + bound) == 100.5
        assert ex.pass_mark("ETH/USDT", now_sec=read_at + bound + 1) is None

    @pytest.mark.parametrize("last", [None, 0, -1.0, "abc", float("nan"), float("inf")])
    def test_a_price_nobody_stated_is_no_mark(self, last):
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._record_pass_marks({"ETH/USDT": {"last": last, "timestamp": time.time() * 1000}},
                              time.time())
        assert ex.pass_mark("ETH/USDT") is None

    def test_a_ticker_that_is_not_a_dict_is_no_mark(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._record_pass_marks({"ETH/USDT": "garbage"}, time.time())
        assert ex.pass_mark("ETH/USDT") is None

    def test_an_executor_built_without_marks_answers_none(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        assert ex.pass_mark("ETH/USDT") is None


# ── the smart exits read it ──────────────────────────────────────────────

def _smart(ex, ws_prices):
    eng = _exit_engine(ex, ws_prices)
    p, _ = _cfg()
    try:
        asyncio.run(eng._evaluate_live_smart_exits(ex))
    finally:
        p.stop()
    return ex.closed


OLD = timedelta(hours=60)          # past the swing no-progress window


class TestTheSmartExitsHaveAPrice:
    def test_a_symbol_the_ws_feed_does_not_carry_is_closed_off_the_pass_mark(self):
        ex = _ExitExecutor([_pos(symbol="PENDLE/USDT", opened_at=_now() - OLD)])
        ex.pass_mark = lambda sym, now_sec=None: {"PENDLE/USDT": 100.5}.get(sym)
        closed = _smart(ex, {"BTC/USDT": 100.5, "ETH/USDT": 100.5, "SOL/USDT": 100.5})
        assert [c[0] for c in closed] == ["t1"]

    @pytest.mark.parametrize("mark, ws, closes", [(101.0, 100.1, True),
                                                   (100.1, 101.0, False)])
    def test_the_pass_mark_decides_over_the_ws_tick(self, mark, ws, closes):
        # A two-minute-old VWAP reversion, so only the price decides: 1% over
        # its VWAP completes it, 0.1% does not. The rule reads the price the
        # stop check used, whatever the WS tick says. (A 60h swing cannot tell
        # the two apart: its hard hold limit closes it at any price.)
        ex = _ExitExecutor([_vwap_pos("A", 100.0, 100.0)])
        ex.pass_mark = lambda sym, now_sec=None: mark
        assert bool(_smart(ex, {"SOL/USDT": ws})) is closes

    @pytest.mark.parametrize("ws", [-1.0, float("inf")])
    def test_a_junk_ws_tick_never_reaches_the_vwap_exit(self, ws):
        # The R rules refuse a junk price by themselves (`r_multiple_now`);
        # the VWAP rule does not, and read -1.0 as 101% under VWAP (failed)
        # and infinity as a reversion complete.
        ex = _ExitExecutor([_vwap_pos("A", 100.0, 100.0)])
        ex.pass_mark = lambda sym, now_sec=None: None
        assert _smart(ex, {"SOL/USDT": ws}) == []

    def test_no_mark_falls_back_to_a_fresh_ws_tick(self):
        ex = _ExitExecutor([_pos(opened_at=_now() - OLD)])
        ex.pass_mark = lambda sym, now_sec=None: None
        assert len(_smart(ex, {"BTC/USDT": 100.5})) == 1

    @pytest.mark.parametrize("ws", [{}, {"PENDLE/USDT": 0}, {"PENDLE/USDT": float("nan")},
                                    {"PENDLE/USDT": "abc"}])
    def test_no_price_anywhere_closes_nothing(self, ws):
        ex = _ExitExecutor([_pos(symbol="PENDLE/USDT", opened_at=_now() - OLD)])
        ex.pass_mark = lambda sym, now_sec=None: None
        assert _smart(ex, ws) == []

    def test_end_to_end_the_real_pass_prices_the_real_smart_exit(self, monkeypatch):
        """A real executor's pass reads PENDLE, the smart exits run next, and
        the 16h hold limit on a momentum signal closes a 17h position the WS
        feed has no tick for. The executor's own time stop (swing, 48h) does
        not fire, so the close is the smart exit's."""
        ex, box, _m, closes = _trail_executor(monkeypatch)
        pos = _open_row(ex, "PENDLE/USDT", hours=17)
        box["p"] = 100.2
        asyncio.run(ex.check_positions())
        assert closes == []                         # the executor closed nothing
        eng = _exit_engine(ex, {"BTC/USDT": 100.0})
        p, _ = _cfg()
        try:
            asyncio.run(eng._evaluate_live_smart_exits(ex))
        finally:
            p.stop()
        assert len(closes) == 1, closes
        assert closes[0][0].startswith("smart_exit:")
        assert pos.trade_id


# ── live mode leaves the shared paper book alone ─────────────────────────

def _stale_paper(real, symbol="ETH/USDT"):
    idea = TradeIdea(asset=symbol, direction=Direction.LONG, entry_price=2000.0,
                     stop_loss=1950.0, take_profit=2100.0, confidence=0.7, reasoning="x")
    real.portfolio.open_position(idea, 200.0, leverage=1)


def _pass(real, prices, monkeypatch, subscribed=None):
    real.ws_feed = NS(is_connected=lambda: True,
                      get_prices=lambda max_age_sec=None: prices,
                      subscribe=lambda s: (subscribed.extend(s)
                                           if subscribed is not None else None),
                      seconds_since_last_msg=lambda: 0.0)
    pushed: list = []
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda **kw: pushed.append(kw))
    real._last_sltp_verify_ts = time.monotonic()
    for n in ("close", "fill", "sync"):
        setattr(real, f"_{n}_notify_callback", AsyncMock())
    real._owner_notify_callback = AsyncMock()
    asyncio.run(real._check_open_positions())
    return pushed


def _paused(real):
    return bool(real._cooldown_until) and time.monotonic() < real._cooldown_until


@pytest.mark.usefixtures("live_multi_user")
class TestLiveModeLeavesTheSharedPaperBook:
    def _live(self):
        real = RuneClawEngine()
        real.live_executor = _Ex(None, checked=[])
        real._user_executors = {}
        return real

    def test_a_breached_stale_paper_stop_feeds_nothing_live(self, monkeypatch):
        real = self._live()
        _stale_paper(real)
        subscribed: list = []
        pushed = _pass(real, {"ETH/USDT": 1940.0}, monkeypatch, subscribed)
        assert real.risk._consecutive_losses == 0
        assert len(real.risk._realized_pnl_window) == 0
        assert not _paused(real) and real.state != AgentState.COOLING_DOWN
        assert pushed == []
        # Left exactly as it was: it resumes if the bot returns to paper.
        assert [p.asset for p in real.portfolio.open_positions] == ["ETH/USDT"]
        # Not valued at the live price either: the paper book's equity is what
        # the drawdown reading falls back to before a live equity is read.
        assert real.portfolio.paper_mark("ETH/USDT")[0] != 1940.0
        # And the feed is not asked to carry its symbols for it.
        assert "ETH/USDT" not in subscribed

    def test_a_stale_paper_position_past_its_hold_limit_is_left_open(self, monkeypatch):
        # 20h old on a momentum signal is past the 16h hard hold limit, and
        # 2010 is above its stop: only the paper hold loop could close it.
        real = self._live()
        _stale_paper(real)
        pos = real.portfolio.open_positions[0]
        pos.opened_at = _now() - timedelta(hours=20)
        _pass(real, {"ETH/USDT": 2010.0}, monkeypatch)
        assert [p.asset for p in real.portfolio.open_positions] == ["ETH/USDT"]
        assert real.portfolio.trade_history == []

    def test_it_is_said_once_with_its_positions(self, monkeypatch):
        real = self._live()
        _stale_paper(real)
        said: list = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda log, msg, **kw: said.append((msg, kw)))
        _pass(real, {"ETH/USDT": 1940.0}, monkeypatch)
        _pass(real, {"ETH/USDT": 1940.0}, monkeypatch)
        idle = [s for s in said if s[1].get("action") == "paper_book_idle"]
        assert len(idle) == 1
        msg, kw = idle[0]
        assert "1 position(s)" in msg and "ETH/USDT" in msg
        assert kw["data"] == {"count": 1, "symbols": ["ETH/USDT"]}

    def test_an_empty_shared_book_says_nothing(self, monkeypatch):
        real = self._live()
        said: list = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda log, msg, **kw: said.append((msg, kw)))
        real._note_idle_paper_book([])
        assert said == []

    def test_a_practice_book_is_still_monitored_in_live_mode(self, monkeypatch):
        real = self._live()
        idea = TradeIdea(asset="ETH/USDT", direction=Direction.LONG, entry_price=2000.0,
                         stop_loss=1950.0, take_profit=2100.0, confidence=0.7,
                         reasoning="x")
        book = real.user_portfolios.get("7")
        book.open_position(idea, 200.0, leverage=1)
        _pass(real, {"ETH/USDT": 1940.0}, monkeypatch)
        assert book.open_positions == []


class TestPaperModeIsUnchanged:
    def test_a_breached_shared_paper_stop_still_closes_in_paper_mode(self, monkeypatch):
        assert not CONFIG.is_live()
        real = RuneClawEngine()
        _stale_paper(real)
        _pass(real, {"ETH/USDT": 1940.0}, monkeypatch)
        assert real.portfolio.open_positions == []
        assert real.risk._consecutive_losses == 1

    def test_the_hold_loop_still_closes_a_paper_position_in_paper_mode(self, monkeypatch):
        # The live-mode hold test's fixture really reaches the hold rule.
        assert not CONFIG.is_live()
        real = RuneClawEngine()
        _stale_paper(real)
        real.portfolio.open_positions[0].opened_at = _now() - timedelta(hours=20)
        _pass(real, {"ETH/USDT": 2010.0}, monkeypatch)
        assert real.portfolio.open_positions == []


# ── the VWAP is the position's own ───────────────────────────────────────

def _vwap_pos(trade_id, entry, vwap, **kw):
    return _pos(trade_id=trade_id, symbol="SOL/USDT", signal_type="vwap_reversion",
                strategy_type="intraday", entry_price=entry, stop_loss=entry - 2.0,
                opened_at=_now() - timedelta(minutes=2), entry_vwap=vwap, **kw)


class TestTheVwapIsThePositions:
    def test_two_positions_on_one_symbol_each_read_their_own_vwap(self):
        # One price, two positions on the symbol. A reads its reversion as
        # complete (+0.35% over its VWAP, 0.3% band); B, entered under a
        # higher VWAP, is inside its bands. One VWAP per symbol would answer
        # both alike.
        a = _vwap_pos("A", 99.9, 100.0)
        b = _vwap_pos("B", 100.4, 100.5)
        ex = _ExitExecutor([a, b])
        closed = _smart(ex, {"SOL/USDT": 100.35})
        assert [c[0] for c in closed] == ["A"]
        assert "VWAP reversion complete" in closed[0][1]

    @pytest.mark.parametrize("vwap", [None, 0, -1.0, float("nan"), "abc"])
    def test_a_position_with_no_vwap_is_not_measured_against_its_entry(self, vwap):
        # The old confirm stood a missing VWAP in with the entry price, which
        # makes the exit fire 0.3% either side of the entry.
        ex = _ExitExecutor([_vwap_pos("A", 100.0, vwap)])
        assert _smart(ex, {"SOL/USDT": 100.35}) == []

    def test_the_engine_holds_no_vwap_of_its_own(self):
        src = code_only(open(engine_mod.__file__).read())
        assert "_last_vwap" not in src
        assert "_entry_vwap" not in code_only(open("bot/core/analyzer.py").read())

    def test_a_paper_position_has_no_vwap_so_the_paper_loop_asks_no_vwap_rule(self):
        """The paper loop's VWAP branch read the dict LIVE confirms wrote. A
        paper position cannot carry a VWAP of its own (the model has no such
        field, and loading ignores the key), so the branch could never fire
        once the dict was gone, and it is deleted. A scan for the call, stated
        as one: the paper loop is a 400-line monitor."""
        from bot.utils.models import TradeExecution
        assert "entry_vwap" not in TradeExecution.model_fields
        src = code_only(open(engine_mod.__file__).read())
        body = src[src.index("async def _check_paper_positions"):]
        body = body[:body.index("\n    def _note_idle_paper_book")]
        assert "check_vwap_reversion_exit" not in body
        assert "entry_vwap" not in body

    def test_the_analyzer_stamps_the_vwap_it_read(self):
        """A scan, stated as one: `analyze()` is a 1,400-line coroutine behind a
        thesis model. The VWAP-reversion idea carries the VWAP on its field."""
        src = code_only(open("bot/core/analyzer.py").read())
        at = src.index('if signal_type == "vwap_reversion":\n'
                       '            idea.entry_vwap = price_on_record(indicators.get("vwap"))')
        assert at > 0


def _filling_venue(price):
    from tests.test_a_resting_limit_is_sized_at_its_own_price import _market
    from tests.test_the_placed_order_is_the_checked_order import _Venue as _PlacedVenue

    class _Filling(_PlacedVenue):
        async def create_order(self, symbol=None, type=None, side=None, amount=None,
                               price=None, params=None, **k):
            self.orders.append({"amount": float(amount), "type": type})
            return {"id": "M1", "status": "closed", "filled": float(amount),
                    "average": self.price, "cost": float(amount) * self.price}

        async def fetch_order(self, oid, symbol=None, params=None):
            return {"id": oid, "status": "closed", "filled": self.orders[-1]["amount"],
                    "average": self.price}

        async def fetch_my_trades(self, *a, **k):
            return []

    return _Filling(_market(), price)


def _execute(idea, tmp_path):
    from bot.config import RUNTIME
    from bot.core import bounds_shadow
    was = RUNTIME.leverage_override
    RUNTIME.leverage_override = None
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = _filling_venue(4000.0)
    ex._place_sl_tp = AsyncMock(return_value=("S1", "T1"))
    ex._reattempt_post_fill_sl = AsyncMock(
        side_effect=lambda exchange, pos, direction, qty, sl, tp, tid: (sl, tp, None))
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._guard_fill_leverage = AsyncMock(return_value=None)
    try:
        with patch.object(le, "audit", lambda *a, **k: None), \
             patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
             patch.object(type(CONFIG), "is_live", return_value=True):
            result = asyncio.run(ex.execute(idea, size_usd=50.0, order_type="market"))
    finally:
        RUNTIME.leverage_override = was
    return ex, result


class TestThePositionCarriesItsVwap:
    def test_execute_records_the_ideas_vwap_on_the_position(self, tmp_path):
        from tests.test_the_placed_order_is_the_checked_order import _idea
        idea = _idea(4000.0).model_copy(update={"entry_vwap": 3990.0,
                                                "signal_type": "vwap_reversion"})
        ex, result = _execute(idea, tmp_path)
        assert "LIVE BUY" in result, result
        (pos,) = ex._positions.values()
        assert getattr(pos, "entry_vwap", None) == 3990.0

    def test_an_idea_with_no_vwap_records_none(self, tmp_path):
        from tests.test_the_placed_order_is_the_checked_order import _idea
        ex, result = _execute(_idea(4000.0), tmp_path)
        (pos,) = ex._positions.values()
        assert getattr(pos, "entry_vwap", None) is None

    def test_it_survives_a_restart(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path), user_id="7")
        p = _open_row(ex, "SOL/USDT")
        p.entry_vwap = 100.4
        ex._save_positions()
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions[p.trade_id]
        assert getattr(back, "entry_vwap", None) == 100.4

    @pytest.mark.parametrize("saved", ["absent", None, 0, -5.0, "abc", True])
    def test_a_saved_vwap_that_is_not_a_price_restores_as_absent(self, tmp_path, saved):
        ex = LiveExecutor(state_dir=str(tmp_path), user_id="7")
        p = _open_row(ex, "SOL/USDT")
        p.entry_vwap = 100.4
        ex._save_positions()
        (f,) = [x for x in tmp_path.iterdir() if x.name.startswith("live_positions")]
        rows = json.loads(f.read_text())
        for row in rows.values() if isinstance(rows, dict) else rows:
            if saved == "absent":
                row.pop("entry_vwap", None)
            else:
                row["entry_vwap"] = saved
        f.write_text(json.dumps(rows))
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions[p.trade_id]
        assert getattr(back, "entry_vwap", None) is None


# ── a drift re-offer keeps its trade ─────────────────────────────────────

def _scalp(entry):
    return TradeIdea(asset="PEPE/USDT", direction=Direction.LONG, entry_price=entry,
                     stop_loss=entry * 0.97, take_profit=entry * 1.06, confidence=0.7,
                     reasoning="x", strategy_type="scalp", signal_type="volume_spike",
                     timeframe="5m", htf_trend="bullish", entry_vwap=entry * 0.999)


class TestTheReOfferKeepsItsTrade:
    def test_it_keeps_what_kind_of_trade_it_is(self):
        o = _scalp(1.12e-05)
        n = reanalyzed_idea(o, 1.1234e-05)
        assert (n.strategy_type, n.signal_type, n.timeframe, n.htf_trend) == \
               ("scalp", "volume_spike", "5m", "bullish")
        assert n.entry_vwap == o.entry_vwap

    @pytest.mark.parametrize("price", [1.1234e-05, 4.9e-07, 0.0523, 63_123.45])
    def test_its_levels_are_the_percentages_the_card_states(self, price):
        n = reanalyzed_idea(_scalp(price), price)
        assert n is not None
        assert n.stop_loss / price == pytest.approx(1 - STOP_PCT, rel=1e-5)
        assert n.take_profit / price == pytest.approx(1 + TARGET_PCT, rel=1e-5)

    def test_the_short_side_mirrors(self):
        o = _scalp(1.12e-05).model_copy(update={"direction": Direction.SHORT,
                                                "stop_loss": 1.12e-05 * 1.03,
                                                "take_profit": 1.12e-05 * 0.94})
        n = reanalyzed_idea(o, 1.1234e-05)
        assert n.stop_loss / 1.1234e-05 == pytest.approx(1 + STOP_PCT, rel=1e-5)
        assert n.take_profit / 1.1234e-05 == pytest.approx(1 - TARGET_PCT, rel=1e-5)
