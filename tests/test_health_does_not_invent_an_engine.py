"""`/health` reported a healthy, flat, unblocked bot with no engine at all.

    "circuit_breaker_active": engine.risk.circuit_breaker_active if engine else False,
    "open_positions":         len(engine.portfolio.open_positions) if engine else 0,

Two of the shapes CLAUDE.md lists by name, on the surface this same file's
comments call "THE surface the operator checked during the 2026-07-29
incident". With `engine` unset — lifespan not finished, or failed — the answer
was 200 with `status: ok`, breaker clear, zero positions. Healthy, trading,
flat, from a process that had no engine.

WHAT MAKES THIS ONE WORTH A TEST RATHER THAN A ONE-LINE PATCH

The same dict literal already got it right twice, on either side of the two
that were wrong:

  * `_health_gate_fields()` returns `trading_gate_unknown: True` when there is
    no engine — its comment says a fabricated "" "would read as 'trading is
    fine'".
  * `engine_universe_size` and `analyze_capacity` are OMITTED until measured —
    "a zero here would read as 'nothing to scan'".

And the sibling endpoint takes the other honest route: `/risk/status` raises
503 "Engine not initialized" rather than describing a risk engine it does not
have. Guard or omit, the two strategies the repository documents. These two
fields did neither, three lines from code doing both.

So the defect is not that the rule was unknown. It is that a field can sit
between two correct ones and be wrong, and nothing notices — which is what this
test is for.

/health OMITS rather than guards because it must stay answerable in order to
report why. `status` and the new `engine` field carry the bad news; the numbers
that would be invented simply are not there.
"""
from __future__ import annotations


import pytest


@pytest.fixture
def bridge(monkeypatch):
    """api_bridge with its module-level engine forced absent or present.

    Imported here rather than at module scope because it refuses to load
    without a JWT secret — deliberately, and not this test's business.
    """
    monkeypatch.setenv("JWT_SECRET", "0" * 64)
    mod = pytest.importorskip("api_bridge")
    return mod


def _health(mod):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(mod.health())


class _Risk:
    circuit_breaker_active = True


class _Portfolio:
    open_positions = [object(), object(), object()]


class _Engine:
    risk = _Risk()
    portfolio = _Portfolio()


# ── the defect ───────────────────────────────────────────────────────

class TestWithNoEngine:
    @pytest.fixture(autouse=True)
    def _no_engine(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge, "engine", None, raising=False)

    def test_it_does_not_claim_the_breaker_is_clear(self, bridge):
        body = _health(bridge)
        assert "circuit_breaker_active" not in body, (
            f"reported circuit_breaker_active={body['circuit_breaker_active']!r} "
            "with no engine — a caller reads that as 'trading is not blocked'")

    def test_it_does_not_claim_zero_open_positions(self, bridge):
        body = _health(bridge)
        assert "open_positions" not in body, (
            f"reported open_positions={body['open_positions']!r} with no engine "
            "— indistinguishable from a genuinely flat book")

    def test_the_headline_is_not_ok(self, bridge):
        """`status: ok` is the field a monitor watches. A bridge with no engine
        is not ok, and a monitor going red for it is right to."""
        body = _health(bridge)
        assert body["status"] != "ok", (
            "status still says ok — every downstream check reads the bridge as "
            "healthy while it has no engine")

    def test_and_it_says_what_is_missing(self, bridge):
        # Omission alone leaves a reader guessing whether a field is absent
        # because it is unknown or because the key was renamed.
        body = _health(bridge)
        assert body.get("engine") == "absent"

    def test_the_gate_field_still_flags_unknown(self, bridge):
        """The one that was already right, pinned so the fix cannot regress it
        while tidying its neighbours."""
        body = _health(bridge)
        assert body.get("trading_gate_unknown") is True

    def test_it_still_answers(self, bridge):
        """OMIT, not guard. /health raising here would remove the only surface
        that can say why the bridge is unhealthy — `/risk/status` already
        covers the guard strategy for the same data."""
        body = _health(bridge)
        assert isinstance(body, dict) and body.get("status")


# ── the other half: a real engine still reports ──────────────────────

class TestWithAnEngine:
    @pytest.fixture(autouse=True)
    def _engine(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge, "engine", _Engine(), raising=False)

    def test_the_numbers_come_back(self, bridge):
        """Otherwise the fix passes every assertion above by emptying the
        endpoint — the failure mode a redaction test has to rule out."""
        body = _health(bridge)
        assert body["circuit_breaker_active"] is True
        assert body["open_positions"] == 3

    def test_and_the_headline_is_ok(self, bridge):
        body = _health(bridge)
        assert body["status"] == "ok"
        assert body["engine"] == "ready"

    def test_a_tripped_breaker_is_still_reported_as_tripped(self, bridge):
        """The planted state: `True` must survive as `True`. A fix that
        omitted the field whenever it was falsy — or truthy — would pass the
        absence tests and hide the breaker."""
        body = _health(bridge)
        assert body["circuit_breaker_active"] is True

    def test_a_clear_breaker_is_reported_as_clear_not_omitted(self, bridge, monkeypatch):
        """`False` is a MEASUREMENT here, and the whole point is telling it
        apart from absent. Omitting a measured False would be the same defect
        with the sign flipped."""
        eng = _Engine()
        eng.risk = type("R", (), {"circuit_breaker_active": False})()
        monkeypatch.setattr(bridge, "engine", eng, raising=False)
        body = _health(bridge)
        assert body["circuit_breaker_active"] is False

    def test_a_genuinely_empty_book_reports_zero(self, bridge, monkeypatch):
        """Same argument for the count: measured 0 must still be published."""
        eng = _Engine()
        eng.portfolio = type("P", (), {"open_positions": []})()
        monkeypatch.setattr(bridge, "engine", eng, raising=False)
        body = _health(bridge)
        assert body["open_positions"] == 0


# ── uptime ───────────────────────────────────────────────────────────

class TestUptime:
    def test_no_recorded_start_is_absent_not_zero(self, bridge, monkeypatch):
        """`uptime_seconds: 0` reads as "just started", which is a measurement
        somebody acts on — the same family as the two above."""
        monkeypatch.setattr(bridge, "engine", None, raising=False)
        monkeypatch.setattr(bridge, "_start_time", None, raising=False)
        assert "uptime_seconds" not in _health(bridge)

    def test_a_recorded_start_still_reports(self, bridge, monkeypatch):
        import time
        monkeypatch.setattr(bridge, "engine", _Engine(), raising=False)
        monkeypatch.setattr(bridge, "_start_time", time.time() - 42, raising=False)
        body = _health(bridge)
        assert body["uptime_seconds"] >= 41


# ── the neighbours that were already right ───────────────────────────

def test_no_engine_derived_field_survives_without_an_engine(bridge, monkeypatch):
    """The general property, so a field added later cannot repeat this.

    Anything /health can only know by asking the engine must be absent when
    there is no engine. Listed explicitly rather than inferred, because the
    body legitimately contains engine-INDEPENDENT numbers —
    `bridge_scan_universe_size` is this bridge's own fixed /scan list and must
    survive.
    """
    monkeypatch.setattr(bridge, "engine", None, raising=False)
    body = _health(bridge)
    engine_derived = ("circuit_breaker_active", "open_positions",
                      "engine_universe_size", "analyze_capacity",
                      "monitor_checks_down")
    present = [k for k in engine_derived if k in body]
    assert not present, f"engine-derived fields survived with no engine: {present}"
    # ...and the field that does NOT come from the engine still does.
    assert "bridge_scan_universe_size" in body, (
        "the fix removed a field the bridge knows on its own")
