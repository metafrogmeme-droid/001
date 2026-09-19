"""A NULL close becomes NaN silently, and every `> 0 else 0` takes its else arm.

THE CHAIN IS THREE STEPS, each driven rather than read:

  1. `ccxt.Exchange.parse_ohlcv` builds the close with `safe_number`, which
     answers `None` for a field that is null, missing OR empty.
  2. `np.array([...], dtype=float)` turns that `None` into `nan` SILENTLY —
     where `float(None)` RAISES, which is what a reader of that line assumes.
  3. `nan > 0` is False, so all three `if x > 0 else 0` guards in
     `fetch_analysis_data` took their else arm.

What that published, before this slice:

  * `change_pct = 0` -> the Velocity Gate stayed SILENT, silent because it read
    0 rather than because the market was calm, with `+0.0%` in the header.
  * `vwap_pct = 0` -> "price is +0.0% ABOVE VWAP", a DIRECTIONAL claim from a
    computation that never happened.
  * `vol_spike = 1.0` -> "no spike", the calm value, off an average nobody
    could compute.

A FOURTH SITE NEEDED NO NaN AT ALL. The orderbook fetch's own `except` set
`{"bids": [], "asks": []}`, so a failed read summed to 0 on both sides — and
three readers published a verdict from it, two of them disagreeing: the header
said "bearish", `_bid_ask_read` said "balanced", the scorer charged -1.

AND THE SHARPEST CONSEQUENCE IS A RANKING, not a number. The comparison card
ends with a bold `Preferred`, scored partly on `abs(vwap_pct) < 10` — True for
an unread distance of 0. Driven with the same book on both assets, the one
whose VWAP could NOT be read scored 2 and one MEASURED 14% extended scored 0.
An unread input did not merely print wrong; it RANKED FAVOURABLY.
"""

from __future__ import annotations

import numpy as np
import pytest

from bot.formatters.rich_cards import (
    _bid_ask_read,
    _fmt_vol,
    _pct,
    fetch_analysis_data,
    render_analysis_card,
    render_comparison_table,
)


# ── the chain, from the library's own contract ───────────────────────────
class TestTheChain:
    def test_ccxt_answers_None_for_a_null_close(self):
        """Not a corner: `safe_number` answers None three different ways."""
        import ccxt
        e = ccxt.Exchange()
        assert e.safe_number({"a": None}, "a") is None
        assert e.safe_number({}, "a") is None
        assert e.safe_number({"a": ""}, "a") is None
        import inspect
        assert "safe_number(ohlcv, 4)" in inspect.getsource(ccxt.Exchange.parse_ohlcv)

    def test_numpy_turns_that_None_into_nan_where_float_would_raise(self):
        """The asymmetry the card used to have, in two lines."""
        with pytest.raises(TypeError):
            float(None)
        arr = np.array([None, 1.0], dtype=float)
        assert arr[0] != arr[0], "np.array coerced None to something finite"

    def test_and_nan_takes_every_else_arm(self):
        nan = float("nan")
        assert not (nan > 0)
        assert not (abs(nan) > 15), "the Velocity Gate's own comparison"


# ── the readings, at the boundary ────────────────────────────────────────
class TestThePercentFormatter:
    def test_an_absence_is_a_dash_and_never_a_signed_zero(self):
        for v in (None, float("nan"), float("inf"), float("-inf")):
            assert _pct(v) == "—", f"{v!r} rendered as a measurement"

    def test_a_measured_zero_is_still_a_measurement(self):
        assert _pct(0.0) == "+0.0%"
        assert _pct(-5.2) == "-5.2%"
        assert _pct(14.0) == "+14.0%"

    def test_the_volume_formatter_guards_too(self):
        assert _fmt_vol(None) == "—"
        assert _fmt_vol(0.0) == "$0"


class TestTheOrderBookBias:
    def test_a_book_nobody_read_is_not_balanced(self):
        """`_bid_ask_read(0, 0)` answered "balanced" — every threshold
        comparison is False at 0/0, so the fall-through won."""
        assert _bid_ask_read(None, None) == "book not read"
        assert _bid_ask_read(0.0, 0.0) == "balanced", (
            "a book that READ as empty on both sides is a real, thin reading")

    def test_and_a_read_book_still_gets_its_verdict(self):
        assert "buyers" in _bid_ask_read(100.0, 10.0)
        assert "sell" in _bid_ask_read(10.0, 100.0)


# ── the producer ─────────────────────────────────────────────────────────
#: `None` is the value being PLANTED here, so it cannot double as the
#: helper's "use the default" sentinel — the first draft wrote
#: `close=None` meaning "default" and the test that asked for a series of
#: null closes silently got an ordinary one. A fixture that cannot produce
#: the state it names measures nothing.
_DEFAULT = object()


#: The last bar is still FORMING, which is what a venue returns and what
#: `drop_forming_candle` exists for. The first draft stamped 2023 timestamps,
#: so every bar's period had elapsed and the "forming" bar was kept as a
#: CLOSED one — a fixture that could not produce the state its test named.
_TF_MS = 3_600_000


def _rows(n=40, *, close=_DEFAULT, volume=10.0):
    import time
    last_open = time.time() * 1000.0 - _TF_MS // 5
    out = []
    for i in range(n - 1, -1, -1):
        base = 1000.0 + i
        c = base + 1 if close is _DEFAULT else close
        out.append([last_open - i * _TF_MS, base, base + 5, base - 5,
                    c, volume])
    return out


class _Venue:
    def __init__(self, rows, book=None, book_raises=False):
        self.rows, self.book, self.book_raises = rows, book, book_raises

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        return [list(r) for r in self.rows]

    async def fetch_order_book(self, symbol, limit=20):
        if self.book_raises:
            raise RuntimeError("venue refused the book")
        return self.book or {"bids": [[1000.0, 1.0]], "asks": [[1001.0, 1.0]]}


class TestTheProducerIsThreeValued:
    async def test_a_null_close_in_the_window_leaves_the_percents_unread(self):
        """The 25th-from-last close is what `change_pct` divides by."""
        rows = _rows(40)
        # The forming bar is DROPPED before the window is built, so the close
        # `change_pct` divides by — `c[-25]` of the closed series — is the
        # raw row at -26. Stated rather than left as index arithmetic.
        rows[-26][4] = None
        d = await fetch_analysis_data(_Venue(rows), "BTC/USDT", "1h")
        assert d is not None, "the card is still buildable; only one term is not"
        assert d["change_pct"] is None, (
            "an unreadable 24h-ago close still produced a measured 0")

    async def test_a_null_close_never_yields_a_measured_zero_anywhere(self, caplog):
        """ABSENT FOR THE RIGHT REASON, which is what the mutation round asked.

        Deleting the price guard leaves the card absent ANYWAY — `vol_24h *
        price` raises on `None` a few lines down and the function's broad
        `except` swallows it. Same outcome, and a materially different one to
        read: a deliberate refusal that says WHY, against a crash at whichever
        line happened to touch the value first. So the reason is asserted, not
        just the absence.
        """
        import logging

        rows = _rows(40, close=None)             # nothing readable at all
        with caplog.at_level(logging.WARNING, logger="runeclaw.formatters"):
            d = await fetch_analysis_data(_Venue(rows), "BTC/USDT", "1h")
        assert d is None, (
            "with no readable price the card has no subject: the GUARD "
            "strategy, which this function already documents")
        assert any("no readable price" in r.getMessage() for r in caplog.records), (
            "the card was absent because something crashed, not because the "
            "price was read and found missing")

    async def test_a_raised_order_book_is_not_an_empty_one(self):
        d = await fetch_analysis_data(
            _Venue(_rows(40), book_raises=True), "BTC/USDT", "1h")
        assert d is not None
        assert d["bid_depth"] is None and d["ask_depth"] is None, (
            "a fetch that RAISED summed to 0 and three readers called it bearish")

    async def test_but_a_book_that_answered_with_no_rows_is_a_reading(self):
        d = await fetch_analysis_data(
            _Venue(_rows(40), book={"bids": [], "asks": []}), "BTC/USDT", "1h")
        assert d is not None
        assert d["bid_depth"] == 0.0 and d["ask_depth"] == 0.0, (
            "a venue that answered with a thin book measured something")

    async def test_zero_volume_leaves_the_spike_unread_not_calm(self):
        d = await fetch_analysis_data(
            _Venue(_rows(40, volume=0.0)), "BTC/USDT", "1h")
        assert d is not None
        assert d["vol_spike"] is None, "1.0 is 'no spike', the calm value"

    async def test_a_null_close_on_the_FORMING_bar_costs_only_the_mark(self):
        """The loud/silent asymmetry, from the other side.

        `mark = float(ohlcv[-1][4])` RAISED on a null forming close, and the
        broad `except` turned that into no card at all — while a null in any
        other row was coerced to NaN and published. One rule for both candles
        now: the mark is simply unread, and the last CLOSED close carries the
        price, so a card the window can still support is still built.
        """
        rows = _rows(40)
        rows[-1][4] = None                       # the forming bar only
        d = await fetch_analysis_data(_Venue(rows), "BTC/USDT", "1h")
        assert d is not None, (
            "a null on the bar that has not closed deleted the whole card")
        assert d["price"] == pytest.approx(rows[-2][4])

    async def test_an_unreadable_vwap_leaves_its_distance_unread(self):
        """`compute_vwap` multiplies the typical price by volume, so ONE null
        high in the window makes the whole VWAP NaN while the last close — and
        therefore the price — stays perfectly readable."""
        rows = _rows(40)
        rows[5][2] = None                        # a high, mid-window
        d = await fetch_analysis_data(_Venue(rows), "BTC/USDT", "1h")
        assert d is not None and d["price"] is not None
        assert d["vwap_pct"] is None, (
            "an unreadable VWAP still produced a measured 0, which the card "
            "reads as 'price is +0.0% above VWAP'")

    async def test_an_ordinary_series_still_measures_everything(self):
        """The cure must not turn a healthy read into an absence."""
        d = await fetch_analysis_data(_Venue(_rows(40)), "BTC/USDT", "1h")
        assert d is not None
        for k in ("change_pct", "vwap_pct", "vol_spike",
                  "bid_depth", "ask_depth"):
            assert d[k] is not None, f"{k} unread on a perfectly good series"


# ── the card ─────────────────────────────────────────────────────────────
def _card(**over):
    base = {
        "symbol": "BTC/USDT:USDT", "pair": "BTC", "price": 63000.0,
        "high_24h": 64000.0, "low_24h": 62000.0, "change_pct": 1.0,
        "volume_24h_usd": 1e6, "vol_spike": 1.5, "vwap": 62500.0,
        "vwap_pct": 0.8, "rsi": 50.0, "atr": 100.0, "sma9": 1.0,
        "sma20": 1.0, "sma50": 1.0, "bid_depth": 100.0, "ask_depth": 90.0,
        "supports": [(61000.0, 61500.0), (60000.0, 60500.0)],
        "resistances": [(64000.0,)], "structure": "Range-bound",
        "timeframe": "1H",
    }
    base.update(over)
    return render_analysis_card(base)


class TestTheCardSaysWhatItDidNotRead:
    def test_an_unread_vwap_makes_no_directional_claim(self):
        out = _card(vwap_pct=None, vwap=float("nan"))
        assert "above VWAP" not in out and "below VWAP" not in out
        assert "distance to VWAP not read" in out

    def test_a_measured_vwap_still_says_which_side(self):
        assert "above VWAP" in _card(vwap_pct=0.8)
        assert "below VWAP" in _card(vwap_pct=-0.8)

    def test_an_unread_move_says_the_gate_was_not_evaluated(self):
        """SILENCE IS TWO FACTS. A change inside the band and a change nobody
        read used to print the same nothing, on the line that says whether a
        counter-trend entry is blocked."""
        out = _card(change_pct=None)
        assert "Velocity Gate" in out
        assert "not evaluated" in out

    def test_a_measured_calm_move_stays_silent(self):
        assert "Velocity Gate" not in _card(change_pct=1.0)

    def test_a_measured_fast_move_still_blocks(self):
        out = _card(change_pct=22.0)
        assert "Velocity Gate" in out and "counter-trend short is blocked" in out

    def test_an_unread_book_prints_no_dominance_concern(self):
        """OMITTED rather than hedged: the Bid/Ask line already says so, and a
        second sentence about one absence is repetition, not disclosure."""
        out = _card(bid_depth=None, ask_depth=None)
        assert "book not read" in out
        assert "Ask-side dominance" not in out

    def test_an_unread_spike_is_a_dash(self):
        assert "Vol spike: —" in _card(vol_spike=None)

    def test_an_unread_vwap_does_not_place_a_support_level(self):
        """`abs(s - vwap) / vwap` with an unread VWAP used to label the level
        "deeper support" — a confident placement against nothing."""
        out = _card(vwap_pct=None, vwap=float("nan"))
        assert "deeper support" not in out and "VWAP area" not in out


# ── the verdict ──────────────────────────────────────────────────────────
def _asset(sym, **over):
    a = {"symbol": sym, "pair": sym.split("/")[0], "price": 100.0,
         "change_pct": 1.0, "volume_24h_usd": 1e6, "vol_spike": 1.5,
         "vwap": 99.0, "vwap_pct": 2.0, "rsi": 50.0, "atr": 1.0,
         "bid_depth": 100.0, "ask_depth": 90.0, "structure": "x",
         "supports": [], "resistances": []}
    a.update(over)
    return a


def _verdict(out):
    for line in out.splitlines():
        if line.startswith("• Verdict:"):
            return line
    raise AssertionError("no verdict row")


class TestAnUnreadAssetIsNotPreferred:
    def test_the_defect_as_it_was(self):
        """Same book on both. The unread one scored 2, the MEASURED 14%
        extended one scored 0, and the unreadable asset won the bold row."""
        out = render_comparison_table(
            [_asset("AAA/USDT", vwap_pct=None), _asset("BBB/USDT", vwap_pct=14.0)], [])
        v = _verdict(out)
        assert "<b>Preferred</b>" not in v, "an unread term ranked favourably"
        assert "VWAP unread" in v

    def test_an_unread_book_is_not_ranked_either(self):
        out = render_comparison_table(
            [_asset("AAA/USDT", bid_depth=None, ask_depth=None),
             _asset("BBB/USDT")], [])
        assert "orderbook unread" in _verdict(out)

    def test_two_readable_assets_still_rank(self):
        """The cure must not make the card refuse to do its job."""
        out = render_comparison_table(
            [_asset("AAA/USDT", vwap_pct=2.0), _asset("BBB/USDT", vwap_pct=14.0)], [])
        v = _verdict(out)
        assert "<b>Preferred</b>" in v and "Secondary" in v

    def test_one_scorable_asset_is_not_a_comparison(self):
        """"Preferred" over a set of one reads as a recommendation and is a
        statement about nothing."""
        out = render_comparison_table(
            [_asset("AAA/USDT"), _asset("BBB/USDT", vol_spike=None)], [])
        assert "<b>Preferred</b>" not in _verdict(out)

    def test_the_cells_say_which_reading_is_missing(self):
        out = render_comparison_table(
            [_asset("AAA/USDT", vwap_pct=None, bid_depth=None, ask_depth=None),
             _asset("BBB/USDT")], [])
        assert "(not read)" in out and "(book not read)" in out
