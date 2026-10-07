"""The calibration file saves what each bin rests on.

The website's calibration chart captioned the fitted curve's `y` as "the
recorded rate". It is not: each bin's recorded rate is shrunk toward the bin's
stated confidence by `shrinkage` pseudo-trades and pooled with its neighbours,
so a bin that won 0 of 3 is drawn at 0.16 and a one-trade bin at 0.96, and the
file held no per-bin count to say so. The document now saves each stored bin's
`trades` and `wins`. They are not used to calibrate; the curve is unchanged.
"""
import pytest

from bot.learning.confidence_calibration import ConfidenceCalibrator

# The review's sample: 3 lost at 0.25, 14 of 26 won at 0.65, 1 of 1 at 0.95.
_SAMPLES = ([(0.25, False)] * 3 + [(0.65, True)] * 14 + [(0.65, False)] * 12
            + [(0.95, True)])


def test_each_stored_bin_carries_its_trades_and_recorded_wins():
    cal = ConfidenceCalibrator().fit(_SAMPLES)
    d = cal.to_dict()
    assert d["x"] == pytest.approx([0.25, 0.65, 0.95])
    assert d["trades"] == [3, 26, 1]
    assert d["wins"] == [0, 14, 1]
    # The fitted values are what they were: shrunk and pooled, not recorded.
    assert d["y"] == pytest.approx([0.15625, 0.5565, 0.9583], abs=1e-4)
    assert d["y"][0] > 0 and d["wins"][0] == 0


def test_the_counts_round_trip_and_do_not_move_the_curve():
    cal = ConfidenceCalibrator().fit(_SAMPLES)
    back = ConfidenceCalibrator().load_dict(cal.to_dict())
    assert back.to_dict() == cal.to_dict()
    for c in (0.1, 0.25, 0.5, 0.65, 0.9):
        assert back.calibrate(c) == cal.calibrate(c)


def test_a_file_saved_before_the_counts_still_loads_and_says_nothing_of_them():
    cal = ConfidenceCalibrator().fit(_SAMPLES)
    old = {k: v for k, v in cal.to_dict().items() if k not in ("trades", "wins")}
    back = ConfidenceCalibrator().load_dict(old)
    assert back.to_dict()["trades"] == [] and back.to_dict()["wins"] == []
    assert back.calibrate(0.65) == cal.calibrate(0.65)
    # Misaligned counts are not attached to the wrong bins.
    bad = dict(cal.to_dict(), trades=[3, 26], wins=[0, 14])
    assert ConfidenceCalibrator().load_dict(bad).to_dict()["trades"] == []


@pytest.mark.parametrize("trades", [
    [3, None, 1], [3, "26", 1], [3, 26.5, 1], [3, -1, 1], [3, True, 1], "3,26,1",
])
def test_an_unreadable_count_drops_the_counts_and_never_fails_the_load(trades, tmp_path):
    cal = ConfidenceCalibrator().fit(_SAMPLES)
    bad = dict(cal.to_dict(), trades=trades)
    back = ConfidenceCalibrator().load_dict(bad)
    assert back.to_dict()["trades"] == [] and back.to_dict()["wins"] == []
    assert back.calibrate(0.65) == cal.calibrate(0.65)
    # Through the file, the way the analyzer loads it.
    f = tmp_path / "confidence_calibration.json"
    f.write_text(__import__("json").dumps(bad), encoding="utf-8")
    loaded = ConfidenceCalibrator.load(str(f))
    assert loaded is not None and loaded.calibrate(0.65) == cal.calibrate(0.65)
    # And the good counts beside a bad one are not kept on their own.
    assert loaded.to_dict()["wins"] == []


def test_an_unfitted_calibrator_saves_no_counts():
    d = ConfidenceCalibrator().fit(_SAMPLES[:5]).to_dict()
    assert d["x"] == [] and d["trades"] == [] and d["wins"] == []
