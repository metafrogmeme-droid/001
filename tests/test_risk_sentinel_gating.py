"""Guardian Risk Sentinel — the engine-level gating + recording contract.

``RuneClawEngine.run_risk_sentinel`` bridges the pure crowding detector and the
tamper-evident chain. Tested against a minimal fake ``self`` (a full engine is
heavy): it always returns the read-only assessment, but only *seals* a SENTINEL
event when the flag is on.
"""
import types

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


class _Recorder:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, actor=""):
        self.events.append((event_type, payload, actor))


# A crowded book: two longs, same correlation group → concentration high.
_CROWDED = [
    {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
     "leverage": 5, "group": "BTC"},
    {"symbol": "ETHUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
     "leverage": 5, "group": "BTC"},
]


class _FakeEngine:
    def __init__(self, positions):
        self._positions = positions
        self.audit_chain = _Recorder()

    def _twin_positions(self, user_id=""):
        return self._positions

    run_risk_sentinel = RuneClawEngine.run_risk_sentinel


def _set_flag(monkeypatch, on):
    fake = types.SimpleNamespace(risk=types.SimpleNamespace(guardian_risk_sentinel_enabled=on))
    monkeypatch.setattr(engine_mod, "CONFIG", fake)


def test_a_flat_book_is_assessed_and_records_nothing(monkeypatch):
    """CONTRACT CHANGED, deliberately — the twin's sibling.

    This asserted `run_risk_sentinel() is None` for an empty book, the same
    value returned when the position read FAILED, and `/sentinel` printed "no
    open positions to assess" for both. `_twin_positions` is three-valued now:
    [] is a reading (crowding across an empty book really is none), None is
    "could not read the book", and the card says which.

    The no-op half of the old name still holds: a flat book seals nothing.
    """
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    report = eng.run_risk_sentinel()
    assert report is not None
    assert report.get("position_count") == 0
    assert eng.audit_chain.events == []


def test_an_unreadable_book_is_not_a_flat_one(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    eng._positions = None                # what _twin_positions now returns
    assert eng.run_risk_sentinel() is None
    assert eng.audit_chain.events == []


def test_flag_off_previews_but_records_nothing(monkeypatch):
    _set_flag(monkeypatch, False)
    eng = _FakeEngine(_CROWDED)
    report = eng.run_risk_sentinel()
    assert report is not None and report["risk"] == "high"
    assert eng.audit_chain.events == []


def test_flag_on_seals_one_sentinel_event(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine(_CROWDED)
    report = eng.run_risk_sentinel()
    assert report is not None and report["risk"] == "high"
    assert len(eng.audit_chain.events) == 1
    etype, payload, actor = eng.audit_chain.events[0]
    assert etype == "SENTINEL"
    assert payload["risk"] == "high" and actor == "operator"


def test_recorder_fault_never_raises(monkeypatch):
    _set_flag(monkeypatch, True)

    class _Boom:
        def append(self, *a, **k):
            raise RuntimeError("chain unavailable")

    eng = _FakeEngine(_CROWDED)
    eng.audit_chain = _Boom()
    report = eng.run_risk_sentinel()
    assert report is not None and report["risk"] == "high"
