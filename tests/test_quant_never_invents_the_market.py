"""`/quant` must not model candles nobody fetched.

`QuantAnalyzeSkill.execute` caught every exchange failure and substituted
`_generate_synthetic_ohlcv(150, seed=hash(symbol))`, then returned the ordinary
report. Driven against a 503 the card read:

    RUNECLAW QUANT REPORT — BTC/USDT [4h]
    Regime:       ↔️  Ranging
    ADX:          22.9  (weak/ranging)
    Hurst (H):    0.851  → trending memory
    GARCH Vol:    curr=0.0116  fcast=0.0120  EXPANDING
    Composite Score: 0.381 → WEAK
    ❌ QUANT GATE: REJECTED — LOW_QUANT_SCORE: 0.381 < 0.4 threshold

Every number invented, `bars_analyzed: 150` for bars nobody fetched, and the
word "synthetic" only in the audit log where no reader of the card would meet
it. The seed is the part that turns a bug into a trap: `hash(symbol)` is
stable, so the SAME fabricated report comes back every time and a user who runs
it twice reads the consistency as corroboration.

The offline path is real and stays — `scripts/e2e_pipeline.py` walks the whole
pipeline with no venue attached and deterministic candles are what let it — so
the fix is not to delete the generator. It is to make asking for it explicit,
and to say so on the card rather than in a log line.
"""

import asyncio

import pytest

from bot.skills.quant_skill import (QuantAnalyzeSkill, _generate_synthetic_ohlcv,
                                    read_ohlcv, synthetic_notice,
                                    unreadable_card)

SYMBOL = "BTC/USDT"


class _Venue:
    def __init__(self, behaviour):
        self._behaviour = behaviour

    async def fetch_ohlcv(self, symbol, timeframe, limit=150):
        if self._behaviour == "dead":
            raise RuntimeError("503 Service Unavailable")
        if self._behaviour == "empty":
            return []
        if self._behaviour == "garbage":
            return [["not", "a", "number"]]
        return _generate_synthetic_ohlcv(limit, seed=7)


class _Engine:
    def __init__(self, venue):
        self._venue = venue

    async def get_exchange(self):
        return self._venue


def _run(**kwargs):
    venue = kwargs.pop("venue")
    return asyncio.run(QuantAnalyzeSkill().execute(_Engine(venue), **kwargs))


# ── the seam ──────────────────────────────────────────────────────────────

class TestReadOhlcvNeverInvents:
    @pytest.mark.parametrize("behaviour,expect", [
        ("dead", "503"),
        ("empty", "no candles"),
        ("garbage", "could not be read"),
    ])
    def test_a_failed_read_answers_a_reason_and_no_bars(self, behaviour, expect):
        bars, failure = asyncio.run(
            read_ohlcv(_Engine(_Venue(behaviour)), SYMBOL, "4h"))
        assert bars == []
        assert failure is not None and expect in failure

    def test_a_good_read_answers_bars_and_no_failure(self):
        bars, failure = asyncio.run(
            read_ohlcv(_Engine(_Venue("live")), SYMBOL, "4h"))
        assert failure is None
        assert len(bars) == 150

    def test_no_exchange_is_a_failure_not_an_empty_report(self):
        bars, failure = asyncio.run(read_ohlcv(_Engine(None), SYMBOL, "4h"))
        assert bars == []
        assert failure is not None

    def test_the_reason_is_trimmed_and_single_line(self):
        """A driver's message reaches a user-facing card, so it is bounded.

        `/readyz` returns a coarse code for this reason; here the text is
        useful (the operator wants to know it was a 503), so it is length-
        capped and whitespace-collapsed instead.
        """
        class Chatty:
            async def fetch_ohlcv(self, *a, **k):
                raise RuntimeError("x" * 500 + "\nsecond line")
        _, failure = asyncio.run(read_ohlcv(_Engine(Chatty()), SYMBOL, "4h"))
        assert failure is not None
        assert len(failure) <= 120
        assert "\n" not in failure


# ── what the operator is told ─────────────────────────────────────────────

class TestTheUnreadableCardQuotesNoStatistic:
    """The whole defect was a card that looked exactly like a good one.

    The first draft of this listed bare words — "Hurst", "ADX", "Regime" — and
    failed on the refusal card's OWN sentence, "Regime, volatility, Hurst and
    the edge gate are all unknown", which is the honest thing to say and names
    no value. That is the trap CLAUDE.md counts six instances of, done a
    seventh time in the file written about it: a short string asserted ABSENT
    matches the surrounding prose. The claim is not "never say Hurst", it is
    "never PRINT one", so these anchor on the report's own rendering — a label,
    a colon, and a number — which prose cannot produce.
    """

    NUMBERS = ("Hurst (H):", "ADX:", "GARCH Vol:", "Composite Score:",
               "Regime:", "Volatility:", "Price Z-Score:", "QUANT GATE:")

    def test_it_names_the_symbol_it_could_not_model(self):
        card = unreadable_card("SOL/USDT", "1h", "503 Service Unavailable")
        assert "SOL/USDT" in card and "1h" in card

    def test_it_states_the_reason(self):
        card = unreadable_card(SYMBOL, "4h", "the venue returned no candles")
        assert "the venue returned no candles" in card

    @pytest.mark.parametrize("field", NUMBERS)
    def test_it_carries_no_measurement(self, field):
        card = unreadable_card(SYMBOL, "4h", "503")
        assert field not in card, (
            f"the no-data card printed {field!r} — it must quote no statistic")

    def test_unknown_is_not_neutral(self):
        """`0.381 → WEAK` was a verdict manufactured from noise.

        Saying "neutral" or "weak" here would repeat the defect in words
        instead of numbers.
        """
        card = unreadable_card(SYMBOL, "4h", "503").lower()
        assert "unknown" in card
        assert "not neutral" in card


class TestTheSyntheticBannerIsUnmissable:
    def test_it_says_the_candles_were_generated(self):
        note = synthetic_notice(SYMBOL, "4h").lower()
        assert "synthetic" in note
        assert "generated" in note
        assert "no venue was" in note

    def test_it_tells_the_reader_not_to_trade_on_it(self):
        assert "do not trade" in synthetic_notice(SYMBOL, "4h").lower()


# ── the command's three outcomes, driven ──────────────────────────────────

class TestChatNeverSeesInventedNumbers:
    def test_a_dead_venue_produces_no_report(self):
        out = _run(venue=_Venue("dead"))
        assert "NO REPORT" in out
        for field in TestTheUnreadableCardQuotesNoStatistic.NUMBERS:
            assert field not in out
        assert "503" in out

    def test_an_empty_answer_produces_no_report(self):
        """Empty is a failure, not an empty report — a single-source panel."""
        out = _run(venue=_Venue("empty"))
        assert "NO REPORT" in out
        assert "Hurst (H):" not in out

    def test_a_live_read_produces_the_report_and_no_banner(self):
        out = _run(venue=_Venue("live"))
        assert "Hurst" in out and "QUANT GATE" in out
        assert "SYNTHETIC" not in out
        assert "NO REPORT" not in out

    def test_synthetic_is_opt_in(self):
        """The default must never reach the generator. This is the fix."""
        default = _run(venue=_Venue("dead"))
        assert "SYNTHETIC" not in default
        assert "Hurst (H):" not in default

    def test_an_opted_in_caller_gets_a_report_under_a_banner(self):
        out = _run(venue=_Venue("dead"), allow_synthetic=True)
        assert "SYNTHETIC DATA" in out
        assert "Hurst" in out, "the e2e pipeline still needs its report"
        assert out.index("SYNTHETIC DATA") < out.index("Hurst"), (
            "the banner must precede the numbers it disclaims")

    def test_the_banner_is_not_glued_to_a_live_report(self):
        """A mutation that always banners would make the label meaningless."""
        assert "SYNTHETIC" not in _run(venue=_Venue("live"),
                                       allow_synthetic=True)


class TestTheStableSeedNoLongerCorroboratesItself:
    def test_two_failed_reads_do_not_return_the_same_confident_report(self):
        """`seed=hash(symbol)` returned an identical fake report every time.

        Consistency across runs is exactly what a user reads as corroboration,
        so it is the property most worth pinning gone.
        """
        first = _run(venue=_Venue("dead"))
        second = _run(venue=_Venue("dead"))
        assert first == second, "the refusal itself should be stable"
        assert "Composite Score" not in first, (
            "a repeatable answer is fine; a repeatable INVENTED MEASUREMENT is "
            "the trap — it reads as corroboration on the second run")


# ── the wiring ────────────────────────────────────────────────────────────

class TestItIsReachableAndPriced:
    def test_chat_declares_a_permission_for_it(self):
        from bot.skills.skill_permissions import permission_for
        assert permission_for("quant_analyze") == "analyze"

    def test_the_permission_is_the_one_on_the_command(self):
        """The table is DERIVED, not invented. Re-derive it here too."""
        import inspect

        from bot.skills.scan_commands import ScanCommands
        block = inspect.getsource(ScanCommands)
        idx = block.index("async def _cmd_quant")
        # The decorator immediately above the def is the fact the table is
        # derived from; anchor on that window, not on the whole class.
        assert '@guard("analyze")' in block[max(0, idx - 200):idx]
        # The DISPATCH, not the source: this method's docstring says it does
        # NOT pass `allow_synthetic`, and a bare substring search matched that
        # sentence — the same misfire as the card assertions above. What
        # matters is the call.
        call = [ln for ln in inspect.getsource(ScanCommands._cmd_quant).split("\n")
                if "dispatch(" in ln or "symbol=symbol" in ln]
        assert call, "the command no longer dispatches quant_analyze"
        assert not any("allow_synthetic" in ln for ln in call), (
            "chat must never ask for generated candles")

    def test_a_transport_dispatches_it(self):
        import inspect

        from bot.skills.telegram_handler import TelegramHandler
        src = inspect.getsource(TelegramHandler)
        assert '("quant", self._cmd_quant)' in src

    def test_the_tier_gate_prices_it(self):
        from bot.token.tier_gate import _DEFAULT_FEATURE_MIN_TIER
        assert _DEFAULT_FEATURE_MIN_TIER["quant_analyze"] == "pro"

    def test_it_is_priced_no_lower_than_deepscan(self):
        """The stated objection was that it gives away more than deepscan."""
        from bot.token.tier_gate import _DEFAULT_FEATURE_MIN_TIER as T
        order = ["basic", "pro", "elite"]
        assert order.index(T["quant_analyze"]) >= order.index(T["deepscan"])

    def test_the_command_checks_the_tier_gate(self):
        import inspect

        from bot.skills.scan_commands import ScanCommands
        src = inspect.getsource(ScanCommands._cmd_quant)
        assert '_token_gate_blocks(update, "analysis", "quant_analyze")' in src

    def test_the_offline_pipeline_asks_for_what_it_needs(self):
        """It ran on generated candles before, silently. Now it says so."""
        import pathlib
        src = pathlib.Path("scripts/e2e_pipeline.py").read_text(encoding="utf-8")
        assert src.count("allow_synthetic=True") == 2
