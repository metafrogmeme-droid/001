"""The POC-retest read, off two real timeframes, and its one door.

`poc_retest` is driven candles-in in its own suite. This file is about
everything between a venue and a chat bubble, and every claim in it is one a
source scan cannot make:

  * A FORMING CANDLE'S CLOSE IS NOT A CLOSE. The whole sequence is closes —
    "a candle close at least 0.25x ATR above it", "the candle must close back
    above POC" — and the last bar a venue hands back is still being built. It
    is DRIVEN here: the same series with a live final bar and with that bar
    already closed produce different states, which is the only proof that the
    hygiene call is reached.
  * A FETCH THAT FAILED IS NOT A SETUP THAT DID NOT FORM. Two timeframes fail
    independently and `no_breakout` is a claim about price.
  * THE CARD SAYS WHAT IT READ, and says that nothing was placed.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import re
import time
from pathlib import Path

import pytest

from bot.core import poc_retest_scan as S
from bot.core.poc_retest import STATES, VERDICTS, PocRetestParams
from bot.core.poc_retest_scan import ENTRY_TF, STRUCTURE_TF, SymbolSetup, read_setup, setup_card
from tests.source_scan import code_only
from tests.test_the_poc_retest_is_a_sequence_not_a_distance import htf, ltf

ROOT = Path(__file__).resolve().parents[1]


# ── fixtures ──────────────────────────────────────────────────────────────

def _rows(highs, lows, closes, volumes=None, *, last_open_ms=None,
          step_ms=3_600_000):
    """ccxt rows: [ts, open, high, low, close, volume].

    `last_open_ms` places the FINAL bar's open time, which is the only thing
    `drop_forming_candle` reads. Left None the series lands far in the past,
    so every bar is closed and the hygiene call is a no-op — which is what
    every other fixture here wants.
    """
    n = len(closes)
    vol = volumes if volumes is not None else [10.0] * n
    end = last_open_ms if last_open_ms is not None else 1_000_000_000_000
    return [[end - (n - 1 - i) * step_ms, closes[i], highs[i], lows[i],
             closes[i], vol[i]] for i in range(n)]


class FakeExchange:
    """Answers per timeframe. A value that is an Exception is RAISED.

    `fetch_ohlcv` is a coroutine here because ccxt's is; one test replaces it
    with a plain function, because `_maybe_await` claims to take both and a
    claim nothing drives is not a claim.
    """

    def __init__(self, by_tf, *, sync=False):
        self.by_tf = by_tf
        self.calls: list[tuple[str, str, int]] = []
        self._sync = sync

    def _answer(self, symbol, timeframe, limit):
        self.calls.append((symbol, timeframe, limit))
        v = self.by_tf.get(timeframe)
        if isinstance(v, Exception):
            raise v
        return v

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        if self._sync:
            return self._answer(symbol, timeframe, limit)

        async def _go():
            return self._answer(symbol, timeframe, limit)
        return _go()


def _good_series():
    from bot.core.poc_retest import leg_poc, swing_leg
    H, L, C, V = htf(up=True)
    poc = leg_poc(H, L, C, V, swing_leg(H, L))
    hi, lo, cl = ltf(poc, side="long")
    return (H, L, C, V), (hi, lo, cl), poc


def _exchange(**tf_override):
    (H, L, C, V), (hi, lo, cl), _ = _good_series()
    by = {STRUCTURE_TF: _rows(H, L, C, V), ENTRY_TF: _rows(hi, lo, cl)}
    by.update(tf_override)
    return FakeExchange(by)


def _read(ex, symbol="ARB/USDT", **kw):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        read_setup(ex, symbol, **kw))


# ── the two timeframes ────────────────────────────────────────────────────

class TestTheSplitIsTheSpecs:
    def test_the_structure_comes_from_4h_and_the_sequence_from_1h(self):
        """"4-hour chart: identify swing highs/lows and calculate the volume
        profile. 1-hour chart: confirm the POC retest and execute the entry."
        """
        assert (STRUCTURE_TF, ENTRY_TF) == ("4h", "1h")
        ex = _exchange()
        s = _read(ex)
        assert s.read is not None and s.read.state == "confirmed", s
        asked = {tf for _, tf, _ in ex.calls}
        assert asked == {"4h", "1h"}, asked

    def test_the_atr_is_the_ENTRY_timeframe_s(self):
        """A 4h ATR is roughly twice as wide and would quietly widen both the
        decisive-close buffer and the stop. Driven by changing only the 1h
        series and watching the ATR move."""
        from bot.core.position_telemetry import atr_reading
        (H, L, C, V), (hi, lo, cl), _ = _good_series()
        s = _read(_exchange())
        assert s.read.atr == pytest.approx(atr_reading(hi, lo, cl, 14))

    def test_a_plain_fetch_ohlcv_is_accepted_too(self):
        """`_maybe_await`'s own claim. `exchange_flow.py` carries this guard
        and its docstring records the bug shipping once before: a coroutine
        called without `await` fails into a broad handler and the reading
        answers None forever — wired, called, and dead."""
        (H, L, C, V), (hi, lo, cl), _ = _good_series()
        ex = FakeExchange({STRUCTURE_TF: _rows(H, L, C, V),
                           ENTRY_TF: _rows(hi, lo, cl)}, sync=True)
        s = _read(ex)
        assert s.read is not None and s.read.state == "confirmed"


class TestAFormingCandleIsNotAClose:
    """The one claim in this file that no scan can make.

    The hygiene call is reached, or it is not, and the only evidence is that
    the same series answers differently depending on whether its final bar's
    period has elapsed.
    """

    def _series_whose_last_bar_is_the_breakout(self):
        """A 1h series where the LAST bar is the decisive close.

        Drop it and nothing ever cleared the buffer; keep it and the sequence
        arms. Two states from one series, decided entirely by the hygiene
        call.
        """
        (H, L, C, V), _, poc = _good_series()
        hi, lo, cl = ltf(poc, side="long")
        return (H, L, C, V), (hi[:-1], lo[:-1], cl[:-1])

    def test_the_still_forming_bar_is_dropped(self):
        from bot.config import CONFIG
        if not getattr(CONFIG.analyzer, "drop_unclosed_candle_enabled", False):
            pytest.skip("DROP_UNCLOSED_CANDLE_ENABLED is off in this config")
        (H, L, C, V), (hi, lo, cl) = self._series_whose_last_bar_is_the_breakout()
        now_ms = time.time() * 1000.0
        # The final bar opened one minute ago on a 1h chart: still forming.
        live = FakeExchange({
            STRUCTURE_TF: _rows(H, L, C, V, last_open_ms=int(now_ms),
                                step_ms=4 * 3_600_000),
            ENTRY_TF: _rows(hi, lo, cl, last_open_ms=int(now_ms - 60_000))})
        # The same series with that bar's period long elapsed.
        closed = FakeExchange({
            STRUCTURE_TF: _rows(H, L, C, V, step_ms=4 * 3_600_000),
            ENTRY_TF: _rows(hi, lo, cl)})
        forming = _read(live)
        settled = _read(closed)
        assert settled.read.state == "awaiting_retest", settled.read.why
        assert forming.read.state == "no_breakout", (
            "the forming bar WAS the decisive close — reading it as a close "
            f"arms a sequence on a bar that has not happened: {forming.read}")
        assert forming.entry_bars == settled.entry_bars - 1

    def test_the_hygiene_call_is_on_both_timeframes(self):
        """A scan, and it says so: the DRIVE above proves the entry
        timeframe, and the structure one is the same line in the same helper
        — so what is worth pinning is that neither reads raw rows."""
        src = code_only(Path(S.__file__).read_text(encoding="utf-8"))
        tree = ast.parse(src)
        fn, = [n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_ohlcv"]
        body = ast.unparse(fn)
        assert "drop_forming_candle" in body
        # And the one fetch helper is the only place rows are read, so a
        # second fetch cannot skip it.
        assert src.count("fetch_ohlcv") == 1


# ── a failed read is never a state ────────────────────────────────────────

class TestAFailedFetchIsNotAMarketFact:
    @pytest.mark.parametrize("tf", [STRUCTURE_TF, ENTRY_TF])
    def test_a_raising_fetch_is_unread_naming_its_timeframe(self, tf):
        """THE SENTENCE IS THE DISTINCTION, not the fact of being unread.

        The first draft asserted only that the answer was `unread` and named
        its timeframe — and the mutation that folds a RAISING fetch into an
        empty list survived it, because an empty list then trips the
        candle-count shortfall and comes back unread naming the same
        timeframe. "only 0 closed 4h candles" sends an operator looking for a
        thin market; "could not be fetched" sends them to the network. Three
        different facts, three sentences.
        """
        s = _read(_exchange(**{tf: RuntimeError("venue exploded")}))
        assert s.read is None and s.verdict is None
        assert s.unread is not None and tf in s.unread
        assert s.unread not in STATES
        assert "could not be fetched" in s.unread, s.unread
        assert "only 0" not in s.unread, "a fetch that failed read no candles"

    @pytest.mark.parametrize("tf", [STRUCTURE_TF, ENTRY_TF])
    def test_an_empty_answer_is_unread_not_a_quiet_market(self, tf):
        """A venue that ANSWERED with nothing is its own third fact, apart
        from a fetch that failed and from a window too short."""
        s = _read(_exchange(**{tf: []}))
        assert s.read is None
        assert s.unread is not None and tf in s.unread
        assert "returned no" in s.unread, s.unread
        assert "could not be fetched" not in s.unread

    def test_the_venue_s_own_words_never_reach_the_card(self):
        """A rejection can echo request parameters, and this string is a chat
        bubble. `_note_sltp_error`'s rule, one module over."""
        s = _read(_exchange(**{ENTRY_TF: RuntimeError(
            "signature=deadbeef apiKey=bg_live_1234 rejected")}))
        card = setup_card(s)
        for leak in ("deadbeef", "bg_live_1234", "apiKey", "signature"):
            assert leak not in card, leak
        assert "RuntimeError" not in card, "not even the class name"

    def test_a_window_too_short_is_unread_not_no_leg(self):
        """`swing_leg` answers `no_leg` for a short series and it is TRUE —
        of candles nobody has. On the card it reads as a fact about the 4h
        chart, so the shortfall is named instead."""
        (H, L, C, V), (hi, lo, cl), _ = _good_series()
        s = _read(_exchange(**{STRUCTURE_TF: _rows(H, L, C, V)[:8]}))
        assert s.read is None, "no_leg would be a claim about the market"
        assert s.unread is not None
        assert "8" in s.unread and STRUCTURE_TF in s.unread
        assert s.structure_bars == 8

    def test_too_few_entry_candles_is_unread_not_atr_unread(self):
        (H, L, C, V), (hi, lo, cl), _ = _good_series()
        s = _read(_exchange(**{ENTRY_TF: _rows(hi, lo, cl)[:10]}))
        assert s.read is None
        assert "ATR(14)" in s.unread and "10" in s.unread

    def test_the_4h_floor_moves_with_its_parameter(self):
        """Raise `swing_order` and the 4h requirement moves with it — the
        same rule the detector's own suite applies to its three thresholds.
        """
        s = _read(_exchange(), params=PocRetestParams(swing_order=25))
        assert s.read is None, "40 candles cannot carry an order-25 fractal"
        assert "52" in s.unread, s.unread

    def test_the_1h_floor_moves_with_its_parameter(self):
        """The sibling of the test above, and it was missing: no fixture
        moved `atr_period`, so `need_ltf = 15` as a literal survived the
        whole first mutation round. ATR(n) needs n+1 candles because a true
        range needs a previous close.
        """
        s = _read(_exchange(), params=PocRetestParams(atr_period=40))
        assert s.read is None, "27 candles cannot carry ATR(40)"
        assert "ATR(40)" in s.unread and "41" in s.unread, s.unread


# ── the card ──────────────────────────────────────────────────────────────

class TestTheCardSaysWhatItRead:
    def test_it_names_the_sample_it_read(self):
        """A sequence read off 20 closed 1h bars and one read off 120 are
        identical on the card and are not the same evidence. That is the
        CROSSFIRE focus-room footnote, on a setup rather than a chart."""
        s = _read(_exchange())
        card = setup_card(s)
        assert f"{s.structure_bars} closed {STRUCTURE_TF}" in card
        assert f"{s.entry_bars} closed {ENTRY_TF}" in card

    def test_a_sample_nobody_recorded_is_not_printed_as_a_count(self):
        """`SymbolSetup` is a dataclass any caller can build.

        The first draft interpolated the counts raw, so a setup carrying a
        read and no counts printed "Read off None closed 4h and None closed
        1h candles" — an absence rendered as a measurement, on the one line
        whose whole job is to say what was read. Found by reading the card
        rather than the diff, which is how every other instance in this repo
        was found.
        """
        s = _read(_exchange())
        bare = SymbolSetup("ARB/USDT", read=s.read, verdict=s.verdict)
        card = setup_card(bare)
        assert "None closed" not in card
        assert "was not recorded" in card
        # And the two things the card is never allowed to stop saying.
        assert "Nothing was placed" in card

    def test_it_says_nothing_was_placed(self):
        card = setup_card(_read(_exchange()))
        assert "Nothing was placed" in card
        assert "nothing was armed" in card

    def test_it_says_the_thresholds_are_not_validated(self):
        """The operator's own framing — "sensible starting rules, not yet
        validated results" — on the surface that would otherwise read as a
        recommendation."""
        card = setup_card(_read(_exchange()))
        assert "starting values, not validated results" in card

    def test_a_confirmed_card_carries_all_four_levels(self):
        s = _read(_exchange())
        card = setup_card(s)
        assert s.read.state == "confirmed"
        for v in (s.read.poc, s.read.entry, s.read.stop, s.read.target):
            assert f"{v:,.6f}" in card, v

    def test_the_stop_names_the_extreme_it_sits_past(self):
        """"past it" was the first draft, and "it" could be read as the entry
        or the POC — on the one level where a misreading costs money."""
        card = setup_card(_read(_exchange()))
        assert "below the retest candle's low" in card

    def test_an_unread_card_claims_nothing_about_the_market(self):
        card = setup_card(_read(_exchange(**{ENTRY_TF: []})))
        assert "Nothing was measured" in card
        for state_word in ("breakout", "retest held", "swing leg"):
            assert state_word not in card.lower(), state_word

    def test_the_card_can_place_every_state_and_every_verdict(self):
        """An allow-list whose stale entries fail — the `known_failures.txt`
        rule. The fallback says THIS BUILD cannot place the state rather than
        borrowing "no reading", which would report a detector the card does
        not understand as a market that did nothing."""
        assert set(S._HEADLINE) == set(STATES), (
            sorted(set(STATES) ^ set(S._HEADLINE)))
        assert set(S._VERDICT_HEAD) == set(VERDICTS), (
            sorted(set(VERDICTS) ^ set(S._VERDICT_HEAD)))

    def test_a_state_this_build_cannot_place_says_SO(self):
        """The fallback, DRIVEN — the set-equality pin above means no product
        state reaches it, so nothing rendered it and the mutation that made
        it borrow "no reading" survived the first round. A word the card
        cannot place is the CARD's failure, not the market's, and reporting a
        detector this build does not understand as a market that did nothing
        is the confident negative everything here is about.
        """
        s = _read(_exchange())
        martian = type(s.read)(**{**s.read.__dict__, "state": "martian"})
        card = setup_card(SymbolSetup(
            "ARB/USDT", read=martian, verdict=s.verdict,
            structure_bars=s.structure_bars, entry_bars=s.entry_bars))
        assert "cannot place that state" in card
        assert "No reading" not in card, (
            "borrowing the ATR headline reports a state this build does not "
            "know as a market that did nothing")

    def test_a_setup_with_neither_a_read_nor_a_reason_is_still_honest(self):
        """`read_setup` never produces this; `SymbolSetup` is a dataclass any
        caller can build, and the first draft asserted its way out — which is
        stripped under -O and would then index None."""
        card = setup_card(SymbolSetup("ARB/USDT"))
        assert "Not read" in card and "no reason recorded" in card

    def test_the_symbol_is_escaped(self):
        card = setup_card(SymbolSetup("<b>X</b>/USDT", unread="nope"))
        assert "<b>X</b>/USDT" not in card
        assert "&lt;b&gt;X&lt;/b&gt;/USDT" in card


# ── the door ──────────────────────────────────────────────────────────────

class TestTheDoor:
    def test_the_command_is_guarded_on_analyze(self):
        """One asset read in depth is `/quant`'s question one layer deeper,
        not the universe sweep `scan` gates."""
        baseline = (ROOT / "tests" / "guarded_commands_baseline.txt").read_text(
            encoding="utf-8")
        assert "_cmd_pocretest analyze" in baseline

    def test_the_handler_builds_no_card_of_its_own(self):
        """A card built inline in a handler is a card no test can run —
        #999's own lesson, and the reason this handler is four lines."""
        from bot.skills.scan_commands import ScanCommands
        body = code_only(inspect.getsource(ScanCommands._cmd_pocretest))
        assert "setup_card" in body and "read_setup" in body
        # No card assembly here: no state vocabulary, no level formatting.
        for own in ("POC:", "Entry (", "🟢", "Nothing was placed"):
            assert own not in body, own

    def test_it_is_registered_under_its_own_name(self):
        src = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(
            encoding="utf-8")
        assert '("pocretest", self._cmd_pocretest)' in src

    def test_the_catalogue_row_names_the_timeframe_and_the_argument(self):
        from bot.skills.command_catalog import all_entries
        entries = all_entries()
        assert "pocretest" in entries
        # (group, audience, description) — not a bare string, which is what
        # the first draft of this assertion expected.
        group, audience, desc = entries["pocretest"]
        assert STRUCTURE_TF in desc and "/pocretest" in desc, desc
        assert audience == "user", audience
        assert "Scan" in group, group

    def test_every_locale_carries_the_row(self):
        """Fourteen languages, and `translate`'s English fallback is silent —
        the deck study's own trap, so the files are read directly."""
        import json
        d = ROOT / "bot" / "skills" / "command_catalog_locales"
        langs = sorted(p.stem for p in d.glob("*.json"))
        assert len(langs) == 12, langs        # plus en + zh, held inline
        for lang in langs:
            data = json.loads((d / f"{lang}.json").read_text(encoding="utf-8"))
            text = data["desc"].get("pocretest")
            assert isinstance(text, str) and text.strip(), lang

    def test_the_symbol_takes_a_bare_ticker_or_a_pair(self):
        """`_extract_symbol` produces `ETH/USDT`, and every sibling in this
        file takes a bare ticker. Both reach the read as one pair."""
        src = code_only(inspect.getsource(
            __import__("bot.skills.scan_commands", fromlist=["x"])
            .ScanCommands._cmd_pocretest))
        assert re.search(r'raw if "/" in raw else', src), src
