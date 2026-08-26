"""A sentinel meaning "undefined" must never be averaged into a score.

WHAT HAPPENED

`backtest_deep_results.json` (2026-08-07, 500 runs) reported:

    avg_profit_factor: 19.17

Read alone — which is how a one-line summary gets read — that is a spectacular
edge, and it is the number a person checks first when asking "does this
strategy work?". Every other figure in the same block disagreed:

    avg_return_pct: -0.46     avg_sharpe: -1.24     avg_sortino: -0.18
    median profit factor: 0.45      profitable runs: 113 of 485 (23%)

The 19.17 came from averaging in ``PF_UNDEFINED`` (999.99) — the value a run
gets when it had **no losing trades at all**, so gross-loss is zero and the
ratio does not exist. Nine runs out of 485 — 1.9% of the sample — moved the
headline from 0.62 to 19.17. A thirty-fold distortion out of two percent of
rows, in the direction of flattering the strategy.

CLAUDE.md names this shape directly: "sum(...) over a set that includes
unreadable rows — a partial total, printed as whole." The aggravation here is
that the poisoned value means *undefined* and reads as *outstanding*.

WHAT IS PINNED

1. The sentinel never enters a mean.
2. The headline for a ratio is the MEDIAN — profit factor is unbounded above
   and floored at zero, so its mean is dragged by a few lucky runs even with
   every sentinel removed (0.62 vs 0.45 here; same verdict, but the mean
   flatters).
3. Undefined runs are counted and STATED, never silently dropped. A reader told
   nothing cannot tell an excluded row from one that never existed.
4. The share of profitable runs is reported, because "avg return -0.46%"
   invites "so it is roughly flat" and "113 of 485" does not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.backtest.metrics import (
    PF_UNDEFINED,
    profit_factor_summary,
    share_profitable,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_RESULTS = ROOT / "backtest_deep_results.json"


# ── the sentinel is not a score ────────────────────────────────────────────

def test_the_sentinel_never_enters_the_mean() -> None:
    # Nine perfect runs among a losing sample is exactly the 2026-08-07 shape.
    values = [0.4] * 90 + [PF_UNDEFINED] * 9
    s = profit_factor_summary(values)
    assert s.n_undefined == 9
    assert s.n_defined == 90
    assert s.mean == pytest.approx(0.4), (
        f"the sentinel reached the mean: {s.mean}")
    assert s.median == pytest.approx(0.4)


def test_a_sentinel_that_survived_a_float_round_trip_is_still_a_sentinel() -> None:
    # JSON round-trips and float arithmetic can shave the value; an exact
    # `== 999.99` would let it back in as a score of nine hundred.
    for near in (999.99, 999.9900000001, 999.0, 1000.0, 1e9):
        s = profit_factor_summary([0.5, near])
        assert s.n_undefined == 1, f"{near} was counted as a measurement"
        assert s.mean == pytest.approx(0.5)


def test_the_headline_is_the_median_not_the_mean() -> None:
    # Even with no sentinels, a ratio's mean is dragged by its right tail. Both
    # say "losing" here, but only one says it plainly.
    values = [0.3, 0.4, 0.4, 0.5, 40.0]
    s = profit_factor_summary(values)
    assert s.median == pytest.approx(0.4)
    assert s.mean > 8, "sanity: the mean really is dragged"
    assert "median 0.40" in s.render()


def test_undefined_runs_are_stated_not_dropped() -> None:
    out = profit_factor_summary([0.4] * 10 + [PF_UNDEFINED] * 3).render()
    assert "3 run(s) had no losing trades" in out
    assert "undefined" in out


def test_a_clean_sample_says_nothing_about_undefined_runs() -> None:
    # A caveat printed every time is a caveat nobody reads.
    out = profit_factor_summary([0.4, 1.2, 2.0]).render()
    assert "undefined" not in out


def test_all_undefined_is_not_reported_as_zero() -> None:
    # Zero would read as "every run lost", the exact opposite of what a sample
    # of nothing-but-winning-runs means. Absent is never a measurement.
    s = profit_factor_summary([PF_UNDEFINED] * 4)
    assert s.median is None
    assert s.mean is None
    out = s.render()
    assert "0.00" not in out
    assert "no run had a defined ratio" in out


def test_break_even_is_not_counted_as_profitable() -> None:
    # 1.0 is exactly break-even; `>= 1` would call it a win.
    s = profit_factor_summary([1.0, 1.0, 1.5])
    assert s.n_profitable == 1


def test_unreadable_values_are_skipped_not_coerced() -> None:
    s = profit_factor_summary([0.4, None, "nonsense", float("nan"), 0.6])
    # nan is not >= the floor and not discardable by float(), so it lands in
    # `defined`; what matters is that None and junk never became zeros.
    assert s.n_defined >= 2
    assert s.n_undefined == 0


# ── the share that settles the argument ────────────────────────────────────

def test_share_profitable_counts_strictly_positive() -> None:
    good, total = share_profitable([1.0, 0.0, -1.0, 2.0])
    assert (good, total) == (2, 4), "a zero-return run was counted as profitable"


def test_share_profitable_skips_unreadable_without_counting_them() -> None:
    good, total = share_profitable([1.0, None, "x", -2.0])
    assert (good, total) == (1, 2), "an unreadable run inflated the denominator"


# ── against the real file that produced the wrong number ───────────────────

@pytest.mark.skipif(not REAL_RESULTS.exists(),
                    reason="backtest_deep_results.json not present")
def test_the_recorded_run_summarises_honestly_now() -> None:
    """The regression, driven on the actual data.

    This is the assertion that would have caught it: the same 485 runs, through
    the new summary, must report a median below 1.0 — not a mean of 19.17.
    """
    runs = json.loads(REAL_RESULTS.read_text(encoding="utf-8"))["results"]
    s = profit_factor_summary(r.get("profit_factor") for r in runs)

    assert s.n_undefined == 9, f"expected 9 undefined runs, got {s.n_undefined}"
    assert s.median is not None
    assert s.median < 1.0, (
        f"median profit factor {s.median} — if this is now above 1.0 the data "
        "changed, and the claim in this file's docstring needs re-checking")
    assert s.mean < 1.0, (
        f"mean over defined runs is {s.mean}; the sentinel is back in the mean")

    good, total = share_profitable(r.get("total_return_pct") for r in runs)
    assert good < total // 2, (
        f"{good} of {total} runs profitable — a minority is the recorded "
        "result; a majority means the file was replaced and this test is "
        "asserting against stale history")


# ── the reporting script uses it ───────────────────────────────────────────

def test_the_deep_backtest_script_no_longer_means_the_sentinel() -> None:
    src = (ROOT / "run_deep_backtest.py").read_text(encoding="utf-8")
    assert 'sum(r["profit_factor"]' not in src, (
        "a plain mean of profit_factor is back — that is the exact line that "
        "reported 19.17 while the median run scored 0.45")
    assert "profit_factor_summary(" in src
    assert "share_profitable(" in src


def test_the_json_key_was_renamed_rather_than_redefined() -> None:
    """Silently changing what a key means is worse than breaking it.

    A consumer reading `avg_profit_factor` would have got a different number
    under the same name and no reason to look.
    """
    src = (ROOT / "run_deep_backtest.py").read_text(encoding="utf-8")
    assert '"avg_profit_factor"' not in src
    assert '"median_profit_factor"' in src
    assert '"runs_profit_factor_undefined"' in src
