"""A halt that arrives while the order is being prepared must still stop it.

THE RACE (audit M18)

The engine checks `_halted` immediately before calling `executor.execute()`.
`execute()` then does real network work before it submits anything —
`load_markets`, `_ensure_leverage`, `fetch_ticker` — and none of it is
cancellable. Nothing between the engine's check and the order re-read the flag,
so a `/halt` or a breaker trip landing inside that window opened a live position
on an account somebody had just stopped.

The upstream check was not an oversight; it was believed sufficient. The
exclusion comment in `guard_lint.py` said so in as many words — "the normal open
path is gated upstream at engine.py's last-mile check" — which is the defect
written down as a justification, and is why this is driven rather than read.

WORSE THAN A STRAY POSITION

`emergency_halt_all` sets the flag and then awaits `flatten_all_positions`,
which iterates a SNAPSHOT of `self._positions`. An order landing after that
snapshot is not in it, so the flatten walks straight past. The position is still
tracked and still gets SL/TP — it is not naked — but the operator asked for
everything closed and one thing stayed open.

WHAT MUST NOT BREAK

Reducing paths keep working while halted, by design: `_place_sl_tp`,
`_partial_close`, `_update_exchange_sl`, `_close_position_inner`. Gating those
would stop a protective stop-loss being placed during a kill switch, which is
strictly worse than the bug being fixed. That is also why the fix lives at the
two entry CALL SITES and not inside `_create_order_idempotent`: the transport
helper must stay usable by a path that reduces exposure.
"""
from __future__ import annotations

import pytest

from tests.dep_policy import require

require("ccxt", "live_executor imports it")   # pinned: absent ⇒ fail, not skip

from unittest.mock import patch  # noqa: E402

from bot.core import live_executor as le  # noqa: E402
from bot.core.live_executor import LiveExecutor  # noqa: E402
from bot.utils.models import Direction, TradeIdea  # noqa: E402


def _idea() -> TradeIdea:
    return TradeIdea(
        id="TI-HALT-001", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100_000.0, stop_loss=98_000.0, take_profit=105_000.0,
        confidence=0.85, reasoning="halt-race fixture",
    )


def _exchange():
    """A mock exchange, borrowed from the live_executor suite's builder."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_le_fixtures",
        pathlib.Path(__file__).resolve().parent / "test_live_executor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._mock_exchange()


def _executor():
    ex = _exchange()
    ex_owner = LiveExecutor()
    ex_owner._exchange = ex
    return ex_owner, ex


@pytest.fixture(autouse=True)
def _live_and_clean():
    from bot.config import CONFIG
    with patch.object(type(CONFIG), "is_live", return_value=True):
        yield
    le.set_halt_check(None)


# ── the race, driven ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_halt_landing_during_preparation_blocks_the_order():
    """THE test. Halt is off when execute() starts — so the engine's upstream
    check passed — and flips on during a preparation await, exactly as a /halt
    tapped a moment after a confirm would."""
    ex_owner, ex = _executor()
    halted = {"on": False}
    le.set_halt_check(lambda: halted["on"])

    _real_ticker = ex.fetch_ticker

    async def _operator_taps_halt(*a, **kw):
        halted["on"] = True
        return await _real_ticker(*a, **kw)

    ex.fetch_ticker = _operator_taps_halt

    result = await ex_owner.execute(_idea(), size_usd=10.0)

    ex.create_order.assert_not_called()
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_the_refusal_says_no_exposure_was_opened():
    """An operator reading this has just hit the kill switch and needs to know
    whether they now have a position. 'Failed' would not answer that."""
    ex_owner, ex = _executor()
    le.set_halt_check(lambda: True)
    result = await ex_owner.execute(_idea(), size_usd=10.0)
    assert "No exposure was opened" in result


@pytest.mark.asyncio
async def test_an_unreadable_halt_state_also_blocks():
    """trading_halted() fails closed, and the entry path inherits that. Opening
    on an account whose halt state cannot be read is the expensive direction."""
    ex_owner, ex = _executor()

    def _boom():
        raise RuntimeError("risk engine unavailable")

    le.set_halt_check(_boom)
    result = await ex_owner.execute(_idea(), size_usd=10.0)
    ex.create_order.assert_not_called()
    assert "BLOCKED" in result


# ── and it still opens when it should ────────────────────────────────

@pytest.mark.asyncio
async def test_an_unhalted_entry_still_places_its_order():
    """The half that makes the other half meaningful. A check that blocked
    everything would pass every test above and stop the bot trading."""
    ex_owner, ex = _executor()
    le.set_halt_check(lambda: False)
    result = await ex_owner.execute(_idea(), size_usd=10.0)
    ex.create_order.assert_called()
    assert "BLOCKED" not in result


@pytest.mark.asyncio
async def test_an_unwired_halt_check_does_not_block():
    """Standalone use — tests, scripts — must behave as before. Reachability is
    guard_lint's job, not a runtime surprise for every existing caller."""
    ex_owner, ex = _executor()
    le.set_halt_check(None)
    result = await ex_owner.execute(_idea(), size_usd=10.0)
    ex.create_order.assert_called()
    assert "BLOCKED" not in result


# ── the reducing paths are untouched ─────────────────────────────────

def test_the_transport_helper_is_not_gated():
    """`_create_order_idempotent` must stay usable by a path that REDUCES
    exposure. Gating it is the plausible fix that would stop a stop-loss being
    placed during a halt — strictly worse than the bug. The gate is at the two
    entry call sites instead, which guard_lint's `entry-order-halt` enforces."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "bot" / "core" / "live_executor.py").read_text()
    body = {n.name: (ast.get_source_segment(src, n) or "")
            for n in ast.walk(ast.parse(src))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "trading_halted()" not in body["_create_order_idempotent"], (
        "the transport helper is now gated; a reducing path routed through it "
        "would be refused during a halt")


def test_every_entry_submission_is_preceded_by_a_check():
    """STRUCTURAL, and deliberately so.

    There are two submission sites in `execute()`: the entry order, and the
    POST_ONLY retry. The first is driven above. Reaching the second needs the
    venue to reject a post-only order and `_find_order_by_client_oid` to confirm
    it never landed — a path this suite cannot stage honestly, and the retry has
    the WIDER window of the two, since getting there costs a round trip plus
    that lookup.

    guard_lint's rule is function-granular, so one check anywhere in `execute()`
    satisfies it. This pins the position: every submission has a halt check
    between it and the one before.
    """
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "bot" / "core" / "live_executor.py").read_text()
    body = next(ast.get_source_segment(src, n)
                for n in ast.walk(ast.parse(src))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "execute")

    def _at(needle):
        out, i = [], body.find(needle)
        while i != -1:
            out.append(i)
            i = body.find(needle, i + 1)
        return out

    submits = _at("_create_order_idempotent(exchange")
    checks = _at("trading_halted()")
    assert len(submits) == 2, f"expected 2 entry submissions, found {len(submits)}"
    prev = 0
    for n, site in enumerate(submits):
        assert any(prev <= c < site for c in checks), (
            f"entry submission #{n + 1} has no halt check between it and the "
            "previous one — a halt arriving in that window opens a position")
        prev = site


def test_guard_lint_enforces_the_call_site():
    """The rule has to exist AND bite. Without it the next entry path added
    inherits the original defect — which is how this one survived: the upstream
    check was assumed sufficient and written down as the reason to skip."""
    import importlib.util
    import pathlib
    import sys
    repo = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_guard_lint", repo / "scripts" / "guard_lint.py")
    gl = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves the defining class through
    # sys.modules, so an unregistered module makes the decorator itself raise.
    sys.modules[spec.name] = gl
    try:
        spec.loader.exec_module(gl)
    finally:
        sys.modules.pop(spec.name, None)
    rule = next((r for r in gl.RULES if r.name == "entry-order-halt"), None)
    assert rule is not None, "the entry-order-halt rule is gone"
    assert "trading_halted" in rule.guard
    assert "_create_order_idempotent" not in rule.exclude_functions, (
        "excluding the call sites would make the rule inert")
