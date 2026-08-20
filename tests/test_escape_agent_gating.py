"""Guardian Escape Agent — the engine-level gating + recording contract.

``RuneClawEngine.run_escape_agent`` bridges the pure exit planner and the
tamper-evident chain. Tested against a minimal fake ``self`` (a full engine is
heavy): it always returns the read-only plan, but only *seals* an ESCAPE event
when the flag is on. It plans only — it never closes anything.
"""
import types

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


class _Recorder:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, actor=""):
        self.events.append((event_type, payload, actor))


_BOOK = [
    {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 5.0,
     "leverage": 20, "group": "BTC", "cost_usd": 25.0},
    {"symbol": "ETHUSDT", "direction": "SHORT", "entry": 100.0, "qty": 1.0,
     "leverage": 3, "group": "ETH", "cost_usd": 33.3},
]


class _FakeEngine:
    def __init__(self, positions):
        self._positions = positions
        self.audit_chain = _Recorder()

    def _twin_positions(self, user_id=""):
        return self._positions

    run_escape_agent = RuneClawEngine.run_escape_agent


def _set_flag(monkeypatch, on):
    fake = types.SimpleNamespace(risk=types.SimpleNamespace(guardian_escape_enabled=on))
    monkeypatch.setattr(engine_mod, "CONFIG", fake)


def test_no_positions_returns_a_flat_plan_not_none(monkeypatch):
    """This test used to assert `is None` for a flat book.

    That was the conflation: `None` was returned both for "nothing to unwind"
    and from the `except` arm for "the plan could not be built", and the
    `/escape` card rendered both as "🪂 no open positions to unwind" — an
    all-clear assembled from a failure, on the emergency-exit surface.

    A flat book now gets the planner's own flat document, and `None` is
    reserved for could-not-tell. Sealing nothing is unchanged: there is no
    plan to seal while flat.
    """
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    report = eng.run_escape_agent()
    assert report is not None, "a flat book is a real, assessable answer"
    assert report["ok"] is True
    assert report["steps"] == []
    assert report["position_count"] == 0
    assert report["risk"] == "none", "a flat book genuinely is calm"
    assert eng.audit_chain.events == []


def test_a_planner_fault_is_none_and_seals_nothing(monkeypatch):
    """The other half of what `None` now means, and the reason it was worth
    separating: this is the case that used to answer "you are flat"."""
    import bot.guardian.escape_agent as _ea
    _set_flag(monkeypatch, True)

    def _boom(*a, **k):
        raise RuntimeError("planner fault")

    monkeypatch.setattr(_ea, "plan", _boom)
    eng = _FakeEngine(_BOOK)
    assert eng.run_escape_agent() is None
    assert eng.audit_chain.events == [], "a failed plan was sealed as evidence"


def test_flag_off_previews_but_records_nothing(monkeypatch):
    _set_flag(monkeypatch, False)
    eng = _FakeEngine(_BOOK)
    report = eng.run_escape_agent()
    assert report is not None and report["steps"]
    # The 20x BTC is the most dangerous → planned first.
    assert report["steps"][0]["symbol"] == "BTCUSDT"
    assert eng.audit_chain.events == []


def test_flag_on_seals_one_escape_event(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine(_BOOK)
    report = eng.run_escape_agent()
    assert report is not None and report["steps"]
    assert len(eng.audit_chain.events) == 1
    etype, payload, actor = eng.audit_chain.events[0]
    assert etype == "ESCAPE"
    assert payload["position_count"] == 2 and actor == "operator"
    assert payload["order"][0]["symbol"] == "BTCUSDT"


def test_recorder_fault_never_raises(monkeypatch):
    _set_flag(monkeypatch, True)

    class _Boom:
        def append(self, *a, **k):
            raise RuntimeError("chain unavailable")

    eng = _FakeEngine(_BOOK)
    eng.audit_chain = _Boom()
    report = eng.run_escape_agent()
    assert report is not None and report["steps"]
