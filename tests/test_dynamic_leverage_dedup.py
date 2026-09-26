"""
Unified dynamic-leverage computation (deep-audit medium).

The set-leverage path (_ensure_leverage) and the sizing path (execute) each had
their own copy of the dynamic-leverage logic, and they had DIVERGED: the set
path still scaled leverage UP ×1.4 in low vol while sizing was reduce-only, so
the leverage SET on the exchange disagreed with the leverage used to SIZE the
order. Both now call the single reduce-only _compute_target_leverage, which
never exceeds the configured default.
"""

import ast
import inspect
import textwrap
from types import SimpleNamespace

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor


class _AnyATR(dict):
    """dict whose .get() always yields a fixed ATR regardless of the symbol key."""
    def __init__(self, val):
        super().__init__()
        self._val = val

    def get(self, key, default=None):
        return self._val


def _exec(monkeypatch, *, default_lev=10, min_lev=2, enabled=True, atr=0.02):
    executor = LiveExecutor()
    executor._last_atr_pct = _AnyATR(atr)
    monkeypatch.setattr(live_executor_mod, "CONFIG", SimpleNamespace(
        exchange=SimpleNamespace(
            default_leverage=default_lev, min_leverage=min_lev,
            dynamic_leverage_enabled=enabled)))
    return executor


class TestComputeTargetLeverage:
    def test_disabled_returns_default(self, monkeypatch):
        ex = _exec(monkeypatch, enabled=False, atr=0.005)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_high_vol_halves(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.05)  # >4% ATR
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 5

    def test_elevated_vol_scales_07(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.035)  # 3–4% ATR → int(10*0.7)=7
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 7

    def test_low_vol_does_not_upscale(self, monkeypatch):
        # THE divergence regression: low vol must KEEP the default, never ×1.4.
        ex = _exec(monkeypatch, atr=0.005)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_normal_vol_keeps_default(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.02)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_never_exceeds_default(self, monkeypatch):
        # Across a sweep of ATRs the result is always ≤ default and ≥ 1.
        for atr in (0.0, 0.005, 0.02, 0.031, 0.05, 0.2):
            ex = _exec(monkeypatch, atr=atr)
            lev = ex._compute_target_leverage("BTC/USDT:USDT")
            assert 1 <= lev <= 10

    def test_min_leverage_floor(self, monkeypatch):
        # default 3, high vol → 3//2=1 → floored to min_lev=2.
        ex = _exec(monkeypatch, default_lev=3, min_lev=2, atr=0.05)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 2

    def test_exception_falls_back_to_one(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.02)
        monkeypatch.setattr(live_executor_mod, "normalize_symbol",
                            lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 1


def _decides_leverage(method) -> bool:
    """Does this method ASK for the leverage rather than work one out?

    An AST walk rather than a literal, because the literal is what rotted:
    both pins here spelled `self._compute_target_leverage(symbol)`, and the
    call grew an `idea` argument the day the margin-risk cap had to reach the
    venue — so they failed on a rename while the property they name held
    throughout. A guard written against one SPELLING is not a guard about the
    claim.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    return any(isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_compute_target_leverage"
               and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "self"
               for n in ast.walk(tree))


class TestBothPathsUseHelper:
    def test_set_path_uses_helper(self):
        assert _decides_leverage(LiveExecutor._ensure_leverage)
        # The old up-scaling branch is gone.
        assert "* 1.4" not in inspect.getsource(LiveExecutor._ensure_leverage)

    def test_the_other_set_path_uses_helper(self):
        # The venue-neutral ccxt path. It was never pinned here, and it is the
        # one a non-Bitget venue takes — so a leverage rule that reached only
        # the Bitget topology would have been invisible from this file.
        assert _decides_leverage(LiveExecutor._ensure_leverage_generic)

    def test_size_path_uses_helper(self):
        # The size path is `_size_or_block`, extracted from execute() verbatim.
        assert _decides_leverage(LiveExecutor._size_or_block)
        assert "self._size_or_block(" in inspect.getsource(LiveExecutor.execute)

    def test_nothing_else_decides_a_leverage(self):
        """Exactly four methods ask, and no fifth quietly works one out.

        The number is DERIVED — a list of them would be the shape where the
        one added tomorrow is the one missing from it. `execute` is the fourth:
        it reads the leverage ONCE per order and hands that number to the set
        and to the sizing, because each reading it for itself let a
        `/leverage` change between them set the venue to one leverage and
        size the order at another
        (tests/test_the_placed_order_is_the_checked_order.py). The other three
        still read it when no order hands them one.
        """
        tree = ast.parse(inspect.getsource(live_executor_mod))
        asks = sorted(
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_compute_target_leverage"
                    for n in ast.walk(fn))
        )
        assert asks == ["_ensure_leverage", "_ensure_leverage_generic",
                        "_size_or_block", "execute"], asks
