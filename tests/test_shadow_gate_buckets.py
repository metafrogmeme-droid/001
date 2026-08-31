"""One gate is one bucket, whatever reading tripped it.

Found while fixing the doubled "LIQUIDITY: LIQUIDITY:" label, and worse than
the thing that led to it. The shadow book keys each blocked trade on
``gates[0]`` — a string that carries the MEASURED VALUE:

    CONFIDENCE: 0.55 < 0.6 minimum     11 trades
    CONFIDENCE: 0.57 < 0.6 minimum      8
    CONFIDENCE: 0.56 < 0.6 minimum      8
    CONFIDENCE: 0.59 < 0.6 minimum      4
    ... four more

That is the live ledger on 2026-08-31: ONE gate, the confidence floor, split
across nine buckets by the reading that tripped it.

WHY IT MATTERS RATHER THAN JUST LOOKING UNTIDY. The nightly self-audit takes
``next(iter(gate_report()))`` — the single highest-net_r bucket — and prints
it as "the costliest gate ... over N blocked trades". So it named a shard of
4 and presented it as the whole: a partial printed as a total, in the
scoreboard that decides which gate to loosen on a live trading bot. The true
aggregate was 41 trades under one gate.

Fixed at READ time, so the rows already on disk aggregate without a
migration. Storage keeps the full string, so nothing is lost.
"""

from __future__ import annotations

import pytest

from bot.core.shadow_book import ShadowBook, gate_category

# ── the canonicaliser ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("CONFIDENCE: 0.55 < 0.6 minimum", "CONFIDENCE"),
    ("CONFIDENCE: 0.57 < 0.6 minimum", "CONFIDENCE"),
    ("LIQUIDITY: spread 42.0bps > 30.0bps", "LIQUIDITY"),
    ("LIQUIDITY: LIQUIDITY: spread 42.0bps", "LIQUIDITY"),
    ("MAX_POSITIONS", "MAX_POSITIONS"),
    ("  confidence: 0.5 < 0.6  ", "CONFIDENCE"),
])
def test_readings_collapse_to_their_gate(raw, want):
    assert gate_category(raw) == want


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_an_unlabelled_rejection_keeps_its_own_bucket(raw):
    """Not merged into a neighbour.

    Folding an unnamed rejection into whichever gate sorts next to it would
    credit its R to a gate that did not earn it — the same misattribution the
    fragmentation caused, pointing the other way.
    """
    assert gate_category(raw) == "UNLABELLED"


def test_different_gates_do_not_collapse_into_each_other():
    """Guard the guard: a canonicaliser that returns one value always passes."""
    assert gate_category("CONFIDENCE: 0.5") != gate_category("LIQUIDITY: x")


# ── the scoreboard ────────────────────────────────────────────────────────

def _book(tmp_path, gates):
    b = ShadowBook(state_file=str(tmp_path / "sb.json"))
    b._trades = [
        {"status": "closed", "r": r, "gate": g, "gates": [g], "regime": "TREND"}
        for g, r in gates
    ]
    b._load = lambda: None      # the rows are planted, not read from disk
    return b


def test_the_confidence_floor_reports_as_one_gate(tmp_path):
    """The live ledger's shape: nine keys, one gate."""
    rows = [(f"CONFIDENCE: 0.5{i} < 0.6 minimum", 1.0) for i in range(9)]
    rep = _book(tmp_path, rows).gate_report()
    assert list(rep) == ["CONFIDENCE"]
    assert rep["CONFIDENCE"]["n"] == 9


def test_the_costliest_gate_is_the_whole_gate_not_its_largest_shard(tmp_path):
    """The defect exactly as the card printed it.

    Fragmented, LIQUIDITY's single best shard (+3.0R) outranks each CONFIDENCE
    shard (+2.0R) and gets named "costliest". Aggregated, CONFIDENCE is
    +8.0R over four trades and is the real answer. The audit reads
    `next(iter(...))`, so the ordering IS the verdict.
    """
    rows = [("LIQUIDITY: spread 42.0bps", 3.0)]
    rows += [(f"CONFIDENCE: 0.5{i} < 0.6", 2.0) for i in range(4)]
    rep = _book(tmp_path, rows).gate_report()
    worst = next(iter(rep.items()))
    assert worst[0] == "CONFIDENCE"
    assert worst[1]["net_r"] == 8.0
    assert worst[1]["n"] == 4


def test_wins_and_losses_survive_the_grouping(tmp_path):
    rep = _book(tmp_path, [
        ("CONFIDENCE: 0.55 < 0.6", 2.0),
        ("CONFIDENCE: 0.57 < 0.6", -1.0),
        ("CONFIDENCE: 0.58 < 0.6", 0.0),
    ]).gate_report()
    g = rep["CONFIDENCE"]
    assert (g["n"], g["wins"], g["losses"]) == (3, 1, 1)
    assert g["net_r"] == 1.0


def test_the_regime_scoreboard_groups_the_same_way(tmp_path):
    """The corollary: the other surface making the same claim.

    gate_regime_report keys on the same field and had the same split.
    """
    rep = _book(tmp_path, [
        ("CONFIDENCE: 0.55 < 0.6", 1.0),
        ("CONFIDENCE: 0.57 < 0.6", 1.0),
    ]).gate_regime_report()
    assert list(rep) == ["CONFIDENCE"]
