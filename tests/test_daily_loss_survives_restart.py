"""The daily-loss accumulator must not be handed back to a restart empty.

`_live_daily_pnl` is what the DAILY_LOSS breaker gates on in pure-live mode —
the paper portfolio is never updated there, so this accumulator IS the day's
realized loss. Its own comment said:

    UTC-day reset; in-memory (rebuild after restart is safe — a fresh day
    starts flat).

A fresh DAY starts flat. A restart is not a fresh day. Lose 4.5% against a 5%
cap, redeploy, lose 4.5% again, and the gate reads 4.5% while the day is
actually 9.0% — the breaker that exists to stop exactly that never trips. This
deployment redeploys often, which makes a mid-day restart the ordinary path
rather than the exotic one.

TWO WAYS TO GET THIS WRONG, and the file already carries the scar of the
second one for `_live_equity_peak`:

  * not persisting it at all — the original defect;
  * persisting it and restoring yesterday's loss into today, which would trip
    the breaker on a day that has lost nothing.

So the restore is conditional on the stored UTC day matching, and the
initialiser had to move ABOVE `_load_state()`. The comment beside
`_live_equity_peak` says why in the author's own words: "The first version set
this 20 lines below the load and silently stomped every restored peak back to
0.0 — the restore ran, audited PEAK_RESTORED, and had no effect. Init-after-
load is invisible in review; a test caught it."
"""

import json
import time

import pytest

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "risk_state.json")


def _engine(state_file):
    # A real PortfolioTracker: the accumulator under test is fed by
    # record_live_trade_result and read by the DAILY_LOSS gate, neither of
    # which the portfolio mediates — but the constructor requires one, and a
    # mock here would be a mock in the position the defect lives next to.
    return RiskEngine(PortfolioTracker(), state_file=state_file)


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime(int(time.time())))


class TestTheAccumulatorSurvivesARestart:
    def test_a_mid_day_restart_does_not_forget_the_days_loss(self, state_file):
        first = _engine(state_file)
        first.record_live_trade_result(-45.0)
        first.record_live_trade_result(-30.0)
        assert first._live_daily_pnl == pytest.approx(-75.0)
        first._save_state()

        # The redeploy.
        second = _engine(state_file)
        assert second._live_daily_pnl == pytest.approx(-75.0), (
            "the day's realized loss was reset to zero by a restart — the "
            "daily-loss breaker now has the full cap to spend again")
        assert second._live_daily_day == _today()

    def test_it_is_actually_written_to_the_file(self, state_file):
        eng = _engine(state_file)
        eng.record_live_trade_result(-12.5)
        eng._save_state()
        data = json.loads(open(state_file).read())
        assert "live_daily_pnl" in data, "nothing to restore from"
        assert data["live_daily_pnl"] == pytest.approx(-12.5)
        assert data["live_daily_day"] == _today()

    def test_a_further_loss_after_restart_ADDS_to_the_restored_total(self, state_file):
        # The point of restoring: the second 4.5% must land on top of the
        # first, not replace it.
        first = _engine(state_file)
        first.record_live_trade_result(-45.0)
        first._save_state()

        second = _engine(state_file)
        second.record_live_trade_result(-45.0)
        assert second._live_daily_pnl == pytest.approx(-90.0)


class TestItDoesNotRestoreTheWrongDay:
    def test_yesterdays_loss_is_not_carried_into_today(self, state_file):
        # The opposite error, and it trips a breaker on a day that has lost
        # nothing. Absent is not zero — but neither is stale.
        eng = _engine(state_file)
        eng.record_live_trade_result(-99.0)
        eng._save_state()

        data = json.loads(open(state_file).read())
        data["live_daily_day"] = "2001-01-01"
        open(state_file, "w").write(json.dumps(data))

        fresh = _engine(state_file)
        assert fresh._live_daily_pnl == 0.0, (
            "a previous day's loss was restored into today")
        assert fresh._live_daily_day in ("", _today())

    def test_a_missing_or_corrupt_day_starts_flat(self, state_file):
        eng = _engine(state_file)
        eng.record_live_trade_result(-99.0)
        eng._save_state()
        for bad in (None, "", 12345, "not-a-date"):
            data = json.loads(open(state_file).read())
            data["live_daily_day"] = bad
            open(state_file, "w").write(json.dumps(data))
            assert _engine(state_file)._live_daily_pnl == 0.0, f"day={bad!r}"

    def test_a_non_numeric_stored_total_starts_flat(self, state_file):
        eng = _engine(state_file)
        eng.record_live_trade_result(-99.0)
        eng._save_state()
        data = json.loads(open(state_file).read())
        data["live_daily_pnl"] = "lots"
        open(state_file, "w").write(json.dumps(data))
        assert _engine(state_file)._live_daily_pnl == 0.0


class TestTheInitialiserRunsBeforeTheLoad:
    def test_the_restore_is_not_stomped_by_a_later_assignment(self, state_file):
        """The `_live_equity_peak` scar, restated as a test.

        A restore that runs and is then overwritten by an initialiser twenty
        lines further down looks identical to a restore that works — right up
        until the value is read. Asserting the restored value on a FRESH engine
        is what distinguishes them.
        """
        first = _engine(state_file)
        first.record_live_trade_result(-33.0)
        first._save_state()
        assert _engine(state_file)._live_daily_pnl == pytest.approx(-33.0)
