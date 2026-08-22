"""A volume spike with no measured move was scored as a bearish one.

`MarketSignal.change_pct_24h` is a plain `float`, and every scanner that
cannot read one writes 0.0:

    float(ticker.get("percentage", 0) or 0)      skill_registry
    data["change_pct"] if data else 0.0          scan_skill

So 0.0 means "the market did not move" AND "the exchange sent nothing", and
two places in the analyzer asked a BINARY question of it.

CONFIDENCE. On a volume spike:

    price_moving_up = signal.change_pct_24h > 0
    if (price_moving_up and LONG) or (not price_moving_up and SHORT):
        blended_confidence += vol_bonus     # "volume confirms direction"
    else:
        blended_confidence -= vol_bonus

`> 0` is False for "fell" and False for 0, and there is no third branch. So an
unreported 24h change handed every SHORT the confirmation bonus and docked
every LONG the same amount — on a number nobody measured, into the value
`min_confidence` gates trades on.

VOTES. On the same spike:

    votes.append(1.0 if (signal.change_pct_24h or 0) > 0 else -1.0)

a full-weight bearish vote from the same absent reading. Three lines below,
the file already had the answer: `elif not _skip_missing: votes.append(0.0)`,
the policy the RSI, MACD and Bollinger voters follow. The spike branch simply
never consulted it.

WHY THIS NEEDS NO Optional PLUMBING. Making the field `Optional[float]` was
the queued fix, and for a DIRECTION question it is not required: "flat" and
"absent" deserve the same answer, because a spike with no move confirms
nothing either way. Retyping a required Pydantic field read in fifteen places
— several on the hot analysis path — would have bought no additional
correctness at these two sites. Where the distinction DOES matter is display,
and that is noted rather than done.
"""

from __future__ import annotations

import inspect
import pathlib
import textwrap

import pytest

import bot.core.analyzer as A

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Direction:
    LONG = "LONG"
    SHORT = "SHORT"


def _confidence_delta(change_pct, direction, *, base=0.60, bonus=0.05):
    """Run the real confidence branch out of the live source.

    BOTH SLICE BOUNDARIES USED TO BE COMMENTS:

        i = src.index("if signal.volume_spike:\n            # A BINARY ON A VALUE")
        code = src[i:src.index("\n        # IMPROVEMENT #2", i)]

    Extracting the live branch and EXECUTING it is the strongest thing a test
    in this repo does — it cannot pass against code that was deleted, unlike
    every source match. Anchoring it on prose gave that strength a hinge:
    rewording either comment breaks it on unchanged behaviour, and moving the
    `# IMPROVEMENT #2` marker earlier shrinks the extracted block, which still
    executes and still passes while covering less.

    The start anchor carried the comment for a real reason — `analyzer.py` has
    TWO `if signal.volume_spike:` blocks and the comment disambiguated them.
    `_chg = signal.change_pct_24h or 0.0` is unique and is code, so the block is
    now found from there and ends where its indentation does.
    """
    src = inspect.getsource(A)
    anchor = "_chg = signal.change_pct_24h or 0.0"
    i = src.index(anchor)
    start = src.rindex("\n", 0, i) + 1
    indent = len(src[start:i])
    # To the first later line that is non-blank and dedents out of the block.
    lines, out = src[start:].split("\n"), []
    for ln in lines:
        if out and ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        out.append(ln)
    code = textwrap.dedent("\n".join(out))
    ns = {
        "signal": type("S", (), {"volume_spike": True,
                                 "change_pct_24h": change_pct})(),
        "direction": direction, "Direction": _Direction,
        "blended_confidence": base, "vol_bonus": bonus,
    }
    exec(compile(code, "<confidence>", "exec"), ns)
    return round(ns["blended_confidence"] - base, 6)


# ── the confidence adjustment ───────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_an_unmeasured_move_neither_confirms_nor_contradicts(direction):
    """THE DEFECT. A 24h change of 0 used to hand SHORT +bonus under the label
    "volume confirms direction" and dock LONG the same."""
    assert _confidence_delta(0.0, direction) == 0.0


def test_a_real_move_still_confirms_and_contradicts():
    """CONTROL. Neutralising everything would remove the signal, not the
    fabrication — volume genuinely confirming a direction must still count."""
    assert _confidence_delta(2.5, "LONG") == pytest.approx(0.05)
    assert _confidence_delta(2.5, "SHORT") == pytest.approx(-0.05)
    assert _confidence_delta(-2.5, "SHORT") == pytest.approx(0.05)
    assert _confidence_delta(-2.5, "LONG") == pytest.approx(-0.05)


def test_the_bonus_is_symmetric_between_sides_on_an_unknown_move():
    """The shape of the bug: it was not merely wrong, it was DIRECTIONAL —
    absent data leaned short on every symbol at once."""
    assert _confidence_delta(0.0, "LONG") == _confidence_delta(0.0, "SHORT")


@pytest.mark.parametrize("tiny", [1e-9, -1e-9])
def test_a_genuinely_tiny_move_is_still_a_move(tiny):
    """The guard is `!= 0.0`, not a tolerance. Inventing an epsilon here would
    be a second unmeasured judgement about what counts as flat."""
    assert _confidence_delta(tiny, "LONG") != 0.0


# ── the vote ────────────────────────────────────────────────────────────────

def test_the_spike_vote_no_longer_invents_a_side():
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    i = src.index("if _bar_spike or signal.volume_spike:")
    seg = src[i:i + 1200]
    assert "votes.append(1.0 if (signal.change_pct_24h or 0) > 0 else -1.0)" not in seg, (
        "a spike with no measured direction casts a full-weight bearish vote "
        "again")
    assert "_chg != 0.0" in seg, "the direction is not checked before voting"


def test_the_spike_vote_follows_the_same_missing_data_policy_as_the_others():
    """`voter_skip_missing_enabled` already existed and the RSI, MACD and
    Bollinger voters already honoured it. The spike branch was the one that
    did not."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    i = src.index("if _bar_spike or signal.volume_spike:")
    seg = src[i:i + 1400]
    assert "elif not _skip_missing:" in seg, (
        "the spike branch no longer defers to the shared skip-missing policy")


def test_the_bar_level_spike_is_untouched():
    """CONTROL. `vol_spike_bar_dir` is a MEASURED bar direction, not a 24h
    change, and it keeps voting on 0 because 0 there is a real reading."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    assert 'votes.append(float(indicators.get("vol_spike_bar_dir", 1)))' in src


# ── what was deliberately not changed ───────────────────────────────────────

def test_the_scan_board_deferral_has_been_resolved_not_forgotten():
    """THIS TEST USED TO PIN THE DEFECT.

    It asserted that `"dir": "LONG" if sig.change_pct_24h > 0 else "SHORT"`
    was still present, and that the "KNOWN DEFECT, DELIBERATELY NOT FIXED
    HERE" note explaining why was still beside it — a deferral with its reason
    attached, so a known defect could not quietly become an unknown one.

    The deferral named its own precondition: the six `dir` consumers in
    `_build_scan_payload` had to be audited before a third value could exist,
    because each one's `else` branch meant SHORT. That audit is done and those
    consumers are fixed, so the pin has to go in the same commit — the
    `known_failures.txt` rule, for the same reason. A stale pin protects a
    defect that is no longer there and hides the one that is.

    What replaces it is the opposite assertion: the two-valued expression must
    be GONE. Behaviour is covered by
    `tests/test_absent_move_is_not_a_short_setup.py`.
    """
    from tests.source_scan import code_only

    raw = (ROOT / "bot" / "skills" / "skill_registry.py").read_text(encoding="utf-8")
    src = code_only(raw)
    assert '"LONG" if sig.change_pct_24h > 0 else "SHORT"' not in src, (
        "the two-valued expression is back: an unreadable 24h move is being "
        "labelled SHORT again")
    assert "_dir_from_change(sig.change_pct_24h)" in src, (
        "the scan payload no longer routes direction through the helper that "
        "can answer UNKNOWN")
    # NOT a check that the old deferral note is gone. The first draft asserted
    # exactly that against the raw file, and failed — because the replacement
    # comment QUOTES the note it replaced, which is indistinguishable from the
    # note still being there. That is the trap CLAUDE.md records under "strip
    # comments first", and this is the fifth time it has fired. The two
    # assertions above are about code, which is what can actually regress.
    assert raw  # (raw is read above only so this reasoning has somewhere to live)


def test_dir_is_now_a_three_valued_contract_everywhere_it_is_read():
    """The premise the old deferral rested on, inverted.

    Every consumer that used to resolve "not LONG" as SHORT must now name
    SHORT explicitly, so a third value falls through to a branch that says so
    rather than to one that picks a side.
    """
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "scan_skill.py")
                    .read_text(encoding="utf-8"))
    assert 'book_side = "BID" if r["dir"] == "LONG" else "ASK"' not in src, (
        "book_side still puts every non-LONG row on the ask")
    assert 'shorts = len(results) - longs' not in src, (
        "the market-bias headline still counts undetermined rows as shorts")
    assert 'shorts = sum(1 for r in results if r["dir"] == "SHORT")' in src
