"""A chart pattern's price levels are recorded, not rounded to the cent.

#509 sent every pattern description through `fmt_price`, so a sub-cent level
stopped printing as $0.00. The numbers beside the sentence did not move:
`detect_fibonacci_extensions` still wrote its `key_levels` with
`round(x, 2)`, and those levels, not the sentence, are what the analyzer's
limit entry harvests as candidates (it keeps a candidate only when it is
above zero) and what /api/patterns forwards. On a sub-cent symbol every
extension was 0.0 and dropped without a word; on a sub-dollar one each level
was off by up to half a cent and could bind as the limit price. The ending
diagonal's convergence point was the same rounding, one detector over.
`record_level` is the repo's reading of a recorded level.
"""
import ast
import re
from pathlib import Path

import numpy as np
import pytest

from bot.core.chart_patterns import detect_elliott_diagonal, detect_fibonacci_extensions
from bot.core.signal_levels import record_level

ROOT = Path(__file__).resolve().parent.parent
_RATIOS = (1.000, 1.272, 1.618, 2.000, 2.618)


def _drive(swings, price):
    n = 10
    arr = np.full(n, price)
    return detect_fibonacci_extensions(arr, arr, arr, swings=swings)


def _bull(start, high, retrace):
    return _drive({"swing_lows": [(1, start), (7, retrace)], "swing_highs": [(4, high)]}, retrace)


def _bear(start, low, retrace):
    return _drive({"swing_highs": [(1, start), (7, retrace)], "swing_lows": [(4, low)]}, retrace)


@pytest.mark.parametrize("build,start,mid,retrace,sign", [
    (_bull, 0.0000100, 0.0000120, 0.0000112, 1),
    (_bear, 0.0000120, 0.0000100, 0.0000108, -1),
])
def test_a_sub_cent_extension_is_a_level_not_zero(build, start, mid, retrace, sign):
    r = build(start, mid, retrace)
    assert r is not None
    impulse = abs(mid - start)
    for ratio in _RATIOS:
        got = r["key_levels"][f"ext_{ratio:.3f}"]
        exact = retrace + sign * impulse * ratio
        assert got > 0, f"ext_{ratio:.3f} is a level, not 0.0"
        assert got == record_level(exact)
        assert abs(got - exact) / exact < 1e-5


def test_a_sub_dollar_extension_keeps_its_sub_cent_digits():
    r = _bull(0.3500, 0.3900, 0.3721)
    assert r["key_levels"]["ext_1.000"] == pytest.approx(0.4121, abs=1e-12)
    assert r["key_levels"]["ext_1.000"] != 0.41
    # A price above a dollar records as before, at six places.
    big = _bull(100.0, 120.0, 112.0)
    assert big["key_levels"]["ext_1.618"] == round(112.0 + 20.0 * 1.618, 6)


def test_a_sub_cent_ending_diagonal_converges_on_a_level_not_zero():
    sw = {"swing_lows": [(0, 0.0000100), (4, 0.0000110), (8, 0.0000120)],
          "swing_highs": [(2, 0.0000140), (6, 0.0000145), (10, 0.0000150)]}
    a = np.full(12, 0.0000150)
    r = detect_elliott_diagonal(a, a, a, swings=sw)
    assert r is not None and r["name"] == "Elliott Ending Diagonal"
    exact = 0.0000150 + (0.0000150 - 0.0000145) * 0.5
    got = r["key_levels"]["convergence_point"]
    assert got > 0, "the convergence point is a level, not 0.0"
    assert got == record_level(exact)
    assert abs(got - exact) / exact < 1e-5


# ── the rule over the file ──────────────────────────────────────────────

#: A `round()` in chart_patterns.py is a confidence or a ratio, never a price.
_SCALE_FREE = re.compile(r"(^confidence$|_fib$|_retrace$|ratio$)")


def _rounded_keys(tree):
    for node in ast.walk(tree):
        pairs = []
        if isinstance(node, ast.Dict):
            pairs = list(zip(node.keys, node.values))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Subscript):
            pairs = [(node.targets[0].slice, node.value)]
        for key, value in pairs:
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "round"):
                name = key.value if isinstance(key, ast.Constant) else ast.unparse(key)
                yield node.lineno, str(name)


def _offences(src):
    return [(ln, k) for ln, k in _rounded_keys(ast.parse(src)) if not _SCALE_FREE.search(k)]


def test_no_pattern_level_is_rounded():
    src = (ROOT / "bot/core/chart_patterns.py").read_text()
    seen = list(_rounded_keys(ast.parse(src)))
    assert len(seen) >= 20, "the walk found the confidences and ratios it exists to pass"
    assert _offences(src) == []


def test_the_rule_reports_a_planted_level_and_passes_a_ratio():
    planted = 'r = {"confidence": round(c, 2), "key_levels": {"ext_1.000": round(x, 2)}}\n'
    assert _offences(planted) == [(1, "ext_1.000")]
    sub = 'levels["neckline"] = round(n, 2)\nlevels["c_extension_fib"] = round(f, 3)\n'
    assert _offences(sub) == [(1, "neckline")]
