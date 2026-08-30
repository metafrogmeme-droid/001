"""The leverage rest was written under one symbol and read under another.

The overshoot guard closes a position the venue over-levered, then rests the
symbol so the engine cannot immediately re-signal it — Bitget's sticky
per-symbol leverage does not heal because we closed, so without the rest the
guard is a fee pump. Its own comment says exactly that, twice.

Both halves existed. They did not use the same key.

    execute()            symbol = self._venue.swap_symbol(idea.asset)
                         self._leverage_blocked_until[symbol] = ...
                                                    ->  "APT/USDT:USDT"

    execute()            self._preflight_check(size_usd, symbol=idea.asset)
    _preflight_check()   self._leverage_blocked_until.get(symbol)
                                                    ->  "APT/USDT"

`symbol` is reassigned to the perp form ~1200 lines above the write, for a
completely unrelated and correct reason (the perp tick grid is coarser than the
spot one, and a spot-rounded price gets rejected with 45115). The rest
inherited it. A lookup of "APT/USDT" against a key of "APT/USDT:USDT" misses,
so on the futures path — which is every path; TRADE_MODE is validated to
"futures" and nothing else — the rest the market guard set could never be found
by the check that exists to read it.

The fill path (`_guard_fill_leverage`) rests `pos.symbol`, which is
`idea.asset` for a position this bot opened and the VENUE's own perp form for
one adopted off the exchange. So the same map held both spellings, and the
reader knew one of them.

WHY THE EXISTING TESTS PASSED. Two of them cover this rest, and both plant a
key and then read back the same literal:

    ex._leverage_blocked_until[sym] = time.monotonic() + _LEVERAGE_BLOCK_SECONDS
    assert ex._preflight_check(10.0, symbol=sym) is not None

That is the confidence-floor shape one level down: every gate agreed with
itself. Nothing asked whether the string the writer produces is the string the
reader asks for, because the test supplied both. They go through the write path
now.

There was already a canonicaliser in this file — `normalize_symbol`, used by
`_recent_local_opens` and `_last_sltp_error` for this exact reason. The rest
simply never called it.
"""

from __future__ import annotations

import io
import time
import tokenize
from pathlib import Path

import pytest

from bot.core.live_executor import (_LEVERAGE_BLOCK_SECONDS, LiveExecutor,
                                    normalize_symbol)


@pytest.fixture
def ex(tmp_path):
    return LiveExecutor(state_dir=str(tmp_path))


# ── the defect, as the two real paths ────────────────────────────────────

class TestTheWriterAndTheReaderAgree:
    def test_the_market_path_rest_is_found_by_the_check_that_reads_it(self, ex):
        asset = "APT/USDT"                       # idea.asset, as execute() has it
        perp = ex._venue.swap_symbol(asset)      # what `symbol` had become by then
        assert perp != asset, (
            "premise check: if these are the same string this test proves "
            "nothing and the defect it describes does not exist")

        ex._rest_symbol(perp)

        err = ex._preflight_check(10.0, symbol=asset)
        assert err is not None, (
            "the guard rested the perp symbol and the check asks under the "
            "spot symbol — so the rest existed, was correct, and was "
            "unreachable, and the engine re-signalled a symbol the venue "
            "over-levers")
        assert "resting" in err

    def test_an_adopted_position_rested_by_venue_symbol_is_found_too(self, ex):
        # pos.symbol for an adopted position is the exchange's own perp form,
        # so the fill path put that spelling in the map as well.
        ex._rest_symbol("APT/USDT:USDT")
        assert ex._preflight_check(10.0, symbol="APT/USDT") is not None

    def test_the_reverse_direction_holds_as_well(self, ex):
        # A rest set under the spot form must refuse a check made under the
        # perp form. One key, asked either way.
        ex._rest_symbol("APT/USDT")
        assert ex._preflight_check(10.0, symbol="APT/USDT:USDT") is not None

    def test_it_is_still_PER_SYMBOL(self, ex):
        # Canonicalising must not collapse the book onto one key. One sticky
        # symbol halting every entry would be a worse bug than the one fixed.
        ex._rest_symbol("APT/USDT:USDT")
        assert ex._preflight_check(10.0, symbol="BTC/USDT") is None

    def test_the_rest_still_expires_and_is_dropped(self, ex):
        ex._rest_symbol("APT/USDT:USDT", seconds=-1.0)
        assert ex._preflight_check(10.0, symbol="APT/USDT") is None
        assert not ex._leverage_blocked_until, (
            "an expired rest must be dropped under the SAME key it was stored "
            "under, or the map grows for the lifetime of the process")


class TestTheKeyIsTheFileAlreadyHadOne:
    def test_the_rest_is_per_ASSET_not_per_spelling(self, ex):
        # normalize_symbol reduces to the bare base, so "APT/USDT:USDT",
        # "APT/USDT" and "APT" are one key — which is the point, those being
        # exactly the spellings that kept the rest from firing.
        for spelling in ("APT/USDT:USDT", "APT/USDT", "APT"):
            assert normalize_symbol(spelling) == "APT"
            assert ex._preflight_check(10.0, symbol=spelling) is None
        ex._rest_symbol("APT/USDT")
        for spelling in ("APT/USDT:USDT", "APT/USDT", "APT"):
            assert ex._preflight_check(10.0, symbol=spelling) is not None

    def test_a_different_quote_resting_too_is_deliberate(self, ex):
        # normalize_symbol also collapses APT/USDC onto APT. Recorded as a
        # decision rather than left to be discovered: the sticky setting that
        # caused the incident belongs to the venue's leverage for that ASSET,
        # the rest lasts an hour, and it follows a CONFIRMED over-levered fill.
        # Over-resting one asset costs an entry; under-resting it costs the
        # fee-pump loop the rest exists to stop. The bot is single-quote in
        # practice, so this is a direction, not a daily event.
        ex._rest_symbol("APT/USDT")
        assert ex._preflight_check(10.0, symbol="APT/USDC") is not None

    def test_an_unnamed_symbol_is_never_rested_and_never_matches(self, ex):
        # normalize_symbol("   ") is "   " — truthy, and a perfectly good dict
        # key. Both sides strip first, so blank input cannot become a rest and
        # cannot find one.
        for bad in ("", "   ", None):
            assert ex._rest_key(bad) == ""
            ex._rest_symbol(bad)
        assert not ex._leverage_blocked_until, (
            "a blank symbol was rested under an empty key — every later check "
            "made with a blank symbol would then read as blocked")
        ex._rest_symbol("APT/USDT")
        assert ex._preflight_check(10.0, symbol="   ") is None


class TestTheRestIsWrittenInOnePlace:
    """The reachability half.

    The defect was not in what either side DID; it was that the key was formed
    twice, from two different variables, 2800 lines apart. A new raw subscript
    puts it straight back and looks fine, because whoever writes it will read
    it back with the same string in their own test.
    """

    @staticmethod
    def _code_only(path):
        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(io.BytesIO(fh.read()).readline):
                if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                    out.append(tok.string)
        return " ".join(out)

    def test_the_map_is_subscripted_in_exactly_one_place(self):
        code = self._code_only(Path("bot/core/live_executor.py"))
        assert code.count("_leverage_blocked_until [") == 1, (
            "a leverage rest is written by subscripting the map directly "
            "again. Use self._rest_symbol(symbol) — otherwise the key depends "
            "on which spelling of the symbol that call site happens to hold, "
            "which is the whole defect.")
        assert "_leverage_blocked_until [ key ] =" in code, (
            "the single write does not go through the canonical key")

    def test_both_guard_paths_rest_through_the_helper(self):
        code = self._code_only(Path("bot/core/live_executor.py"))
        assert code.count("self . _rest_symbol (") >= 2, (
            "the market path and the fill path must both rest through the "
            "helper — there were two writers, and that is how they diverged")


class TestItReallyIsWiredIntoPreflight:
    def test_preflight_reads_through_the_canonical_key(self, ex):
        # #58: a canonicaliser nothing calls is indistinguishable from one that
        # does not work. Planted at the map under the canonical key, read
        # through the check under a different spelling.
        ex._leverage_blocked_until["APT"] = time.monotonic() + _LEVERAGE_BLOCK_SECONDS
        assert ex._preflight_check(10.0, symbol="APT/USDT:USDT") is not None

    def test_the_refusal_names_the_symbol_the_CALLER_asked_about(self, ex):
        # The operator asked about the perp symbol; answering with the internal
        # key would be answering a question nobody asked.
        ex._rest_symbol("APT/USDT")
        err = ex._preflight_check(10.0, symbol="APT/USDT:USDT")
        assert "APT/USDT:USDT" in err
