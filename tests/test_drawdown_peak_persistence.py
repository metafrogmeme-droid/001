"""The drawdown breaker's memory died with the process.

`_live_equity_peak` — the high-water mark the live drawdown breaker gates on —
was in-memory only. A restart re-seeded it at current equity, so a 6% loss, a
restart, and another 6% loss never tripped the 7% breaker: the protection
restarted from zero with the process. (The same fact had an upside on
2026-07-30: the operator's fund transfer + restart avoided a phantom trip.
That upside now has its own mechanism — the trip card's transfer hint (#996)
plus /resume — so the hole can be closed without reintroducing phantom trips
as a silent surprise.)

The design, as tests:

- PERSIST_LIVE_DRAWDOWN_PEAK defaults OFF: byte-identical restart behaviour
  until the operator opts in, because the flag changes WHEN a live safety
  gate fires (§4: engine gates default OFF).
- The peak is always WRITTEN to state (it is just a number); it is only
  RESTORED behind the flag — so flipping the flag on protects from the very
  next restart, no "first restart writes, second restores" lag.
- Garbage in the state file restores nothing: a tampered peak of 1e12 would
  pin drawdown at ~100% forever. The fail-closed answer to garbage is "no
  peak, re-seed live" — the breaker still gates on fresh readings either way.
- /resume still clears the peak (that is the transfer remedy), and a cleared
  peak persists as None.
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
import bot.risk.risk_engine as risk_mod


class _RiskProxy:
    """CONFIG.risk is a frozen dataclass, so the flag is patched via a
    delegating proxy on the module's CONFIG reference instead."""
    def __init__(self, real, flag):
        self._real, self._flag = real, flag

    def __getattr__(self, name):
        if name == "persist_live_drawdown_peak":
            return self._flag
        return getattr(self._real, name)


class _CfgProxy:
    def __init__(self, real, flag):
        self._real, self._risk = real, _RiskProxy(real.risk, flag)

    def __getattr__(self, name):
        if name == "risk":
            return self._risk
        return getattr(self._real, name)


def _state_file() -> str:
    return os.path.join(tempfile.mkdtemp(prefix="rc-peak-"), "risk_state.json")


def _engine(state_file: str) -> RiskEngine:
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=state_file)


def _flag(value: bool):
    return patch.object(risk_mod, "CONFIG", _CfgProxy(CONFIG, value))


class TestDefaultOff:
    def test_the_flag_defaults_off(self):
        # Changes when a live safety gate fires → operator opt-in (§4).
        assert CONFIG.risk.persist_live_drawdown_peak is False

    def test_restart_reseeds_without_the_flag(self):
        sf = _state_file()
        eng = _engine(sf)
        eng._live_equity_peak = 883.18
        eng._save_state()
        with _flag(False):
            fresh = _engine(sf)
        assert fresh._live_equity_peak == 0.0


class TestOptedIn:
    def test_the_peak_survives_a_restart(self):
        sf = _state_file()
        eng = _engine(sf)
        eng._live_equity_peak = 883.18
        eng._save_state()
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_equity_peak == 883.18

    def test_written_even_while_off_so_enabling_has_no_lag(self):
        # The write is unconditional; only the RESTORE is gated. Flipping the
        # flag on must protect from the very next restart.
        sf = _state_file()
        with _flag(False):
            eng = _engine(sf)
            eng._live_equity_peak = 700.0
            eng._save_state()
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_equity_peak == 700.0

    def test_resume_clears_the_peak_and_the_clear_persists(self):
        # /resume is the transfer remedy — its reset must survive a restart,
        # or the phantom-trip loop would resume on the next boot.
        sf = _state_file()
        eng = _engine(sf)
        eng._live_equity_peak = 883.18
        eng._trip_circuit_breaker("max drawdown breached", cause="drawdown")
        eng.reset_circuit_breaker()
        assert eng._live_equity_peak == 0.0
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_equity_peak == 0.0


class TestGarbageRestoresNothing:
    def _load(self, peak_value):
        sf = _state_file()
        eng = _engine(sf)
        data = eng._export_state_dict()
        data["live_equity_peak"] = peak_value
        with open(sf, "w") as f:
            json.dump(data, f)
        with _flag(True):
            return _engine(sf)

    def test_absurd_peak_is_ignored(self):
        # 1e12 would pin drawdown at ~100% forever; re-seed live instead.
        assert self._load(1e15)._live_equity_peak == 0.0

    def test_negative_zero_and_none_are_ignored(self):
        assert self._load(-5.0)._live_equity_peak == 0.0
        assert self._load(0.0)._live_equity_peak == 0.0
        assert self._load(None)._live_equity_peak == 0.0

    def test_non_numeric_is_ignored(self):
        assert self._load("883.18")._live_equity_peak == 0.0
