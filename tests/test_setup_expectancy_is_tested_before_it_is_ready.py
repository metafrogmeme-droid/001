"""Setup expectancy is READY when its record tells unseen trades apart, not
when a bucket holds ten trades.

A live readiness card read:

    ✅ setup_expectancy: READY
       setup-expectancy: 122 setups, 218 trades, 0 setup(s) at/above
       10-trade threshold; backing off to regime=2 direction=2
    • setup_expectancy is validated but not applied — consider
      SETUP_EXPECTANCY_BACKOFF_ENABLED=true

READY was `is_ready()`, which is "some bucket at some tier holds ten trades",
and with the backoff the direction tier is one bucket of every long and one of
every short. Nothing had tested whether a bucket's win rate predicts the next
trades, and the recommendation called that "validated". The voter-weight
learner beside it already has to pass an out-of-sample test with an interval.

`validate_oos` fits on the earlier trades and compares the later ones the
record nudged UP with the ones it nudged DOWN. The claim is the difference:
nudging everything one way predicts the base rate and tells nothing apart.

The same card printed "Decisions on record: 5000", which was the LENGTH of a
read capped at 5000, while two of its components read up to 100,000.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.learning import readiness as rd
from bot.learning import setup_expectancy as sx
from bot.learning.setup_expectancy import SetupExpectancy, validate_oos


def _block(regime, n, wins, start):
    """`n` LONG trades in `regime`, `wins` of them won, each on its own symbol
    so no SETUP-level bucket forms and the REGIME tier is what answers."""
    return [(f"S{start + i}", regime, "LONG", i < wins) for i in range(n)]


def _discriminating():
    # Train (first 70): TREND longs mostly win, RANGE longs mostly lose.
    # Test (last 30): the same holds, 15 of each.
    return (_block("TREND", 35, 28, 0) + _block("RANGE", 35, 7, 100)
            + _block("TREND", 15, 12, 200) + _block("RANGE", 15, 3, 300))


def _reversed():
    # Same training record; the unseen trades go the OTHER way.
    return (_block("TREND", 35, 28, 0) + _block("RANGE", 35, 7, 100)
            + _block("TREND", 15, 3, 200) + _block("RANGE", 15, 12, 300))


def _overturned():
    # The unseen trades are strong enough to FLIP each bucket's sign if they
    # were fitted on: a fit that leaks the test block scores the record on
    # trades it learned from and reads a contradiction as a +100% edge.
    return (_block("TREND", 35, 19, 0) + _block("RANGE", 35, 16, 100)
            + _block("TREND", 15, 0, 200) + _block("RANGE", 15, 15, 300))


def _straddling():
    # Favoured 60% against disfavoured 40%: a real-looking gap whose interval
    # on 15 and 15 still reaches below zero.
    return (_block("TREND", 35, 28, 0) + _block("RANGE", 35, 7, 100)
            + _block("TREND", 15, 9, 200) + _block("RANGE", 15, 6, 300))


def _high_base_rate():
    # Everything unseen mostly wins, and the disfavoured bucket wins MORE:
    # a favoured win rate over 50% says nothing, the difference says no.
    return (_block("TREND", 35, 28, 0) + _block("RANGE", 35, 7, 100)
            + _block("TREND", 15, 10, 200) + _block("RANGE", 15, 11, 300))


def _unmatched():
    # Half the unseen trades are SHORTS, which no training bucket covers: they
    # draw no nudge and are in NEITHER group.
    return (_block("TREND", 35, 28, 0) + _block("RANGE", 35, 7, 100)
            + _block("TREND", 15, 12, 200)
            + [(f"S{400 + i}", "TREND", "SHORT", i % 2 == 0) for i in range(15)])


def _direction_only():
    # Every trade in a regime of its own: only the DIRECTION tier qualifies,
    # so every unseen long draws the same nudge. The live card's shape.
    return [(f"S{i}", f"R{i}", "LONG", i % 3 != 0) for i in range(100)]


# ── the test itself ──────────────────────────────────────────────────────


def test_a_record_that_tells_them_apart_clears_the_interval():
    oos = validate_oos(_discriminating())
    assert (oos["n_favoured"], oos["n_disfavoured"]) == (15, 15)
    assert oos["win_favoured"] == pytest.approx(0.8)
    assert oos["win_disfavoured"] == pytest.approx(0.2)
    assert oos["lower"] > 0
    assert oos["tiers"] == {"regime": 30}


def test_a_record_the_unseen_trades_contradict_does_not():
    oos = validate_oos(_reversed())
    assert oos["difference"] == pytest.approx(-0.6)
    assert oos["lower"] < 0


def test_one_direction_for_everything_measures_no_difference():
    oos = validate_oos(_direction_only())
    assert oos["n_disfavoured"] == 0 or oos["n_favoured"] == 0
    assert oos["difference"] is None and oos["lower"] is None


def test_a_record_is_not_scored_on_trades_it_learned_from():
    oos = validate_oos(_overturned())
    assert oos["difference"] == pytest.approx(-1.0), oos
    assert oos["lower"] < 0


def test_the_interval_decides_not_the_gap():
    oos = validate_oos(_straddling())
    assert oos["difference"] == pytest.approx(0.2)
    assert oos["lower"] < 0, "a 20-point gap on 15 and 15 is inside the noise"


def test_a_trade_the_record_does_not_cover_is_in_neither_group():
    oos = validate_oos(_unmatched())
    assert (oos["n_favoured"], oos["n_disfavoured"]) == (15, 0)
    assert "none" not in oos["tiers"]


def test_it_fits_only_on_the_earlier_trades():
    # A fit on ALL the trades would learn the reversed test block too and
    # report a smaller contradiction; fitted on the first 70 it sees the
    # training record alone.
    assert validate_oos(_reversed())["n_train"] == 70


# ── the card ─────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, samples):
        self._d = [SimpleNamespace(symbol=s, market_regime=r, direction=d,
                                   pnl_result=(1.0 if won else -1.0))
                   for s, r, d, won in samples]

    def get_decisions(self, symbol=None, limit=100):
        return self._d[-limit:]


def _assess(monkeypatch, samples, *, backoff=False):
    from bot.config import CONFIG
    fitted = SetupExpectancy().ingest(samples)
    monkeypatch.setattr(sx, "get_setup_expectancy", lambda reload=False: fitted)
    before = CONFIG.analyzer.setup_expectancy_backoff_enabled
    object.__setattr__(CONFIG.analyzer, "setup_expectancy_backoff_enabled", backoff)
    try:
        return rd.assess_readiness(store=_Store(samples))
    finally:
        object.__setattr__(CONFIG.analyzer, "setup_expectancy_backoff_enabled", before)


def test_the_live_cards_shape_is_not_ready(monkeypatch):
    out = _assess(monkeypatch, _direction_only())
    comp = out["components"]["setup_expectancy"]
    assert comp["state"] == "VALIDATING", comp
    assert "need >= 15 of each" in comp["note"]
    assert not any("SETUP_EXPECTANCY_BACKOFF_ENABLED=true" in r
                   for r in out["recommendations"]), out["recommendations"]


def test_a_record_that_tells_them_apart_is_ready_and_recommended(monkeypatch):
    out = _assess(monkeypatch, _discriminating())
    comp = out["components"]["setup_expectancy"]
    assert comp["state"] == "READY", comp
    assert "won 80% against 20%" in comp["note"]
    assert any("consider SETUP_EXPECTANCY_BACKOFF_ENABLED=true" in r
               for r in out["recommendations"])


def test_a_winning_base_rate_is_not_a_validated_record(monkeypatch):
    comp = _assess(monkeypatch, _high_base_rate())["components"]["setup_expectancy"]
    assert comp["state"] == "VALIDATING", comp
    assert "won 67% against 73%" in comp["note"]


def test_a_straddling_gap_is_not_ready(monkeypatch):
    comp = _assess(monkeypatch, _straddling())["components"]["setup_expectancy"]
    assert comp["state"] == "VALIDATING", comp


def test_a_contradicted_record_says_so(monkeypatch):
    comp = _assess(monkeypatch, _reversed())["components"]["setup_expectancy"]
    assert comp["state"] == "VALIDATING"
    assert "does not yet tell winners from losers" in comp["note"]


def test_an_applied_untested_record_is_a_warning(monkeypatch):
    # Backoff switched on over a record nothing validated: the one of the four
    # (state, applied) pairs that means something is already wrong.
    out = _assess(monkeypatch, _direction_only(), backoff=True)
    assert out["components"]["setup_expectancy"]["applied"] is True
    assert any(r.startswith("⚠️ setup_expectancy is APPLIED but NOT validated")
               for r in out["recommendations"]), out["recommendations"]


def test_an_unreadable_store_is_untested_not_ready(monkeypatch):
    fitted = SetupExpectancy().ingest(_discriminating())
    monkeypatch.setattr(sx, "get_setup_expectancy", lambda reload=False: fitted)

    class _Broken:
        def get_decisions(self, symbol=None, limit=100):
            raise OSError("disk")

    out = rd.assess_readiness(store=_Broken())
    comp = out["components"]["setup_expectancy"]
    assert comp["state"] == "VALIDATING"
    assert "could not be read" in comp["note"]
    assert out["decisions_on_record"] is None


# ── the pool ─────────────────────────────────────────────────────────────


def test_the_pool_is_every_decision_not_the_calibrators_slice(monkeypatch):
    seen = {}
    from bot.learning.confidence_calibration import ConfidenceCalibrator

    def _spy(decisions):
        seen["n"] = len(decisions)
        return []

    monkeypatch.setattr(ConfidenceCalibrator, "samples_from_decisions", staticmethod(_spy))
    out = _assess(monkeypatch, _direction_only() * 60)       # 6000 decisions
    assert out["decisions_on_record"] == 6000
    assert out["decisions_capped"] is False
    assert seen["n"] == 5000, "the calibrator is handed the most recent 5000, as before"


def test_a_read_that_hit_its_limit_says_the_count_is_a_floor(monkeypatch):
    monkeypatch.setattr(rd, "_POOL_LIMIT", 50)
    out = _assess(monkeypatch, _direction_only())             # 100 decisions
    assert out["decisions_on_record"] == 50 and out["decisions_capped"] is True
    card = rd.render_report(out)
    assert "Decisions on record: <code>at least 50 (the read stops there)</code>" in card


def test_the_pool_survives_a_calibration_fault(monkeypatch):
    from bot.learning.confidence_calibration import ConfidenceCalibrator

    def _boom(decisions):
        raise RuntimeError("calibration broke")

    monkeypatch.setattr(ConfidenceCalibrator, "samples_from_decisions", staticmethod(_boom))
    out = _assess(monkeypatch, _direction_only())
    assert out["components"]["calibration"]["state"] == "ERROR"
    assert out["decisions_on_record"] == 100
