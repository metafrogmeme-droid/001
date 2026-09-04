"""Guardian Digital Twin — the engine-level gating + recording contract.

``RuneClawEngine.run_digital_twin`` is the thin, fail-open bridge between the
pure stress simulator and the tamper-evident chain. These tests pin its contract
against a minimal fake ``self`` (constructing a full engine is heavy): it always
returns the read-only report (foresight is safe), but only *seals* a TWIN event
when the flag is on.

Contract:
  * no open positions        -> returns None, records nothing
  * flag OFF, open positions -> returns the report, records nothing (preview only)
  * flag ON,  open positions -> returns the report AND seals ONE TWIN event
  * a recorder fault          -> never raises (foresight must never break a caller)
"""
import types

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


class _Recorder:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, actor=""):
        self.events.append((event_type, payload, actor))


_FRAGILE_BOOK = [
    {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
     "leverage": 10, "group": "BTC"},
]


class _FakeEngine:
    """Just enough surface for the unbound run_digital_twin method."""
    def __init__(self, positions):
        self._positions = positions
        self.audit_chain = _Recorder()

    def _twin_positions(self, user_id=""):
        return self._positions

    def get_effective_equity(self, user_id=""):
        return 30.0

    run_digital_twin = RuneClawEngine.run_digital_twin


def _set_flag(monkeypatch, on):
    fake = types.SimpleNamespace(risk=types.SimpleNamespace(guardian_digital_twin_enabled=on))
    monkeypatch.setattr(engine_mod, "CONFIG", fake)


def test_a_flat_book_is_assessed_and_records_nothing(monkeypatch):
    """CONTRACT CHANGED, deliberately: `[]` is a reading, `None` is not.

    This asserted `run_digital_twin() is None` for an empty book — the same
    value the method returns when the position read FAILED. Both then rendered
    as "no open positions to stress-test", so a crash on the foresight screen
    produced an all-clear. `_twin_positions` is three-valued now, so a flat
    book comes back as a real report saying so, and None is reserved for
    "could not read the book".

    The no-op half of the old name still holds: a flat book seals nothing.
    """
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    report = eng.run_digital_twin()
    assert report is not None
    assert report["flat_book"] is True
    assert report["risk"] == "none"          # a flat book really is calm
    assert report["position_count"] == 0
    assert eng.audit_chain.events == []


def test_an_unreadable_book_is_not_a_flat_one(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    eng._positions = None                    # what _twin_positions now says
    assert eng.run_digital_twin() is None    # the caller must render a failure
    assert eng.audit_chain.events == []


def test_flag_off_previews_but_records_nothing(monkeypatch):
    _set_flag(monkeypatch, False)
    eng = _FakeEngine(_FRAGILE_BOOK)
    report = eng.run_digital_twin()
    assert report is not None and report["risk"] == "high"   # foresight still computes
    assert eng.audit_chain.events == []                      # but nothing sealed


def test_flag_on_seals_one_twin_event(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine(_FRAGILE_BOOK)
    report = eng.run_digital_twin()
    assert report is not None and report["risk"] == "high"
    assert len(eng.audit_chain.events) == 1
    etype, payload, actor = eng.audit_chain.events[0]
    assert etype == "TWIN"
    assert payload["risk"] == "high" and payload["position_count"] == 1
    assert actor == "operator"


def test_recorder_fault_never_raises(monkeypatch):
    _set_flag(monkeypatch, True)

    class _Boom:
        def append(self, *a, **k):
            raise RuntimeError("chain unavailable")

    eng = _FakeEngine(_FRAGILE_BOOK)
    eng.audit_chain = _Boom()
    report = eng.run_digital_twin()          # sealing fault degrades gracefully
    assert report is not None and report["risk"] == "high"
