"""Three scheduled messages the previous slice filed, re-driven and fixed.

1. THE TIME-STOP ALERT READ NOTHING THE EXIT CODE READS. It judged "intraday
   or swing" off the stop distance (under 2% = intraday) and warned and
   "closed" on four TIME_STOP_* hours no other code read, while the executor's
   time stop closes on the strategy table and four smart exits can close
   earlier (`bot/core/time_exits.py` is the one reading of all of them).
   Driven before the fix, each on a live operator position:

   * a swing trade with a 1.5% stop at 5h: CRITICAL "NOT in profit -- AUTO-CLOSE
     recommended", when the first rule that can close it is at 48h;
   * a scalp at 2.0h, which the executor closes at 2h: "Auto-close in: 2.0h ...
     Position will be flagged for close at 4h";
   * a swing trade 50h old, up +0.05% gross (under its fees, so the executor
     closes it): no alert at all, because "in profit" was read gross;
   * an adopted position with no recorded strategy (no time exit applies, the
     operator's decision of 2026-09-24) and a practice position (no time exit
     reads a practice book): CRITICAL "AUTO-CLOSE recommended";
   * every CLOSE alert: "/liveclose <trade_id> -- close it manually", for a
     close the bot makes by itself, a command that is admin-only.

   The alert is a third reader of `time_exits` now: `plan_for`, `clock_reading`
   (the fee-aware profit test), `due_exit` and `next_exit`.

2. THE MORNING BRIEF COUNTED A RESTING ORDER AS AN OPEN POSITION. One filled
   position and two resting limit orders read "Carrying 3 open position(s)";
   and a live book with nothing in it fell through to the shared PAPER book, so
   a position an older build left there was the operator's live position, with
   its side printed "DIREC".

3. A PRIVATE CLOSE CARD WITH NO RECORD HEADED A KEPT-OPEN MESSAGE "CLOSED".
   "Smart-exit close FAILED ... the position is still OPEN" went to the
   operator as "⚪ Closed" (or "❌ Closed" off a "-$" in the text), was recorded
   in the transcript as TRADE_CLOSED -- and was posted to the PUBLIC channels
   as "⚪ TRADE CLOSED #TradeResult", an unprotected position's "No exchange
   stop-loss could be placed" included.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import re
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import bot.core.proactive_monitor as pm
from bot.config import CONFIG
from bot.core import time_exits as tx
from bot.core.live_executor import LivePosition
from bot.core.proactive_monitor import ProactiveMonitor

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _monitor_clock(monkeypatch):
    """The monitor reads `datetime.now(UTC)`; a fixed one lets a fixture sit
    exactly ON an hour, which is the only input that tells `>=` from `>`."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(pm, "datetime", _Frozen)

@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


@pytest.fixture
def time_stop():
    """Plant TimeStopConfig fields on the frozen config, restored after."""
    original = CONFIG.time_stop

    def _set(**kw):
        object.__setattr__(CONFIG, "time_stop",
                           dataclasses.replace(original, **kw))

    yield _set
    object.__setattr__(CONFIG, "time_stop", original)


class _Feed:
    def __init__(self, prices):
        self._p = prices

    def is_connected(self):
        return True

    def get_prices(self, max_age_sec=0):
        return self._p


def _lp(symbol="BTC/USDT", entry=100.0, sl=97.0, hours=30.0, *,
        strategy="swing", signal="breakout", tid=None, **kw):
    """A live position. `breakout` has no signal hold limit, so a swing trade
    on it is closed by the 48h rules alone; a test that wants the hold limits
    names `momentum_confluence`."""
    p = LivePosition(trade_id=tid or f"T-{symbol[:3]}", symbol=symbol,
                     direction=kw.pop("direction", "LONG"), entry_price=entry,
                     quantity=0.01, cost_usd=100.0, stop_loss=sl,
                     take_profit=entry * 1.2, status=kw.pop("status", "open"),
                     opened_at=NOW - timedelta(hours=hours))
    p.strategy_type, p.signal_type = strategy, signal
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _engine(op=(), users=None, practice=None, shared=(), prices=None):
    opx = NS(user_id=None, open_positions=list(op))
    execs = [opx] + [NS(user_id=uid, open_positions=list(v))
                     for uid, v in (users or {}).items()]
    books = {uid: NS(open_positions=list(v)) for uid, v in (practice or {}).items()}
    return NS(live_executor=opx, _all_live_executors=lambda: execs,
              user_portfolios=NS(all_portfolios=lambda: books,
                                 get=lambda uid: books[uid]),
              portfolio=NS(open_positions=list(shared)),
              ws_feed=_Feed(prices if prices is not None else {"BTC/USDT": 99.5}))


def _stops(eng):
    return ProactiveMonitor(eng)._check_time_stops()


def _plain(body: str) -> str:
    return re.sub(r"<[^>]+>", "", body)


# ── 1. the time-stop alert reads the plan ────────────────────────────────

class TestTheTimeStopAlertReadsThePlan:

    def test_a_swing_trade_with_a_tight_stop_is_not_called_intraday(self, live):
        """The heuristic's worst case: CRITICAL "AUTO-CLOSE recommended" at 5h
        on a position nothing closes before 48h."""
        assert _stops(_engine([_lp(sl=98.5, hours=5)])) == []

    def test_from_the_warn_hour_it_names_the_rule_that_closes_first(self, live):
        [a] = _stops(_engine([_lp(sl=98.5, hours=13)]))
        assert (a.alert_type, a.severity, a.audience) == (
            "TIME_STOP_WARN", "WARNING", "admin")
        body = _plain(a.body)
        assert ("As it stands, the bot closes it at market by itself on this "
                "rule: after 48h if under 0.5R (in 35h).") in body, body

    @pytest.mark.parametrize("hours,warned", [(11.99, False), (12.0, True)])
    def test_the_warning_waits_for_the_strategy_tables_warn_hour(
            self, live, hours, warned):
        """Swing warns from 12h (`get_time_warn_hours`), the hour the
        executor's own time stop reads -- not TIME_STOP_SWING_WARN_H, which no
        exit read. One fixture ON the hour and one just short of it."""
        assert CONFIG.strategy_types.get_time_warn_hours("swing") == 12.0
        got = _stops(_engine([_lp(hours=hours)]))
        assert bool(got) is warned, got

    def test_the_scalp_the_executor_closes_at_2h_is_due_at_2h(self, live):
        [a] = _stops(_engine([_lp(sl=99.0, hours=2.05, strategy="scalp")],
                             prices={"BTC/USDT": 99.8}))
        assert (a.alert_type, a.severity) == ("TIME_STOP_CLOSE", "CRITICAL")
        body = _plain(a.body)
        assert ("The bot closes it at market by itself on this rule: after 2h "
                "unless in profit after fees (due now).") in body, body
        assert "4h" not in body and "Auto-close in" not in body

    def test_the_scalp_is_warned_about_the_executors_own_time_stop(self, live):
        """Scalp warns from 1h; the first rule that would close it as it
        stands is the executor's, at 2h, on the fee-aware reading."""
        [a] = _stops(_engine([_lp(sl=99.0, hours=1.2, strategy="scalp")],
                             prices={"BTC/USDT": 99.8}))
        assert a.alert_type == "TIME_STOP_WARN"
        assert ("on this rule: after 2h unless in profit after fees (in 48m)."
                in _plain(a.body)), a.body

    def test_up_less_than_its_fees_is_not_in_profit(self, live):
        """+0.05% gross is under a round trip in fees: the executor closes it,
        and the alert used to skip it as "in profit"."""
        pos = _lp(hours=50, sl=97.0)
        [a] = _stops(_engine([pos], prices={"BTC/USDT": 100.05}))
        assert a.alert_type == "TIME_STOP_CLOSE"
        assert "not in profit after fees" in _plain(a.body)

    def test_a_position_the_rules_spare_is_not_alerted(self, live):
        """Past 48h, in profit after fees and over 0.5R: no rule closes it."""
        assert _stops(_engine([_lp(hours=50)], prices={"BTC/USDT": 102.0})) == []

    def test_the_momentum_trade_is_warned_about_its_hard_limit(self, live):
        """Over 1R at 12h, the 8h rule is armed and not met, and the next rule
        is the 16h hard limit, which closes it whatever the R."""
        [a] = _stops(_engine([_lp(hours=12.5, signal="momentum_confluence")],
                             prices={"BTC/USDT": 103.5}))
        assert a.alert_type == "TIME_STOP_WARN"
        assert "on this rule: after 16h whatever the R (in 3h30m)" in _plain(a.body)
        assert "swing · momentum_confluence" in _plain(a.body)

    def test_an_adopted_position_with_no_strategy_gets_nothing(self, live):
        """No time exit applies to it (the operator's decision, 2026-09-24)."""
        assert _stops(_engine([_lp(hours=30, origin="adopted")])) == []

    def test_an_adopted_position_that_inherited_a_strategy_is_alerted(self, live):
        assert _stops(_engine([_lp(hours=30, origin="adopted",
                                   thesis_source="inherited")]))

    def test_a_practice_position_gets_nothing(self, live):
        """No time exit reads a practice book; the alert told its owner to run
        /liveclose, a live command, on a practice position."""
        pr = NS(asset="BTC/USDT", direction=NS(value="LONG"), entry_price=100.0,
                stop_loss=97.0, take_profit=120.0, trade_id="T-PR",
                opened_at=NOW - timedelta(hours=30))
        assert _stops(_engine(practice={"777": [pr]})) == []

    def test_the_shared_paper_book_gets_nothing_in_paper_mode(self):
        """Its time exits are the paper loop's own copy of the smart exits,
        which `time_exits` does not describe."""
        paper = NS(asset="BTC/USDT", direction=NS(value="LONG"),
                   entry_price=100.0, stop_loss=97.0, take_profit=120.0,
                   trade_id="T-SH", opened_at=NOW - timedelta(hours=30))
        assert _stops(_engine(shared=[paper])) == []

    def test_time_stops_off_is_nothing(self, live, time_stop):
        time_stop(enabled=False)
        assert _stops(_engine([_lp(hours=60)])) == []

    def test_with_the_smart_exits_off_only_the_time_stop_is_named(
            self, live, time_stop):
        time_stop(live_auto_close_enabled=False)
        [a] = _stops(_engine([_lp(hours=30, signal="momentum_confluence")]))
        assert "on this rule: after 48h unless in profit after fees (in 18h)" \
            in _plain(a.body)

    def test_no_door_to_close_it_by_hand(self, live):
        [a] = _stops(_engine([_lp(hours=60, signal="momentum_confluence")]))
        assert a.alert_type == "TIME_STOP_CLOSE"
        assert "/liveclose" not in a.body and "manually" not in a.body
        assert "by itself" in a.body

    def test_the_alert_states_the_rules_the_card_states(self, live):
        """One reading: the line under the alert is the /positions card's."""
        pos = _lp(hours=30, signal="momentum_confluence")
        [a] = _stops(_engine([pos]))
        line = tx.position_time_exit_line(pos, 99.5, NOW, CONFIG.time_stop,
                                          CONFIG.strategy_types)
        assert line in _plain(a.body), (line, a.body)

    def test_a_row_with_no_time_on_record_does_not_silence_the_next(self, live):
        """No entry time is no age, and the rows after it are still read."""
        undated = _lp(tid="T-UNDATED", hours=60)
        undated.opened_at = None
        [a] = _stops(_engine([undated, _lp(tid="T-DUE", hours=60,
                                          signal="momentum_confluence")]))
        assert a.dedup_key == "time_close_T-DUE"

    def test_no_fresh_mark_is_no_verdict(self, live):
        assert _stops(_engine([_lp(hours=60, signal="momentum_confluence")],
                              prices={})) == []

    def test_the_owner_is_told_and_nobody_else(self, live):
        [a] = _stops(_engine(users={"777": [_lp(hours=60,
                                                  signal="momentum_confluence")]}))
        assert (a.user_id, a.audience) == ("777", "all")

    def test_a_resting_order_is_not_timed(self, live):
        assert _stops(_engine([_lp(hours=60, status="pending_fill")])) == []

    def test_the_symbol_is_escaped(self, live):
        pos = _lp(symbol="<b>X/USDT", hours=60, signal="momentum_confluence")
        [a] = _stops(_engine([pos], prices={"<b>X/USDT": 99.5}))
        assert "&lt;b&gt;X/USDT" in a.body and "<b>X/USDT" not in a.body


class TestTheReadingIsOneReading:

    @pytest.mark.parametrize("rule,r,fee,want", [
        (tx.TimeExit("t", 1.0, fee_profit=True), 0.0, None, None),
        (tx.TimeExit("t", 1.0, fee_profit=True), 0.0, True, False),
        (tx.TimeExit("t", 1.0, fee_profit=True), 0.0, False, True),
        (tx.TimeExit("t", 1.0), None, None, True),
        (tx.TimeExit("t", 1.0, r_below=1.0), None, True, None),
        (tx.TimeExit("t", 1.0, r_below=1.0), 0.99, None, True),
        (tx.TimeExit("t", 1.0, r_below=1.0), 1.0, None, False),
    ])
    def test_a_rules_condition_is_three_valued(self, rule, r, fee, want):
        assert tx.closes_at_reading(rule, r, fee) is want

    def test_an_unread_condition_is_neither_due_nor_predicted(self):
        plan = tx.TimeExitPlan("armed", (tx.TimeExit("time_stop", 2.0,
                                                     fee_profit=True),))
        assert tx.due_exit(plan, 3.0, None, None) is None
        assert tx.next_exit(plan, 1.0, None, None) is None
        assert tx.next_exit(plan, 1.0, None, False).rule == "time_stop"

    def test_a_plan_that_runs_nothing_has_nothing_due(self):
        for state in ("no_thesis", "off", "practice", "untracked"):
            assert tx.due_exit(tx.TimeExitPlan(state), 99.0, -1.0, False) is None
            assert tx.next_exit(tx.TimeExitPlan(state), 0.0, -1.0, False) is None

    def test_no_age_is_nothing(self):
        plan = tx.TimeExitPlan("armed", (tx.TimeExit("hard", 1.0),))
        assert tx.due_exit(plan, None, None, None) is None
        assert tx.next_exit(plan, None, None, None) is None

    def test_the_first_rule_decides(self):
        plan = tx.TimeExitPlan("armed", (tx.TimeExit("a", 1.0, r_below=1.0),
                                         tx.TimeExit("b", 2.0),
                                         tx.TimeExit("c", 3.0)))
        assert tx.due_exit(plan, 2.5, 0.5, None).rule == "a"
        assert tx.due_exit(plan, 2.5, 1.5, None).rule == "b"
        assert tx.next_exit(plan, 1.5, 1.5, None).rule == "b"
        assert tx.next_exit(plan, 0.5, 1.5, None).rule == "b"
        assert tx.next_exit(plan, 0.5, 0.5, None).rule == "a"

    def test_due_starts_at_the_rules_hour(self):
        hour = 1.0
        plan = tx.TimeExitPlan("armed", (tx.TimeExit("hard", hour),))
        assert tx.due_exit(plan, hour, None, None) is not None
        assert tx.next_exit(plan, hour, None, None) is None
        assert tx.due_exit(plan, hour - 0.01, None, None) is None
        assert tx.next_exit(plan, hour - 0.01, None, None) is not None

    def test_due_is_exactly_when_the_exit_code_closes(self):
        """The alert's "due now" is the exit code's decision, over the grid
        the card suite drives the plan against."""
        from tests.test_a_position_card_states_its_time_exits import RS, SIGNALS, STRATEGIES, _code_closes, _hours_grid
        for strategy, signal in itertools.product(STRATEGIES, SIGNALS):
            plan = tx.TimeExitPlan("armed", tx.rules_for(
                strategy, signal, r_readable=True, time_stop_on=True,
                smart_exits_on=True, strategy_types=CONFIG.strategy_types))
            for h in _hours_grid(strategy, signal):
                for r in RS:
                    for fee in (True, False):
                        assert (tx.due_exit(plan, h, r, fee) is not None) == \
                            _code_closes(strategy, signal, h, r, fee), (
                                strategy, signal, h, r, fee)

    def test_the_card_status_asks_the_same_reading(self, monkeypatch):
        """Patch the condition and the card's status answers what it said."""
        monkeypatch.setattr(tx, "closes_at_reading", lambda e, r, f: True)
        e = tx.TimeExit("t", 1.0, r_below=1.0)
        assert tx.exit_phrase(e, 2.0, 5.0, None) == "after 1h if under 1R (due now)"


class TestNoTimeStopFieldIsReadByNobody:

    def test_every_time_stop_field_has_a_reader(self):
        """TIME_STOP_{INTRA,SWING}_{WARN,CLOSE}_H and LIMIT_EXPIRE_{INTRA,
        SWING}_H were read by nothing but the heuristic, or by nothing at all:
        a setting an operator changes and nothing reads. Derived from the
        dataclass, so a field added tomorrow needs a reader too."""
        import pathlib

        from bot.config import TimeStopConfig
        src = "\n".join(p.read_text(encoding="utf-8")
                        for p in pathlib.Path("bot").rglob("*.py")
                        if p.name != "config.py")
        for f in dataclasses.fields(TimeStopConfig):
            assert re.search(rf"\.{f.name}\b", src), f.name


# ── 2. the brief counts positions and resting orders apart ───────────────

def _digest_engine(rows=(), shared=(), balance=None, executor=True):
    from bot.core.engine import RuneClawEngine
    ex = (NS(open_positions=list(rows), closed_positions=[],
             closed_trades_read_failed=False) if executor else None)
    eng = NS(live_executor=ex, portfolio=NS(open_positions=list(shared)),
             state="EngineState.IDLE", _live_balance_cache=balance or {},
             _live_balance_cache_ts=0.0)
    eng.live_balance_cached = types.MethodType(
        RuneClawEngine.live_balance_cached, eng)
    return eng


def _row(sym, status="open", direction="LONG"):
    return NS(symbol=sym, direction=direction, status=status)


BOOK = [_row("BTC/USDT"), _row("ETH/USDT", "pending_fill"),
        _row("SOL/USDT", "pending_fill", "SHORT")]


class TestTheBriefCountsPositionsAndOrdersApart:

    def test_a_resting_order_is_not_an_open_position(self, live):
        body = ProactiveMonitor(_digest_engine(BOOK))._digest_body("brief")
        assert "Carrying <b>1</b> open position(s): BTC LONG —" in body, body
        assert ("<b>2</b> resting limit order(s), not yet filled: ETH LONG, "
                "SOL SHORT") in body, body

    def test_the_wrap_says_the_same(self, live):
        body = ProactiveMonitor(_digest_engine(BOOK))._digest_body("wrap")
        assert "Still open: <b>1</b> — BTC LONG" in body, body
        assert "Resting limit orders: <b>2</b> — ETH LONG, SOL SHORT" in body

    def test_no_resting_order_is_no_line(self, live):
        for kind in ("brief", "wrap"):
            body = ProactiveMonitor(_digest_engine(BOOK[:1]))._digest_body(kind)
            assert "resting" not in body.lower(), body

    def test_an_empty_live_book_is_not_the_paper_book(self, live):
        paper = NS(asset="PEPE/USDT", direction=NS(value="LONG"))
        body = ProactiveMonitor(_digest_engine(shared=[paper]))._digest_body("brief")
        assert "Carrying <b>0</b> open position(s) —" in body, body
        assert "PEPE" not in body

    def test_paper_reads_the_shared_book_and_its_direction(self):
        from bot.utils.models import Direction
        paper = NS(asset="PEPE/USDT", direction=Direction.LONG)
        body = ProactiveMonitor(_digest_engine(
            rows=[_row("BTC/USDT")], shared=[paper]))._digest_body("brief")
        assert "Carrying <b>1</b> open position(s): PEPE LONG —" in body, body
        assert "DIREC" not in body

    @pytest.mark.parametrize("kind,words", [
        ("brief", "Open positions <code>unread</code>"),
        ("wrap", "Still open: <code>unread</code>")])
    def test_a_book_that_cannot_be_read_is_not_zero(self, live, kind, words):
        body = ProactiveMonitor(_digest_engine(executor=False))._digest_body(kind)
        assert words in body, body
        assert "<b>0</b>" not in body

    def test_a_long_list_says_how_many_it_left_out(self, live):
        rows = [_row(f"C{i}/USDT") for i in range(8)]
        body = ProactiveMonitor(_digest_engine(rows))._digest_body("brief")
        assert "Carrying <b>8</b>" in body
        assert "C5 LONG and 2 more" in body and "C6" not in body

    def test_a_symbol_is_escaped(self, live):
        body = ProactiveMonitor(_digest_engine([_row("<i>X")]))._digest_body("wrap")
        assert "&lt;i&gt;X LONG" in body


# ── 3. a close that did not happen is not headed "Closed" ────────────────

KEPT_OPEN = {
    "close failed": ("🚨 Smart-exit close FAILED for BTC/USDT — the position is "
                     "still OPEN.\nCLOSE FAILED for BTC/USDT: venue said no"),
    "kept open": ("CLOSE NOT CONFIRMED for ETH/USDT — kept OPEN and tracked; "
                  "PnL so far -$3.20"),
    "unprotected": ("🚨 UNPROTECTED POSITION — SOL/USDT LONG\nNo exchange "
                    "stop-loss could be placed."),
}
CLOSED = ("CLOSED LONG BTC/USDT (TP HIT)\nEntry: $100.0000 → Exit: $110.0000\n"
          "PnL: +$1.0000 (+10.00% on margin after fees / +10.00% move, 1×)")


@pytest.fixture
def operator_chat():
    from tests.test_the_scheduled_posts_say_whose_book_they_read import OPERATOR
    original = CONFIG.telegram
    object.__setattr__(CONFIG, "telegram", dataclasses.replace(
        original, chat_id=OPERATOR, admin_ids=""))
    yield OPERATOR
    object.__setattr__(CONFIG, "telegram", original)


def _close(msg, slot=None, door="close"):
    """The real `start_monitor`, the real forwarder, a stand-in bot."""
    from tests.test_the_scheduled_posts_say_whose_book_they_read import _HookEngine, _wire
    # The operator's slot holds an EARLIER close of another symbol: the card
    # guard refuses it, so the text path is the one driven.
    eng = _HookEngine(live_executor=NS(_last_close_data=slot, _positions={}))
    w = _wire(eng, watchers=())
    if door == "close":
        asyncio.run(w.hooks["set_close_notify_callback"](msg))
    else:
        asyncio.run(w.hooks["set_owner_notify_callback"]("777", "close", msg, None))
    return w


class TestAKeptOpenMessageIsNotHeadedClosed:

    @pytest.mark.parametrize("shape", sorted(KEPT_OPEN))
    def test_the_operator_is_told_it_was_not_closed(self, operator_chat, shape):
        w = _close(KEPT_OPEN[shape])
        [(text, _)] = w.bot.to(operator_chat)
        assert text.startswith("⚠️ <b>Not closed</b>"), text
        assert "Closed" not in text.split("\n")[0]

    @pytest.mark.parametrize("shape", sorted(KEPT_OPEN))
    def test_it_is_not_posted_publicly(self, operator_chat, shape):
        assert _close(KEPT_OPEN[shape]).bot.public() == []

    def test_it_is_recorded_as_not_closed(self, operator_chat):
        w = _close(KEPT_OPEN["close failed"])
        assert [kind for _, kind, _ in w.recorded] == ["NOT_CLOSED"]

    def test_the_owner_is_told_the_same(self, operator_chat):
        w = _close(KEPT_OPEN["kept open"], door="owner")
        [(text, _)] = w.bot.to("777")
        assert text.startswith("⚠️ <b>Not closed</b>"), text

    def test_a_close_with_no_record_is_still_a_close(self, operator_chat):
        """The control: the heading and the public post a real close keeps."""
        w = _close(CLOSED)
        [(text, _)] = w.bot.to(operator_chat)
        assert text.startswith("✅ <b>Closed</b>"), text
        assert len(w.bot.public()) == 1
        assert [kind for _, kind, _ in w.recorded] == ["TRADE_CLOSED"]
