"""A health field named `universe_size` that is not the universe.

    "universe_size": len(UNIVERSE)

UNIVERSE is a FIXED 70-symbol list used by this bridge's own /scan endpoint.
The engine's trading universe is built fresh each cycle from a volume-filtered
ticker sweep, capped by TOP_MOVERS_COUNT. The two routinely differ — 70 here
against 161 live on 2026-07-29.

The bare name read as THE universe, and both the operator and I used it that
way while diagnosing an analyze-phase timeout:

    "the health endpoint reports universe_size: 70 — so the venue is
     returning 70 eligible symbols after volume/liquidity filtering"

It was not. The phase was working through 161. An instrument whose name
implies more than it measures is the same defect as an instrument whose
coverage is narrower than its claim; this one just states it in the key.

The adjacent comment also said "66 symbols" over a list of 70 — a count
written in prose, which drifts, so it is gone rather than corrected.
"""

import re
from pathlib import Path

SRC = Path("api_bridge.py").read_text(encoding="utf-8")


def _universe() -> list[str]:
    m = re.search(r"UNIVERSE: list\[str\] = \[(.*?)\n\]", SRC, re.S)
    assert m, "UNIVERSE list not found"
    return re.findall(r'"([^"]+)"', m.group(1))


# ── the field says what it is ─────────────────────────────────────────────

def test_the_bare_name_is_gone():
    assert '"universe_size": len(UNIVERSE)' not in SRC, (
        "the unqualified name is what made it readable as the engine's universe")


def test_the_static_list_is_named_as_this_bridge_s_own():
    assert '"bridge_scan_universe_size": len(UNIVERSE)' in SRC


def test_the_real_number_is_reported_too():
    """The number people actually mean when they ask how many symbols the
    engine is working through."""
    assert '"engine_universe_size"' in SRC
    assert "_engine_universe_size(engine)" in SRC


def test_the_real_number_is_omitted_rather_than_zeroed():
    """A zero on a health endpoint reads as "there is nothing to scan", not
    as "no batch has run yet"."""
    block = SRC[SRC.index("def _engine_universe_size"):SRC.index('@app.post("/scan")')]
    assert "return None" in block
    assert "absent is not zero" in block.lower() or "Absent is not zero" in block
    assert 'if _engine_universe_size(engine) is not None else {}' in SRC


def test_it_never_breaks_the_health_endpoint():
    """A health check that 500s because a field could not be computed is
    worse than one that omits the field."""
    block = SRC[SRC.index("def _engine_universe_size"):SRC.index('@app.post("/scan")')]
    assert "except Exception:" in block


def test_it_reads_the_engine_s_own_record():
    block = SRC[SRC.index("def _engine_universe_size"):SRC.index('@app.post("/scan")')]
    assert '_analyze_progress' in block, (
        "the live count must come from the batch that actually ran, not from "
        "a config value that only bounds it")


# ── the prose count is gone ───────────────────────────────────────────────

def test_no_symbol_count_is_written_in_prose():
    """It said 66 over a list of 70. A number in a comment drifts silently;
    len() cannot."""
    header = SRC[SRC.index("# ── Universe"):SRC.index("UNIVERSE: list[str] = [")]
    assert not re.search(r"\b\d+\s+symbols\b", header), (
        "a hand-written count will go stale exactly as the last one did")


def test_the_comment_says_it_is_not_the_engine_universe():
    header = SRC[SRC.index("# ── Universe"):SRC.index("UNIVERSE: list[str] = [")]
    # Prose wraps across lines with a "# " prefix; assert on the words, not
    # on the line breaks a reflow would move.
    flat = " ".join(header.replace("#", " ").split())
    assert "not the engine's trading universe" in flat
    assert "TOP_MOVERS_COUNT" in header


def test_the_list_is_still_intact():
    """The rename must not have disturbed the data."""
    u = _universe()
    assert len(u) == 70
    assert "BTC/USDT" in u and "ETH/USDT" in u
    assert len(set(u)) == len(u), "no duplicates"
    assert all(s.endswith("/USDT") for s in u)
