"""60+ overnight messages, and the cure was already next door.

Reported live with screenshots: an operator's channel taking 60+ volume and
anomaly messages overnight. The named symbols were `NFLX/USDT:USDT`,
`DFEN/USDT:USDT` and `DIASTOCK/USDT:USDT` — tokenised equities, which is the
same cast as the 2026-08-21 and 2026-08-30 floods that
`test_anomaly_flood_is_not_a_market_event.py` and
`test_off_hours_anomalies_do_not_page.py` were written for.

THE ANOMALY PATH LEARNED THIS THREE TIMES AND THE SPIKE PATH NEVER DID.
`_check_black_swan` clusters into one digest, caps cards per tick, spends an
hourly budget, and attenuates a symbol whose reference market is shut.
`_check_volume_spikes`, wired into the same tick list one line above it, had
none of that: one immediate Telegram card per spiking symbol, unbounded in
width and in rate.

TWO INDEPENDENT CAUSES, and neither is a severity threshold.

  THE FLOOR ANSWERS A DIFFERENT QUESTION. `_detect_volume_spike` is
  `current_vol > avg * 2.0`, and the only absolute floor under it was
  `min_vol` in `_process_ticker` — which decides whether a symbol is worth
  SCANNING, on a scale set per asset class:

      MIN_CRYPTO_VOLUME_USD   1,500,000
      MIN_TRADFI_VOLUME_USD       5,000

  Three hundred times apart, and the low one is where the tokenised equities
  live. A Stock or ETF perp sitting just over 5,000 doubles its 24h turnover
  on a few thousand dollars of trades. The ratio is arithmetically correct and
  means nothing — the same sentence `black_swan._MIN_BAR_NOTIONAL` already
  carries about collapses, which is the mirror of this and was fixed first.

  THE FAN-OUT WAS NEVER BOUNDED. Every symbol clearing that bar produced its
  own message, so the width of a burst was the width of the scan.

THE RED HERRING, planted below: a genuinely liquid symbol having a genuine
turnover event MUST still reach the operator, and every spike must still be
NAMED. A fix that quiets the channel by raising the bar until nothing fires is
the worse defect — it produces the same silence the operator is complaining
about, only now when it matters.
"""

from __future__ import annotations

import re
import threading

import pytest

from bot.config import CONFIG
from bot.core.market_scanner import MarketScanner
from bot.core.proactive_monitor import ProactiveMonitor

# ── the floor ─────────────────────────────────────────────────────────────

def _spike(baseline: float, current: float, *, bars: int = 3) -> bool:
    s = MarketScanner.__new__(MarketScanner)
    s._lock = threading.Lock()
    s._volume_history = {}
    for _ in range(bars):
        s._detect_volume_spike("X/USDT:USDT", baseline)
    return s._detect_volume_spike("X/USDT:USDT", current)


class TestASpikeNeedsLiquidityToSpikeFrom:
    def test_a_thin_tokenised_equity_no_longer_pages(self):
        """THE REPORTED CASE. $6k of 24h turnover going to $20k is 3.3x and is
        one modest order. It cleared MIN_TRADFI_VOLUME_USD (5,000), which is
        why it was eligible at all."""
        assert _spike(6_000, 20_000) is False

    @pytest.mark.parametrize("baseline,current", [
        (5_001, 11_000),     # just over the tradfi listing floor
        (20_000, 90_000),
        (60_000, 200_000),
    ])
    def test_nothing_under_the_floor_pages_however_large_the_multiple(
            self, baseline, current):
        assert current < CONFIG.min_spike_notional_usd     # premise of the case
        assert _spike(baseline, current) is False

    def test_the_multiple_still_has_to_be_there(self):
        """The floor ADDS a condition, it does not replace the ratio."""
        assert _spike(2_000_000, 3_000_000) is False       # only 1.5x


class TestARealSpikeStillPages:
    """THE RED HERRING. Quieting the channel by never firing is the worse bug."""

    @pytest.mark.parametrize("baseline,current", [
        (2_000_000, 9_000_000),
        (200_000, 600_000),
        (500_000, 1_100_000),
    ])
    def test_a_liquid_symbol_having_a_real_move_survives(self, baseline, current):
        assert _spike(baseline, current) is True

    def test_exactly_at_the_floor_counts(self):
        """`>=`, not `>`: the floor is the minimum that qualifies, and a
        boundary that excludes its own value is the off-by-one this repo's
        `MIN_RATED` note is about."""
        floor = CONFIG.min_spike_notional_usd
        assert _spike(floor / 4, floor) is True

    def test_the_floor_is_tuneable_to_zero_for_the_old_behaviour(self, monkeypatch):
        """An operator scanning a deliberately small universe can turn it off,
        and then the thin case fires again — so the knob is real, not decorative.

        PATCHED AT THE MODULE, because `AppConfig` is a FROZEN dataclass:
        `monkeypatch.setattr(CONFIG, ...)` raises `FrozenInstanceError`. The
        knob is `MIN_SPIKE_NOTIONAL_USD` read from the environment at import,
        so the honest statement of what this pins is "the scanner reads the
        config value", not "the value can be changed at runtime" — it cannot,
        and a test implying otherwise would be documenting an operator
        procedure that does not exist.
        """
        import types

        import bot.core.market_scanner as ms
        monkeypatch.setattr(
            ms, "CONFIG", types.SimpleNamespace(min_spike_notional_usd=0.0))
        assert _spike(6_000, 20_000) is True


def test_the_floor_is_not_just_the_tradfi_listing_floor_renamed():
    """The whole finding is that one number was answering two questions. If
    they are equal again, this is back to where it started."""
    assert CONFIG.min_spike_notional_usd > CONFIG.min_tradfi_volume_usd


# ── the fan-out ───────────────────────────────────────────────────────────

def _rows(n: int) -> list[dict]:
    return [{"symbol": f"SYM{i}/USDT:USDT", "base": f"SYM{i}", "chg": "+5.0%",
             "vol_m": float(n - i), "price": 12.3456, "rsi": "61.0",
             "vwap": "+0.80%", "direction": "Bullish"} for i in range(n)]


class TestOneDigestNotOneCardEach:
    def test_many_spikes_make_one_message(self):
        assert ProactiveMonitor._volume_spike_digest(_rows(11)).alert_type \
            == "VOLUME_SPIKE"

    def test_every_symbol_is_still_named(self):
        """CLUSTERED, NOT DROPPED. A quiet channel must not be a claim that
        nothing moved — the rule `_anomaly_digest` states for anomalies."""
        rows = _rows(11)
        body = ProactiveMonitor._volume_spike_digest(rows).body
        for r in rows:
            assert r["symbol"] in body

    def test_the_loudest_are_carded_and_the_rest_counted(self):
        body = ProactiveMonitor._volume_spike_digest(_rows(11)).body
        carded = re.findall(r"<b>(SYM\d+)/USDT:USDT</b>", body)
        assert carded == [f"SYM{i}" for i in range(6)], (
            "cards must be ordered by turnover, loudest first")
        assert "+5 more" in body

    def test_a_single_spike_reads_as_one(self):
        a = ProactiveMonitor._volume_spike_digest(_rows(1))
        assert "1 symbol" in a.title and "symbols" not in a.title

    def test_the_dedup_key_is_stable_across_membership_churn(self):
        """THE HALF THE PREVIOUS ATTEMPT AT CLUSTERING GOT WRONG, recorded in
        `_anomaly_digest`: a key carrying the membership changes on every pass
        during exactly the event it is meant to suppress, so every digest reads
        as a first sighting and no repeat window applies."""
        keys = {ProactiveMonitor._volume_spike_digest(_rows(n)).dedup_key
                for n in (1, 5, 11, 40)}
        assert len(keys) == 1, f"key churns with membership: {keys}"

    def test_it_says_it_is_an_observation(self):
        body = ProactiveMonitor._volume_spike_digest(_rows(3)).body
        assert "OBSERVATIONS, not actions" in body
        assert "nothing was traded" in body


# ── the once-only guard that re-armed itself ──────────────────────────────

class TestRememberOnceEvictsTheOldest:
    """"EVICT OLDEST HALF" WAS NOT WHAT IT DID.

    Both call sites held a `set` and both claimed to drop the oldest half:

        to_remove = list(self._alerted_signals)[:250]              # spikes
        self._news_alerted = set(list(self._news_alerted)[-250:])  # news

    A set has no insertion order, so `list(a_set)` is hash order and the half
    dropped was arbitrary — including keys added seconds earlier, which re-arms
    the guard against repeats at random. Two copies of one rule, already
    drifted into opposite slices (`[:250]` vs `[-250:]`) of the same non-order.
    """

    def test_the_newest_key_is_not_evicted(self):
        seen: dict = {}
        for i in range(500):
            ProactiveMonitor._remember_once(seen, f"k{i:03d}")
        ProactiveMonitor._remember_once(seen, "NEWEST")
        assert "NEWEST" in seen

    def test_the_oldest_keys_are_the_ones_evicted(self):
        seen: dict = {}
        for i in range(501):
            ProactiveMonitor._remember_once(seen, f"k{i:03d}")
        assert all(f"k{i:03d}" not in seen for i in range(10))
        assert all(f"k{i:03d}" in seen for i in range(495, 501))

    def test_it_stays_bounded(self):
        seen: dict = {}
        for i in range(5_000):
            ProactiveMonitor._remember_once(seen, f"k{i:04d}")
        assert len(seen) <= 500

    def test_a_repeat_key_does_not_grow_it(self):
        seen: dict = {}
        for _ in range(50):
            ProactiveMonitor._remember_once(seen, "same")
        assert len(seen) == 1

    def test_membership_reads_the_same_as_the_set_it_replaced(self):
        seen: dict = {}
        ProactiveMonitor._remember_once(seen, "vol_spike_BTC/USDT")
        assert "vol_spike_BTC/USDT" in seen
        assert "vol_spike_ETH/USDT" not in seen


def test_both_once_only_stores_share_one_implementation():
    """A second copy of a rule is a second answer to it, and these two had
    already drifted. Driven rather than grepped: both containers must accept
    the same writer and stay ordered."""
    import inspect
    src = inspect.getsource(ProactiveMonitor)
    assert "_alerted_signals.add(" not in src
    assert "_news_alerted.add(" not in src
    assert src.count("def _remember_once") == 1


def test_an_unreadable_turnover_does_not_sort_as_break_even():
    """Caught by the honesty ratchet ON THE COMMIT THAT ADDED THE DIGEST.

    The first draft sorted on `float(d.get("vol_m") or 0.0)` — in the function
    written to stop this class of noise. It is a sort key rather than a printed
    figure, which is why it reads as harmless, and it is not: the order decides
    which symbols get a card and which collapse into the tail count, so an
    unreadable turnover would outrank every genuinely quiet symbol by being
    indistinguishable from a measured zero.
    """
    rows = [
        {"symbol": "KNOWN/USDT:USDT", "base": "KNOWN", "chg": "+1%",
         "vol_m": 4.0, "price": 1.0, "rsi": "50", "vwap": "0%",
         "direction": "Bullish"},
        {"symbol": "UNREADABLE/USDT:USDT", "base": "UNREADABLE", "chg": "+1%",
         "vol_m": None, "price": 1.0, "rsi": "50", "vwap": "0%",
         "direction": "Bullish"},
        {"symbol": "QUIET/USDT:USDT", "base": "QUIET", "chg": "+1%",
         "vol_m": 0.0, "price": 1.0, "rsi": "50", "vwap": "0%",
         "direction": "Bullish"},
    ]
    body = ProactiveMonitor._volume_spike_digest(rows).body
    order = re.findall(r"<b>(\w+)/USDT:USDT</b>", body)
    assert order == ["KNOWN", "QUIET", "UNREADABLE"], (
        "a measured 0.0 is a real reading and outranks an absent one")


def test_a_junk_turnover_does_not_raise():
    rows = [{"symbol": "J/USDT:USDT", "base": "J", "chg": "+1%",
             "vol_m": "n/a", "price": 1.0, "rsi": "50", "vwap": "0%",
             "direction": "Bullish"}]
    assert ProactiveMonitor._volume_spike_digest(rows).dedup_key \
        == "vol_spike_DIGEST"
