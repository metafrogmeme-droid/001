"""A published signal is walked on hourly candles and its outcome is re-sent.

Both producers of the public signal stream pushed ``status: NEW`` and nothing
ever pushed a second row, so every signal stayed NEW for good and the stats
panel's promise that "outcomes appear once signals hit target or stop" had no
path. ``bot.core.signal_outcomes`` is that path; these drive it.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.core import signal_outcomes as so

ROOT = Path(__file__).resolve().parents[1]
H = so.BAR_MS
# 2026-09-28 10:17 UTC: published mid-bar, so the first bar read opens at 11:00.
CREATED_ISO = "2026-09-28T10:17:00+00:00"
CREATED = so.parse_time_ms(CREATED_ISO)
FIRST = CREATED - (CREATED % H) + H          # 11:00, the first bar opened after it
NOW = FIRST + 400 * H                        # long after everything below


def _long(**kw):
    row = {"signal_key": "K1", "symbol": "BTC/USDT", "direction": "LONG",
           "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
           "confidence": 0.71, "created_at": CREATED_ISO, "status": "NEW"}
    row.update(kw)
    return row


def _short(**kw):
    base = {"direction": "SHORT", "entry_price": 100.0, "stop_loss": 105.0,
            "take_profit": 90.0}
    base.update(kw)
    return _long(**base)


def _bar(i, o, h, lo, c):
    return [FIRST + i * H, o, h, lo, c, 1.0]


# ── the walk ────────────────────────────────────────────────────────────────

class TestTheWalk:
    def test_a_pullback_long_reaches_its_entry_then_its_target(self):
        bars = [_bar(0, 102, 103, 101, 102),          # above the entry: waiting
                _bar(1, 102, 102, 99.5, 100),         # trades down to 100: filled
                _bar(2, 100, 111, 99.8, 110)]         # reaches 110: target
        res = so.resolve(_long(), bars, NOW)
        assert res.status == so.TARGET
        assert res.r == 2.0                            # (110-100)/(100-95)
        assert res.triggered_ms == FIRST + H
        assert res.resolved_ms == FIRST + 3 * H

    def test_a_break_long_is_reached_by_a_bar_trading_up_to_it(self):
        row = _long(entry_price=104.0, stop_loss=100.0, take_profit=112.0)
        bars = [_bar(0, 102, 103, 101.5, 102),        # below a break entry
                _bar(1, 102, 104.5, 101.8, 104),      # up to 104: filled
                _bar(2, 104, 104.2, 99.0, 99.5)]      # down to 100: stopped
        res = so.resolve(row, bars, NOW)
        assert res.status == so.STOP and res.r == -1.0

    def test_a_break_entry_price_never_traded_up_to_is_not_reached(self):
        # A long break above the market: a bar that stays under it has not
        # reached it, however far below the entry its low sits.
        row = _long(entry_price=104.0, stop_loss=100.0, take_profit=112.0)
        bars = [_bar(0, 102, 103, 101.5, 102), _bar(1, 102, 103.5, 101, 103)]
        res = so.resolve(row, bars, FIRST + 3 * H)
        assert res.status == so.NEW and res.triggered_ms is None

    def test_a_break_is_reached_on_the_bar_that_trades_up_to_it(self):
        row = _long(entry_price=104.0, stop_loss=100.0, take_profit=112.0)
        bars = [_bar(0, 102, 103, 101.5, 102), _bar(1, 102, 104.5, 101.8, 104)]
        res = so.resolve(row, bars, FIRST + 3 * H)
        assert res.status == so.OPEN and res.triggered_ms == FIRST + H

    def test_a_gap_past_a_pullback_entry_is_not_read_as_a_break(self):
        # One rule for both would read "never traded up to 100" as unfilled.
        bars = [_bar(0, 102, 102.5, 98.0, 99)]        # trades down THROUGH 100
        res = so.resolve(_long(), bars, FIRST + 2 * H)
        assert res.status == so.OPEN

    def test_the_level_ahead_on_the_trigger_bar_counts(self):
        # Pullback long: price came from above, so the stop is ahead.
        bars = [_bar(0, 102, 102, 94, 95)]
        assert so.resolve(_long(), bars, NOW).status == so.STOP

    def test_the_level_behind_on_the_trigger_bar_is_ambiguous(self):
        # Pullback long: the target is behind; it may have come before the fill.
        bars = [_bar(0, 102, 111, 99, 100)]
        res = so.resolve(_long(), bars, NOW)
        assert res.status == so.AMBIGUOUS and res.r is None

    def test_a_break_trigger_bar_scores_the_target_and_not_the_stop(self):
        row = _long(entry_price=104.0, stop_loss=100.0, take_profit=112.0)
        assert so.resolve(row, [_bar(0, 102, 113, 101.8, 112)], NOW).status == so.TARGET
        assert so.resolve(row, [_bar(0, 102, 105, 99, 100)], NOW).status == so.AMBIGUOUS

    def test_a_bar_spanning_both_levels_is_ambiguous(self):
        bars = [_bar(0, 102, 102, 99.5, 100), _bar(1, 100, 111, 94, 100)]
        res = so.resolve(_long(), bars, NOW)
        assert res.status == so.AMBIGUOUS and res.r is None

    def test_the_short_side_is_the_mirror(self):
        bars = [_bar(0, 98, 99, 97, 98), _bar(1, 98, 100.5, 97.5, 100),
                _bar(2, 100, 100.2, 89, 90)]
        res = so.resolve(_short(), bars, NOW)
        assert res.status == so.TARGET and res.r == 2.0
        bars = [_bar(0, 98, 99, 97, 98), _bar(1, 98, 100.5, 97.5, 100),
                _bar(2, 100, 106, 99, 105)]
        assert so.resolve(_short(), bars, NOW).status == so.STOP

    def test_a_bar_that_opened_before_publication_is_not_read(self):
        # The bar forming at publication hit the target at 10:30; it is not
        # the market answering the call.
        before = [FIRST - H, 102, 111, 99, 110, 1.0]
        res = so.resolve(_long(), [before, _bar(0, 102, 103, 101, 102)], FIRST + 2 * H)
        assert res.status == so.NEW

    def test_the_entry_window_is_the_one_handed_in_not_the_rows_expires_at(self):
        # expires_at is the five-minute follower window, finer than a bar: a
        # walk that honoured it would expire nearly every signal.
        row = _long(expires_at="2026-09-28T10:22:00+00:00")
        bars = [_bar(0, 102, 103, 101, 102), _bar(1, 102, 102, 99.5, 100),
                _bar(2, 100, 111, 99.8, 110)]
        assert so.resolve(row, bars, NOW, 4 * 3600).status == so.TARGET

    def test_a_signal_not_filled_inside_the_window_expired(self):
        window = 2 * 3600
        bars = [_bar(i, 102, 103, 101, 102) for i in range(4)]
        res = so.resolve(_long(), bars, NOW, window)
        assert res.status == so.EXPIRED and res.r is None
        assert res.resolved_ms == CREATED + window * 1000

    def test_the_window_counts_bars_that_open_before_it_closes(self):
        # Filled on the last bar that opened inside a 2h window (11:00 bar
        # opens 43 minutes after publication, the 12:00 bar 103 minutes).
        bars = [_bar(0, 102, 103, 101, 102), _bar(1, 102, 102, 99.5, 100)]
        assert so.resolve(_long(), bars, FIRST + 3 * H, 2 * 3600).status == so.OPEN
        # A bar opening exactly as the window closes is outside it.
        at_close = CREATED + 2 * 3600 * 1000
        bar = [at_close, 102, 102, 99.5, 100, 1.0]
        res = so.resolve(_long(), [_bar(0, 102, 103, 101, 102), bar], NOW, 2 * 3600)
        assert res.status == so.EXPIRED

    def test_still_waiting_inside_the_window_is_new_not_expired(self):
        bars = [_bar(0, 102, 103, 101, 102)]
        res = so.resolve(_long(), bars, FIRST + H, 4 * 3600)
        assert res.status == so.NEW and not res.retry

    def test_an_open_call_with_no_exit_for_a_week_is_no_exit(self):
        bars = [_bar(0, 102, 102, 99.5, 100)] + [
            _bar(i, 101, 102, 99, 101) for i in range(1, 24 * 7 + 3)]
        res = so.resolve(_long(), bars, NOW)
        assert res.status == so.NO_EXIT
        assert res.resolved_ms == FIRST + so.HORIZON_S * 1000
        # The same, with the bars ending early but the clock past the horizon.
        res = so.resolve(_long(), bars[:3], FIRST + so.HORIZON_S * 1000 + 1)
        assert res.status == so.NO_EXIT

    def test_a_level_reached_after_the_horizon_does_not_score(self):
        # The week ends first: a target touched two bars later is not this
        # call's outcome.
        bars = [_bar(0, 102, 102, 99.5, 100)] + [
            _bar(i, 101, 102, 99, 101) for i in range(1, 24 * 7 + 2)]
        bars.append(_bar(24 * 7 + 2, 101, 111, 100, 110))
        res = so.resolve(_long(), bars, NOW)
        assert res.status == so.NO_EXIT and res.r is None

    def test_an_open_call_inside_the_horizon_stays_open(self):
        bars = [_bar(0, 102, 102, 99.5, 100), _bar(1, 101, 102, 99, 101)]
        res = so.resolve(_long(), bars, FIRST + 3 * H)
        assert res.status == so.OPEN and res.triggered_ms == FIRST

    @pytest.mark.parametrize("row", [
        _long(stop_loss=101.0),                     # stop above a long's entry
        _long(take_profit=99.0),                    # target below it
        _short(stop_loss=99.0),
        _long(direction="SIDEWAYS"),
        _long(entry_price=0), _long(entry_price="junk"), _long(stop_loss=None),
        _long(take_profit=True),                    # a bool is not a price
    ])
    def test_levels_that_describe_no_trade_are_unscored(self, row):
        res = so.resolve(row, [_bar(0, 102, 111, 94, 100)], NOW)
        assert res.status == so.UNSCORED and not res.retry

    def test_an_unreadable_publication_time_is_unscored(self):
        res = so.resolve(_long(created_at="yesterday"), [_bar(0, 1, 1, 1, 1)], NOW)
        assert res.status == so.UNSCORED

    def test_candles_that_start_after_the_signal_are_unscored(self):
        # The bars in between are missing, and the entry may have been in them.
        res = so.resolve(_long(), [_bar(2, 102, 103, 101, 102)], NOW)
        assert res.status == so.UNSCORED

    def test_no_candle_past_the_window_is_unscored_and_inside_it_waits(self):
        assert so.resolve(_long(), [], NOW, 3600).status == so.UNSCORED
        res = so.resolve(_long(), [], CREATED + 60_000, 3600)
        assert res.status == so.NEW and res.retry

    @pytest.mark.parametrize("bad", [
        [FIRST],                                     # too short
        ["noon", 1, 1, 1, 1, 1],                     # time unreadable
        None,
    ])
    def test_a_candle_that_does_not_read_is_asked_again_not_scored(self, bad):
        res = so.resolve(_long(), [bad], NOW)
        assert res.retry and res.status == so.NEW

    def test_a_high_or_low_that_does_not_read_is_asked_again(self):
        bars = [_bar(0, 102, 102, 99.5, 100), [FIRST + H, 101, None, 99, 100, 1]]
        res = so.resolve(_long(), bars, NOW)
        assert res.retry and res.status == so.OPEN and res.triggered_ms == FIRST
        res = so.resolve(_long(), [[FIRST, None, 102, 99, 100, 1]], NOW)
        assert res.retry and res.status == so.NEW


@pytest.mark.parametrize("value, want", [
    (7200, 7200), (7200.9, 7200), (None, so.DEFAULT_ENTRY_WINDOW_S),
    (0, so.DEFAULT_ENTRY_WINDOW_S), (-5, so.DEFAULT_ENTRY_WINDOW_S),
    (True, so.DEFAULT_ENTRY_WINDOW_S), ("junk", so.DEFAULT_ENTRY_WINDOW_S),
    (float("nan"), so.DEFAULT_ENTRY_WINDOW_S),
])
def test_an_entry_window_that_does_not_read_is_the_default(value, want):
    assert so.entry_window_s(value) == want


def test_the_default_window_is_the_resting_limit_clocks_declared_default():
    # Read off the declaration, not the value in force: an operator's .env
    # must not fail a test of the default.
    import re
    src = (ROOT / "bot/config.py").read_text()
    m = re.search(r'expire_seconds: int = int\(_env_float\("LIMIT_ORDER_EXPIRE_SEC", (\d+)\)\)', src)
    assert m, "the resting-limit clock's declaration moved"
    assert so.DEFAULT_ENTRY_WINDOW_S == int(m.group(1))


@pytest.mark.parametrize("sym, want", [
    ("BTC/USDT", "BTC/USDT"), ("btc", "BTC/USDT"), ("BTCUSDT", "BTC/USDT"),
    ("ETH/USDT:USDT", "ETH/USDT:USDT"), ("", None), (None, None), ("BT C", None),
    ("USDT", "USDT/USDT"),
])
def test_the_market_is_read_from_either_producers_spelling(sym, want):
    assert so.market_for(sym) == want


def test_both_publication_time_spellings_read():
    assert so.parse_time_ms("2026-09-28 10:17 UTC") == CREATED
    assert so.parse_time_ms(CREATED_ISO) == CREATED
    assert so.parse_time_ms("2026-09-28T10:17:00") == CREATED    # naive is UTC
    assert so.parse_time_ms("junk") is None and so.parse_time_ms(5) is None


# ── the ledger ──────────────────────────────────────────────────────────────

@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "signal_outcomes.json"
    monkeypatch.setattr(so, "ledger_path", lambda: p)
    return p


class TestTheLedger:
    def test_a_published_row_is_recorded_whole_once(self, ledger):
        assert so.record_published([_long(), _long(), {"signal_key": "x"}]).added == 1
        data = json.loads(ledger.read_text())
        entry = data["signals"]["K1"]
        assert entry["row"]["confidence"] == 0.71        # the WHOLE row
        assert entry["status"] == "NEW" and entry["synced"] is True
        assert so.record_published([_long(status="OPEN")]).added == 0

    def test_an_unreadable_ledger_is_not_written_over(self, ledger, caplog):
        ledger.write_text("{not json")
        with caplog.at_level(logging.ERROR, logger=so.__name__):
            assert so.record_published([_long()]) is None
        assert ledger.read_text() == "{not json"
        assert so.rows_due() is None                         # never an empty list
        assert so.apply("K1", so.Resolution(so.TARGET, "t", r=2.0), NOW) is None
        assert not so.mark_synced("K1")
        assert ledger.read_text() == "{not json"

    def test_due_is_pending_or_unsynced_oldest_checked_first(self, ledger):
        so.record_published([_long(signal_key=k) for k in "ABCD"])
        so.apply("A", so.Resolution(so.OPEN, "o"), 500)
        so.apply("B", so.Resolution(so.TARGET, "t", r=2.0, resolved_ms=9), 100)
        so.apply("C", so.Resolution(so.STOP, "s", r=-1.0, resolved_ms=9), 200)
        so.mark_synced("C")
        keys = [k for k, _ in so.rows_due()]
        assert keys == ["D", "B", "A"]                  # C is final and synced
        assert [k for k, _ in so.rows_due(limit=2)] == ["D", "B"]

    def test_a_changed_word_is_returned_unsynced(self, ledger):
        so.record_published([_long()])
        got = so.apply("K1", so.Resolution(so.TARGET, "t", r=2.0, resolved_ms=5), 7)
        assert got["status"] == so.TARGET and got["r"] == 2.0 and got["synced"] is False
        assert so.apply("K1", so.Resolution(so.STOP, "s", r=-1.0), 8) is None  # final
        entry = json.loads(ledger.read_text())["signals"]["K1"]
        assert entry["status"] == so.TARGET and entry["checked_ms"] == 8

    def test_an_open_call_does_not_go_back_to_new(self, ledger):
        so.record_published([_long()])
        so.apply("K1", so.Resolution(so.OPEN, "o", triggered_ms=1), 1)
        assert so.apply("K1", so.Resolution(so.NEW, "n"), 2) is None
        assert json.loads(ledger.read_text())["signals"]["K1"]["status"] == so.OPEN

    def test_a_retry_records_the_check_and_nothing_else(self, ledger):
        so.record_published([_long()])
        assert so.apply("K1", so.Resolution(so.NEW, "r", retry=True), 9) is None
        entry = json.loads(ledger.read_text())["signals"]["K1"]
        assert entry["checked_ms"] == 9 and entry["status"] == so.NEW

    def test_a_retry_does_not_record_the_word_it_carries(self, ledger):
        # A walk that saw the entry and then an unreadable bar answers OPEN
        # with retry: the word is not recorded until a walk reads through.
        so.record_published([_long()])
        assert so.apply("K1", so.Resolution(so.OPEN, "r", retry=True), 9) is None
        assert json.loads(ledger.read_text())["signals"]["K1"]["status"] == so.NEW

    def test_the_same_word_again_is_not_a_change(self, ledger):
        so.record_published([_long()])
        so.apply("K1", so.Resolution(so.OPEN, "o"), 1)
        so.mark_synced("K1")
        assert so.apply("K1", so.Resolution(so.OPEN, "o"), 2) is None

    def test_an_unknown_key_is_not_created(self, ledger):
        so.record_published([_long()])
        assert so.apply("NOPE", so.Resolution(so.TARGET, "t", r=1.0), 1) is None
        assert "NOPE" not in json.loads(ledger.read_text())["signals"]

    def test_mark_synced_is_one_way(self, ledger):
        so.record_published([_long()])
        so.apply("K1", so.Resolution(so.TARGET, "t", r=2.0), 1)
        assert so.mark_synced("K1") is True
        assert so.mark_synced("K1") is False

    def test_old_synced_final_rows_are_pruned_and_unsynced_kept(self):
        now = 10**13
        old = now - so.KEEP_RESOLVED_S * 1000 - 1
        sigs = {
            "done": {"status": so.TARGET, "synced": True, "resolved_ms": old},
            "unsent": {"status": so.TARGET, "synced": False, "resolved_ms": old},
            "fresh": {"status": so.STOP, "synced": True, "resolved_ms": now},
            "open": {"status": so.OPEN, "synced": True, "resolved_ms": None},
        }
        so._prune(sigs, now)
        assert set(sigs) == {"unsent", "fresh", "open"}

    def test_the_cap_drops_final_rows_before_pending_ones(self, monkeypatch):
        monkeypatch.setattr(so, "MAX_ROWS", 3)
        now = 10**13
        sigs = {
            "p1": {"status": so.NEW}, "p2": {"status": so.OPEN},
            "t_old": {"status": so.TARGET, "synced": False, "resolved_ms": now - 5},
            "t_new": {"status": so.STOP, "synced": False, "resolved_ms": now},
        }
        so._prune(sigs, now)
        assert set(sigs) == {"p1", "p2", "t_new"}


@pytest.mark.parametrize("status, r, want_pnl, final", [
    (so.TARGET, 2.0, 2.0, True), (so.STOP, -1.0, -1.0, True),
    (so.AMBIGUOUS, None, None, True), (so.EXPIRED, 3.0, None, True),
    (so.OPEN, None, None, False), (so.NEW, None, None, False),
])
def test_the_resent_row_carries_an_r_only_for_the_two_scored_words(status, r, want_pnl, final):
    row = so.outcome_row({"row": _long(), "status": status, "r": r,
                          "resolved_ms": FIRST})
    assert row["status"] == status
    assert row["pnl"] == want_pnl
    assert (row["resolved_at"] != "") is final
    assert row["confidence"] == 0.71 and row["entry_price"] == 100.0


def test_publish_records_then_sends(ledger):
    sent = []
    so.publish_signals([_long()], sync_fn=sent.append)
    assert sent == [[_long()]]
    assert "K1" in json.loads(ledger.read_text())["signals"]


def test_publish_defaults_to_the_background_sync(ledger, monkeypatch):
    from bot.utils import website_sync
    sent = []
    monkeypatch.setattr(website_sync, "sync_signals_in_background", sent.append)
    so.publish_signals([_long()])
    assert sent == [[_long()]]


def test_publish_still_sends_when_the_ledger_cannot_record(ledger):
    ledger.write_text("{not json")
    sent = []
    so.publish_signals([_long()], sync_fn=sent.append)
    assert sent and ledger.read_text() == "{not json"


def test_both_producers_publish_through_the_ledger():
    # The engine's publish step is a seam of its own (`_publish_engine_ideas`,
    # so its once-per-call rule can be driven); the tick reaches it, and
    # neither sends a signal around the ledger.
    tick = [n for n in ast.walk(ast.parse((ROOT / "bot/core/engine.py").read_text()))
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_tick"]
    assert len(tick) == 1
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_publish_engine_ideas" for n in ast.walk(tick[0]))
    assert not [n for n in ast.walk(tick[0]) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in ("sync_signals_in_background", "sync_signals")]
    for rel, fn in (("bot/core/engine.py", "_publish_engine_ideas"),
                    ("bot/skills/scan_skill.py", "_push_scan_to_dashboard")):
        tree = ast.parse((ROOT / rel).read_text())
        defs = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn]
        assert len(defs) == 1, rel
        imports = [n for n in ast.walk(defs[0]) if isinstance(n, ast.ImportFrom)
                   and n.module == "bot.core.signal_outcomes"]
        bound = {a.asname or a.name for n in imports for a in n.names if a.name == "publish_signals"}
        assert bound, f"{rel}:{fn} does not import publish_signals"
        calls = [n for n in ast.walk(defs[0]) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id in bound]
        assert calls, f"{rel}:{fn} never calls publish"
        pushes = [n for n in ast.walk(defs[0]) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)
                  and n.func.id in ("sync_signals_in_background", "sync_signals")]
        assert not pushes, f"{rel}:{fn} sends signals around the ledger"


# ── the engine's walk ───────────────────────────────────────────────────────

class _Ex:
    pass


def _engine(bars_for, monkeypatch, sent, ok=True):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    ex = _Ex()
    asked = []

    async def get_ex():
        return ex

    async def cached(exchange, symbol, timeframe, limit=100, ttl=120):
        asked.append((exchange, symbol, timeframe, limit))
        got = bars_for(symbol)
        if isinstance(got, BaseException):
            raise got
        return got

    eng.scanner = SimpleNamespace(_get_exchange=get_ex, _get_futures_exchange=get_ex)
    eng._cached_ohlcv = cached
    from bot.utils import website_sync

    def sync(rows):
        sent.extend(rows)
        return ok() if callable(ok) else ok
    monkeypatch.setattr(website_sync, "sync_signals", sync)
    return eng, asked


def test_the_engine_walks_resolves_and_resends_the_whole_row(ledger, monkeypatch):
    so.record_published([_long()])
    sent = []
    bars = [_bar(0, 102, 103, 101, 102), _bar(1, 102, 102, 99.5, 100),
            _bar(2, 100, 111, 99.8, 110)]
    eng, asked = _engine(lambda s: bars, monkeypatch, sent)
    landed = asyncio.run(eng._resolve_signal_outcomes(now_ms=NOW))
    assert landed == 1
    assert asked and asked[0][1:] == ("BTC/USDT", so.TIMEFRAME, so.FETCH_LIMIT)
    assert len(sent) == 1
    row = sent[0]
    assert row["status"] == so.TARGET and row["pnl"] == 2.0 and row["resolved_at"]
    assert row["confidence"] == 0.71 and row["take_profit"] == 110.0
    entry = json.loads(ledger.read_text())["signals"]["K1"]
    assert entry["synced"] is True


def test_the_walk_hands_the_resting_limit_clock_as_the_window(ledger, monkeypatch):
    from bot.config import CONFIG
    so.record_published([_long()])
    seen = []
    real = so.resolve

    def spy(row, bars, now_ms, window_s=None):
        seen.append(window_s)
        return real(row, bars, now_ms, window_s)
    monkeypatch.setattr(so, "resolve", spy)
    eng, _ = _engine(lambda s: [_bar(0, 102, 103, 101, 102)], monkeypatch, [])
    asyncio.run(eng._resolve_signal_outcomes(now_ms=FIRST + 2 * H))
    assert seen == [CONFIG.limit_orders.expire_seconds]


def test_a_pass_is_throttled(ledger, monkeypatch):
    so.record_published([_long()])
    eng, asked = _engine(lambda s: [_bar(0, 102, 103, 101, 102)], monkeypatch, [])
    asyncio.run(eng._resolve_signal_outcomes(now_ms=FIRST + 2 * H))
    asyncio.run(eng._resolve_signal_outcomes(now_ms=FIRST + 2 * H))
    assert len(asked) == 1


def test_a_resend_that_did_not_land_is_sent_again_without_a_second_walk(ledger, monkeypatch):
    so.record_published([_long()])
    sent = []
    answers = iter([False, True])
    bars = [_bar(0, 102, 102, 94, 95)]
    eng, asked = _engine(lambda s: bars, monkeypatch, sent, ok=lambda: next(answers))
    assert asyncio.run(eng._resolve_signal_outcomes(now_ms=NOW)) == 0
    assert json.loads(ledger.read_text())["signals"]["K1"]["synced"] is False
    eng._signal_outcome_pass_at = None
    assert asyncio.run(eng._resolve_signal_outcomes(now_ms=NOW)) == 1
    assert len(asked) == 1 and [r["status"] for r in sent] == [so.STOP, so.STOP]


def test_a_candle_read_that_raised_is_asked_again_and_sends_nothing(ledger, monkeypatch):
    so.record_published([_long()])
    sent = []
    eng, _ = _engine(lambda s: TimeoutError("boom"), monkeypatch, sent)
    assert asyncio.run(eng._resolve_signal_outcomes(now_ms=NOW)) == 0
    entry = json.loads(ledger.read_text())["signals"]["K1"]
    assert sent == [] and entry["status"] == so.NEW and entry["checked_ms"] is None


def test_a_symbol_naming_no_market_is_sent_unscored(ledger, monkeypatch):
    so.record_published([_long(symbol="B T C")])
    sent = []
    eng, asked = _engine(lambda s: [], monkeypatch, sent)
    asyncio.run(eng._resolve_signal_outcomes(now_ms=NOW))
    assert asked == [] and sent[0]["status"] == so.UNSCORED


def test_a_word_that_did_not_change_is_not_resent(ledger, monkeypatch):
    so.record_published([_long()])
    sent = []
    eng, _ = _engine(lambda s: [_bar(0, 102, 103, 101, 102)], monkeypatch, sent)
    asyncio.run(eng._resolve_signal_outcomes(now_ms=FIRST + 2 * H))
    assert sent == []


def test_an_unreadable_ledger_walks_nothing(ledger, monkeypatch, caplog):
    ledger.write_text("{not json")
    sent = []
    eng, asked = _engine(lambda s: [], monkeypatch, sent)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(eng._resolve_signal_outcomes(now_ms=NOW)) == 0
    assert asked == [] and sent == []
    assert any("could not be read" in r.getMessage() for r in caplog.records)


def test_the_tick_runs_the_walk_under_the_maintenance_cap():
    src = (ROOT / "bot/core/engine.py").read_text()
    tree = ast.parse(src)
    tick = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_tick"]
    assert len(tick) == 1
    body = ast.unparse(tick[0])
    assert "self._with_maintenance_cap(self._resolve_signal_outcomes()" in body


def test_the_harness_cleans_the_ledger():
    # The ledger lives under a directory the harness removes between tests,
    # so a test that records a signal cannot leave it for the next one.
    from bot.utils.paths import REPO_ROOT
    from tests import conftest
    rel = so.ledger_path().relative_to(REPO_ROOT).as_posix()
    assert any(rel.startswith(d.rstrip("/") + "/")
               for d in conftest._STATE_DIRS)
