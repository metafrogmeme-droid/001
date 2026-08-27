"""A dashboard generation the engine started and nothing deployed.

`dashboard_api.py` listens on :9090 and is referenced by no compose service,
no nginx upstream, no Dockerfile and no deploy script — only by one test
asserting it refuses to start without a token. The engine nevertheless built a
`DashboardPusher` in `__init__` and awaited `start()` on every boot. `start()`
logged "DASHBOARD_API_KEY not set — dashboard pusher disabled" and returned,
so the wiring read as live in the source and did nothing in production.

That is the harder direction to notice. Code that is present but never reached
looks exactly like code that works — the same shape as the Telegram adoption
card CLAUDE.md records, which was source-scanned, shipped, and rendered zero
times.

TWO THINGS BEYOND "IT IS UNUSED".

`DashboardPusher.stop()` was written and called from nowhere. It owns an
aiohttp session and a background task, so on any boot where the pusher HAD
been enabled both leaked at shutdown. It never bit because nothing deploys the
consumer, which is not a reason to leave it armed.

And `_build_snapshot` is a FOURTH independent rendering of win rate, equity and
P&L — the exact class this repo keeps being audited for, sitting behind an
environment variable. Constructing it only when configured means it cannot be
reached by accident.

WHAT THIS DELIBERATELY DOES NOT DO: delete the feature. An operator who sets
DASHBOARD_API_KEY still gets exactly what they asked for, and the tests below
pin that too — a fix that quietly removed a capability would be its own kind
of dishonesty.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.core import dashboard_pusher

ROOT = Path(__file__).resolve().parent.parent


# ── the switch ───────────────────────────────────────────────────────

def test_unconfigured_by_default(monkeypatch):
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    assert dashboard_pusher.is_configured() is False


def test_configured_when_the_operator_asks(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "k" * 32)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:9090")
    assert dashboard_pusher.is_configured() is True


def test_a_url_without_a_key_is_not_configured(monkeypatch):
    """The key is what authenticates the push. A URL alone would start a
    pusher that gets rejected every 30 seconds."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:9090")
    assert dashboard_pusher.is_configured() is False


def test_the_check_reads_the_environment_at_call_time(monkeypatch):
    """The module's constants are captured at import, which is exactly why
    this could not be exercised before. Reading at call time is what makes the
    switch testable at all — and what lets a late `.env` be seen."""
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    assert dashboard_pusher.is_configured() is False
    monkeypatch.setenv("DASHBOARD_API_KEY", "late-arrival")
    assert dashboard_pusher.is_configured() is True, (
        "the check is frozen at import and cannot see configuration that "
        "arrives afterwards")


# ── the wiring, which no unit test of the module can reach ───────────

def _engine_src() -> str:
    """engine.py with comments stripped.

    CLAUDE.md, and five separate false failures in this session: a comment
    quoting the construct it forbids is indistinguishable from the construct.
    The note added beside this wiring names `DashboardPusher(self)`, and an
    unstripped scan fails on the change that fixes the defect.
    """
    src = (ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
    return "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())


def test_the_engine_builds_it_only_when_configured():
    src = _engine_src()
    i = src.find("self.dashboard_pusher =")
    assert i > 0, "the engine no longer assigns dashboard_pusher at all"
    # A fixed window rather than a cleverer pattern: the assignment wraps over
    # two lines, and the first regex here matched just the opening paren and
    # reported the defect present on code that fixes it.
    assignment = src[i:i + 200]
    assert "configured()" in assignment, (
        "the pusher is constructed unconditionally again — a generation "
        "nothing deploys, built on every boot")


def test_the_engine_guards_the_start_call():
    src = _engine_src()
    i = src.find("self.dashboard_pusher.start()")
    assert i > 0, "the engine no longer starts the pusher at all"
    preceding = src[max(0, i - 300):i]
    assert "is not None" in preceding, (
        "start() is called without checking the pusher exists — an "
        "AttributeError on every boot now that it can be None")


async def test_shutdown_actually_closes_it():
    """`stop()` existed and was called from nowhere. It owns an aiohttp
    session and a task; both leaked on any boot where the pusher ran.

    DRIVEN, not scanned. The first version of this looked for the string
    `dashboard_pusher.stop()` in the shutdown body — and a mutation that made
    the call unreachable while leaving it in place passed. That is the
    distinction CLAUDE.md says no scan can make: the code was present, it was
    never reached, and the Telegram adoption card shipped exactly that way.
    """
    from bot.core.engine import RuneClawEngine

    stopped = []

    class _Pusher:
        async def stop(self):
            stopped.append(True)

    class _Noop:
        async def stop(self): pass
        async def close(self): pass

    class _Stub:
        _running = True
        ws_feed = _Noop()
        scanner = _Noop()
        live_executor = None
        _user_executors: dict = {}
        dashboard_pusher = _Pusher()

    stub = _Stub()
    try:
        await RuneClawEngine.stop(stub)
    except Exception:
        # Shutdown touches more subsystems further down than this stub has;
        # the pusher is closed near the top and that is what is under test.
        pass
    assert stopped, (
        "engine shutdown never awaited the pusher's stop() — its aiohttp "
        "session and background task leak whenever it is enabled")


async def test_shutdown_survives_the_pusher_being_absent():
    """It is None on every default deployment, which is the common path."""
    from bot.core.engine import RuneClawEngine

    class _Noop:
        async def stop(self): pass
        async def close(self): pass

    class _Stub:
        _running = True
        ws_feed = _Noop()
        scanner = _Noop()
        live_executor = None
        _user_executors: dict = {}
        dashboard_pusher = None

    try:
        await RuneClawEngine.stop(_Stub())
    except AttributeError as exc:
        if "dashboard_pusher" in str(exc) or "stop" in str(exc):
            pytest.fail(f"shutdown broke on an absent pusher: {exc}")
    except Exception:
        pass


# ── the premise, so this file fails if it stops being true ───────────

def test_nothing_in_the_repo_deploys_the_consumer():
    """The whole justification. If someone wires :9090 into a compose service
    or an nginx upstream, this generation stops being dead and the reasoning
    above needs revisiting — so the premise is asserted, not assumed.
    """
    deployers = []
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml",
                    "Dockerfile*", "deploy*.sh", "*.conf"):
        for path in ROOT.rglob(pattern):
            if any(part in {".git", "node_modules", ".mypy_cache"}
                   for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            code = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
            if "dashboard_api" in code or "9090" in code:
                deployers.append(str(path.relative_to(ROOT)))
    assert not deployers, (
        "something now deploys the :9090 dashboard: " + ", ".join(deployers)
        + " — this generation is no longer dead, so revisit whether the "
          "engine should still build its pusher only on request")


def test_the_feature_is_not_deleted():
    """An operator who sets the key must still get the pusher. Removing a
    capability while claiming to fix its wiring would be a worse defect than
    the one being fixed."""
    assert hasattr(dashboard_pusher, "DashboardPusher")
    assert (ROOT / "dashboard_api.py").exists()
