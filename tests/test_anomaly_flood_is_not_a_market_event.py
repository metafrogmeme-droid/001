"""Nine anomaly pages in three minutes, mostly about symbols nobody trades.

REPORTED LIVE, 2026-08-21, with screenshots: `ANOMALY DETECTED` cards arriving
at 05:41, 05:42, 05:43 — six of them sharing one detection timestamp — every
one advising HALT_NEW_TRADES.

They were not repeats. `proactive_monitor` already suppresses an unchanged
standing condition for 15–30 minutes and lets a severity escalation break
through, and that machinery worked. Each card was a FIRST sighting of a
distinct `(type, symbol)` key, paging immediately and, on its own terms,
correctly.

The problem was WHAT was eligible to be measured. Read the symbols:
`EWH/USDT:USDT`, `DIASTOCK/USDT:USDT`, `RTXSTOCK/USDT:USDT`, `SKYAI/USDT` —
tokenised stocks and near-dead alts.

  VOLUME_COLLAPSE. `_check_volume_collapse` guarded `avg_volume == 0` and not
  `avg_volume ≈ 0`. `EWH/USDT:USDT` averages a few contracts a bar; one bar
  with no trades gave `ratio = 0 / tiny = 0.0` and severity 1.00 — the top of
  the scale — advising a halt. Three contracts a bar is not liquidity that
  evaporated, it is a symbol that trades intermittently, and a quiet bar in it
  is the normal case. The ratio was arithmetically correct and meant nothing.

  CORRELATION_BREAKDOWN. The zero-std guard rejected a perfectly constant
  series and admitted one ticking 25.00 / 25.01, whose correlation is decided
  by the last decimal and swings between +1 and -1 between windows. That is
  how `TAO/USDT` and `RTXSTOCK/USDT:USDT` "decorrelated" from 0.776 to -0.949
  — two things with no reason to move together in the first place.

Both are the same shape, and it is the one this codebase keeps finding: a
guard that works, applied to a set that excludes the case that hurts. `== 0`
was handled; `≈ 0` was the whole problem.

THE RED HERRING, planted in every test below: a genuinely liquid symbol having
a genuine event. BTC volume falling off a cliff, or two real majors actually
decorrelating, MUST still page at full severity. A fix that quiets the channel
by raising the bar until nothing fires is the worse defect — it produces the
same silence an operator is complaining about, only now when it matters.
"""

from __future__ import annotations

import random

import pytest

from bot.core.black_swan import BlackSwanDetector

TYPES = lambda alerts: [a.anomaly_type.value for a in alerts]  # noqa: E731


class _a:
    """Minimal stand-in for an AnomalyAlert: the selector reads two fields."""

    def __init__(self, symbol, severity):
        self.symbol = symbol
        self.severity = severity


def feed(det, symbol, *, price, volume, bars):
    for i in range(bars):
        det.update(symbol, price=price(i) if callable(price) else price,
                   volume=volume(i) if callable(volume) else volume)


# ── volume collapse ─────────────────────────────────────────────────────────

class TestAThinSymbolCannotHaveLiquidityEvaporate:
    def test_the_reported_case_no_longer_pages(self):
        """EWH/USDT:USDT — a few contracts a bar, then a bar with no trades."""
        d = BlackSwanDetector()
        feed(d, "EWH/USDT:USDT", price=lambda i: 25.0 + (i % 3) * 0.01,
             volume=3.0, bars=21)
        assert "VOLUME_COLLAPSE" not in TYPES(
            d.update("EWH/USDT:USDT", price=25.0, volume=0.0))

    @pytest.mark.parametrize("px,vol", [(25.0, 3.0), (0.004, 900.0), (1.2, 50.0)])
    def test_no_claim_below_the_turnover_floor(self, px, vol):
        d = BlackSwanDetector()
        feed(d, "THIN/USDT", price=px, volume=vol, bars=21)
        assert "VOLUME_COLLAPSE" not in TYPES(
            d.update("THIN/USDT", price=px, volume=0.0)), (
            f"a symbol turning over ~${px * vol:,.0f} a bar claimed a collapse")

    def test_a_liquid_symbol_still_pages_at_full_severity(self):
        """THE RED HERRING. This is the alert the channel exists for."""
        d = BlackSwanDetector()
        feed(d, "BTC/USDT", price=lambda i: 67000.0 + (i % 3) * 50,
             volume=1200.0, bars=21)
        alerts = [a for a in d.update("BTC/USDT", price=67000.0, volume=10.0)
                  if a.anomaly_type.value == "VOLUME_COLLAPSE"]
        assert alerts, "a real collapse on a liquid symbol went silent"
        assert alerts[0].severity > 0.8
        assert alerts[0].recommended_action == "HALT_NEW_TRADES"

    def test_the_floor_is_turnover_not_contract_count(self):
        """Base volume is not comparable across symbols — BTC trades in tens,
        a memecoin in millions — so a contract-count floor would mute one and
        admit the other. A high-priced, low-count symbol is liquid."""
        d = BlackSwanDetector()
        feed(d, "PRICEY/USDT", price=90_000.0, volume=2.0, bars=21)   # ~$180k/bar
        assert "VOLUME_COLLAPSE" in TYPES(
            d.update("PRICEY/USDT", price=90_000.0, volume=0.0))


# ── correlation breakdown ───────────────────────────────────────────────────

class TestNearlyFlatSeriesDoNotDecorrelate:
    def test_two_stock_tokens_ticking_in_the_last_decimal(self):
        """The fixture matters. A first draft alternated the two series on
        different periods and never reached the 0.6 baseline, so it passed
        against the OLD guard too and proved nothing.

        This one is VERIFIED to fire under the old guard: correlation +1.0
        across the baseline window and -1.0 across the current one, from
        series moving 0.04% and 0.03% of their own price. Restore
        `std == 0` and it pages CORRELATION_BREAKDOWN at severity 1.00. Both
        symbols are stepped on every iteration so the two histories stay the
        same length — a final one-sided update slides the windows out of phase
        and the fixture stops testing anything.
        """
        d = BlackSwanDetector()
        for i in range(20):                      # baseline: drift up together
            d.update("RTXSTOCK/USDT:USDT", price=25.0 + 0.0005 * i, volume=5e5)
            d.update("TAO/USDT", price=300.0 + 0.005 * i, volume=5e5)
        out = []
        for i in range(21):                      # current: drift apart
            d.update("RTXSTOCK/USDT:USDT", price=25.0 + 0.0005 * i, volume=5e5)
            out = d.update("TAO/USDT", price=300.0 - 0.005 * i, volume=5e5)
        assert "CORRELATION_BREAKDOWN" not in TYPES(out)

    def test_a_perfectly_flat_series_is_still_rejected(self):
        """The original guard's case must keep working."""
        d = BlackSwanDetector()
        for i in range(41):
            d.update("FLAT/USDT", price=10.0, volume=5e5)
            d.update("OTHER/USDT", price=10.0 + i * 0.1, volume=5e5)
        assert "CORRELATION_BREAKDOWN" not in TYPES(
            d.update("FLAT/USDT", price=10.0, volume=5e5))

    def test_two_real_movers_that_genuinely_decorrelate_still_page(self):
        """THE RED HERRING again. Correlated majors coming apart is the event
        this detector is FOR, and it must survive the new floor."""
        random.seed(11)
        d = BlackSwanDetector()
        # Baseline window: both trend up together.
        for i in range(20):
            d.update("AAA/USDT", price=100.0 + i * 2.0, volume=5e5)
            d.update("BBB/USDT", price=50.0 + i * 1.0, volume=5e5)
        # Current window: they come apart hard.
        for i in range(20):
            d.update("AAA/USDT", price=140.0 + i * 2.0, volume=5e5)
            d.update("BBB/USDT", price=70.0 - i * 1.0, volume=5e5)
        alerts = d.update("AAA/USDT", price=180.0, volume=5e5)
        assert "CORRELATION_BREAKDOWN" in TYPES(alerts), (
            "the floor muted a real decorrelation between two moving symbols")


# ── breadth, and saying what was dropped ────────────────────────────────────

class TestTheCardsPerTickAreBoundedAndTheRestAreNamed:
    def test_the_cap_is_small_and_explicit(self):
        from bot.core.proactive_monitor import ProactiveMonitor
        assert ProactiveMonitor._SEVERE_CARDS_PER_TICK <= 5

    def test_the_overflow_line_names_what_it_dropped(self):
        """NO SILENT CAPS. A bounded list published as though it were the whole
        one reads as 'these are all the anomalies', which on this surface is an
        all-clear.

        DRIVEN, not scanned. The first version grepped for "bs_overflow" and
        passed against a mutation that made the whole overflow branch
        unreachable — the string was still sitting in the dead code. A source
        scan cannot see reachability, which is the one thing that mattered.
        """
        from bot.core.proactive_monitor import select_severe_cards
        groups = {f"k{i}": [_a(f"S{i}/USDT", 0.9 - i * 0.01)] for i in range(9)}
        shown, spill = select_severe_cards(groups, 3)
        assert len(shown) == 3
        assert len(spill) == 6, "six conditions vanished without being named"
        assert all(s.startswith("S") for s in spill)

    def test_the_overflow_card_is_actually_emitted(self):
        """A WIRING check, and deliberately a source scan.

        `select_severe_cards` is unit-tested above, but whether its spill
        reaches an Alert is a property of the CALL SITE — exactly the case
        CLAUDE.md keeps source matching for. A mutation making the branch
        unreachable leaves the pure function perfectly correct and the
        operator never told, which is the whole defect.
        """
        from tests.source_scan import code_only
        src = code_only(open("bot/core/proactive_monitor.py", encoding="utf-8").read())
        # Anchored on the CALL, not the name — a bare `select_severe_cards(`
        # matches the def first and windows over the function's own body.
        # WINDOWED TO THE ENCLOSING METHOD, not to a character count. This
        # read `src[i:i + 4000]`, and the fixed span made the test a distance
        # measurement wearing a wiring check's clothes: adding fifteen correct
        # lines between the anchor and the Alert pushed `bs_overflow` to 4431
        # characters and failed a file whose wiring was untouched. A scan that
        # breaks when correct code is inserted teaches people to widen the
        # number, and the next widening hides a real break.
        i = src.index("shown, _more = select_severe_cards(")
        rest = src[i:]
        end = rest.find("\n    def ")          # next method at class indent
        window = rest if end == -1 else rest[:end]
        assert "if _more:" in window, (
            "the spill is computed and never branched on — the dropped "
            "conditions are silently dropped after all")
        assert "bs_overflow" in window, "no Alert is built from the spill"

    def test_nothing_spills_when_the_pass_is_small(self):
        from bot.core.proactive_monitor import select_severe_cards
        groups = {f"k{i}": [_a(f"S{i}/USDT", 0.9)] for i in range(2)}
        shown, spill = select_severe_cards(groups, 3)
        assert len(shown) == 2 and spill == []

    def test_the_loudest_are_the_ones_kept(self):
        """The cap must never drop the worst condition of the pass."""
        from bot.core.proactive_monitor import select_severe_cards
        groups = {
            "quiet": [_a("LOW/USDT", 0.81)],
            "loudest": [_a("WORST/USDT", 0.99)],
            "middle": [_a("MID/USDT", 0.90)],
            "also_quiet": [_a("LOW2/USDT", 0.82)],
        }
        shown, spill = select_severe_cards(groups, 1)
        assert [k for k, _ in shown] == ["loudest"], (
            f"the worst condition was truncated away: kept {shown}")
        assert "WORST/USDT" not in spill

    def test_the_overflow_still_says_the_engine_does_not_auto_halt(self):
        from tests.source_scan import code_only
        src = code_only(open("bot/core/proactive_monitor.py", encoding="utf-8").read())
        i = src.index("bs_overflow")
        window = src[max(0, i - 1400):i]
        assert "does NOT" in window and "auto-halt" in window, (
            "the overflow card drops the one line that stops an operator "
            "standing down waiting for an action nobody implemented")
