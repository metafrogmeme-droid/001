"""The wait after a close nobody could price was lifted by a restart.

`note_unpriced_close` stamps `_last_unpriced_close_time`, and the COOLDOWN
check reads it beside `_last_loss_time`: a close the bot could not price is
not a loss, and it is the close it understood least, so the account waits
`COOLDOWN_AFTER_LOSS_SEC` before its next entry. `_export_state_dict` wrote
`last_loss_time` and not this stamp, and the writer did not save, so a
restart inside the wait lifted it. Driven: stamp, restart, `None`.

The stamp is written now, the writer saves as the loss path does, and a
restore helper of its own reads it back on both loaders -- not one of
`_STATE_FIELDS`, because an unreadable value there fails the whole state
closed (the breaker tripped) over a field whose worst case is one wait.
"""
from __future__ import annotations

import json
import math
import os

import pytest

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from tests.test_core import _DEFAULT_ATR, _DEFAULT_MAX_POS, _make_idea

CD = CONFIG.risk.cooldown_after_loss_seconds


def _engine(tmpdir, name="risk_state.json"):
    pf = PortfolioTracker(initial_balance=10_000.0,
                          state_file=os.path.join(str(tmpdir), f"pf-{name}"))
    return RiskEngine(pf, state_file=os.path.join(str(tmpdir), name))


def _cooldown_line(risk):
    check = risk.evaluate(_make_idea(), atr=_DEFAULT_ATR,
                          max_position_usd=_DEFAULT_MAX_POS)
    failed = [x for x in check.checks_failed if x.startswith("COOLDOWN")]
    passed = [x for x in check.checks_passed if x.startswith("COOLDOWN")]
    assert len(failed) + len(passed) == 1, (failed, passed)
    return (failed or passed)[0], bool(failed)


def _state_with(tmp_path, value, *, name="risk_state.json"):
    """A readable saved state carrying `value` as the unpriced-close stamp."""
    eng = _engine(tmp_path, name)
    eng._consecutive_losses = 2
    data = eng._export_state_dict()
    data["last_unpriced_close_time"] = value
    (tmp_path / name).write_text(json.dumps(data))
    return data


class TestTheWaitSurvivesARestart:
    def test_the_stamp_is_restored(self, tmp_path):
        a = _engine(tmp_path)
        a.note_unpriced_close()
        stamp = a._last_unpriced_close_time
        b = _engine(tmp_path)           # no explicit save: the writer saves
        assert b._last_unpriced_close_time == stamp

    def test_the_restarted_account_still_waits(self, tmp_path):
        a = _engine(tmp_path)
        a.note_unpriced_close()
        line, refused = _cooldown_line(_engine(tmp_path))
        assert refused and "could not be priced" in line, line

    def test_the_combined_loader_reads_it_too(self, tmp_path):
        a = _engine(tmp_path, "a.json")
        a.note_unpriced_close()
        b = _engine(tmp_path, "b.json")
        b._load_from_state_dict(a._export_state_dict())
        assert b._last_unpriced_close_time == a._last_unpriced_close_time

    def test_an_older_wait_is_restored_as_it_was(self, tmp_path):
        """A stamp the wait has already run past is kept as written, so the
        restarted account is not made to wait again."""
        eng = _engine(tmp_path)
        old = eng._now() - (CD + 1000)
        _state_with(tmp_path, old)
        b = _engine(tmp_path)
        assert b._last_unpriced_close_time == old
        line, refused = _cooldown_line(b)
        assert not refused and "elapsed" in line, line

    def test_an_older_file_carries_no_stamp(self, tmp_path):
        data = _state_with(tmp_path, None)
        del data["last_unpriced_close_time"]
        (tmp_path / "risk_state.json").write_text(json.dumps(data))
        assert _engine(tmp_path)._last_unpriced_close_time is None

    def test_a_reader_writes_nothing(self, tmp_path):
        a = _engine(tmp_path)
        a.make_reader()
        a.note_unpriced_close()
        assert not (tmp_path / "risk_state.json").exists()


BAD = [
    pytest.param("yesterday", id="text"),
    pytest.param(True, id="bool"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"),
    pytest.param([1], id="list"),
]


class TestABadValueIsNotAHaltedAccount:
    @pytest.mark.parametrize("value", BAD)
    def test_it_is_ignored_and_the_state_still_loads(self, tmp_path, value):
        _state_with(tmp_path, value)
        b = _engine(tmp_path)
        assert b._last_unpriced_close_time is None
        assert b._circuit_open is False, "one bad stamp failed the whole state closed"
        assert b._consecutive_losses == 2, "the rest of the state was not read"

    @pytest.mark.parametrize("value", BAD)
    def test_the_combined_loader_ignores_it_too(self, tmp_path, value):
        data = _state_with(tmp_path, value)
        b = _engine(tmp_path, "b.json")
        b._load_from_state_dict(data)
        assert b._last_unpriced_close_time is None
        assert b._circuit_open is False
        assert b._consecutive_losses == 2

    def test_a_future_stamp_waits_one_period_and_no_longer(self, tmp_path):
        eng = _engine(tmp_path)
        _state_with(tmp_path, eng._now() + 30 * 86400)
        b = _engine(tmp_path)
        assert b._last_unpriced_close_time is not None
        assert b._last_unpriced_close_time <= b._now()
        line, refused = _cooldown_line(b)
        assert refused, line
        remaining = float(line.split("COOLDOWN: ", 1)[1].split("s", 1)[0])
        assert math.isfinite(remaining) and remaining <= CD, line


def test_the_export_carries_the_stamp(tmp_path):
    eng = _engine(tmp_path)
    assert eng._export_state_dict()["last_unpriced_close_time"] is None
    eng.note_unpriced_close()
    assert (eng._export_state_dict()["last_unpriced_close_time"]
            == eng._last_unpriced_close_time)
