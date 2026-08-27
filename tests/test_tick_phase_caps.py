"""Per-phase caps inside a tick — a hang that NAMES itself.

WHY (production, 2026-07-28, live account)

The engine tick hung twice. The whole-tick cap recovered the loop both times
— that hardening works — but the diagnosis said only "engine.py:2226 in run",
the outermost await, because that is all a whole-tick cap can ever know. The
operator was left asking "hung where?" for a live trading bot.

Every await inside _tick() is now bounded individually, so:
  * a hang fails in a third of the time (300s phase vs 900s whole-tick), and
  * the audit line NAMES the phase — positions / scan / analyze — so the
    answer arrives without reading a stack at all.

Two deliberately different treatments, matching the split this codebase
already established:
  * the real phases RE-RAISE on timeout, so the existing tick-failure
    machinery (backoff, degraded alerts) counts a hung phase as the outage
    it is;
  * the live-equity refresh gets the QUIET maintenance cap, because its own
    code says "non-fatal: use cached value" — and an `except` clause cannot
    catch a hang, so only a cap actually delivers that stated intent.
"""

import ast
import asyncio
from pathlib import Path

import pytest

_SRC = Path("bot/core/engine.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _restore_phase_cap():
    """These tests tune the GLOBAL cap; restore it so a later test in the
    suite can never inherit a 0 (disabled) or 0.05s (instant-timeout) value."""
    from bot.config import CONFIG
    before = CONFIG.monitoring.tick_phase_timeout_sec
    yield
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", before)


def _tick_body() -> str:
    """The source of _tick() alone."""
    start = _SRC.index("    async def _tick(self) -> None:")
    nxt = _SRC.index("\n    async def ", start + 10)
    return _SRC[start:nxt]


def _tick_node() -> ast.AsyncFunctionDef:
    r"""_tick as a syntax tree.

    The regex these tests used to share was `self\._phase\([^,]+,\s*"..."`,
    and `[^,]+` cannot cross a comma — so the moment any phase's first
    argument grew a second argument of its own, that `_phase` call became
    invisible and the phase it names silently left the set. It failed exactly
    that way when `_analyze_signals_batched(signals)` became
    `_analyze_signals_batched(signals, background=True)`: the call was still
    bounded and still named "analyze", and the test reported it missing.

    A test that reads code should parse it. Everything below asks the tree.
    """
    for node in ast.walk(ast.parse(_SRC)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_tick":
            return node
    raise AssertionError("_tick not found in bot/core/engine.py")


def _phase_names() -> set[str]:
    """Every name passed to self._phase(...) inside _tick."""
    names = set()
    for node in ast.walk(_tick_node()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_phase"):
            assert len(node.args) >= 2, "a _phase call has no name argument"
            name = node.args[1]
            assert isinstance(name, ast.Constant) and isinstance(name.value, str), (
                "a phase name is computed rather than literal — the audit line "
                "must say the same thing every time it fires")
            names.add(name.value)
    return names


def _is_awaited_directly(attr: str) -> bool:
    """Is self.<attr>(...) awaited WITHOUT a cap wrapping it?

    Argument-agnostic on purpose. The old string checks named exact call text
    (`await self._analyze_signals_batched(signals)`), so adding any argument
    made an unbounded await invisible to the very test written to forbid it.
    """
    for node in ast.walk(_tick_node()):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == attr):
            return True
    return False


def test_every_await_inside_tick_is_bounded():
    """The regression this prevents: adding a new unguarded await to _tick,
    which would reintroduce an unnamed, 900s-to-recover hang."""
    assert not _is_awaited_directly("_check_open_positions"), \
        "an unbounded position check is an unwatched stop-loss during a hang"
    assert not _is_awaited_directly("scan")
    assert not _is_awaited_directly("_analyze_signals_batched")
    assert _tick_body().count("await self._phase(") >= 6, \
        "positions (x4 paths), scan and analyze are each bounded"


def test_each_phase_is_named():
    named = _phase_names()
    assert "scan" in named and "analyze" in named and "positions" in named
    # The position paths are distinguishable — "hung monitoring positions" is
    # useful; knowing WHICH path is more so.
    assert len([n for n in named if n.startswith("positions")]) >= 3


def test_live_equity_refresh_gets_the_QUIET_cap_matching_its_own_intent():
    body = _tick_body()
    assert 'self._with_maintenance_cap(\n                    self.get_live_equity(), "live equity refresh")' in body \
        or ('_with_maintenance_cap' in body and 'get_live_equity' in body), \
        "its stated intent is non-fatal, and only a cap can deliver that"
    assert "cannot catch a HANG" in body, "the reason is written down"


@pytest.mark.asyncio
async def test_phase_timeout_names_the_phase_and_re_raises():
    """A hung phase must be LOUD: named, and counted as a tick failure."""
    from bot.core.engine import RuneClawEngine

    eng = RuneClawEngine.__new__(RuneClawEngine)
    audited = {}

    import bot.core.engine as eng_mod
    real_audit = eng_mod.audit

    def spy(log, msg, **kw):
        audited["msg"] = msg
        audited["kw"] = kw

    eng_mod.audit = spy
    try:
        from bot.config import CONFIG
        object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.05)

        async def hangs():
            await asyncio.sleep(30)

        with pytest.raises(asyncio.TimeoutError):
            await eng._phase(hangs(), "scan")
    finally:
        eng_mod.audit = real_audit

    assert "scan" in audited["msg"], "the audit line names the phase"
    assert audited["kw"]["result"] == "TIMEOUT"
    assert audited["kw"]["data"]["phase"] == "scan"


@pytest.mark.asyncio
async def test_a_healthy_phase_passes_its_value_through_untouched():
    from bot.core.engine import RuneClawEngine
    from bot.config import CONFIG
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 5.0)
    eng = RuneClawEngine.__new__(RuneClawEngine)

    async def quick():
        return ["sig-a", "sig-b"]

    assert await eng._phase(quick(), "scan") == ["sig-a", "sig-b"]


@pytest.mark.asyncio
async def test_zero_disables_the_cap_entirely():
    """The documented escape hatch: 0 = pre-hardening behaviour."""
    from bot.core.engine import RuneClawEngine
    from bot.config import CONFIG
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.0)
    eng = RuneClawEngine.__new__(RuneClawEngine)

    async def quick():
        return "through"

    assert await eng._phase(quick(), "scan") == "through"


def test_the_phase_cap_fires_before_the_whole_tick_cap():
    """Ordering is the whole point: the phase names the hang, the whole-tick
    cap remains the last-resort recovery."""
    # Re-read defaults from the dataclass rather than a mutated instance.
    import bot.config as cfg_mod
    mon = cfg_mod.MonitoringConfig()
    assert 0 < mon.tick_phase_timeout_sec < mon.tick_hard_timeout_sec, \
        "a phase must time out first, or it can never name the hang"
