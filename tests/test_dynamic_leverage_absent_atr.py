"""An ATR nobody measured was audited as 2.00% and granted full leverage.

`_compute_target_leverage` read the volatility it scales by like this:

    sym_base = normalize_symbol(symbol)
    atr_pct = self._last_atr_pct.get(sym_base, 0.02)
    ...
    audit(trade_log, f"Dynamic leverage for {symbol}: {default_lev}x -> {lev}x "
                     f"(ATR={atr_pct:.3%})", action="dynamic_leverage",
          result="ADJUSTED")

`.get(k, 0.02)` — the banned shape, with the expensive default. 2% sits in the
calm band, so a symbol with no reading was left at the FULL standard leverage
and the audit trail recorded "ADJUSTED ... ATR=2.000%": a measurement that was
never taken, printed to three decimal places beside real ones.

IT WAS EVERY SYMBOL, ALWAYS. `update_atr` is the only writer of that map and it
had no caller outside tests, so with DYNAMIC_LEVERAGE_ENABLED on the map is
empty and every line was that fabricated 2%. This is #58 at method scope — a
writer nothing calls is indistinguishable from one that does not work — and it
had a second defect stacked on it: `update_atr` keyed the RAW symbol while the
reader looked up `normalize_symbol(symbol)`, so even a wired-up feed filed
under "BTC/USDT:USDT" could not be found under "BTC". The same mismatch the
leverage rest had, in the same file.

WHY THE SUITE COULDN'T SEE EITHER. `tests/test_dynamic_leverage_dedup.py`
installs this:

    class _AnyATR(dict):
        def get(self, key, default=None):
            return self._val

A dict whose `.get` ignores both the key and the default. It makes every ATR
readable and every key correct — so it hides the empty map AND the key
mismatch, in one fixture, while testing the scaling arithmetic perfectly well.

BEHAVIOUR IS UNCHANGED HERE, DELIBERATELY. An unscaled symbol still gets the
standard leverage — which is exactly what the disabled flag gives it. The claim
changes, not the number: a reading that does not exist is reported as absent
instead of as 2%, at WARNING, so an operator who turns the flag on learns
immediately that the feature has no feed rather than believing volatility
scaling is running. Wiring the feed needs a unit conversion decision
(`quant_skill` reports 2.5 for 2.5%; this map wants 0.025) and the flag is off
by operator directive, so that is not done here.
"""

from __future__ import annotations

import logging

import pytest
from types import SimpleNamespace

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor


@pytest.fixture
def cfg_on(monkeypatch):
    """Dynamic leverage ENABLED, default 10x, min 2x."""
    monkeypatch.setattr(le, "CONFIG", SimpleNamespace(
        exchange=SimpleNamespace(default_leverage=10, min_leverage=2,
                                 dynamic_leverage_enabled=True)))


@pytest.fixture
def audited(monkeypatch):
    """Every audit() line, as (level, message, result)."""
    seen = []

    def _capture(level, msg, *a, **kw):
        extra = kw.get("extra") or {}
        seen.append((level, str(msg), extra.get("result", "")))

    monkeypatch.setattr(le.trade_log, "log", _capture, raising=False)
    return seen


class TestAnAbsentReadingIsNotTwoPercent:
    def test_the_audit_line_does_not_quote_an_ATR_it_never_read(
            self, cfg_on, audited):
        ex = LiveExecutor()
        ex._last_atr_pct = {}                       # production state
        ex._compute_target_leverage("BTC/USDT:USDT")

        lines = [ln for ln in audited if "leverage" in ln[1].lower()]
        assert lines, f"the decision was not audited at all: {audited}"
        assert not any("ATR=" in msg for _, msg, _ in lines), (
            "an ATR reading is still being quoted for a symbol that has none — "
            f"{[m for _, m, _ in lines]}")
        assert not any("2.000%" in msg for _, msg, _ in lines)

    def test_it_says_absent_rather_than_ADJUSTED(self, cfg_on, audited):
        ex = LiveExecutor()
        ex._last_atr_pct = {}
        ex._compute_target_leverage("BTC/USDT:USDT")

        results = [r for _, _, r in audited if r]
        assert "NO_ATR" in results, results
        assert "ADJUSTED" not in results, (
            "nothing was adjusted — the leverage is the standard one, and "
            "calling that an adjustment claims a scaling decision was made")

    def test_it_is_reported_where_an_operator_sees_it(self, cfg_on, audited):
        ex = LiveExecutor()
        ex._last_atr_pct = {}
        ex._compute_target_leverage("BTC/USDT:USDT")
        levels = [lvl for lvl, _, r in audited if r == "NO_ATR"]
        assert levels and levels[0] >= logging.WARNING, (
            "a volatility-scaling feature running on no volatility data is "
            "not an INFO-level detail for whoever just enabled it")

    def test_the_leverage_ITSELF_is_unchanged(self, cfg_on):
        # The fix must not become a silent blanket leverage cut. No reading
        # still means the standard leverage, exactly as the flag-off path.
        ex = LiveExecutor()
        ex._last_atr_pct = {}
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_a_measured_ZERO_is_not_treated_as_absent(self, cfg_on, audited):
        # 0.0 is a real reading of a dead-flat symbol. `is None`, not falsiness
        # — the rule this repo repeats most often, and the reason the branch
        # above cannot be `if not atr_pct`.
        #
        # The LEVERAGE cannot show the difference: 0.0 is below every threshold
        # so the standard leverage comes back either way, and the first version
        # of this test asserted only that and survived the `if not atr_pct`
        # mutation intact. The audit line is where the two differ, because one
        # of them says a reading does not exist while holding it.
        ex = LiveExecutor()
        ex.update_atr("BTC/USDT:USDT", 0.0)
        assert ex._last_atr_pct == {"BTC": 0.0}
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

        results = [r for _, _, r in audited if r]
        assert "NO_ATR" not in results, (
            "a measured 0.0 was reported as no reading at all — flat is a "
            "measurement, and this is the falsiness trap the module docstring "
            "of half this repo's tests is about")
        assert "ADJUSTED" in results


class TestARecordedReadingIsActuallyFound:
    def test_update_atr_files_it_where_the_reader_looks(self, cfg_on):
        # The second defect: update_atr keyed the raw symbol, the reader
        # normalized. A feed wired up under the perp spelling would have been
        # written, been correct, and been invisible — the leverage rest bug,
        # one map over.
        ex = LiveExecutor()
        ex.update_atr("BTC/USDT:USDT", 0.05)         # 5% ATR: high vol
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 5, (
            "a recorded high-vol reading did not de-leverage — update_atr and "
            "_compute_target_leverage are not using the same key")

    def test_it_is_found_under_every_spelling_of_the_symbol(self, cfg_on):
        ex = LiveExecutor()
        ex.update_atr("BTC/USDT", 0.05)
        for spelling in ("BTC/USDT:USDT", "BTC/USDT", "BTC"):
            assert ex._compute_target_leverage(spelling) == 5, spelling

    def test_one_symbols_reading_does_not_answer_for_another(self, cfg_on):
        ex = LiveExecutor()
        ex.update_atr("BTC/USDT", 0.05)
        assert ex._compute_target_leverage("ETH/USDT") == 10, (
            "ETH has no reading and must take the no-reading branch, not "
            "inherit BTC's volatility")


class TestTheFlagIsStillOff:
    def test_disabled_short_circuits_before_any_of_this(self, monkeypatch, audited):
        # The no-reading branch must not start auditing a warning on every
        # sizing call for an operator who has the feature deliberately off.
        monkeypatch.setattr(le, "CONFIG", SimpleNamespace(
            exchange=SimpleNamespace(default_leverage=10, min_leverage=2,
                                     dynamic_leverage_enabled=False)))
        ex = LiveExecutor()
        ex._last_atr_pct = {}
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10
        assert not [r for _, _, r in audited if r == "NO_ATR"]
