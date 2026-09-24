"""A position card states the time exits the code will run on it.

Five rules can close a live position on the clock (`bot/core/time_exits.py`
lists them) and no card said any of them: a swing trade on a momentum signal
is closed at 16h whatever the R, and its card showed a hold time. And adoption
gave a position the bot did not open the dataclass defaults -- driven, one
somebody opened by hand was closed at market at +2R after 16h, on a momentum
signal nobody assigned it. The operator decided (2026-09-24): a position with
no recorded strategy gets no time exits; its stop and target still apply.

The central claim is the EQUIVALENCE: the plan a card renders closes a
position exactly when the check functions the exit code calls would. That is
driven over a grid of hold times, R values and fee states for every strategy
and signal type, so a threshold changed in one place and not the other, or a
pruning rule that drops a rule that could fire, fails here.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import itertools
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.config import CONFIG
from bot.core import smart_exits
from bot.core import time_exits as tx
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils import i18n
from tests.source_scan import code_only

UTC = timezone.utc
STRATEGIES = ("scalp", "intraday", "swing", "position", "unknown_type")
SIGNALS = ("momentum_confluence", "vwap_reversion", "regime_trend", "volume_spike",
           "funding_arb", "vol_breakout", "capitulation_buy", "no_such_signal")
RS = (-1.0, 0.0, 0.29, 0.3, 0.49, 0.5, 0.99, 1.0, 2.5)


def _hours_grid(strategy, signal):
    """Every threshold either rule table can hold, and a hair either side."""
    marks = {0.0, 0.25, 1.0}
    marks.add(CONFIG.strategy_types.get_time_close_hours(strategy))
    marks.add(float(smart_exits.progress_limit(strategy)[0]))
    hold = smart_exits.signal_hold_hours(signal)
    if hold is not None:
        marks.update({hold, hold * smart_exits.HOLD_HARD_LIMIT_MULT})
    for c, _ in smart_exits.VOLUME_DECAY_STAGES:
        marks.add(float(c))
    out = set()
    for m in marks:
        out.update({m, max(0.0, m - 0.01), m + 0.01})
    return sorted(out)


def _code_closes(strategy, signal, h, r, fee_clear, *, time_stop_on=True,
                 smart_on=True, r_readable=True):
    """What the exit code does, called the way the two exit paths call it."""
    if not time_stop_on:
        return False
    if h >= CONFIG.strategy_types.get_time_close_hours(strategy) and not fee_clear:
        return True
    if not (smart_on and r_readable):
        return False
    return (smart_exits.should_time_exit(strategy, int(h), r)[0]
            or smart_exits.check_signal_hold_limit(signal, h, r)[0]
            or smart_exits.should_volume_decay_exit(signal, int(h), r)[0])


def _plan_closes(exits, h, r, fee_clear):
    for e in exits:
        if h < e.hours:
            continue
        if e.always or (e.fee_profit and not fee_clear) or (
                e.r_below is not None and r < e.r_below):
            return True
    return False


# ── the plan is the code ────────────────────────────────────────────────────

@pytest.mark.parametrize("strategy,signal", list(itertools.product(STRATEGIES, SIGNALS)))
def test_the_plan_closes_exactly_when_the_exit_code_does(strategy, signal):
    exits = tx.rules_for(strategy, signal, r_readable=True, time_stop_on=True,
                         smart_exits_on=True, strategy_types=CONFIG.strategy_types)
    for h in _hours_grid(strategy, signal):
        for r in RS:
            for fee_clear in (True, False):
                assert _plan_closes(exits, h, r, fee_clear) == _code_closes(
                    strategy, signal, h, r, fee_clear), (h, r, fee_clear, exits)


@pytest.mark.parametrize("flags", [
    dict(time_stop_on=True, smart_on=False, r_readable=True),
    dict(time_stop_on=True, smart_on=True, r_readable=False),
    dict(time_stop_on=False, smart_on=True, r_readable=True),
])
def test_what_does_not_run_is_not_listed(flags):
    for strategy, signal in itertools.product(STRATEGIES, SIGNALS):
        exits = tx.rules_for(strategy, signal, r_readable=flags["r_readable"],
                             time_stop_on=flags["time_stop_on"],
                             smart_exits_on=flags["smart_on"],
                             strategy_types=CONFIG.strategy_types)
        for h in _hours_grid(strategy, signal):
            for r in RS:
                for fee_clear in (True, False):
                    assert _plan_closes(exits, h, r, fee_clear) == _code_closes(
                        strategy, signal, h, r, fee_clear, **flags), (flags, h, r)


def test_a_rule_another_one_always_beats_is_not_stated():
    # A swing momentum trade: the 16h hard limit closes it before the 48h time
    # stop or the 48-candle progress rule can, so neither is on the card.
    exits = tx.rules_for("swing", "momentum_confluence", r_readable=True,
                         time_stop_on=True, smart_exits_on=True,
                         strategy_types=CONFIG.strategy_types)
    assert [(e.rule, e.hours) for e in exits] == [("signal_hold", 8.0), ("signal_hard", 16.0)]


def test_at_one_hour_the_higher_bar_wins_whatever_the_order():
    ex = tx._prune([tx.TimeExit("progress", 48.0, r_below=0.5),
                    tx.TimeExit("signal_hold", 48.0, r_below=1.0)])
    assert [e.r_below for e in ex] == [1.0]


def test_the_fee_rule_is_never_compared_with_an_r_rule():
    # Its bar is a price; its size in R depends on the stop distance.
    ex = tx._prune([tx.TimeExit("signal_hold", 8.0, r_below=1.0),
                    tx.TimeExit("time_stop", 48.0, fee_profit=True)])
    assert len(ex) == 2


def test_the_live_loop_calls_no_time_rule_the_plan_does_not_cover():
    # A fifth time rule added to the loop is a rule no card states.
    from bot.core.engine import RuneClawEngine
    tree = ast.parse(inspect.getsource(RuneClawEngine._evaluate_live_smart_exits).strip()
                     .replace("\n    ", "\n"))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and hasattr(smart_exits, n.func.id)}
    # VWAP reversion closes on PRICE, not the clock, and is the stated exclusion.
    assert called == {"should_time_exit", "check_signal_hold_limit",
                      "should_volume_decay_exit", "check_vwap_reversion_exit"}


# ── a position with no recorded strategy gets none ──────────────────────────

def _lp(**kw):
    base = dict(trade_id="TI-1", symbol="PENDLE/USDT:USDT", direction="LONG",
                entry_price=2.0, quantity=10, cost_usd=4, stop_loss=1.94,
                take_profit=2.5, leverage=5, is_spot=False, status="open",
                opened_at=datetime.now(UTC) - timedelta(hours=16.1),
                sl_order_id="sl-1", tp_order_id="tp-1")
    base.update(kw)
    return LivePosition(**base)


@pytest.mark.parametrize("origin,marks,recorded", [
    ("executed", {}, True),
    ("adopted", {}, False),
    ("reclaimed", {}, False),
    ("adopted", {"thesis_source": "inherited"}, True),
    # A record written before the marker: the donor that supplied its levels
    # supplied its strategy.
    ("adopted", {"sl_tp_source": "inherited"}, True),
    ("adopted", {"sl_tp_source": "exchange"}, False),
    ("adopted", {"sl_tp_source": "default"}, False),
    # The explicit marker wins over the legacy reading.
    ("adopted", {"thesis_source": "unrecorded", "sl_tp_source": "inherited"}, False),
])
def test_whose_strategy_is_a_recorded_one(origin, marks, recorded):
    p = _lp(origin=origin)
    for k, v in marks.items():
        setattr(p, k, v)
    assert tx.thesis_recorded(p) is recorded


def _smart_exit_closes(pos, mark):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.ws_feed = SimpleNamespace(is_connected=lambda: True,
                                  get_prices=lambda max_age_sec=None: {pos.symbol: mark})
    eng._last_vwap = {}

    async def _announce(*_a):
        return None
    eng._announce_executor_message = _announce
    closed = []

    async def close_position(tid, reason=""):
        closed.append(reason)
        return f"CLOSED {tid}"
    asyncio.run(eng._evaluate_live_smart_exits(
        SimpleNamespace(_positions={pos.trade_id: pos}, close_position=close_position)))
    return closed


def test_an_adopted_position_is_not_closed_by_a_signal_nobody_assigned():
    # +2R at 16.1h: the momentum hard limit closes a bot-opened trade here.
    assert _smart_exit_closes(_lp(), 2.12), "the control must fire"
    assert _smart_exit_closes(_lp(origin="adopted"), 2.12) == []
    assert _smart_exit_closes(_lp(origin="reclaimed"), 2.12) == []


def test_an_adopted_position_that_inherited_a_strategy_keeps_its_exits():
    p = _lp(origin="adopted")
    p.thesis_source = "inherited"
    assert _smart_exit_closes(p, 2.12)


def _time_stop_closes(pos, last):
    ex = LiveExecutor()
    ex._positions[pos.trade_id] = pos
    ex.close_position = AsyncMock(return_value="CLOSED")
    ex._exchange = AsyncMock()
    ex._exchange.fetch_ticker = AsyncMock(return_value={"last": last})
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = __import__("time").time()
    asyncio.run(ex.check_positions())
    return [c.args[1] for c in ex.close_position.call_args_list]


def test_the_executor_time_stop_skips_an_adopted_position_too():
    # Swing, 50h old, just under entry: not in profit after fees, above the stop.
    old = datetime.now(UTC) - timedelta(hours=50)
    closed = _time_stop_closes(_lp(opened_at=old), 1.99)
    assert closed and closed[0].startswith("TIME_STOP"), closed
    assert _time_stop_closes(_lp(opened_at=old, origin="adopted"), 1.99) == []


def test_the_marker_survives_a_restart(tmp_path):
    p = _lp(origin="adopted")
    p.thesis_source = "inherited"
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions[p.trade_id] = p
    ex._save_positions()
    back = LiveExecutor(state_dir=str(tmp_path))._positions[p.trade_id]
    assert getattr(back, "thesis_source", None) == "inherited"
    assert tx.thesis_recorded(back)


def test_adoption_records_the_inherited_strategy_where_it_copies_it():
    src = code_only(inspect.getsource(LiveExecutor))
    i = src.index("lp.signal_type = getattr(")
    assert 'setattr(lp, "thesis_source", "inherited")' in src[i:i + 400]


# ── the line ────────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _line(pos, mark, lang="en", **cfg):
    ts = SimpleNamespace(enabled=cfg.get("enabled", True),
                         live_auto_close_enabled=cfg.get("smart", True))
    return tx.position_time_exit_line(pos, mark, NOW, ts, CONFIG.strategy_types, lang=lang)


def test_it_counts_down_to_each_rule():
    p = _lp(opened_at=NOW - timedelta(hours=5))
    assert _line(p, 2.03) == ("⏱ Time exits: after 8h if under 1R (in 3h) · "
                              "after 16h whatever the R (in 11h)")


@pytest.mark.parametrize("held,left", [
    # Minutes survive: every other fixture here is a whole number of hours,
    # which is how rounding the countdown to the hour survived a first round.
    (timedelta(hours=5, minutes=20), "in 2h40m"),
    (timedelta(hours=7, minutes=20), "in 40m"),
])
def test_the_countdown_keeps_its_minutes(held, left):
    assert f"after 8h if under 1R ({left})" in _line(_lp(opened_at=NOW - held), 2.03)


def test_a_long_countdown_is_in_days():
    p = _lp(strategy_type="position", signal_type="regime_trend",
            opened_at=NOW - timedelta(hours=5))
    assert "after 144h whatever the R (in 5.8d)" in _line(p, 2.03)


@pytest.mark.parametrize("mark,status", [
    (2.12, "armed, at +2.00R"),
    (2.03, "due now"),
    (None, "armed"),
])
def test_a_rule_whose_hour_has_passed_says_whether_it_is_met(mark, status):
    p = _lp(opened_at=NOW - timedelta(hours=9))
    assert f"after 8h if under 1R ({status})" in _line(p, mark)


@pytest.mark.parametrize("mark,status", [(1.99, "due now"), (2.2, "armed, in profit after fees"),
                                         (None, "armed")])
def test_the_fee_rule_reads_the_time_stops_own_test(mark, status):
    p = _lp(strategy_type="intraday", signal_type="regime_trend",
            opened_at=NOW - timedelta(hours=5))
    assert f"after 4h unless in profit after fees ({status})" in _line(p, mark)


def test_an_unreadable_1r_says_the_r_exits_do_not_run():
    line = _line(_lp(stop_loss=0, opened_at=NOW - timedelta(hours=5)), 2.03)
    assert line.startswith("⏱ Time exits: after 48h unless in profit after fees (in 43h)")
    assert line.endswith("The R-based exits do not run: this position's 1R cannot be read.")
    assert "whatever the R" not in line


def test_with_the_smart_exits_off_only_the_time_stop_is_listed():
    line = _line(_lp(opened_at=NOW - timedelta(hours=5)), 2.03, smart=False)
    assert line == "⏱ Time exits: after 48h unless in profit after fees (in 43h)"


@pytest.mark.parametrize("state,words", [
    ("no_thesis", "adopted with no recorded strategy"),
    ("off", "No time exits run on this bot"),
    ("practice", "practice position"),
    ("untracked", "no record of this position"),
])
def test_each_state_that_runs_nothing_has_its_own_sentence(state, words):
    assert words in tx.time_exit_line(tx.TimeExitPlan(state))


def test_an_unknown_state_is_refused_rather_than_rendered():
    with pytest.raises(ValueError):
        tx.time_exit_line(tx.TimeExitPlan("maybe"))


def test_the_bot_being_switched_off_is_not_an_adopted_position():
    assert _line(_lp(opened_at=NOW), 2.03, enabled=False).startswith(
        "⏱ No time exits run on this bot")
    assert "adopted" in _line(_lp(origin="adopted", opened_at=NOW), 2.03, enabled=False)


# ── the words, in every language ────────────────────────────────────────────

TX_KEYS = sorted(k for k in i18n._STRINGS if k.startswith("tx_"))


def test_every_key_the_module_reads_is_in_the_table():
    used = set(re.findall(r'"(tx_\w+)"', (tx.__file__ and open(tx.__file__).read())))
    trading = open(inspect.getsourcefile(
        __import__("bot.skills.trading_commands", fromlist=["x"]))).read()
    used |= set(re.findall(r'"(tx_\w+)"', trading))
    assert used and used <= set(TX_KEYS), used - set(TX_KEYS)


@pytest.mark.parametrize("key", TX_KEYS)
def test_each_key_is_in_all_fourteen_languages_with_its_placeholders(key):
    entry = i18n._STRINGS[key]
    want = set(re.findall(r"\{(\w+)\}", entry["en"]))
    for code in i18n.SUPPORTED_LANGS:
        assert entry.get(code), (key, code)
        assert set(re.findall(r"\{(\w+)\}", entry[code])) == want, (key, code)


# ── every card carries it ───────────────────────────────────────────────────

def test_the_positions_list_renders_the_row_line_escaped():
    from bot.formatters.rich_cards import render_open_positions
    row = {"pair": "BTCUSDT", "direction": "LONG", "entry": 1.0, "pnl_pct": 1.0,
           "current": 1.01, "size_usd": 10.0, "time_exit_line": "⏱ Time exits: a <b> b"}
    assert "⏱ Time exits: a &lt;b&gt; b" in render_open_positions([row])


def test_the_caption_states_each_position_and_counts_what_does_not_fit():
    from bot.skills import trading_commands as tc
    ps = [_lp(trade_id=f"TI-{i}", symbol=f"C{i}/USDT:USDT", opened_at=NOW)
          for i in range(20)]
    cap = tc._caption_with_time_exits("HEAD", ps, NOW, "en")
    assert len(cap) <= tc._CAPTION_BUDGET
    shown = cap.count("⏱ Time exits:")
    assert 0 < shown < 20
    assert cap.endswith(f"Time exits for {20 - shown} more: open each on /positions.")
    few = tc._caption_with_time_exits("HEAD", ps[:2], NOW, "en")
    assert few.count("⏱ Time exits:") == 2 and "more:" not in few


def test_the_caption_uses_the_marks_the_card_read():
    from bot.skills import trading_commands as tc
    p = _lp(opened_at=NOW - timedelta(hours=9))
    cap = tc._caption_with_time_exits("HEAD", [p], NOW, "en", {p.trade_id: 2.12})
    assert "armed, at +2.00R" in cap


@pytest.mark.parametrize("path,anchor", [
    ("bot/skills/callback_handler.py", "html.escape(_time_line)"),
    ("bot/skills/trading_commands.py", '- {html.escape(_tx)}'),
    ("bot/skills/trading_commands.py", '"time_exit_line": ('),
    ("bot/skills/trading_commands.py", 'cap += "\\n" + html.escape(pos["time_exit_line"])'),
    ("bot/skills/trading_commands.py", "_caption_with_time_exits(\n"),
])
def test_each_card_is_wired(path, anchor):
    # The detail card and the two /livepositions renderers are branches of
    # handlers behind an exchange and a Telegram update; that each renders the
    # line is a shape, stated as one.
    assert anchor in code_only(open(path).read()), (path, anchor)
