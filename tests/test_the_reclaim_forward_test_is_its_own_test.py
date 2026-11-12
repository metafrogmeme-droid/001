"""The corrected reclaim variants are a new forward test, not members of the old one.

Owner decision of 2026-11-12: after the reclaim fill was fixed (PR #773), V1
and V3 as they now fill are pre-registered fresh, with their own cutoff and
their own N. The original test's record stays exactly as written, labelled
pre-fix. These tests hold both halves: the new test's rules are the ones its
document states, and the old record is byte-for-byte the one that was sealed.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from bot.core import limit_shadow as ls
from bot.core.limit_scanner import ScannerParams

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / "docs" / "research" / "prereg" / "limit_scanner_reclaim_forward" / "PREREGISTRATION.md"
OLD = ROOT / "docs" / "research" / "prereg" / "limit_scanner_forward"

# The original record as sealed. A change to any of these is a change to a
# pre-registration that has already been read, and this test refuses it.
SEALED = {
    "PREREGISTRATION.md": "d6e0db90ad2caa1415307cfed41bff303e77101518d6c5601c3d234688889b5b",
    "POSTHOC.md": "594525e8bbbef142e27beb8a2a3852c3f127dd508781161acc0c7e633b744ff1",
    "AMENDMENT_2026-11-11.md": "061823808f9583ae9e14be956792232ed08ca07f45b966a4263746a0bfdb0fb7",
}


def _trade(fill_ms, decision_ms=None):
    return {"fill_ms": fill_ms, "decision_ms": fill_ms - 3_600_000 if decision_ms is None else decision_ms,
            "net_bp": 5.0, "gross_bp": 10.0, "symbol": "ETH", "funding": "unpriced"}


def test_the_new_test_has_its_own_cutoff_and_its_own_n():
    assert ls.RECLAIM_FORWARD_CUTOFF == "2026-11-12T00:00:00+00:00"
    assert ls.RECLAIM_FORWARD_VARIANTS == ("V1", "V3")
    assert ls.N_RECLAIM_FORWARD == len(ls.RECLAIM_FORWARD_VARIANTS) == 2
    assert ls.RECLAIM_FORWARD_CUTOFF_MS > ls.FORWARD_CUTOFF_MS


def test_its_variants_fill_at_the_reclaim_close():
    for name in ls.RECLAIM_FORWARD_VARIANTS:
        params, spec = ls.variant_params(ScannerParams(), name)
        assert params.reclaim_fill and spec.reclaim, name


def test_the_new_cutoff_refuses_a_trade_the_old_one_accepts():
    between = ls.RECLAIM_FORWARD_CUTOFF_MS - 3_600_000      # after 10-23, before 11-12
    after = ls.RECLAIM_FORWARD_CUTOFF_MS + 3_600_000
    new = dict(cutoff_ms=ls.RECLAIM_FORWARD_CUTOFF_MS, cutoff=ls.RECLAIM_FORWARD_CUTOFF,
               n_trials=ls.N_RECLAIM_FORWARD)
    refused = ls.evaluate_forward([_trade(between)], funding_priced=True, **new)
    assert refused["verdict"] == "refused" and "2026-11-12" in refused["reason"]
    read = ls.evaluate_forward([_trade(after)], funding_priced=True, **new)
    assert read["verdict"] == "inconclusive" and read["n"] == 1
    assert read["cutoff"] == ls.RECLAIM_FORWARD_CUTOFF and read["n_trials"] == 2
    # The original test, called as it always was, still reads the trade the
    # new one refuses.
    old = ls.evaluate_forward([_trade(between)], funding_priced=True)
    assert old["verdict"] == "inconclusive" and old["cutoff"] == ls.FORWARD_CUTOFF
    assert old["n_trials"] == ls.N_PREREGISTERED == 4


def test_an_empty_new_window_names_its_own_cutoff():
    empty = ls.evaluate_forward([], funding_priced=False, cutoff_ms=ls.RECLAIM_FORWARD_CUTOFF_MS,
                                cutoff=ls.RECLAIM_FORWARD_CUTOFF, n_trials=ls.N_RECLAIM_FORWARD)
    assert empty["mean_net_bp"] is None and "2026-11-12" in empty["reason"]


def test_the_document_states_the_rules_the_code_runs():
    text = NEW.read_text(encoding="utf-8")
    assert ls.RECLAIM_FORWARD_CUTOFF in text
    assert re.search(rf"\bN is {ls.N_RECLAIM_FORWARD}\b", text)
    for name in ls.RECLAIM_FORWARD_VARIANTS:
        assert f"**{name}" in text, name
    assert f"at least {ls.MIN_TRADES} closed trades" in text
    assert f"at least {ls.DSR_MIN}" in text


def test_the_measure_script_scores_the_corrected_reclaim_in_its_own_test():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "limit_scanner_measure", ROOT / "scripts" / "limit_scanner_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bars = {"ETH/USDT:USDT": [(1_700_000_000_000 + i * 3_600_000, 100.0, 101.0, 99.0, 100.0, 1.0)
                              for i in range(5)]}
    v1 = mod._row("V1_reclaim", *ls.variant_params(ScannerParams(), "V1"), bars)
    v0 = mod._row("V0_fixed_realistic", *ls.variant_params(ScannerParams(), "V0"), bars)
    assert v1["forward_test"] == "limit_scanner_reclaim_forward"
    assert v1["forward"]["cutoff"] == ls.RECLAIM_FORWARD_CUTOFF and v1["forward"]["n_trials"] == 2
    assert v0["forward_test"] == "limit_scanner_forward"
    assert v0["forward"]["cutoff"] == ls.FORWARD_CUTOFF and v0["forward"]["n_trials"] == 4


def test_the_original_record_is_the_one_that_was_sealed_and_says_it_is_pre_fix():
    for name, digest in SEALED.items():
        assert hashlib.sha256((OLD / name).read_bytes()).hexdigest() == digest, name
    label = (OLD / "PRE_FIX.md").read_text(encoding="utf-8")
    assert "pre-fix" in label.lower() and "limit_scanner_reclaim_forward" in label
