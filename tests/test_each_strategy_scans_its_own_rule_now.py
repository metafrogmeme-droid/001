"""Each strategy the live bot does not run can be scanned now, by its own door.

Until this, nothing read those rules live: `/run` showed their text and a
frozen backtest was the only thing that ever evaluated them. `/limitscan`,
`/multitrend`, `/altsweep`, `/ethma` and `/volrotation` (and the website's
"Scan now") read each listed market's closed 1h bars and run the preset's own
rule, the function its backtest runs. Driven through the real
`strategy_scan.scan`, the real pager and the real rule modules; only Bitget is
a stand-in, serving hand-built bars and recording what it was asked.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from bot.core import strategy_catalog as sc
from bot.core import strategy_scan as ss
from bot.skills.skill_registry import RunStrategySkill

HOUR = ss.HOUR_MS
#: The scan's clock: seven minutes into the hour after the last closed bar.
LAST_OPEN = int(datetime(2026, 1, 20, 9, tzinfo=timezone.utc).timestamp() * 1000)
NOW = LAST_OPEN + HOUR + 7 * 60_000


def _series(closes, last_open=LAST_OPEN, volume=1_000.0):
    """Hourly rows ending at ``last_open``, one per close."""
    n = len(closes)
    return [[last_open - (n - 1 - i) * HOUR, c, c * 1.002, c * 0.998, c, volume]
            for i, c in enumerate(closes)]


def _ramp(n, start, step):
    return [start + i * step for i in range(n)]


class Venue:
    """Bitget's futures candles for hand-built series; a symbol it has no
    series for raises, as a delisted or unreachable market does."""

    def __init__(self, series: dict, raises: dict | None = None):
        self.series = series
        self.raises = raises or {}
        self.calls: list[str] = []

    def milliseconds(self):
        return NOW

    def parse_timeframe(self, tf):
        assert tf == "1h"
        return 3600

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        self.calls.append(symbol)
        base = symbol.split("/")[0]
        if base in self.raises:
            raise self.raises[base]
        rows = self.series.get(base)
        if rows is None:
            raise LookupError(f"no market {symbol}")
        if since is None:
            return [list(r) for r in rows[-limit:]]
        until = (params or {}).get("until", float("inf"))
        return [list(r) for r in rows if since < r[0] <= until][:limit]


class Engine:
    def __init__(self, venue):
        self.venue = venue
        engine = self

        class Scanner:
            async def _get_futures_exchange(self):
                return engine.venue

        self.scanner = Scanner()


@pytest.fixture(autouse=True)
def _fresh_cache():
    ss._CACHE.clear()
    ss._LOCKS.clear()
    yield
    ss._CACHE.clear()
    ss._LOCKS.clear()


def _scan(engine, key, now=NOW):
    return asyncio.run(ss.scan(engine, key, now_ms=now))


# ── which presets have a door ────────────────────────────────────────────────

def test_every_preset_the_live_bot_does_not_run_has_a_scan_door_and_no_other_does():
    doors = {k: ss.scan_command(k) for k in RunStrategySkill.PRESETS}
    for key, cfg in RunStrategySkill.PRESETS.items():
        assert (doors[key] is None) == sc.live_runs(cfg), key
    assert {d for d in doors.values() if d} == {
        "limitscan", "multitrend", "altsweep", "ethma", "volrotation", "dailytrend"}
    # A door is the preset's own alias, so `run <alias>` and the scan name one preset.
    for key, door in doors.items():
        if door:
            assert RunStrategySkill.ALIASES[door] == key


def test_a_house_preset_and_an_unknown_name_have_no_scan():
    house = next(k for k, c in RunStrategySkill.PRESETS.items() if sc.live_runs(c))
    for key in (house, "no such preset"):
        with pytest.raises(ss.NoScan):
            _scan(Engine(Venue({})), key)


# ── the moving-average books ─────────────────────────────────────────────────

ALTS = [ss._base(s) for s in RunStrategySkill.PRESETS["alt sweep"]["symbols"]]


def test_alt_sweep_names_each_side_and_each_market_it_could_not_read():
    series = {b: _series(_ramp(160, 100.0, 0.5)) for b in ALTS}      # rising: fast above slow
    series["VET"] = _series(_ramp(160, 100.0, -0.3))                    # falling: fast below slow
    del series["ZIL"]                                                   # the venue has no market
    series["WIF"] = _series(_ramp(160, 100.0, 0.5), last_open=LAST_OPEN - 2 * HOUR)  # stale
    venue = Venue(series, raises={"GALA": TimeoutError("slow")})
    board = _scan(Engine(venue), "alt sweep")
    sides = {r["symbol"]: r["side"] for r in board["rows"]}
    assert sides["ADA"] == "LONG" and sides["VET"] == "SHORT"
    unread = {u["symbol"]: u["why"] for u in board["unread"]}
    assert unread["ZIL"] == "LookupError" and unread["GALA"] == "TimeoutError"
    assert unread["WIF"] == "its newest bar closed at 20 Jan 08:00 UTC"
    # An unread market is named apart, never given a side.
    assert not {"ZIL", "GALA", "WIF"} & set(sides)
    assert board["read"] == sorted(sides)
    assert "short VET" in board["verdict"] and "long ADA" in board["verdict"]
    assert board["bar_close"] == "20 Jan 10:00 UTC" and board["places"] is False


def test_eth_ma_trend_reads_its_average_on_closed_4h_bars_paged_from_1h():
    cfg = RunStrategySkill.PRESETS["eth ma trend"]
    need = ss.bars_needed(cfg)
    assert need == (cfg["slow_period"] + 2) * 4
    # Months of decline, then nine days of rise: on 1h closes the 50 hours
    # sit above the 200 (long); on 4h closes the 50 bars (200 hours) sit
    # below the 200 bars (800 hours), which still hold the decline (short).
    closes = _ramp(need - 208, 3000.0, -1000.0 / (need - 208)) + _ramp(208, 2000.0, 300.0 / 208)
    venue = Venue({"ETH": _series(closes)})
    board = _scan(Engine(venue), "eth ma trend")
    assert len(venue.calls) > 1, "the 1h window is wider than a page"
    (row,) = board["rows"]
    assert row["symbol"] == "ETH" and row["side"] == "SHORT", "read on 1h closes"
    assert row["fast"] < row["slow"]
    from bot.core.ma_trend import ma_side
    from bot.core.ta_utils import sma_last
    assert ma_side(sma_last(closes, 50), sma_last(closes, 200)) == "LONG", \
        "the fixture cannot tell 1h closes from 4h closes"


# ── the momentum trend ───────────────────────────────────────────────────────

def test_the_momentum_trend_signals_only_when_average_and_momentum_agree():
    up = _ramp(40, 100.0, 1.0)
    # Averages up; the close is below the close three bars back, though the
    # last bar rose: three-bar momentum says no, one-bar momentum would say yes.
    pullback = up[:-3] + [up[-4] - 3, up[-4] - 2, up[-4] - 1]
    venue = Venue({"BTC": _series(up), "ETH": _series(pullback),
                   "SOL": _series(_ramp(40, 100.0, -1.0))},
                  raises={"PONS": LookupError("unlisted"), "BTW": LookupError("unlisted")})
    board = _scan(Engine(venue), "multi trend")
    rows = {r["symbol"]: r for r in board["rows"]}
    assert rows["BTC"]["signal"] == "LONG"
    assert rows["ETH"]["trend"] == "LONG" and rows["ETH"]["signal"] is None
    assert rows["SOL"]["signal"] == "SHORT"
    assert {u["symbol"] for u in board["unread"]} == {"PONS", "BTW"}
    assert board["verdict"] == "Signals now: long BTC, short SOL."


# ── the daily rotation ───────────────────────────────────────────────────────

def test_the_rotation_names_the_one_market_it_would_hold():
    cfg = RunStrategySkill.PRESETS["daily vol rotation"]
    need = ss.bars_needed(cfg)
    # The rule wants a 20-day rise of at least its threshold (50%): SOL climbs
    # 0.1% an hour (about 62% in 20 days), the rest 0.02% (about 10%).
    series = {ss._base(s): _series([100.0 * 1.0002 ** i for i in range(need)])
              for s in cfg["symbols"]}
    series["SOL"] = _series([100.0 * 1.001 ** i for i in range(need)])
    board = _scan(Engine(Venue(series)), "daily vol rotation")
    assert board["rule"] == ss.VOL_ROTATION
    assert board["verdict"].startswith("It would hold SOL")
    assert {r["symbol"] for r in board["rows"]} == set(series)


# ── the limit scanner ────────────────────────────────────────────────────────

def test_an_unread_leader_opens_nothing_for_its_class_and_says_so():
    cfg = RunStrategySkill.PRESETS["limit scanner"]
    series = {ss._base(s): _series(_ramp(80, 100.0, 0.05), volume=1e6) for s in cfg["symbols"]}
    venue = Venue(series, raises={"XAU": TimeoutError("slow")})
    board = _scan(Engine(venue), "limit scanner")
    classes = {c["name"]: c for c in board["classes"]}
    assert classes["metals"]["leader"] == "XAU"
    assert classes["metals"]["side"] is None and classes["metals"]["plan"] is None
    assert "could not be read" in classes["metals"]["regime"]
    assert classes["crypto"]["leader"] == "BTC"
    assert {u["symbol"]: u["why"] for u in board["unread"]} == {"XAU": "TimeoutError"}
    # The board is scored by the scanner's own reading: out of what was read.
    for c in board["classes"]:
        for row in c["board"]:
            assert row["out_of"] <= 100


# ── one board per closed hour ────────────────────────────────────────────────

def test_one_read_per_closed_hour_and_a_partial_board_is_re_read(monkeypatch):
    series = {b: _series(_ramp(160, 100.0, 0.5)) for b in ALTS}
    venue = Venue(series)
    engine = Engine(venue)
    first = _scan(engine, "alt sweep")
    asked = len(venue.calls)
    assert _scan(engine, "alt sweep", now=NOW + 40 * 60_000) is first
    assert len(venue.calls) == asked, "the same closed hour was read twice"
    # The next hour's close is a new read.
    venue.series = {b: _series(_ramp(161, 100.0, 0.5), last_open=LAST_OPEN + HOUR) for b in ALTS}
    _scan(engine, "alt sweep", now=NOW + HOUR)
    assert len(venue.calls) == 2 * asked
    # A board with an unread market is kept a minute, not the hour.
    ss._CACHE.clear()
    venue.raises = {"ADA": TimeoutError("slow")}
    partial = _scan(engine, "alt sweep", now=NOW + HOUR)
    assert partial["unread"]
    clock = [0.0]
    monkeypatch.setattr(ss.time, "monotonic", lambda: clock[0])
    ss._CACHE["alt sweep"] = (ss._CACHE["alt sweep"][0], 0.0, partial)
    clock[0] = ss.PARTIAL_TTL_S - 1
    assert _scan(engine, "alt sweep", now=NOW + HOUR) is partial
    clock[0] = ss.PARTIAL_TTL_S + 1
    assert _scan(engine, "alt sweep", now=NOW + HOUR) is not partial


# ── what it says ─────────────────────────────────────────────────────────────

def test_the_board_carries_no_dollar_figure_and_the_card_says_it_places_nothing():
    series = {b: _series(_ramp(160, 100.0, 0.5)) for b in ALTS}
    board = _scan(Engine(Venue(series, raises={"ADA": TimeoutError()})), "alt sweep")
    text = json.dumps(board)
    assert "$" not in text and "usd" not in text.lower()
    card = ss.render_text(board)
    assert board["verdict"] in card
    assert "Unread, so not on the board: ADA (TimeoutError)." in card
    assert "Nothing is placed or queued" in card


# ── the Telegram doors ───────────────────────────────────────────────────────

class _Chat:
    """The handler's own ``_strategy_scan`` and commands, bound to a stand-in
    that records what it sent and lets the guard through."""

    def __init__(self, engine):
        from bot.skills.scan_commands import ScanCommands
        self.engine = engine
        self.sent: list[str] = []
        for name in ("_strategy_scan", *(f"_cmd_{d}" for d in
                                         ("limitscan", "multitrend", "altsweep", "ethma",
                                          "volrotation", "dailytrend"))):
            setattr(self, name, getattr(ScanCommands, name).__get__(self))

    async def _guard(self, update, command, ctx):
        self.guarded = command
        return True

    async def _send(self, update, text, **kw):
        self.sent.append(text)


@pytest.mark.parametrize("door", ["limitscan", "multitrend", "altsweep", "ethma", "volrotation",
                                  "dailytrend"])
def test_each_door_scans_the_preset_it_is_the_alias_of(door, monkeypatch):
    asked = []

    async def scan(engine, key, **kw):
        asked.append(key)
        return {"label": key, "bar_close": "x", "rule": ss.MA_TREND, "rows": [],
                "verdict": f"the board of {key}", "unread": []}

    monkeypatch.setattr(ss, "scan", scan)
    chat = _Chat(Engine(Venue({})))
    asyncio.run(getattr(chat, f"_cmd_{door}")(object(), None))
    assert asked == [RunStrategySkill.ALIASES[door]]
    assert chat.guarded == "run"
    assert f"the board of {asked[0]}" in chat.sent[-1]


def test_a_door_that_cannot_reach_the_venue_names_the_class_and_never_the_message(monkeypatch):
    async def scan(engine, key, **kw):
        raise ConnectionError("https://api.bitget.com/?apiKey=SECRET refused")

    monkeypatch.setattr(ss, "scan", scan)
    chat = _Chat(Engine(Venue({})))
    asyncio.run(chat._cmd_altsweep(object(), None))
    assert "ConnectionError" in chat.sent[-1] and "Nothing was placed" in chat.sent[-1]
    assert "SECRET" not in chat.sent[-1] and "bitget.com" not in chat.sent[-1]


# ── the website's door ───────────────────────────────────────────────────────

def _gateway(query_id, engine):
    from bot.web.user_gateway import handle_strategy_scan_public

    class Req:
        query = {"id": query_id}
        app = {"engine": engine}

    resp = asyncio.run(handle_strategy_scan_public(Req()))
    return resp.status, json.loads(resp.text)


def test_the_public_route_serves_a_board_by_slug_and_refuses_a_house_preset():
    series = {b: _series(_ramp(160, 100.0, 0.5)) for b in ALTS}
    status, body = _gateway("alt-sweep", Engine(Venue(series)))
    assert status == 200 and body["public"] is True
    assert body["board"]["preset"] == "alt sweep" and body["board"]["command"] == "/altsweep"
    for name in ("full-scan", "no-such-thing", ""):
        status, body = _gateway(name, Engine(Venue({})))
        assert status == 404 and body["error"] == "no_scan", name
    assert _gateway("alt-sweep", None)[0] == 503


def test_an_unreachable_venue_is_named_by_its_class_on_the_public_route():
    class Down:
        class scanner:
            @staticmethod
            async def _get_futures_exchange():
                raise ConnectionError("https://api.bitget.com/?apiKey=SECRET refused")

    status, body = _gateway("alt-sweep", Down())
    assert status == 502 and body == {"error": "scan_failed", "detail": "ConnectionError"}


# ── the catalogue says which door scans it ───────────────────────────────────

def test_each_card_names_its_scan_door_and_the_new_preset_says_what_it_is():
    cards = {c["id"]: c for c in sc.catalog()}
    assert cards["alt-sweep"]["scan"] == "/altsweep"
    assert cards["limit-scanner"]["scan"] == "/limitscan"
    assert cards["full-scan"]["scan"] is None
    assert "/altsweep reads its markets' last closed 1h bars" in cards["alt-sweep"]["how"]
    assert "/altsweep" not in cards["full-scan"]["how"]
    mt = cards["multi-trend"]
    assert mt["scan"] == "/multitrend" and mt["how"].startswith(sc.NOT_RUN_LIVE)
    assert "No frozen backtest evaluates it yet." in mt["how"]
    assert "9 is a reading" in mt["how"] and "20× leverage is recorded and not applied" in mt["how"]
    assert mt["scorecard"] == {
        "omitted": "No track record published: no frozen backtest runs this rule yet."}
    assert mt["copy_follow"] is False
    assert "$" not in json.dumps(mt)


# ── the momentum trend's numbers ─────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"fast_period": 21, "slow_period": 21},                     # the fast is not faster
    {"fast_period": 30, "slow_period": 21},
    {"momentum_period": 0}, {"fast_period": 1.5}, {"fast_period": True},
    {"stop_loss_pct": -1}, {"take_profit_pct": 0}, {"stop_loss_pct": float("inf")},
    {"stop_loss_pct": float("nan")}, {"invented": 1},
])
def test_the_momentum_trend_refuses_numbers_it_cannot_read(bad):
    from bot.core import momentum_trend as mt
    assert mt.params_from_preset({"momentum_trend": bad}) is None
    assert mt.momentum_signal  # the reader exists for the arm below


def test_the_momentum_trend_preset_reads_and_a_short_window_is_unread():
    from bot.core import momentum_trend as mt
    p = mt.params_from_preset(RunStrategySkill.PRESETS["multi trend"])
    assert p == mt.TrendParams(9, 21, 3, 1.2, 2.4)
    short = mt.momentum_signal("PONS", [1.0] * (p.bars_needed - 1), p)
    assert short.signal is None and short.reason.startswith("unread: 20 closed bars, 21 needed")
    flat = mt.momentum_signal("BTC", [100.0] * 30, p)
    assert flat.trend is None and flat.reason == "the averages are equal: no side"
