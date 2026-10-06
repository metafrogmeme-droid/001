"""Momentum Hunter gated on a volume ratio the live scanner never measured.

PR 484 replaced ``ratio >= min or volume_spike`` with
``signal_clears_volume_min(s, 3.0)``, which needs a MEASURED
``volume_spike_ratio``. The only live producer of a ``MarketSignal``,
``MarketScanner._process_ticker``, computed the 2x spike flag from the ratio
and dropped the ratio, so every live signal carried the model's default of
0.0 and the preset returned "No signals matched filters" on every run,
forever, while the marketplace card said "vol spike > 3x". Only the
backtest's ``_bar_to_signal`` ever wrote the field. A dead door, and a 0.0
standing in for "nobody measured".

The scanner now measures the ratio once (`_measure_volume`) and the flag
is derived from it; the signal carries the ratio as measured, or None when
it could not be (fewer than three prior scans, a zero average, or turnover
under the liquidity floor a multiple is believed from). None does not
clear a gate, and the run answer says how many signals were refused for
having no ratio yet rather than for a low one.
"""

from __future__ import annotations

import asyncio

import pytest

from bot.config import CONFIG
from bot.core.market_scanner import MarketScanner
from bot.core.strategy_gate import signal_clears_volume_min, volume_ratio_clears
from bot.skills.skill_registry import RunStrategySkill
from bot.utils.models import MarketSignal

FLOOR = float(CONFIG.min_spike_notional_usd)
assert FLOOR >= 100_000, "the fixtures below sit on either side of the floor"


def _tick(volume: float, change: float = 5.0, last: float = 100.0) -> dict:
    return {"percentage": change, "quoteVolume": volume, "last": last,
            "active": True, "info": {}}


def _scanned(scanner: MarketScanner, symbol: str, volumes) -> None:
    for v in volumes:
        assert scanner._process_ticker(symbol, _tick(v), min_vol=1.0) is not None


# ── the scanner measures the ratio, and the flag is derived from it ───────

def test_a_signal_carries_the_measured_ratio_and_the_flag_derived_from_it():
    s = MarketScanner()
    _scanned(s, "JUP/USDT", [1_000_000.0] * 3)
    sig = s._process_ticker("JUP/USDT", _tick(5_000_000.0), min_vol=1.0)
    assert sig is not None
    assert sig.volume_spike_ratio == pytest.approx(5.0)
    assert sig.volume_spike is True
    assert signal_clears_volume_min(sig, 3.0) is True


def test_a_ratio_under_the_spike_line_is_still_measured():
    """Both arms of the flag off one ratio: 1.5x is measured and is not a
    spike, and it does not clear a 3x gate."""
    s = MarketScanner()
    _scanned(s, "JUP/USDT", [1_000_000.0] * 3)
    sig = s._process_ticker("JUP/USDT", _tick(1_500_000.0), min_vol=1.0)
    assert sig.volume_spike_ratio == pytest.approx(1.5)
    assert sig.volume_spike is False
    assert signal_clears_volume_min(sig, 3.0) is False
    assert signal_clears_volume_min(sig, 1.5) is True


def test_fewer_than_three_prior_scans_is_unmeasured_not_zero():
    s = MarketScanner()
    _scanned(s, "JUP/USDT", [1_000_000.0] * 2)
    sig = s._process_ticker("JUP/USDT", _tick(5_000_000.0), min_vol=1.0)
    assert sig.volume_spike_ratio is None
    assert sig.volume_spike is False
    assert signal_clears_volume_min(sig, 3.0) is False


def test_turnover_under_the_liquidity_floor_is_unmeasured():
    """A 4x multiple over a baseline of dust is not a reading: the flag's
    own docstring says why, and the ratio the gates read agrees."""
    s = MarketScanner()
    base = FLOOR / 10.0
    _scanned(s, "DUST/USDT", [base] * 3)
    sig = s._process_ticker("DUST/USDT", _tick(base * 4), min_vol=1.0)
    assert sig.volume_spike_ratio is None
    assert sig.volume_spike is False
    # The same multiple over a believable baseline IS a reading.
    s2 = MarketScanner()
    _scanned(s2, "BIG/USDT", [FLOOR] * 3)
    sig2 = s2._process_ticker("BIG/USDT", _tick(FLOOR * 4), min_vol=1.0)
    assert sig2.volume_spike_ratio == pytest.approx(4.0)
    assert sig2.volume_spike is True


def test_the_flag_reader_is_the_ratio_reader():
    """`_detect_volume_spike` answers the bool off the same measurement
    and records the scan the same way."""
    s = MarketScanner()
    for _ in range(3):
        assert s._detect_volume_spike("JUP/USDT", 1_000_000.0) is False
    assert s._detect_volume_spike("JUP/USDT", 3_000_000.0) is True
    assert len(s._volume_history["JUP/USDT"]) == 4


def test_the_model_default_is_unmeasured():
    """Built the way the scanner used to build it, with no ratio: None,
    which clears nothing. 0.0 was the dead door."""
    sig = MarketSignal(symbol="BTC/USDT:USDT", price=100.0, change_pct_24h=5.0,
                       volume_usd_24h=5_000_000.0, volume_spike=True, momentum_score=0.5)
    assert sig.volume_spike_ratio is None
    assert signal_clears_volume_min(sig, 3.0) is False
    assert volume_ratio_clears(None, 3.0) is False
    assert volume_ratio_clears(3.0, 3.0) is True


# ── the preset door opens on a measured ratio, and says why it did not ───

class _Scanner:
    def __init__(self, signals):
        self.signals = signals

    async def scan(self):
        return list(self.signals)


class _Engine:
    def __init__(self, signals):
        self.scanner = _Scanner(signals)
        self._last_strategy_setups: list = []
        self._pending_ideas: dict = {}
        self.analyzed: list = []

    async def _analyze_signal(self, sig):
        self.analyzed.append(sig)
        return None


def _signal(ratio):
    return MarketSignal(symbol="JUP/USDT:USDT", price=0.5, change_pct_24h=8.0,
                        volume_usd_24h=5_000_000.0, volume_spike=True,
                        momentum_score=0.7, volume_spike_ratio=ratio)


def _run(engine):
    return asyncio.run(RunStrategySkill().execute(engine, strategy="momentum hunter"))


def test_momentum_hunter_reaches_the_analyzer_on_a_measured_spike():
    engine = _Engine([_signal(5.0)])
    out = _run(engine)
    assert engine.analyzed == engine.scanner.signals
    assert "No signals matched filters" not in out
    assert "Scanned <code>1</code>" in out


def test_an_unmeasured_ratio_is_refused_and_counted_as_unmeasured():
    engine = _Engine([_signal(None), _signal(None)])
    out = _run(engine)
    assert engine.analyzed == []
    assert "No signals matched filters" in out
    assert "2 signal(s) carried no volume ratio yet" in out
    assert "three prior scans" in out


def test_a_low_ratio_is_refused_without_the_unmeasured_note():
    engine = _Engine([_signal(2.5)])
    out = _run(engine)
    assert engine.analyzed == []
    assert "No signals matched filters" in out
    assert "carried no volume ratio" not in out


def test_the_card_says_at_least_three_times_which_is_the_gate():
    desc = RunStrategySkill.PRESETS["momentum hunter"]["desc"]
    assert "≥ 3x" in desc and "&gt; 3x" not in desc
    assert RunStrategySkill.PRESETS["momentum hunter"]["volume_spike_min"] == 3.0
    assert volume_ratio_clears(3.0, 3.0) is True
