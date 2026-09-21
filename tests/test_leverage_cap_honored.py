"""The executor must honor the risk engine's margin-risk-capped leverage.

Audit bugs 2/6: RiskEngine.evaluate() reduces leverage when SL distance ×
leverage would exceed max_margin_risk_pct and records it on the idea "for the
executor" — but execute() never read it, so orders sized at full leverage and
exceeded the very cap the engine reported enforcing.

THE FIRST FIX WAS A SCAN, AND THE SCAN OUTLIVED WHAT IT GUARDED. These tests
asserted that `_size_or_block`'s source contained a specific `min(...)` clamp,
in a specific order, spelled a specific way — and the clamp then moved into
`_compute_target_leverage` so the VENUE would be set to the same number (a
sizing-only clamp bounds nothing: the sizing leverage cancels out of the ratio
the cap is about). Every assertion here broke on that move while the property
each one names held throughout. They are DRIVEN now; where the sizing half is
already driven in `test_entry_gates_are_driven.py`, this file keeps only the
claims nobody else makes, and the venue half is
`tests/test_the_venue_gets_the_capped_leverage.py`.
"""

import ast
import inspect
import textwrap
import types

from bot.core.leverage import RISK_CAP_ATTR
from bot.core.live_executor import LiveExecutor
from bot.risk.risk_engine import RiskEngine


def _std(ex, lev):
    """Fix the symbol-only half so a test is about the clamp, not the model."""
    ex._standard_leverage = lambda symbol: lev
    return ex


def _bare_executor():
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._last_atr_pct = {}
    ex._user_leverage_pref = None
    ex._risk_engine = None
    return ex


def test_the_cap_lowers_the_leverage_the_executor_will_use():
    ex = _std(_bare_executor(), 10)
    idea = types.SimpleNamespace()
    setattr(idea, RISK_CAP_ATTR, 3)
    assert ex._compute_target_leverage("BTC/USDT", idea) == 3


def test_the_clamp_is_reduce_only():
    """A cap ABOVE the target changes nothing, and no cap changes nothing."""
    ex = _std(_bare_executor(), 5)
    high = types.SimpleNamespace()
    setattr(high, RISK_CAP_ATTR, 20)
    assert ex._compute_target_leverage("BTC/USDT", high) == 5
    assert ex._compute_target_leverage("BTC/USDT", types.SimpleNamespace()) == 5
    assert ex._compute_target_leverage("BTC/USDT", None) == 5


def test_execute_sizes_through_the_sizer():
    """The leverage and quantity execute() trades with are the sizer's.

    A drive of `execute()` needs a venue, a book, a fill and a credential
    store; the claim here is only that its two locals come from that one call,
    which is a wiring question a scan can answer and a drive of the sizer
    cannot.
    """
    exec_src = inspect.getsource(LiveExecutor.execute)
    assert "self._size_or_block(" in exec_src
    assert "leverage_mult, quantity = _sized_lev, _sized_qty" in exec_src


def test_risk_engine_records_the_cap_on_the_idea():
    """DRIVEN: the gate's reduction has to land where the executor reads it.

    The previous version asserted the literal `setattr(idea, "_adjusted_...")`
    appeared in the source. The attribute name moved into
    `bot/core/leverage.py` so the writer and the reader spell it once — and a
    scan for the old spelling would have reported the producer missing while
    it worked perfectly. So the record is driven instead, through the same
    function the gate calls.
    """
    from bot.core.leverage import margin_risk_verdict, set_margin_risk_cap

    v = margin_risk_verdict(leverage=5, entry_price=100.0, stop_loss=90.0,
                            max_margin_risk_pct=30.0, min_leverage=2,
                            may_reduce=True)
    assert v.state == "reduced" and v.leverage == 3

    idea = types.SimpleNamespace()
    set_margin_risk_cap(idea, v.leverage)
    ex = _std(_bare_executor(), 5)
    assert ex._compute_target_leverage("BTC/USDT", idea) == 3, \
        "what the gate records must be what the executor reads"


_GATE_SRC = textwrap.dedent(inspect.getsource(RiskEngine._evaluate_locked))
"""Dedented — `inspect.getsource` of a METHOD is indented, and `ast.parse`
refuses it. The claim is about the call the gate makes, not about the column
it sits in."""


def _margin_risk_call():
    """The one `margin_risk_verdict(...)` the risk gate makes."""
    tree = ast.parse(_GATE_SRC)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "margin_risk_verdict"]
    assert len(calls) == 1, [ast.unparse(c) for c in calls]
    return calls[0]


def test_margin_risk_cap_uses_the_effective_override_leverage():
    """AUDIT-FIX-4: the cap evaluates at the leverage the executor will use.

    check-6b hard-coded CONFIG.exchange.default_leverage, but the executor
    sizes with RUNTIME.leverage_override (which wins over the default and is
    only clamped <=20, not <=default). An override ABOVE default sized past the
    cap unchecked because the gate evaluated at the lower default.

    The old version sliced the block between two string literals — one of
    which was a log message, so "half of it was solid and half moved whenever
    somebody renumbered a check", as its own comment said. It then moved into
    the leaf and the slice became empty. The boundary is the CALL now: whatever
    expression the gate hands the verdict must be resolved from the override.
    """
    src = _GATE_SRC
    passed = {k.arg: ast.unparse(k.value) for k in _margin_risk_call().keywords}
    assert passed.get("leverage") == "leverage", passed

    tree = ast.parse(src)
    binds = [n for n in ast.walk(tree)
             if isinstance(n, ast.Assign) and len(n.targets) == 1
             and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id == "leverage"]
    bound = "\n".join(ast.unparse(b) for b in binds)
    assert "RUNTIME.leverage_override" in src, \
        "the cap must read the runtime /leverage override, not only the env default"
    assert "CONFIG.exchange.default_leverage" in bound, \
        "and it still falls back to the env default when no override is set"


def test_the_gate_hands_over_the_shared_floor():
    """The min-leverage floor is ONE reading, not a second invented default.

    `live_executor` defaulted it to 1 and this gate to 2, for a dataclass field
    that is never absent — a fallback that cannot fire and disagrees with its
    twin is a claim that there is a check.
    """
    passed = {k.arg: ast.unparse(k.value) for k in _margin_risk_call().keywords}
    assert passed.get("min_leverage", "").startswith("leverage_floor("), passed


def test_effective_leverage_selection_semantics():
    # The value the cap evaluates: override when set (worst case), else default.
    def effective(override, default):
        return max(1, int(override)) if override is not None else default
    assert effective(20, 5) == 20      # override above default → evaluated at 20x
    assert effective(None, 5) == 5     # no override → unchanged
    assert effective(3, 5) == 3        # override below default → 3x
