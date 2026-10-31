"""The xsmom + BTC regime PAPER sleeve: what it records, and what it never does.

`bot/sleeve/paper.py` forward-tests the one candidate of the 2026-10-28 search
that cleared its bars. It is a simulated book: weights of sleeve equity, an
index from 100, BTC buy-and-hold beside it from the same close. These tests
drive it: the flag's two arms, the 14-day cadence, a regime flip, the fee, a
missing candle (skipped and said so, never a zero return), a coin with too
little history, a restart, a record that does not read, the `/sleeve` card,
the handler and the engine hook. The rule itself is held to the study by
`tests/test_the_xsmom_sleeve_matches_the_study.py`.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import ccxt
import numpy as np
import pytest

from bot.config import CONFIG
from bot.sleeve import paper
from bot.sleeve.paper import (
    ALREADY,
    DISABLED,
    NOT_ASKED,
    RECORDED,
    SKIPPED,
    UNREADABLE,
    CandleRead,
    advance,
    card_text,
    load_record,
    max_drawdown,
    run_once,
)
from bot.sleeve.xsmom_regime import FEE, PRIMARY, UNIVERSE, Params, next_rebalance_day
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
#: A scheduled rebalance close, and the first one after it.
R = next_rebalance_day(date(2026, 3, 1))
R2 = R + timedelta(days=14)
NOW = "2026-01-01T00:00:00+00:00"


def make_closes(end: date, n: int = 300, *, btc_drift: float = 0.004, seed: int = 3,
                crash_from: date | None = None, crash: float = 0.05) -> dict:
    """Twenty coins of daily closes ending at ``end``. BTC trends steadily by
    ``btc_drift`` a day (a flat noise of 0.2%), so its regime is unambiguous;
    from ``crash_from`` it falls ``crash`` a day. The alts carry drifts from
    -0.2% to +0.4% a day and 3% daily noise, so the top of the ranking has
    positive momentum and a real vol."""
    rng = np.random.default_rng(seed)
    days = [end - timedelta(days=n - 1 - i) for i in range(n)]
    out: dict = {}
    for j, sym in enumerate(UNIVERSE):
        if sym == "BTC":
            steps = btc_drift + 0.002 * rng.standard_normal(n)
            if crash_from is not None:
                steps = np.where([d >= crash_from for d in days], -crash, steps)
        else:
            steps = 0.001 * ((j % 7) - 2) + 0.03 * rng.standard_normal(n)
        path = 100 * np.exp(np.cumsum(steps))
        out[sym] = {d.isoformat(): float(v) for d, v in zip(days, path)}
    return out


def read_of(closes: dict, *, drop: dict | None = None, absent: dict | None = None,
            unread: dict | None = None) -> CandleRead:
    """The run's read: ``drop`` removes closes ({sym: [iso, ...]})."""
    c = copy.deepcopy(closes)
    for sym, days in (drop or {}).items():
        for d in days:
            c[sym].pop(d, None)
    for sym in absent or {}:
        c.pop(sym, None)
    for sym in unread or {}:
        c.pop(sym, None)
    return CandleRead(closes=c, unread=dict(unread or {}), absent=dict(absent or {}))


def run_days(closes: dict, first: date, last: date, state=None, p: Params = PRIMARY):
    """Run once a day, ``today`` from ``first`` to ``last``, on full data."""
    outs = []
    d = first
    while d <= last:
        state, outcome, _note = advance(state, read_of(closes), d, NOW, p)
        outs.append(outcome)
        d += timedelta(days=1)
    return state, outs


@pytest.fixture
def flag():
    was = CONFIG.sleeve_paper_enabled

    def _set(on: bool) -> None:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", on)
    yield _set
    object.__setattr__(CONFIG, "sleeve_paper_enabled", was)


class FakeVenue:
    """A public candle source: what ``fetch_closed_ohlcv`` asks of a ccxt
    client, and a record of every symbol asked for."""

    def __init__(self, closes: dict, *, fail=(), unknown=()):
        self.closes, self.fail, self.unknown = closes, set(fail), set(unknown)
        self.calls: list[str] = []

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        assert timeframe == "1d"
        self.calls.append(symbol)
        base = symbol.split("/")[0]
        if base in self.unknown:
            raise ccxt.BadSymbol(f"bitget does not have market symbol {symbol}")
        if base in self.fail:
            raise ccxt.NetworkError("a message with apiKey=SECRET in it")
        rows = []
        for d, c in sorted(self.closes[base].items()):
            ms = int(datetime.fromisoformat(d).replace(tzinfo=UTC).timestamp() * 1000)
            rows.append([ms, c, c, c, c, 1.0])
        return rows[-limit:]

    def parse_timeframe(self, tf):
        return 86400

    def milliseconds(self):
        return int(time.time() * 1000)


def _run(coro):
    return asyncio.run(coro)


# ── the flag ─────────────────────────────────────────────────────────────────

def test_the_flag_ships_off():
    """Default OFF, read where every other flag is read."""
    from bot.config import AppConfig
    assert AppConfig.__dataclass_fields__["sleeve_paper_enabled"].default is False
    assert "SLEEVE_PAPER_ENABLED" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_flag_off_reads_nothing_and_writes_nothing(tmp_path, flag):
    flag(False)
    venue = FakeVenue(make_closes(R))
    path = tmp_path / "rec.json"
    res = _run(run_once(venue, today=R, path=path))
    assert res.outcome == DISABLED and "SLEEVE_PAPER_ENABLED" in res.note
    assert venue.calls == [] and not path.exists()


def test_flag_on_reads_the_universe_and_writes_the_record(tmp_path, flag):
    flag(True)
    venue = FakeVenue(make_closes(R))
    path = tmp_path / "rec.json"
    res = _run(run_once(venue, today=R, path=path))
    assert res.outcome == RECORDED
    assert sorted(venue.calls) == sorted(f"{s}/USDT" for s in UNIVERSE)
    st = json.loads(path.read_text())
    assert st["paper_only"] is True and st["started"] == (R - timedelta(days=1)).isoformat()
    assert st["sleeve_index"] == 100.0 and st["btc_index"] == 100.0


# ── the cadence, the fee, the regime ─────────────────────────────────────────

def test_the_book_starts_in_cash_and_rebalances_every_14_days():
    closes = make_closes(R2 + timedelta(days=5))
    st, outs = run_days(closes, R - timedelta(days=2), R2 + timedelta(days=3))
    assert set(outs) == {RECORDED}
    assert [r["day"] for r in st["rebalances"]] == [R.isoformat(), R2.isoformat()]
    assert all(r["why"] == "schedule" for r in st["rebalances"])
    # Before the first scheduled close the book held nothing and earned nothing.
    pre = [h for h in st["history"] if h["day"] < R.isoformat()]
    assert pre and all(h["sleeve"] == 100.0 for h in pre)
    assert st["rebalances"][0]["target"], "the regime is on: the first rebalance holds coins"
    assert all(0 < w for w in st["weights"].values())
    assert sum(st["weights"].values()) <= 1.0 + 1e-12


def test_a_rebalance_pays_the_fee_on_the_weight_it_trades():
    closes = make_closes(R2 + timedelta(days=2))
    st, _ = run_days(closes, R, R)              # inception the day before R
    before = st["sleeve_index"]
    st, _ = run_days(closes, R + timedelta(days=1), R + timedelta(days=1), st)
    reb = st["rebalances"][0]
    traded = sum(reb["target"].values())         # from all cash
    assert traded > 0
    assert reb["turnover"] == pytest.approx(traded, rel=1e-12)
    assert reb["cost_fraction"] == pytest.approx(FEE * traded, rel=1e-12)
    # Cash earns nothing, so the only move on the day is the fee.
    assert st["sleeve_index"] == pytest.approx(before * (1 - FEE * traded), rel=1e-12)
    assert st["history"][-1]["sleeve"] == st["sleeve_index"]
    # The other arm: no fee, no move.
    free, _ = run_days(closes, R, R + timedelta(days=1), p=Params(fee=0.0))
    assert free["sleeve_index"] == before


def test_regime_off_is_all_cash():
    closes = make_closes(R + timedelta(days=2), btc_drift=-0.004)
    st, _ = run_days(closes, R - timedelta(days=1), R + timedelta(days=1))
    reb = st["rebalances"][0]
    assert reb["day"] == R.isoformat() and reb["regime_on"] is False
    assert reb["target"] == {} and st["weights"] == {}
    assert "below" in reb["reason"] and st["regime"]["on"] is False
    assert reb["cost_fraction"] == 0.0


def test_a_regime_flip_forces_the_book_to_cash_between_scheduled_closes():
    crash = R + timedelta(days=3)
    closes = make_closes(R2 + timedelta(days=2), crash_from=crash)
    st, _ = run_days(closes, R, R2 + timedelta(days=1))
    flips = [r for r in st["rebalances"] if "regime flip" in r["why"]]
    assert len(flips) == 1, st["rebalances"]
    flip = flips[0]
    day = date.fromisoformat(flip["day"])
    assert R < day < R2 and flip["why"] == "regime flip"
    assert flip["target"] == {} and flip["regime_on"] is False
    # Selling the whole drifted book pays the fee on all of it.
    assert flip["cost_fraction"] == pytest.approx(FEE * flip["turnover"], rel=1e-12)
    assert flip["turnover"] > 0
    # The scheduled close after the flip finds the regime still off.
    assert st["rebalances"][-1]["day"] == R2.isoformat()
    assert st["rebalances"][-1]["target"] == {}


# ── unreadable is never zero ─────────────────────────────────────────────────

def _holding(closes):
    st, _ = run_days(closes, R, R + timedelta(days=1))
    assert st["weights"], "the fixture must hold coins for this test to mean anything"
    return st


def test_a_held_coins_missing_close_skips_the_run_and_marks_no_zero_return():
    d1, d2 = R + timedelta(days=1), R + timedelta(days=2)
    closes = make_closes(R + timedelta(days=5))
    st = _holding(closes)
    held = sorted(st["weights"])[0]
    gap, outcome, note = advance(st, read_of(closes, drop={held: [d1.isoformat()]}),
                                 d2, NOW)
    assert outcome == SKIPPED and held in note
    assert gap["sleeve_index"] == st["sleeve_index"]
    assert gap["last_day"] == st["last_day"] and len(gap["history"]) == len(st["history"])
    assert gap["last_run"]["outcome"] == SKIPPED
    # The next run reads both days and marks them at what the prices did.
    after, outcome, _ = advance(gap, read_of(closes), d2 + timedelta(days=1), NOW)
    control, _ = run_days(closes, d2, d2 + timedelta(days=1), copy.deepcopy(st))
    assert outcome == RECORDED
    assert [h["day"] for h in after["history"][-2:]] == [d1.isoformat(), d2.isoformat()]
    assert after["sleeve_index"] == pytest.approx(control["sleeve_index"], rel=1e-12)
    assert after["sleeve_index"] != st["sleeve_index"]


def test_a_missing_candle_on_a_rebalance_close_skips_that_rebalance_and_says_why():
    closes = make_closes(R + timedelta(days=5))
    st, _ = run_days(closes, R - timedelta(days=2), R)       # marked through R-1, cash
    st, outcome, note = advance(st, read_of(closes, drop={"ETH": [R.isoformat()]}),
                                R + timedelta(days=1), NOW)
    assert outcome == SKIPPED and "ETH" in note and st["last_day"] < R.isoformat()
    assert st["rebalances"] == [] and st["skipped"] == []
    st, outcome, _ = advance(st, read_of(closes), R + timedelta(days=2), NOW)
    assert outcome == RECORDED
    assert st["rebalances"] == [] and st["weights"] == {}
    [skip] = st["skipped"]
    assert skip["day"] == R.isoformat() and skip["due"] == "schedule"
    assert "ETH" in skip["reason"] and "kept its weights" in skip["reason"]


def test_a_day_no_run_reached_is_a_skipped_rebalance_not_a_retroactive_trade():
    closes = make_closes(R + timedelta(days=5))
    st, _ = run_days(closes, R - timedelta(days=2), R - timedelta(days=2))
    st, outcome, _ = advance(st, read_of(closes), R + timedelta(days=3), NOW)
    assert outcome == RECORDED and st["rebalances"] == []
    assert [s["day"] for s in st["skipped"]] == [R.isoformat()]
    assert "no run completed" in st["skipped"][0]["reason"]


def test_a_regime_nobody_read_is_not_a_decision_to_hold_cash():
    """BTC read, but too short for its 150-day mean: the due rebalance is not
    decided at all, rather than decided as 'regime off'."""
    closes = make_closes(R + timedelta(days=2))
    st, _ = run_days(closes, R - timedelta(days=1), R - timedelta(days=1))
    short = copy.deepcopy(closes)
    first = (R - timedelta(days=99)).isoformat()
    short["BTC"] = {d: c for d, c in short["BTC"].items() if d >= first}
    new, outcome, note = advance(st, read_of(short), R + timedelta(days=1), NOW)
    assert outcome == SKIPPED and "150 closes do not all read" in note
    assert new["rebalances"] == [] and new["last_day"] == st["last_day"]
    # The other arm: the full history decides, and the regime is on.
    full, outcome, _ = advance(st, read_of(closes), R + timedelta(days=1), NOW)
    assert outcome == RECORDED and full["rebalances"][0]["regime_on"] is True


def test_btc_unreadable_on_the_first_run_writes_nothing():
    closes = make_closes(R)
    new, outcome, note = advance(None, read_of(closes, unread={"BTC": "its candle read "
                                                                    "failed (NetworkError)"}),
                                 R, NOW)
    assert new is None and outcome == UNREADABLE and "BTC" in note


def test_too_little_history_and_an_unlisted_coin_are_excluded_and_recorded():
    closes = make_closes(R + timedelta(days=2))
    # NEAR listed 100 days before the decision close, R included.
    first = (R - timedelta(days=99)).isoformat()
    closes["NEAR"] = {d: c for d, c in closes["NEAR"].items() if d >= first}
    st, _ = run_days(closes, R - timedelta(days=1), R - timedelta(days=1))
    st, outcome, _ = advance(
        st, read_of(closes, absent={"ALGO": "the venue lists no ALGO/USDT spot market"}),
        R + timedelta(days=1), NOW)
    assert outcome == RECORDED
    excluded = st["rebalances"][0]["excluded"]
    assert excluded["NEAR"] == "100 closes of history, 150 needed"
    assert excluded["ALGO"] == "the venue lists no ALGO/USDT spot market"
    assert "NEAR" not in st["rebalances"][0]["eligible"]


def test_a_failed_read_is_named_by_its_class_and_skips_the_due_rebalance(tmp_path, flag):
    flag(True)
    path = tmp_path / "rec.json"
    closes = make_closes(R + timedelta(days=2))
    _run(run_once(FakeVenue(closes), today=R, path=path))       # inception at R-1
    venue = FakeVenue(closes, fail={"DOGE"}, unknown={"ALGO"})
    res = _run(run_once(venue, today=R + timedelta(days=1), path=path))
    assert res.outcome == SKIPPED
    assert "DOGE: its candle read failed (NetworkError)" in res.note
    assert "SECRET" not in res.note and "ALGO" not in res.note
    st = json.loads(path.read_text())
    assert st["last_day"] == (R - timedelta(days=1)).isoformat()


# ── the record survives a restart, and an unreadable one is left alone ───────

def test_the_record_survives_a_restart(tmp_path, flag):
    flag(True)
    path = tmp_path / "rec.json"
    closes = make_closes(R + timedelta(days=4))
    assert _run(run_once(FakeVenue(closes), today=R, path=path)).outcome == RECORDED
    assert _run(run_once(FakeVenue(closes), today=R + timedelta(days=1),
                         path=path)).outcome == RECORDED
    first = json.loads(path.read_text())
    # A new process: nothing in memory, only the file.
    again = FakeVenue(closes)
    assert _run(run_once(again, today=R + timedelta(days=1), path=path)).outcome == ALREADY
    assert again.calls == []
    assert _run(run_once(FakeVenue(closes), today=R + timedelta(days=2),
                         path=path)).outcome == RECORDED
    st = json.loads(path.read_text())
    assert st["started"] == first["started"]
    assert st["rebalances"] == first["rebalances"]
    assert [h["day"] for h in st["history"]] == [
        (R + timedelta(days=i)).isoformat() for i in (-1, 0, 1)]
    assert load_record(path).state == "read"


def test_an_unreadable_record_is_never_written_over(tmp_path, flag):
    flag(True)
    path = tmp_path / "rec.json"
    path.write_text("{ not json")
    venue = FakeVenue(make_closes(R))
    res = _run(run_once(venue, today=R, path=path))
    assert res.outcome == UNREADABLE and venue.calls == []
    assert path.read_text() == "{ not json"
    text = card_text(load_record(path), flag_on=True)
    assert "could not be read" in text and "not an empty book" in text


# ── the card ─────────────────────────────────────────────────────────────────

def test_the_card_says_percent_drawdowns_and_skips_and_no_money():
    closes = make_closes(R2 + timedelta(days=3))
    st, _ = run_days(closes, R - timedelta(days=1), R + timedelta(days=4))
    st, _o, _n = advance(st, read_of(closes), R2 + timedelta(days=2), NOW)  # misses R2
    text = card_text(NS(state="read", data=st, detail=""), flag_on=True,
                     today=R2 + timedelta(days=2))
    assert "$" not in text and "USD " not in text
    assert "Runner: SLEEVE_PAPER_ENABLED is on." in text
    assert f"Last rebalance: the {R.isoformat()} close (schedule)" in text
    assert f"Next scheduled rebalance: the {(R2 + timedelta(days=14)).isoformat()} close" in text
    assert "Regime at the" in text and "BTC above its 150-day mean" in text
    sleeve = st["sleeve_index"] / 100 - 1
    btc = st["btc_index"] / 100 - 1
    assert f"sleeve {sleeve * 100:+.2f}%, BTC buy-and-hold {btc * 100:+.2f}%" in text
    s_dd = max_drawdown([100.0] + [h["sleeve"] for h in st["history"]])
    assert f"Max drawdown: sleeve {s_dd * 100:.2f}%" in text
    assert "Skipped rebalances: 1." in text and R2.isoformat() in text
    for sym in st["weights"]:
        assert f"{sym} " in text


def test_the_card_without_a_record_says_which_arm_of_the_flag_it_is_on():
    fresh = NS(state="fresh", data=None, detail="")
    off = card_text(fresh, flag_on=False)
    on = card_text(fresh, flag_on=True)
    assert "No paper record yet." in off and "while it is off" in off
    assert "has not recorded a day yet" in on and "while it is off" not in on


def test_max_drawdown_is_the_largest_fall_from_a_peak():
    assert max_drawdown([]) is None
    assert max_drawdown([100, 110, 99, 120, 90]) == pytest.approx(0.25)
    assert max_drawdown([100, 101, 102]) == 0.0


# ── the command, the engine hook, the shape ──────────────────────────────────

def test_the_sleeve_command_is_admin_gated_and_reads_the_record(tmp_path, monkeypatch, flag):
    from bot.skills.telegram_handler import TelegramHandler
    flag(False)
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    sent: list[str] = []
    asked: list[str] = []

    def stub(allow: bool):
        async def _guard(update, command="", ctx=None):
            asked.append(command)
            return allow

        async def _send(update, text, reply_markup=None, edit=False):
            sent.append(text)
        return NS(_guard=_guard, _send=_send)

    _run(TelegramHandler._cmd_sleeve(stub(False), NS(), NS(args=[])))
    assert asked == ["admin"] and sent == []
    _run(TelegramHandler._cmd_sleeve(stub(True), NS(), NS(args=[])))
    assert len(sent) == 1 and "No paper record yet." in sent[0]
    assert not (tmp_path / "sleeve").exists(), "a read-only command wrote something"


def _yesterday_closes():
    return make_closes(datetime.now(UTC).date() - timedelta(days=1))


def test_the_engine_hook_is_off_with_the_flag_and_runs_once_a_day_with_it(
        tmp_path, monkeypatch, flag):
    from bot.core.engine import RuneClawEngine
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    venue = FakeVenue(_yesterday_closes())

    async def _get_exchange():
        return venue
    eng = NS(scanner=NS(_exchange=venue, _get_exchange=_get_exchange),
             _sleeve_call_callback=None)
    # The real announce step, bound: a RECORDED run asks it, and with no
    # poster registered it hands nothing on.
    eng._announce_sleeve_call = RuneClawEngine._announce_sleeve_call.__get__(eng)
    hook = RuneClawEngine._record_sleeve_paper_day

    flag(False)
    assert _run(hook(eng)) == DISABLED and venue.calls == []
    flag(True)
    bare = NS(scanner=NS(_exchange=None))
    bare._announce_sleeve_call = RuneClawEngine._announce_sleeve_call.__get__(bare)
    assert _run(hook(bare)) == NOT_ASKED
    assert _run(hook(eng)) == RECORDED and len(venue.calls) == len(UNIVERSE)
    assert _run(hook(eng)) == ALREADY and len(venue.calls) == len(UNIVERSE)
    assert paper.state_file() == tmp_path / "sleeve" / "xsmom_regime_paper.json"
    assert paper.state_file().exists()


def test_a_failed_engine_run_is_not_retried_every_tick(tmp_path, monkeypatch, flag):
    from bot.core.engine import RuneClawEngine
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    flag(True)
    venue = FakeVenue(_yesterday_closes(), fail={"BTC"})

    async def _get_exchange():
        return venue
    eng = NS(scanner=NS(_exchange=venue, _get_exchange=_get_exchange))
    eng._announce_sleeve_call = RuneClawEngine._announce_sleeve_call.__get__(eng)
    assert _run(RuneClawEngine._record_sleeve_paper_day(eng)) == UNREADABLE
    asked = len(venue.calls)
    assert _run(RuneClawEngine._record_sleeve_paper_day(eng)) == NOT_ASKED
    assert len(venue.calls) == asked


def test_the_tick_calls_the_hook_only_behind_the_flag():
    """A scan, because the tick is a 400-line coroutine no test can drive to
    this line cheaply: the call sits under `if CONFIG.sleeve_paper_enabled:`
    and behind the maintenance cap, as the daily trend book's does."""
    src = code_only((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    at = src.index("self._record_sleeve_paper_day()")
    window = src[src.rindex("if CONFIG.sleeve_paper_enabled:", 0, at):at]
    assert "_with_maintenance_cap(" in window and window.count("\n") <= 4


def test_the_sleeve_package_places_nothing_and_holds_no_key():
    """Paper only, by construction: no order call, no executor, no private
    endpoint, no credential. Scanned with comments and docstrings blanked,
    because the docstrings say exactly these words to promise their absence."""
    forbidden = ("create_order", "cancel_order", "live_executor", "LiveExecutor",
                 "fetch_balance", "fetch_positions", "apiKey", "secret", "privateGet",
                 "privatePost", "sign(", "private_key")
    for path in sorted((ROOT / "bot" / "sleeve").glob("*.py")):
        src = code_only(path.read_text(encoding="utf-8"))
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"{path.name}: {hits}"
