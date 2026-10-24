"""Published-print replay and the weekend score bar."""
from __future__ import annotations

from bot.fvb.replay import SK_DROP, SK_PRINT, run_sk_hynix, run_weekends, sk_prior, weekend_gaps


def test_sk_hynix_stays_on_the_pre_handoff_book():
    prior = sk_prior()
    assert abs((prior - SK_PRINT) / prior - SK_DROP) < 1e-12
    readings = run_sk_hynix()
    assert len(readings) == 121
    for reading in readings:
        assert reading.fv is not None
        assert abs(reading.fv - prior) / prior <= 0.05
        assert reading.confirmed is False
        assert reading.w_ext is not None and reading.w_ext < 0.05
        assert reading.borrow_power == SK_PRINT
        assert reading.liquidation is not None
        assert reading.liquidation > reading.borrow_power
        assert reading.borrow_power <= reading.liquidation
        assert reading.unit == "KRW"
        assert "$" not in reading.basis
        assert "rclaw" not in reading.basis.lower()


def test_weekends_clear_the_score_bar_and_do_not_false_hold():
    gaps = weekend_gaps()
    assert len(gaps) == 20
    readings = run_weekends()
    assert len(readings) == 20
    errors = []
    holds = 0
    for reading, gap in zip(readings, gaps):
        reopen = 100.0 * (1.0 + gap)
        assert reading.fv is not None
        errors.append(abs(reading.fv - reopen) / reopen)
        assert reading.confirmed is True
        assert reading.confirm_delay_s == 0.0
        assert reading.w_ext is not None and reading.w_ext >= 0.9 - 1e-9
        if reading.confirm_delay_s is None or reading.confirm_delay_s > 60.0:
            holds += 1
    errors.sort()
    median = (errors[9] + errors[10]) / 2.0
    assert median <= 0.00384
    assert holds / len(readings) <= 0.05
