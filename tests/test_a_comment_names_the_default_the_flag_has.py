"""A comment must not name a default the flag does not have.

CLAUDE.md records this shape and then let the instance stand: *"The comment over
that scan named the wrong half as off. It said 'Default OFF (no scan) — this can
never break a chat'; `guardian_firewall_enabled` defaults to **True** ... A
comment that misdescribes which half of a security gate is disabled is how the
gate goes unexamined."* The lesson was written down; the line was not fixed, and
`bot/config.py` still read "Default OFF → no scan runs" above a `True` default
until this slice.

Driven over the whole tree it was not one line but **eighteen**, and five of them
sit on the live sizing path — the live-performance governor, correlation sizing,
Kelly, the volatility-targeted cap — each described as opt-in and off while
shrinking every order the engine places. The sixth in `risk_engine` is worse than
a size multiplier: it does `self._circuit_open = False`, so a reader was told the
daily-loss breaker latches until a human runs `/reset` when by default it clears
itself at the UTC day rollover.

Six more were `bot/config.py`'s OWN declarations, and every one had the same
history: a later measurement or audit changed the default, the new sentence was
appended, and the opening parenthetical was left stale — so the first thing a
reader sees is the wrong half. Two of those point the other way and are the more
dangerous direction: `MODE_MIN_CONFIDENCE_ENABLED` and `STRUCTURE_TRAIL_ENABLED`
promised a protection was ON while the flag shipped False.

NO BASELINE. The tree is at zero, so this holds from here with nothing to
forgive — the precedent the i18n guard set. An exception would be a comment that
is still wrong, and the remedy for that is the comment.
"""

from __future__ import annotations

import pytest

from tests.default_comments import (
    Finding,
    declaration_findings,
    declared_defaults,
    describe,
    reader_findings,
    reader_sources,
)

# A tree with exactly the shape under test and nothing else. The real tree is at
# zero, so every rule below would otherwise be a claim that there is a check —
# the argument `command_gates.py` and `candle_hygiene_baseline` already make.
PLANTED_CONFIG = (
    'flag_a: bool = _env_bool("FLAG_A", True)\n'
    'flag_b: bool = _env_bool("FLAG_B", False)\n'
)
PLANTED_DECL = {"flag_a": ("FLAG_A", True), "flag_b": ("FLAG_B", False)}


def test_no_comment_in_the_tree_names_a_default_the_flag_does_not_have():
    """The whole-tree claim. Every other test here proves this one can fail."""
    found = declaration_findings() + reader_findings()
    assert found == [], (
        "A comment claims a default its flag does not have:\n\n"
        + "\n\n".join(describe(f) for f in sorted(found,
                                                  key=lambda f: (f.path, f.line)))
        + "\n\nFix the comment. There is no baseline: the tree is at zero."
    )


def test_the_reader_walk_actually_reaches_the_tree():
    """A scan that matches nothing passes every assertion while checking none.

    With the tree at zero, narrowing this walk changes no verdict — so without
    this the mutation "skip bot/risk" would survive the round in silence. The
    old guard carried the same assertion for the same reason.
    """
    srcs = reader_sources()
    assert len(srcs) >= 250, f"only {len(srcs)} files walked; the walk has drifted"
    for must in ("bot/risk/risk_engine.py", "bot/core/engine.py",
                 "bot/core/analyzer.py", "bot/core/chart_patterns.py"):
        assert must in srcs, f"{must} is not being walked"
    assert "bot/config.py" not in srcs, \
        "config.py's own declarations are the declaration rule's, not the reader's"


class TestTheReaderRuleBites:
    """A comment above a USE of a flag."""

    def test_off_claimed_over_a_true_flag_is_a_finding(self):
        src = "# Feature X (default OFF).\nif CONFIG.risk.flag_a:\n    pass\n"
        got = reader_findings({"planted.py": src}, PLANTED_DECL)
        assert [(f.env, f.claimed, f.actual) for f in got] == [("FLAG_A", "off", True)]

    def test_on_claimed_over_a_false_flag_is_a_finding(self):
        """The direction the real tree can no longer reach.

        Both instances of it — MODE_MIN_CONFIDENCE_ENABLED and
        STRUCTURE_TRAIL_ENABLED — were fixed by this slice, so without a planted
        tree this rule would be measured by nothing.
        """
        src = "# Feature Y (default ON).\nif CONFIG.risk.flag_b:\n    pass\n"
        got = reader_findings({"planted.py": src}, PLANTED_DECL)
        assert [(f.env, f.claimed, f.actual) for f in got] == [("FLAG_B", "on", False)]

    def test_a_comment_that_agrees_with_its_flag_is_not_a_finding(self):
        src = ("# Feature X (default ON).\nif CONFIG.risk.flag_a:\n    pass\n"
               "# Feature Y (default OFF).\nif CONFIG.risk.flag_b:\n    pass\n")
        assert reader_findings({"planted.py": src}, PLANTED_DECL) == []

    def test_a_claim_split_across_two_comment_lines_is_still_caught(self):
        """The case `test_flag_prose_matches_default` was built around.

        `LEARN_FROM_PAPER_CLOSES` wrote "(opt-in, default" at the end of one
        line and "OFF; deep-audit medium)" at the start of the next, so a
        per-line match saw neither half. The first draft of THIS reading matched
        per line and had the same gap; reading that guard is what found it.
        """
        src = ("# Feed things into the loop (opt-in, default\n"
               "# OFF). Longer explanation follows.\n"
               "if CONFIG.risk.flag_a:\n    pass\n")
        got = reader_findings({"planted.py": src}, PLANTED_DECL)
        assert [(f.env, f.claimed) for f in got] == [("FLAG_A", "off")]

    def test_a_long_block_does_not_push_its_own_flag_out_of_reach(self):
        """Found by the mutation round, not by reading.

        The window used to be measured from the block START, so an eighteen-line
        explanation left its own `CONFIG.` reference beyond it and the rule went
        SILENT — the mutation restoring Kelly's false "opt-in, default OFF"
        survived a whole round because of exactly that. The window is measured
        from the block END now, where the flag is.
        """
        filler = "".join(f"# explanatory line {n}\n" for n in range(18))
        src = filler + "# Feature X (default OFF).\nif CONFIG.risk.flag_a:\n    pass\n"
        got = reader_findings({"planted.py": src}, PLANTED_DECL)
        assert [(f.env, f.claimed) for f in got] == [("FLAG_A", "off")], \
            "a long comment block must not hide the flag it is about"

    def test_a_comment_with_no_flag_in_reach_is_not_a_finding(self):
        """Naming a flag that is not there would be the guess this was corrected for."""
        src = "# Something is default OFF.\nreturn 1\n"
        assert reader_findings({"planted.py": src}, PLANTED_DECL) == []


class TestTheTwoFalseAccusationsItAlreadyMade:
    """Both were manufactured by the instrument and found by reading its output.

    *A checker with a blind spot manufactures exactly the accusation it exists to
    prevent* — twice, on the first two runs of this reading.
    """

    def test_a_local_env_bool_wins_over_a_config_attr_beside_it(self):
        """The first blind spot: four correct comments accused.

        `backtest/engine.py` twice and `order_flow.py` twice say "Own env flag,
        default OFF" about a LOCAL declaration that really is False, with an
        unrelated `CONFIG.` reference on the next line. Pairing the comment with
        the nearest `CONFIG.` attribute accused all four.
        """
        src = ('# Own env flag, default OFF for byte-identical behaviour.\n'
               'self._x = (_env_bool("LOCAL_THING", False)\n'
               '           and CONFIG.risk.flag_a)\n')
        assert reader_findings({"planted.py": src}, PLANTED_DECL) == [], \
            "the comment describes the LOCAL flag, which is False — not CONFIG.flag_a"

    def test_the_same_shape_still_bites_when_the_local_flag_disagrees(self):
        """And the preference must not become a blanket acquittal.

        Preferring the local flag is only honest if it is then CHECKED. This is
        how `chart_patterns.py` was found — a comment one line above the
        declaration it contradicted, which the CONFIG-only draft could not see.
        """
        src = ('# Default OFF keeps the legacy behaviour byte-identical.\n'
               'use_own_close = _env_bool("LOCAL_THING", True)\n')
        got = reader_findings({"planted.py": src}, PLANTED_DECL)
        assert [(f.env, f.claimed, f.actual, f.how) for f in got] == \
            [("LOCAL_THING", "off", True, "local _env_bool")]

    def test_describing_a_state_is_not_claiming_a_default(self):
        """The second blind spot: an AUTHORIZATION flag accused.

        `LIVE_OPEN_TO_KEY_HOLDERS`'s comment reads "OFF restores the staged
        rollout (opt-in allowlist)" — that says what the OFF state DOES and
        claims nothing about which state ships. A bare `opt-in` trigger read the
        noun phrase as a default claim. Requiring the word "default" cost no
        coverage: every real finding in the tree also spelled "default OFF".
        """
        src = ('# When ON, the gate opens. OFF restores the staged rollout\n'
               '# (opt-in allowlist).\n'
               'if CONFIG.risk.flag_a:\n    pass\n')
        assert reader_findings({"planted.py": src}, PLANTED_DECL) == []


class TestTheDeclarationRule:
    """The comment block directly above a `_env_bool` declaration."""

    def test_a_block_claiming_both_defaults_is_a_finding_whatever_the_flag_is(self):
        """Six blocks in `bot/config.py` claimed both. One block, two answers.

        It is a finding even when one of the two happens to be right, because a
        reader cannot tell which sentence is the live one — and in all six the
        stale claim was the OPENING parenthetical, which is what a skimming
        reader reads.
        """
        src = ('    # Feature A (default ON; shrink-only). Long explanation here.\n'
               '    # Default OFF makes this byte-identical to prior behaviour.\n'
               '    flag_a: bool = _env_bool("FLAG_A", True)\n')
        got = declaration_findings(src, "planted_config.py")
        assert [(f.env, f.claimed) for f in got] == [("FLAG_A", "both")]

    def test_a_block_that_agrees_with_its_declaration_is_not_a_finding(self):
        src = ('    # Feature A (default ON; shrink-only).\n'
               '    flag_a: bool = _env_bool("FLAG_A", True)\n'
               '    # Feature B (default OFF — measured).\n'
               '    flag_b: bool = _env_bool("FLAG_B", False)\n')
        assert declaration_findings(src, "planted_config.py") == []

    def test_a_declaration_with_no_comment_is_not_a_finding(self):
        src = '    flag_a: bool = _env_bool("FLAG_A", True)\n'
        assert declaration_findings(src, "planted_config.py") == []


def test_the_declared_defaults_are_read_from_config_not_written_down_here():
    """The expectation is DERIVED. A guard that carries its own copy of the
    thing it guards moves with it and can see nothing — the lesson the leverage
    slice's `MIN_LEVERAGE_DEFAULT` mutation taught one module over."""
    decl = declared_defaults()
    assert len(decl) > 100, f"only {len(decl)} bool flags parsed out of bot/config.py"
    assert decl["kelly_sizing_enabled"] == ("KELLY_SIZING_ENABLED", True)
    assert decl["guardian_firewall_block_high"] == ("GUARDIAN_FIREWALL_BLOCK_HIGH", False)


@pytest.mark.parametrize("env,expected", [
    ("KELLY_SIZING_ENABLED", True),
    ("LIVE_PERFORMANCE_GOVERNOR_ENABLED", True),
    ("CORRELATION_SIZING_ENABLED", True),
    ("VOL_TARGET_SIZING_ENABLED", True),
    ("DAILY_LOSS_BREAKER_AUTORESET", True),
    ("GUARDIAN_FIREWALL_ENABLED", True),
    ("MODE_MIN_CONFIDENCE_ENABLED", False),
    ("STRUCTURE_TRAIL_ENABLED", False),
])
def test_the_flags_this_slice_corrected_still_have_the_defaults_it_recorded(env, expected):
    """The comments now say these. If a default is deliberately flipped, the
    comment moves in the same commit — which is the whole point — and this row
    moves with it rather than the pair drifting apart again."""
    by_env = {e: d for e, d in declared_defaults().values()}
    assert by_env[env] is expected


def test_the_finding_key_survives_a_line_move():
    """A baseline or an issue keyed by line churns on every edit above it; the
    reason `network_reach_baseline` is keyed by FILE and not by nodeid."""
    a = Finding("p.py", 10, "X", "off", True, "CONFIG attr", "t")
    b = Finding("p.py", 900, "X", "off", True, "CONFIG attr", "t")
    assert a.key == b.key
