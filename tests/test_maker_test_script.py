"""The maker test runs three arms of the live system that differ only in entry fills."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import maker_test as mt  # noqa: E402


def test_arms_match_the_registration_and_the_repos_realistic_fill():
    text = (Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg"
            / "maker_orders_2026-10" / "PREREGISTRATION.md").read_text()
    assert set(mt.ARMS) == {"market", "touch", "realistic", "realistic_taker"}
    assert mt.ARMS["market"]["env"] == {"LIMIT_ORDERS_ENABLED": "0"}
    assert mt.ARMS["touch"] == {"env": {}, "args": []}
    from bot.core.limit_shadow import REALISTIC
    args = mt.ARMS["realistic"]["args"]
    assert args[args.index("--limit-through-bp") + 1] == str(int(REALISTIC.through_bp))
    assert float(args[args.index("--limit-queue-p") + 1]) == REALISTIC.queue_p
    assert args[args.index("--limit-queue-sure-bp") + 1] == str(int(REALISTIC.queue_sure_bp))
    for token in ("--limit-through-bp 5", "--limit-queue-p 0.5", "--limit-queue-sure-bp 10",
                  "LIMIT_ORDERS_ENABLED=0"):
        assert token in text


def test_lifecycle_sums_every_fold_and_a_run_with_no_limits_has_no_fill_rate():
    runs = [{"folds": [{"trades": 10, "ideas": 20, "limits": {"filled": 6, "expired": 3, "cancelled_drift": 1}},
                       {"trades": 5, "ideas": 8, "limits": {"filled": 4, "expired": 0, "cancelled_drift": 0}}]},
            {"folds": [{"trades": 7, "ideas": 9}]}]
    lc = mt.lifecycle(runs)
    assert (lc["trades"], lc["ideas"], lc["filled"], lc["expired"]) == (22, 37, 10, 3)
    assert lc["fill_rate"] == 10 / 14
    assert mt.lifecycle([{"folds": [{"trades": 3, "ideas": 4}]}])["fill_rate"] is None


def test_the_market_arm_turns_the_analyzers_limit_entries_off_not_just_their_default_type():
    """DEFAULT_ORDER_TYPE=market still left limit entries on: the analyzer sets
    order_type to limit whenever it finds a pullback level, so the arm that was
    meant to be all-taker reported a 0.48 limit fill rate."""
    src = (Path(__file__).resolve().parent.parent / "bot" / "core" / "analyzer.py").read_text()
    assert 'if CONFIG.limit_orders.enabled else "market"' in src
    assert "DEFAULT_ORDER_TYPE" not in mt.ARMS["market"]["env"]


def test_skip_arms_use_the_registered_rates_and_three_seeds():
    text = (Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg"
            / "maker_orders_2026-10" / "PREREGISTRATION.md").read_text()
    for ds, p in mt.SKIP_P.items():
        assert f"| {ds} |" in text and f"| {p:.3f} |" in text, (ds, p)
    assert mt.SKIP_ARMS == ("skip_s0", "skip_s1", "skip_s2")
    spec = mt.arm_spec("skip_s2", "majors_1h_v2")
    assert spec["env"] == {"LIMIT_ORDERS_ENABLED": "0"}
    assert spec["args"] == ["--entry-skip-p", "0.51", "--entry-skip-seed", "2"]
    assert mt.arm_spec("realistic", "alts_1h_v3") == mt.ARMS["realistic"]


def test_average_runs_is_the_foldwise_mean():
    def f(k, r, dd, n):
        return {"fold": k, "oos_start": "a", "oos_end": "b",
                "return_pct": r, "max_dd_pct": dd, "trades": n}

    avg = mt.average_runs([{"folds": [f(0, -2.0, 4.0, 10), f(1, 1.0, 2.0, 6)]},
                           {"folds": [f(0, -4.0, 6.0, 20), f(1, 3.0, 4.0, 8)]}])
    assert [(x["fold"], x["return_pct"], x["max_dd_pct"], x["trades"]) for x in avg["folds"]] == [
        (0, -3.0, 5.0, 15.0), (1, 2.0, 3.0, 7.0)]


def test_next_p_moves_toward_the_target_and_stays_in_bounds():
    # more positions than the target: skip more; fewer: skip less
    assert mt.next_p([(0.30, 600.0)], 500, 700) > 0.30
    assert mt.next_p([(0.30, 400.0)], 500, 700) < 0.30
    # secant on two points: counts 600 at 0.2 and 500 at 0.4 -> target 550 is p 0.3
    assert mt.next_p([(0.2, 600.0), (0.4, 500.0)], 550, 700) == 0.3
    # a flat or upward slope falls back to the first-step rule, never divides by zero
    assert 0.0 <= mt.next_p([(0.3, 500.0), (0.3, 500.0)], 400, 700) <= 0.95
    assert mt.next_p([(0.9, 900.0)], 100, 100) == 0.95          # clamped high
    assert mt.next_p([(0.01, 10.0)], 500, 100) == 0.0            # clamped low


def test_the_tolerance_is_five_percent_or_three_positions():
    # 5% of 495 is 24.75; 5% of 80 is 4, above the 3-position floor
    assert mt.within_tolerance(519, 495) and not mt.within_tolerance(520, 495)
    assert mt.within_tolerance(84, 80) and not mt.within_tolerance(85, 80)
    # a tiny target falls back to the 3-position floor
    assert mt.within_tolerance(13, 10) and not mt.within_tolerance(14, 10)


def test_matched_arms_need_a_calibrated_rate_and_use_the_targets_on_file():
    import pytest
    with pytest.raises(KeyError):
        mt.arm_spec("skipm_s1", "alts_1h_v3")
    spec = mt.arm_spec("skipm_s1", "alts_1h_v3", 0.123)
    assert spec["args"] == ["--entry-skip-p", "0.123", "--entry-skip-seed", "1"]
    text = (Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg"
            / "maker_orders_2026-10" / "PREREGISTRATION.md").read_text()
    for ds, n in mt.TARGETS.items():
        assert f"{ds} {n}" in text.replace(",", ""), (ds, n)


def test_the_taker_arm_is_the_realistic_arm_plus_the_cost_flag_and_nothing_else():
    real, taker = mt.ARMS["realistic"], mt.ARMS[mt.TAKER_ARM]
    assert taker["env"] == real["env"]
    assert taker["args"] == [*real["args"], "--limit-fills-pay-taker"]
    # the original three-arm report and run do not pick it up by accident
    assert mt.TAKER_ARM not in [a for a in mt.ARMS if a != mt.TAKER_ARM]
    text = (Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg"
            / "maker_orders_2026-10" / "PREREGISTRATION.md").read_text()
    assert "realistic_taker" in text and "--limit-fills-pay-taker" in text
