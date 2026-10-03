"""
Tests for bot.skills.chart_renderer.

The renderer degrades gracefully when matplotlib/mplfinance aren't installed.
Until 2026-08-17 the tests below degraded with it: `skipif(not
charts_available())` meant that on an environment without the libs this file
reported twenty skips and the suite went green. The libs were installed in NO
environment, so all twenty had never executed once. The operator's report was
"cards work now, but charts don't render" — and nothing in CI disagreed.

The three libs are pinned now, so absence is an environment failure rather
than a configuration choice, and `tests.dep_policy.require` says so instead of
skipping. The fallback paths and async send are still tested regardless: the
`_CHARTS_AVAILABLE = False` branch is real and must keep returning None.
"""
import functools
import math
import time

from bot.skills import chart_renderer as cr
from tests.dep_policy import require


def _candles(n: int = 60):
    out, t0, price = [], 1_700_000_000_000, 60_000.0
    for i in range(n):
        o = price
        c = price * (1 + 0.004 * math.sin(i / 3.0))
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        out.append([t0 + i * 3_600_000, o, h, l, c, 100.0 + i])
        price = c
    return out


def needs_charts(fn):
    """Was `skipif(not charts_available())`. See this file's docstring.

    Names each missing library rather than reporting the aggregate flag: the
    old reason string said "mplfinance not installed" whichever of the three
    was actually absent, which would have sent somebody to the wrong package.
    """
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        for mod in ("matplotlib", "mplfinance", "pandas"):
            require(mod, "bot/skills/chart_renderer.py renders with it")
        assert cr.charts_available(), (
            "all three chart libraries import, yet chart_renderer decided "
            f"charts are unavailable: {cr._IMPORT_ERROR!r}")
        return fn(*a, **kw)
    return wrapper


@needs_charts
def test_indicators_are_sane():
    df = cr.compute_chart_indicators(_candles())
    assert {"Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_21", "RSI"} <= set(df.columns)
    # The warm-up bars have no RSI: Wilder's average needs `length` changes, and
    # the chart used to draw them as a flat 50, a reading nobody took. After
    # the warm-up every value is a real one.
    rsi = df["RSI"]
    assert rsi.iloc[:14].isna().all()
    assert rsi.iloc[14:].notna().all()
    assert rsi.dropna().between(0, 100).all()
    last = df.iloc[-1]
    # EMA9 (faster) tracks price more closely than EMA21
    assert abs(last["EMA_9"] - last["Close"]) <= abs(last["EMA_21"] - last["Close"]) + 1e-9


@needs_charts
def test_render_produces_valid_png():
    png = cr.build_chart_png(_candles(), title="BTC/USDT Test", dpi=120)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"   # PNG magic header
    assert len(png) > 5000


@needs_charts
def test_default_canvas_is_large_for_legibility():
    # C2: the default render bumped to a much larger canvas (was ~520px wide)
    # so the dense SMC/pattern overlays stay readable.
    import struct
    df = cr.compute_chart_indicators(_candles(120))
    png = cr.render_chart_png(df, title="WLD 1h", levels={"entry": 0})
    w, h = struct.unpack(">II", png[16:24])
    assert w >= 1500 and h >= 900


@needs_charts
def test_live_position_overlays_render():
    # C4: trail SL / liquidation / Playbook threshold levels render alongside
    # entry/SL/TP without raising (each gets a distinct right-edge tag).
    df = cr.compute_chart_indicators(_candles(80))
    last = float(df["Close"].iloc[-1])
    png = cr.render_chart_png(df, title="WLD 1h", levels={
        "entry": last, "stop_loss": last * 1.02, "take_profit": last * 0.94,
        "trail": last * 1.01, "liq": last * 1.18, "threshold": last * 0.99,
    })
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@needs_charts
def test_many_patterns_render_without_crashing():
    # C1: a long, choppy series triggers many overlapping pattern detections;
    # the declutter cap + label de-collision must never raise.
    import math
    candles, t0, price = [], 1_700_000_000_000, 100.0
    for i in range(200):
        o = price
        c = price * (1 + 0.02 * math.sin(i / 4.0) + 0.01 * math.sin(i / 1.7))
        h = max(o, c) * 1.01
        lo = min(o, c) * 0.99
        candles.append([t0 + i * 3_600_000, o, h, lo, c, 100.0 + (i % 7)])
        price = c
    df = cr.compute_chart_indicators(candles)
    png = cr.render_chart_png(df, title="chop", levels={"entry": price})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_build_returns_none_on_bad_input():
    # These must never raise — they return None so callers fall back to text.
    assert cr.build_chart_png([], "empty") is None
    assert cr.build_chart_png(_candles(5), "too short") is None


@needs_charts
async def test_send_chart_delivers_photo_off_thread():
    captured = {}

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption, parse_mode):
            data = photo.read()
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            captured["photo"] = (chat_id, len(caption), parse_mode)

        async def send_message(self, chat_id, text, parse_mode):
            captured["msg"] = (chat_id, text)

    sent = await cr.send_chart(FakeBot(), 12345, _candles(),
                               caption="<b>BTC</b> long", title="BTC")
    assert sent is True
    assert captured["photo"][0] == 12345


@needs_charts
async def test_caption_clamped_to_1024():
    captured = {}

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption, parse_mode):
            captured["len"] = len(caption)
        async def send_message(self, *a, **k):
            pass

    await cr.send_chart(FakeBot(), 1, _candles(), caption="x" * 5000)
    assert captured["len"] == 1024


async def test_send_chart_falls_back_to_text_without_chart():
    captured = {}

    class FakeBot:
        async def send_photo(self, *a, **k):
            raise AssertionError("should not send a photo when there's no chart")
        async def send_message(self, chat_id, text, parse_mode):
            captured["msg"] = text

    # empty candles -> no chart -> text fallback
    sent = await cr.send_chart(FakeBot(), 1, [], caption="<b>no chart</b>")
    assert sent is False
    assert "msg" in captured


@needs_charts
def test_render_with_trade_levels():
    df = cr.compute_chart_indicators(_candles())
    levels = {"entry": 60000.0, "stop_loss": 58800.0, "take_profit": 62400.0}
    for theme in ("dark", "light"):
        png = cr.render_chart_png(df, title="BTC LONG", dpi=110, levels=levels,
                                  theme=theme, subtitle="LONG · conf 78% · R:R 1:2.4")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # zero/missing levels and an unknown theme must not break rendering
    png2 = cr.render_chart_png(df, dpi=110, levels={"entry": 0, "stop_loss": None},
                               theme="nonsense")
    assert png2[:8] == b"\x89PNG\r\n\x1a\n"


def test_levels_from_idea_extracts_fields():
    from types import SimpleNamespace
    idea = SimpleNamespace(entry_price=100.0, stop_loss=95.0, take_profit=110.0)
    lv = cr._levels_from_idea(idea)
    assert lv == {"entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0}
    assert cr._levels_from_idea(None) is None


@needs_charts
def test_vwap_is_computed_and_bounded():
    df = cr.compute_chart_indicators(_candles())
    assert "VWAP" in df.columns
    assert df["VWAP"].notna().all()
    assert df["VWAP"].min() >= df["Low"].min() - 1e-6
    assert df["VWAP"].max() <= df["High"].max() + 1e-6


def test_structure_lines_never_raise_on_short_data():
    # Too-short / unavailable input must return [] rather than raising.
    assert cr._market_structure_lines(None) == []


@needs_charts
def test_render_draws_structure_lines(monkeypatch):
    # Deterministically exercise the BOS/CHoCH drawing path regardless of the
    # detector's thresholds by injecting known structure lines.
    monkeypatch.setattr(cr, "_market_structure_lines", lambda df: [
        {"start": 10, "level": float(df["High"].iloc[10]), "label": "BOS", "color_key": "up"},
        {"start": 30, "level": float(df["Low"].iloc[30]), "label": "CHoCH", "color_key": "choch"},
    ])
    png = cr.build_chart_png(_candles(), title="BTC", dpi=110)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@needs_charts
def test_smc_helpers_never_raise():
    df = cr.compute_chart_indicators(_candles())
    # all return lists / dict-or-None and never raise on normal data
    assert isinstance(cr._fair_value_gaps(df), list)
    assert isinstance(cr._order_blocks(df), list)
    assert cr._liquidity_sweep(df) is None or isinstance(cr._liquidity_sweep(df), dict)
    assert isinstance(cr._swing_labels(df), list)
    # short data is safe too
    short = cr.compute_chart_indicators(_candles(8))
    assert cr._order_blocks(short) == []


@needs_charts
def test_render_with_smc_off_and_on():
    candles = _candles()
    on = cr.build_chart_png(candles, title="BTC", dpi=110, smc=True)
    off = cr.build_chart_png(candles, title="BTC", dpi=110, smc=False)
    assert on[:8] == b"\x89PNG\r\n\x1a\n"
    assert off[:8] == b"\x89PNG\r\n\x1a\n"


def _idea():
    from types import SimpleNamespace
    return SimpleNamespace(
        asset="BTC/USDT", direction=SimpleNamespace(value="LONG"),
        entry_price=60000.0, stop_loss=58800.0, take_profit=63000.0,
        confidence=0.78, risk_reward_ratio=2.4,
    )


class _FakeBot:
    def __init__(self):
        self.photo_calls = 0
        self.group_sizes = []

    async def send_photo(self, chat_id, photo, caption, parse_mode):
        assert photo.read()[:8] == b"\x89PNG\r\n\x1a\n"
        self.photo_calls += 1

    async def send_media_group(self, chat_id, media):
        self.group_sizes.append(len(media))

    async def send_message(self, **k):
        pass


def needs_telegram(fn):
    """python-telegram-bot is pinned, so absence is a broken environment.

    This was `skipif(not _have_telegram)` over a bare try/except import — the
    delivery half of charting (send_photo / send_media_group) would have gone
    quiet in exactly the same way the render half did.
    """
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        require("telegram", "chart delivery goes through python-telegram-bot")
        return fn(*a, **kw)
    return wrapper


@needs_charts
async def test_single_timeframe_sends_one_photo():
    bot = _FakeBot()
    ok = await cr.send_idea_charts_multi(bot, 1, {"1h": _candles()}, _idea())
    assert ok and bot.photo_calls == 1 and bot.group_sizes == []


@needs_charts
@needs_telegram
async def test_multiple_timeframes_send_album():
    bot = _FakeBot()
    ok = await cr.send_idea_charts_multi(
        bot, 1, {"4h": _candles(), "1h": _candles()}, _idea())
    assert ok and bot.group_sizes == [2]


@needs_charts
async def test_multi_tf_empty_falls_back_to_text():
    bot = _FakeBot()
    ok = await cr.send_idea_charts_multi(bot, 1, {"1h": []}, _idea())
    assert ok is False


@needs_charts
async def test_send_idea_chart_draws_levels_and_sends():
    from types import SimpleNamespace
    captured = {}

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption, parse_mode):
            assert photo.read()[:8] == b"\x89PNG\r\n\x1a\n"
            captured["caption"] = caption
        async def send_message(self, *a, **k):
            pass

    idea = SimpleNamespace(
        asset="BTC/USDT",
        direction=SimpleNamespace(value="LONG"),
        entry_price=60000.0, stop_loss=58800.0, take_profit=62400.0,
    )
    sent = await cr.send_idea_chart(FakeBot(), 42, _candles(), idea)
    assert sent is True
    assert "BTC" in captured["caption"] and "LONG" in captured["caption"]


_H = 3_600_000
_DAY0 = 1_784_900_000_000 - (1_784_900_000_000 % 86_400_000)


def _path(waypoints):
    """Piecewise candles through [bar_index, price], tiny wicks, volume 1."""
    out = []
    for w in range(len(waypoints) - 1):
        i0, p0 = waypoints[w]
        i1, p1 = waypoints[w + 1]
        for i in range(i0, i1):
            p = p0 + (p1 - p0) * ((i - i0) / (i1 - i0))
            out.append([_DAY0 + i * _H, p, p + 0.5, p - 0.5, p, 1.0])
    i_last, p_last = waypoints[-1]
    out.append([_DAY0 + i_last * _H, p_last, p_last + 0.5, p_last - 0.5, p_last, 1.0])
    return out


def _forming_break():
    """A settled series with no BOS, then one bar whose close breaks the swing.

    The added bar's own high stays under the level that would become the new
    pivot, so the break vanishes only because the bar is still forming.
    Volume on that bar is large enough that a VWAP which included it would
    move.
    """
    settled = _path([[0, 100], [5, 90], [12, 110], [19, 95], [26, 115], [32, 108], [38, 114.9]])
    open_ms = settled[-1][0] + _H
    forming = settled + [[open_ms, 114.9, 116.8, 114.8, 116.5, 100.0]]
    return settled, forming, open_ms


def _bos(df, timeframe, now_ms):
    frame = cr.closed_overlay_frame(df, timeframe, now_ms)
    return [ln["label"] for ln in cr._market_structure_lines(frame)], len(frame)


@needs_charts
def test_a_forming_bar_that_breaks_a_swing_is_not_a_bos_until_it_closes():
    _settled, forming, open_ms = _forming_break()
    df = cr.compute_chart_indicators(forming)
    live = open_ms + 1000
    labels, n = _bos(df, "1h", live)
    assert "BOS" not in labels
    assert n == len(df) - 1
    # Bitget's token for fifteen minutes. A bar opened a second ago is still
    # forming under that spelling too.
    labels_min, n_min = _bos(df, "15min", live)
    assert "BOS" not in labels_min and n_min == len(df) - 1
    closed, n_closed = _bos(df, "1h", open_ms + _H)
    assert "BOS" in closed and n_closed == len(df)
    # No timeframe, or one this parser does not know, leaves the series alone.
    named, n_named = _bos(df, None, live)
    unknown, n_unknown = _bos(df, "not-a-tf", live)
    assert "BOS" in named and n_named == len(df)
    assert "BOS" in unknown and n_unknown == len(df)


@needs_charts
def test_an_unreadable_open_or_clock_is_not_the_epoch():
    _settled, forming, open_ms = _forming_break()
    df = cr.compute_chart_indicators(forming)
    # NaT is not time 0. now=1000 would drop a bar opened at the epoch
    # (1000 < 0 + 1h) and hide the break.
    import pandas as pd
    blank = df.copy()
    idx = list(blank.index)
    idx[-1] = pd.NaT
    blank.index = pd.DatetimeIndex(idx)
    labels, n = _bos(blank, "1h", 1000)
    assert n == len(blank)
    assert "BOS" in labels
    # A row number is not an open time either.
    ranged = df.copy()
    ranged.index = range(len(ranged))
    labels_r, n_r = _bos(ranged, "1h", 1000)
    assert n_r == len(ranged) and "BOS" in labels_r
    # A missing clock is the wall clock. This break closed in July 2026, so
    # a clock of 0 would still call the bar forming and drop the BOS.
    labels_now, n_now = _bos(df, "1h", None)
    assert n_now == len(df) and "BOS" in labels_now
    # The other arm: a bar that opened this instant is still forming.
    fresh = df.copy()
    now = time.time() * 1000.0
    shift = now - fresh.index[-1].value / 1_000_000.0
    fresh.index = fresh.index + pd.to_timedelta(shift, unit="ms")
    _labels_fresh, n_fresh = _bos(fresh, "1h", None)
    assert n_fresh == len(fresh) - 1


@needs_charts
def test_an_unreadable_close_is_not_a_break_and_not_a_zero():
    settled, _forming, _open_ms = _forming_break()
    # The settled series ends inside the threshold. Its last bar has closed.
    base = cr.compute_chart_indicators(settled)
    closed_at = base.index[-1].value / 1_000_000.0 + _H
    quiet, _n = _bos(base, "1h", closed_at)
    assert "BOS" not in quiet
    broken = base.copy()
    broken.iloc[-1, broken.columns.get_loc("Close")] = 1.0
    labels, n = _bos(broken, "1h", closed_at)
    assert "BOS" in labels and n == len(broken)
    unread = base.copy()
    unread.iloc[-1, unread.columns.get_loc("Close")] = float("nan")
    labels_nan, n_nan = _bos(unread, "1h", closed_at)
    assert "BOS" not in labels_nan and n_nan == len(unread)
    # The held VWAP of a forming bar keeps an unreadable closed value.
    _s, forming, open_ms = _forming_break()
    wild = cr.compute_chart_indicators(forming)
    wild.iloc[-2, wild.columns.get_loc("VWAP")] = float("nan")
    held = cr._vwap_on_closed_bars(wild, wild.iloc[:-1])
    assert math.isnan(held["VWAP"].iloc[-1])
    readable = cr.compute_chart_indicators(forming)
    assert readable["VWAP"].iloc[-1] != readable["VWAP"].iloc[-2]
    kept = cr._vwap_on_closed_bars(readable, readable.iloc[:-1])
    assert kept["VWAP"].iloc[-1] == readable["VWAP"].iloc[-2]
    untouched = cr._vwap_on_closed_bars(readable, readable)
    assert untouched["VWAP"].iloc[-1] == readable["VWAP"].iloc[-1]


@needs_charts
def test_the_picture_draws_the_forming_bar_and_overlays_read_the_closed_series(monkeypatch):
    _settled, forming, open_ms = _forming_break()
    df = cr.compute_chart_indicators(forming)
    raw_last = float(df["VWAP"].iloc[-1])
    raw_prev = float(df["VWAP"].iloc[-2])
    assert raw_last != raw_prev
    seen = {}

    def wrap(name, fn):
        def inner(frame, *args, **kwargs):
            seen.setdefault("lens", {})[name] = len(frame)
            out = fn(frame, *args, **kwargs)
            if name == "_market_structure_lines":
                seen["lines"] = out
            return out
        return inner

    for name in ("_market_structure_lines", "_fair_value_gaps", "_order_blocks",
                 "_liquidity_sweep", "_swing_labels", "_elliott_wave_overlay",
                 "_fibonacci_levels_overlay", "_pattern_zones_overlay"):
        monkeypatch.setattr(cr, name, wrap(name, getattr(cr, name)))
    real_plot = cr.mpf.plot

    def plot_spy(data, **kwargs):
        seen["drawn"] = len(data)
        seen["close"] = float(data["Close"].iloc[-1])
        seen["vwap"] = float(data["VWAP"].iloc[-1])
        return real_plot(data, **kwargs)

    monkeypatch.setattr(cr.mpf, "plot", plot_spy)
    png = cr.render_chart_png(df, title="BTC 1h", dpi=72, timeframe="1h",
                              now_ms=open_ms + 1000)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert seen["drawn"] == len(df)
    assert seen["close"] == float(df["Close"].iloc[-1])
    assert seen["vwap"] == raw_prev
    assert set(seen["lens"]) == {
        "_market_structure_lines", "_fair_value_gaps", "_order_blocks",
        "_liquidity_sweep", "_swing_labels", "_elliott_wave_overlay",
        "_fibonacci_levels_overlay", "_pattern_zones_overlay",
    }
    assert set(seen["lens"].values()) == {len(df) - 1}
    assert "BOS" not in [ln["label"] for ln in seen["lines"]]
    png_closed = cr.render_chart_png(df, title="BTC 1h", dpi=72, timeframe="1h",
                                     now_ms=open_ms + _H)
    assert png_closed[:8] == b"\x89PNG\r\n\x1a\n"
    assert seen["drawn"] == len(df)
    assert seen["vwap"] == raw_last
    assert set(seen["lens"].values()) == {len(df)}
    assert "BOS" in [ln["label"] for ln in seen["lines"]]


@needs_charts
async def test_callers_that_know_a_timeframe_hand_it_to_the_chart(monkeypatch):
    seen = []

    def fake(*_args, **kwargs):
        seen.append(kwargs.get("timeframe"))
        return None

    monkeypatch.setattr(cr, "build_chart_png", fake)
    bot = _FakeBot()
    idea = _idea()
    await cr.send_idea_charts_multi(bot, 1, {"4h": _candles(), "1h": _candles()}, idea)
    assert seen == ["4h", "1h"]
    seen.clear()
    await cr.build_idea_chart_composite({"15m": _candles()}, idea)
    assert seen == ["15m"]
    seen.clear()
    idea.timeframe = "1h"
    await cr.send_idea_chart(bot, 1, _candles(), idea)
    assert seen == ["1h"]
    seen.clear()
    await cr.send_idea_chart(bot, 1, _candles(), _idea())
    assert seen == [None]

    import ccxt.async_support as ccxt_async

    class _Exchange:
        async def fetch_ohlcv(self, *_a, **_k):
            return _candles()

        async def close(self):
            return None

    monkeypatch.setattr(ccxt_async, "bitget", lambda *_a, **_k: _Exchange())
    seen.clear()
    await cr.build_position_chart(bot, "BTC/USDT")
    assert seen == ["1h"]


@needs_charts
def test_a_rally_with_no_loss_reads_100_and_a_flat_window_reads_nothing():
    # `avg_gain / avg_loss` over a window with no losing bar divides by zero.
    # The old fill turned that into 50 (neutral) on the strongest rally there
    # is; a window that did not move at all has no RSI either way.
    import pandas as pd
    up = cr._wilder_rsi(pd.Series([float(i) for i in range(1, 40)]), 14)
    assert up.iloc[-1] == 100.0
    flat = cr._wilder_rsi(pd.Series([5.0] * 40), 14)
    assert flat.isna().all()
