"""A Telegram chart links the live TradingView chart of the same signal.

A Telegram chart is a PNG: drawn once, nothing to zoom, nothing after its last
candle. `/embed/chart` on the website is the same setup on the site's
TradingView chart, live, with the levels the signal published. The link is the
bridge, so what it carries is a claim: the market and the levels the card
printed, and nothing the card did not. A level that is not a price on record is
left out of the link rather than sent as 0, because the page draws what it is
handed. And an album cannot carry a button (Telegram puts no inline keyboard on
a media group), so the link is in the caption, and survives the plain-text
retry Telegram forces when it refuses the HTML.
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bot.skills import chart_renderer as cr

REPO = Path(__file__).resolve().parents[1]


class _Dir(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


def _idea(asset="LINK/USDT", entry=15.0885, stop=14.271, target=16.3148, direction=_Dir.LONG):
    return NS(asset=asset, entry_price=entry, stop_loss=stop, take_profit=target,
              direction=direction, confidence=0.6, risk_reward_ratio=1.5)


def _q(url):
    parts = urllib.parse.urlsplit(url)
    return parts, dict(urllib.parse.parse_qsl(parts.query))


class TestTheLink:
    def test_it_names_the_market_the_timeframe_and_the_levels(self, monkeypatch):
        monkeypatch.setenv("WEBSITE_URL", "https://example.test/")
        parts, q = _q(cr.live_chart_url(_idea(), "4h"))
        assert (parts.scheme, parts.netloc, parts.path) == ("https", "example.test", "/embed/chart")
        assert q == {"s": "LINKUSDT", "tf": "4h", "e": "15.0885", "sl": "14.271",
                     "tp": "16.3148", "d": "LONG"}

    def test_the_symbol_is_spelled_the_way_the_candle_route_takes_it(self):
        assert _q(cr.live_chart_url(_idea(asset="BTC/USDT:USDT"), "1h"))[1]["s"] == "BTCUSDT"
        assert _q(cr.live_chart_url(_idea(asset="eth/usdt"), "1h"))[1]["s"] == "ETHUSDT"

    @pytest.mark.parametrize("asset", ["", None, "<b>/USDT", "A", "BTC USDT", "X" * 25])
    def test_a_symbol_it_cannot_place_is_no_link_at_all(self, asset):
        assert cr.live_chart_url(_idea(asset=asset), "1h") is None
        assert cr.live_chart_line(_idea(asset=asset), "1h") == ""

    @pytest.mark.parametrize("tf", ["5m", "30m", "", None, "4H"])
    def test_a_timeframe_the_page_does_not_draw_is_linked_as_1h(self, tf):
        assert _q(cr.live_chart_url(_idea(), tf))[1]["tf"] == "1h"

    @pytest.mark.parametrize("bad", [0, 0.0, -1.0, float("nan"), float("inf"), "abc", None])
    def test_a_level_not_on_record_is_left_out_rather_than_sent_as_zero(self, bad):
        q = _q(cr.live_chart_url(_idea(stop=bad), "1h"))[1]
        assert "sl" not in q
        assert q["e"] == "15.0885" and q["tp"] == "16.3148"

    @pytest.mark.parametrize("typed", [12345.67, 999999.99, 0.00001234, 63000.0, 1.171])
    def test_a_level_round_trips(self, typed):
        # The replay chapter's lesson: `:g` kept six significant digits and
        # sent a different figure from the one on the card.
        assert float(_q(cr.live_chart_url(_idea(entry=typed), "1h"))[1]["e"]) == typed

    def test_a_direction_is_long_short_or_not_stated(self):
        assert _q(cr.live_chart_url(_idea(direction=_Dir.SHORT), "1h"))[1]["d"] == "SHORT"
        assert "d" not in _q(cr.live_chart_url(_idea(direction=None), "1h"))[1]
        assert "d" not in _q(cr.live_chart_url(_idea(direction=NS(value="BUY")), "1h"))[1]

    def test_the_line_escapes_the_href(self):
        line = cr.live_chart_line(_idea(), "1h")
        assert "&amp;tf=1h" in line
        assert re.search(r'<a href="[^"<>]+">Open the live TradingView chart</a>', line)


class TestThePageAgrees:
    """The bot writes the link and the page reads it: two runtimes, one contract."""

    def test_the_timeframes_are_the_pages(self):
        src = (REPO / "app/public/js/embed-chart.js").read_text()
        block = re.search(r"var TIMEFRAMES = \{([^}]*)\}", src).group(1)
        page = tuple(re.findall(r"'([0-9]+[mhd])'\s*:", block))
        assert page == cr.LIVE_CHART_TIMEFRAMES

    def test_the_symbol_rule_is_the_pages(self):
        src = (REPO / "app/public/js/embed-chart.js").read_text()
        assert "/^[A-Z0-9]{2,24}$/" in src
        assert cr._LIVE_CHART_SYMBOL.pattern == "^[A-Z0-9]{2,24}$"


class _Bot:
    """Records what reached Telegram; refuses HTML once when told to."""

    def __init__(self, refuse_html=False):
        self.photos, self.albums, self.messages = [], [], []
        self.refuse_html = refuse_html

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, **kw):
        if self.refuse_html and parse_mode == "HTML":
            self.refuse_html = False
            raise RuntimeError("can't parse entities")
        self.photos.append((caption, parse_mode))

    async def send_media_group(self, chat_id, media, **kw):
        if self.refuse_html and any(getattr(m, "parse_mode", None) == "HTML" for m in media):
            self.refuse_html = False
            raise RuntimeError("can't parse entities")
        self.albums.append([getattr(m, "caption", None) for m in media])

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.messages.append((text, parse_mode))


def _send(bot, tfs, monkeypatch, png=b"\x89PNG-fake"):
    monkeypatch.setattr(cr, "build_chart_png", lambda *a, **k: png)
    return asyncio.run(cr.send_idea_charts_multi(bot, 1, {tf: [] for tf in tfs}, _idea()))


class TestTheCaptionCarriesIt:
    def test_a_single_chart_links_its_own_timeframe(self, monkeypatch):
        bot = _Bot()
        assert _send(bot, ["4h"], monkeypatch) is True
        caption, mode = bot.photos[0]
        assert mode == "HTML"
        href = re.search(r'href="([^"]+)"', caption).group(1)
        assert "tf=4h" in href and "s=LINKUSDT" in href

    def test_an_album_links_in_its_first_caption(self, monkeypatch):
        pytest.importorskip("telegram")
        bot = _Bot()
        assert _send(bot, ["4h", "1h"], monkeypatch) is True
        first, second = bot.albums[0]
        assert "Open the live TradingView chart" in first and "tf=4h" in first
        assert second is None

    def test_a_chart_that_did_not_render_still_links_the_live_one(self, monkeypatch):
        bot = _Bot()
        assert _send(bot, ["1h"], monkeypatch, png=None) is False
        text, _ = bot.messages[0]
        assert "Open the live TradingView chart" in text

    def test_the_plain_retry_keeps_the_address(self, monkeypatch):
        bot = _Bot(refuse_html=True)
        assert _send(bot, ["1h"], monkeypatch) is True
        caption, mode = bot.photos[0]
        assert mode is None
        assert "<a" not in caption
        assert re.search(r"Open the live TradingView chart: https://\S+/embed/chart\?s=LINKUSDT&tf=1h", caption)

    def test_the_plain_retry_of_an_album_keeps_the_address(self, monkeypatch):
        pytest.importorskip("telegram")
        bot = _Bot(refuse_html=True)
        assert _send(bot, ["4h", "1h"], monkeypatch) is True
        assert re.search(r"chart: https://\S+/embed/chart\?s=LINKUSDT&tf=4h", bot.albums[0][0])

    def test_the_single_chart_wrapper_puts_the_link_above_the_extra_text(self, monkeypatch):
        monkeypatch.setattr(cr, "build_chart_png", lambda *a, **k: b"\x89PNG-fake")
        bot = _Bot()
        extra = "x" * 2000
        assert asyncio.run(cr.send_idea_chart(bot, 1, [], _idea(), extra_caption=extra)) is True
        caption, _ = bot.photos[0]
        # Cut at the photo limit, the tail goes and the tag stays whole.
        assert re.search(r'<a href="[^"]+">Open the live TradingView chart</a>', caption)


def test_strip_html_keeps_a_links_address_and_unescapes_it():
    out = cr._strip_html('<b>A</b> <a href="https://x.test/c?s=A&amp;tf=1h">Open</a>')
    assert out == "A Open: https://x.test/c?s=A&tf=1h"


# ── the picture itself: its figures are the card's figures ──────────────────

def _needs_charts():
    if not cr.charts_available():
        pytest.skip(f"chart libraries unavailable: {cr._IMPORT_ERROR!r}")


def _candles(n=80, base=0.0000112, step=0.004):
    import math
    out, p = [], base
    for i in range(n):
        o = p
        p = p * (1 + math.sin(i / 6) * step)
        out.append([1_700_000_000_000 + i * 3_600_000, o, max(o, p) * 1.003,
                    min(o, p) * 0.997, p, 1000 + i])
    return out


@pytest.mark.parametrize("price", [63012.5, 15.0885, 14.271, 0.5123, 0.0000112, 0.0000110, 0.0012345])
def test_a_chart_price_is_the_cards_price(price):
    from bot.formatters.rich_cards import _fmt_price
    assert cr._fmt(price) == _fmt_price(price).lstrip("$")


def test_a_sub_cent_entry_and_stop_are_two_different_tags():
    # Six decimal places printed both as 0.000011: the stop drawn ON the entry.
    assert cr._fmt(0.0000112) != cr._fmt(0.0000110)


def _texts_drawn(monkeypatch, candles, levels):
    """Every string the render writes onto an axes, and every formatter set."""
    _needs_charts()
    import matplotlib.axes
    import matplotlib.axis
    texts, formatters = [], []
    real_text = matplotlib.axes.Axes.text
    real_fmt = matplotlib.axis.Axis.set_major_formatter

    def text(self, x, y, s, *a, **k):
        texts.append(str(s))
        return real_text(self, x, y, s, *a, **k)

    def fmt(self, formatter):
        formatters.append(formatter)
        return real_fmt(self, formatter)

    monkeypatch.setattr(matplotlib.axes.Axes, "text", text)
    monkeypatch.setattr(matplotlib.axis.Axis, "set_major_formatter", fmt)
    png = cr.build_chart_png(candles, title="PEPEUSDT LONG", dpi=60, levels=levels)
    assert png is not None
    return texts, formatters


def test_the_tags_carry_the_cards_figures_and_the_last_price(monkeypatch):
    c = _candles()
    last = c[-1][4]
    levels = {"entry": last * 0.999, "stop_loss": last * 0.982, "take_profit": last * 1.06}
    texts, _ = _texts_drawn(monkeypatch, c, levels)
    tags = [s.strip() for s in texts]
    assert f"Entry {cr._fmt(levels['entry'])}" in tags
    assert f"SL {cr._fmt(levels['stop_loss'])}" in tags
    assert f"TP {cr._fmt(levels['take_profit'])}" in tags
    # The last price keeps its tag even beside the entry: the old rule dropped
    # it as a "duplicate" of any level within 5% of the range.
    assert cr._fmt(last) in tags


def test_the_price_axis_prints_prices_not_a_multiplier(monkeypatch):
    c = _candles()
    _, formatters = _texts_drawn(monkeypatch, c, None)
    labels = [f(0.0000112, 0) for f in formatters if callable(getattr(f, "func", None)) or callable(f)]
    assert cr._fmt(0.0000112) in labels, "the price axis kept matplotlib's scaled labels"


class TestTheLegend:
    def _df(self, closes, opens=None):
        import pandas as pd
        opens = opens or closes
        return pd.DataFrame({
            "Open": opens, "High": [max(a, b) for a, b in zip(opens, closes)],
            "Low": [min(a, b) for a, b in zip(opens, closes)], "Close": closes,
            "EMA_9": [float("nan")] * len(closes), "EMA_21": closes, "VWAP": closes,
        })

    def _t(self):
        return {"up": "UP", "down": "DOWN", "muted": "MUTED", "ema_fast": "F",
                "ema_slow": "S", "vwap": "V", "rsi": "R"}

    def test_the_bar_values_wear_the_bars_colour(self):
        row1, _ = cr._tv_price_legend_rows(self._df([10.0, 11.0], [10.0, 10.5]), self._t())
        assert ("C", "MUTED") in row1
        assert (cr._fmt(11.0), "UP") in row1
        assert row1[-1] == ("+10.00%", "UP")

    def test_an_unread_close_is_neither_up_nor_down(self):
        row1, _ = cr._tv_price_legend_rows(self._df([10.0, float("nan")], [10.0, 10.5]), self._t())
        assert ("—", "MUTED") in row1
        colours = {c for txt, c in row1}
        assert "DOWN" not in colours and "UP" not in colours

    def test_a_flat_bar_move_is_muted(self):
        row1, _ = cr._tv_price_legend_rows(self._df([10.0, 10.0], [10.0, 10.0]), self._t())
        assert row1[-1] == ("+0.00%", "MUTED")

    def test_an_overlay_still_warming_up_prints_a_dash(self):
        _, row2 = cr._tv_price_legend_rows(self._df([10.0, 11.0]), self._t())
        i = row2.index(("EMA 9", "MUTED"))
        assert row2[i + 1] == ("—", "F")

    def test_the_rsi_legend_is_a_dash_during_warm_up(self):
        import pandas as pd
        assert cr._tv_rsi_legend_rows(pd.DataFrame({"RSI": [float("nan")] * 3}), self._t()) == \
            [[("RSI 14", "MUTED"), ("—", "R")]]
