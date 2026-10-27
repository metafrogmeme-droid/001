"""The maker test runs three arms of the live system that differ only in entry fills."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import maker_test as mt  # noqa: E402


def test_arms_match_the_registration_and_the_repos_realistic_fill():
    text = (Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg"
            / "maker_orders_2026-10" / "PREREGISTRATION.md").read_text()
    assert set(mt.ARMS) == {"market", "touch", "realistic"}
    assert mt.ARMS["market"]["env"] == {"DEFAULT_ORDER_TYPE": "market"}
    assert mt.ARMS["touch"] == {"env": {}, "args": []}
    from bot.core.limit_shadow import REALISTIC
    args = mt.ARMS["realistic"]["args"]
    assert args[args.index("--limit-through-bp") + 1] == str(int(REALISTIC.through_bp))
    assert float(args[args.index("--limit-queue-p") + 1]) == REALISTIC.queue_p
    assert args[args.index("--limit-queue-sure-bp") + 1] == str(int(REALISTIC.queue_sure_bp))
    for token in ("--limit-through-bp 5", "--limit-queue-p 0.5", "--limit-queue-sure-bp 10",
                  "DEFAULT_ORDER_TYPE=market"):
        assert token in text


def test_lifecycle_sums_every_fold_and_a_run_with_no_limits_has_no_fill_rate():
    runs = [{"folds": [{"trades": 10, "ideas": 20, "limits": {"filled": 6, "expired": 3, "cancelled_drift": 1}},
                       {"trades": 5, "ideas": 8, "limits": {"filled": 4, "expired": 0, "cancelled_drift": 0}}]},
            {"folds": [{"trades": 7, "ideas": 9}]}]
    lc = mt.lifecycle(runs)
    assert (lc["trades"], lc["ideas"], lc["filled"], lc["expired"]) == (22, 37, 10, 3)
    assert lc["fill_rate"] == 10 / 14
    assert mt.lifecycle([{"folds": [{"trades": 3, "ideas": 4}]}])["fill_rate"] is None
