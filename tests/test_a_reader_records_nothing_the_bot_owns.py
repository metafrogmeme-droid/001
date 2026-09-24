"""A reader engine's risk evaluations record nothing the bot owns.

`detach_state_persistence` makes a second process's engine a READER of the
operator's state, because the combined saver writes the whole file from the
caller's memory. Two writes never pass through that saver:

- **The ladder ledger.** `RiskEngine.evaluate` records every sized evaluation
  to `data/ladder_ledger.json`, and `ShadowLedger` loads its file once and
  rewrites it WHOLE from this process's memory on every record. The API
  bridge's `/analyze` runs `engine.risk.evaluate` in its own process, so one
  bridge evaluation stamps the rows the bridge loaded at startup over every
  row the bot recorded since. Driven below: the bot records three, a second
  instance records one, and the file holds one.
- **A per-user engine's own state file.** `risk_for` builds engines that save
  to `risk_state_{user}.json` directly, not through the combined saver.

A reader's evaluation is also not one of the bot's: it sizes off a copy of the
state. So `RiskEngine.make_reader()` writes neither, and a detached engine
marks its own risk engine and every per-user engine it builds.
"""
from __future__ import annotations

import dataclasses
import json
import os
import secrets
import tempfile
from datetime import datetime

import pytest

os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

from bot.compat import UTC  # noqa: E402
from bot.config import CONFIG  # noqa: E402
from bot.risk import ladder_shadow as ls  # noqa: E402
from bot.risk import risk_engine as rem  # noqa: E402
from bot.risk.portfolio import PortfolioTracker  # noqa: E402
from bot.risk.risk_engine import RiskEngine  # noqa: E402
from bot.utils.models import Direction, RiskVerdict, TradeIdea  # noqa: E402


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-rd-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=0.72,
                     reasoning="reader", source="scan", timestamp=datetime.now(UTC))


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    led = ls.LadderLedger(str(tmp_path / "ladder_ledger.json"))
    monkeypatch.setattr(ls, "LADDER_LEDGER", led)
    monkeypatch.setattr(rem, "CONFIG", dataclasses.replace(
        CONFIG, risk=dataclasses.replace(CONFIG.risk, quality_ladder_size_enabled=False,
                                         quality_ladder_leverage_enabled=False)))
    return led


def test_the_fixture_reaches_the_defect(tmp_path):
    # Two processes, one file: whichever records last owns it. Without this
    # the tests below would pass over a ledger that could not be erased.
    path = str(tmp_path / "ladder_ledger.json")
    bot, bridge = ls.LadderLedger(path), ls.LadderLedger(path)
    for i in range(3):
        bot.record({"ts": 1.0, "tag": f"bot-{i}"})
    bridge.record({"ts": 1.0, "tag": "bridge"})
    rows = json.load(open(path))["rows"]
    assert [r.get("tag") for r in rows] == ["bridge"]


def test_the_bots_engine_still_records(ledger):
    chk = _engine().evaluate(_idea(), atr=2.0)
    assert chk.verdict == RiskVerdict.APPROVED
    assert len(ledger.rows()) == 1


def test_a_reader_records_no_ladder_row(ledger, tmp_path):
    eng = _engine()
    eng.make_reader()
    chk = eng.evaluate(_idea(), atr=2.0)
    assert chk.verdict == RiskVerdict.APPROVED, "the evaluation itself is unchanged"
    assert ledger.rows() == []
    assert not os.path.exists(tmp_path / "ladder_ledger.json")


def test_a_reader_saves_no_state_file():
    eng = _engine()
    eng.make_reader()
    eng.emergency_halt("a copy's halt")
    eng.record_live_trade_result(-10.0)
    assert not os.path.exists(eng._state_file)
    assert eng.circuit_breaker_active, "a reader still holds its state in memory"


def test_the_bots_engine_still_saves_its_state():
    eng = _engine()
    eng.emergency_halt("the bot's halt")
    assert os.path.exists(eng._state_file)


# ── the engine marks its risk engines ─────────────────────────────────────

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path)
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda *a, **k: None, raising=False)
    return tmp_path


def test_detaching_marks_the_operators_risk_engine(data_dir):
    from bot.core.engine import RuneClawEngine
    reader = RuneClawEngine()
    assert reader.risk._reader is False
    reader.detach_state_persistence()
    assert reader.risk._reader is True


def test_a_per_user_engine_built_before_the_detach_is_marked(data_dir):
    from bot.core.engine import RuneClawEngine
    reader = RuneClawEngine()
    reader._user_risk["7"] = _engine()
    reader.detach_state_persistence()
    assert reader._user_risk["7"]._reader is True


def _per_user_live(monkeypatch):
    monkeypatch.setattr("bot.core.engine.CONFIG",
                        dataclasses.replace(CONFIG, per_user_live_enabled=True))


def test_a_per_user_engine_built_after_the_detach_is_marked(data_dir, monkeypatch):
    from bot.core.engine import RuneClawEngine
    _per_user_live(monkeypatch)
    reader = RuneClawEngine()
    reader.detach_state_persistence()
    eng = reader.risk_for("7")
    assert eng is not reader.risk, "the fixture must build a per-user engine"
    assert eng._reader is True


def test_the_bots_per_user_engines_are_writers(data_dir, monkeypatch):
    from bot.core.engine import RuneClawEngine
    _per_user_live(monkeypatch)
    bot = RuneClawEngine()
    eng = bot.risk_for("7")
    assert eng is not bot.risk
    assert eng._reader is False
