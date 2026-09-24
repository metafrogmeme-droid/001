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
    env_example_blocks,
    env_example_findings,
    env_example_silent_overrides,
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


# ---------------------------------------------------------------------------
# THE TWO DECLENSIONS, AND WHY BOTH HALVES LAND TOGETHER.
#
# `_CONFIG_REF` demanded a SECTIONED two-dot reference, so every comment about
# a flag read FLAT (`CONFIG.auto_confirm_live_enabled`) resolved to nothing
# and its claim was dropped in silence. Widening it is one line — and driven,
# widening it ALONE produces exactly one new finding, and that finding is the
# RC-AUD-002 RETRACTION at bot/core/engine.py:5382: a correction naming the
# false sentence it corrects, accused by the rule the correction motivated.
#
# So the quote strip is not a nicety, it is the other half. Both are driven
# here on PLANTED sources, because the real tree is at zero either way and a
# rule no input can reach is a claim that there is a check.
# ---------------------------------------------------------------------------

_FLAT_CLAIM = '''
# Default OFF: nothing runs unless it is switched on.
if CONFIG.auto_confirm_live_enabled:
    pass
'''

_FLAT_RETRACTION = '''
# THIS COMMENT USED TO SAY the gate is "disabled by default", which was false.
if CONFIG.auto_confirm_live_enabled:
    pass
'''

_FLAT_RETRACTION_WRAPPED = '''
# THIS USED TO SAY the gate is "disabled by
# default", which was false of the dataclass.
if CONFIG.auto_confirm_live_enabled:
    pass
'''

_FLAT_LIVE_CLAIM_BESIDE_A_QUOTE = '''
# It used to say "something else entirely". It is default OFF.
if CONFIG.auto_confirm_live_enabled:
    pass
'''

# TWO quoted spans with the live claim BETWEEN them. A strip that runs from
# the first quote to the last -- `".*"` with DOTALL, which is what a reader
# reaching for "strip the quotes" writes -- swallows the claim and acquits.
# With only one quoted span, greedy and non-greedy agree, so the fixture
# above cannot tell them apart.
_FLAT_CLAIM_BETWEEN_TWO_QUOTES = '''
# It used to say "one thing". It is default OFF. It never said "the other".
if CONFIG.auto_confirm_live_enabled:
    pass
'''


def _findings(src: str):
    return reader_findings({"planted.py": src})


def test_a_flat_config_read_is_now_resolved():
    """The first declension: no section, so nothing resolved and the claim
    was dropped. This is the case that was invisible."""
    found = _findings(_FLAT_CLAIM)
    assert len(found) == 1, found
    assert found[0].env == "AUTO_CONFIRM_LIVE_ENABLED"
    assert found[0].how == "CONFIG flat attr"


def test_a_quoted_retraction_is_not_a_claim():
    """The second declension. Nobody writes a LIVE claim inside quotes."""
    assert not _findings(_FLAT_RETRACTION)


def test_the_strip_works_on_a_quote_that_wraps_across_lines():
    """POST-JOIN is the mechanism: `"[^"\\n]*"` refuses newlines, and the real
    retraction wraps. Applied per line, this fixture still claims 'off'."""
    assert not _findings(_FLAT_RETRACTION_WRAPPED)


def test_a_live_claim_between_two_quotes_still_bites():
    """The strip must be per-span, not first-quote-to-last."""
    found = _findings(_FLAT_CLAIM_BETWEEN_TWO_QUOTES)
    assert len(found) == 1, (
        "a greedy strip swallowed the live claim between the two quoted "
        f"spans and acquitted the block: {found}")


def test_a_live_claim_beside_a_quote_still_bites():
    """Without this the strip could become a blanket acquittal and nothing
    would say so — a quoted span nearby must not buy silence for an
    UNQUOTED claim in the same block."""
    found = _findings(_FLAT_LIVE_CLAIM_BESIDE_A_QUOTE)
    assert len(found) == 1, found


def test_the_sectioned_resolution_still_wins():
    """The ORDER. A flat pattern tried first reads `CONFIG.risk.flag` as the
    section name and loses every sectioned resolution."""
    from tests.default_comments import _resolve
    decl = declared_defaults()
    # A SECTIONED read of a flag that really is in the map -- the first draft
    # of this fixture used `CONFIG.risk.min_confidence`, which is a FLOAT and
    # therefore absent from `declared_defaults()`, so the sectioned loop found
    # nothing and the flat fallback answered. The assertion was right and the
    # fixture could not produce the state it named.
    sectioned = next(k for k, v in decl.items() if v)
    got = _resolve(
        f"CONFIG.risk.{sectioned} beside CONFIG.auto_confirm_live_enabled", decl)
    assert got is not None and got[2] == "CONFIG attr", got


def test_widening_the_declaration_rule_to_non_bool_is_refused():
    """RECORDED, NOT DONE, and the reason is measured rather than tidy.

    `_DECL` collects only `: bool = _env_bool(...)`, so a FLOAT flag whose
    "off" is a sentinel — `auto_confirm_threshold`, off at 1.0 — never enters
    `declared_defaults()`. That is a real second declension and widening it is
    actively WRONG: driven, 183 non-bool declarations would enter, ZERO
    produce a declaration finding (no non-bool block spells the claim
    vocabulary), and on the reader side they produce three findings, ALL THREE
    FALSE ACCUSATIONS — because `actual` is compared as a boolean and
    `not 0.06` is False, so any nearby float makes an honest "default OFF"
    about some other flag register as a mismatch.

    The two widenings are not the same kind of move. One is cheap and one
    must not be made, and this asserts the second stays unmade.
    """
    decl = declared_defaults()
    assert decl, "an empty declaration map would pass this vacuously"
    # AND IT IS THE DEFAULT GROUP, NOT THE TYPE ANNOTATION, THAT KEEPS THEM
    # OUT -- which the round is what said. Widening only `: bool` to `: \w+`
    # admits NOTHING, because the second half still demands a literal
    # `(True|False)` where a float flag has `0.85`. An equivalent mutant, and
    # the reason it matters is that a future reader loosening the annotation
    # "to be more general" would change no verdict and conclude the rule is
    # about the annotation.
    assert "auto_confirm_threshold" not in decl, (
        "the float flag is deliberately absent — see this test's docstring")

    # AND THE COST IS DRIVEN, NOT ASSERTED. The first version of this test
    # checked that every recorded default `isinstance(..., bool)` — and
    # `m.group(3) == "True"` is a bool whatever `_DECL` matches, so widening
    # it kept the assertion true and the mutation survived a green round.
    # What widening really costs is a FALSE ACCUSATION, so that is what is
    # planted: a float flag in the map, and an honest comment about a
    # DIFFERENT flag beside it.
    widened = dict(decl)
    widened["some_rate_pct"] = ("SOME_RATE_PCT", 0.06)   # a float "off" is a sentinel
    planted = ('''
# Default OFF: nothing runs unless it is switched on.
if CONFIG.some_rate_pct:
    pass
''')
    accused = reader_findings({"planted.py": planted}, decl=widened)
    assert accused, (
        "this is the measurement the refusal rests on: with a float in the "
        "map, `not 0.06` is False, so an honest 'default OFF' registers as a "
        "mismatch. If this stops accusing, re-read the refusal — the reason "
        "for it may have gone")


# ── the third claim site: .env.example ───────────────────────────────────


def test_no_env_example_block_names_a_default_the_flag_does_not_have():
    """Twelve did, all saying OFF over a control that ships ON."""
    found = env_example_findings()
    assert found == [], (
        "`.env.example` prose claims a default its flag does not have:\n\n"
        + "\n\n".join(describe(f) for f in found)
        + "\n\nFix the prose. There is no baseline: the file is at zero.")


def test_every_live_example_line_that_inverts_a_default_says_so():
    """`cp .env.example .env` is the documented install, so a LIVE line is
    what that install runs. Six set a flag opposite to its default; five sat
    under prose calling that flag default OFF, so the file read consistent
    while the install switched off five controls the runbook lists as ON."""
    found = env_example_silent_overrides()
    assert found == [], (
        "a live `.env.example` line sets a flag opposite to its declared "
        "default and the prose above it does not say so:\n"
        + "\n".join(f"  .env.example:{f.line}  {f.env}={'true' if f.claimed == 'on' else 'false'}"
                     f" (declared {f.actual})" for f in found)
        + "\nSay what the line does (\"THE LINE BELOW SETS IT OFF\"), or "
          "change the value -- which is a decision about what a fresh deploy "
          "runs, not a wording fix.")


def test_the_env_example_walk_reaches_the_file():
    """A walk that pairs nothing passes both assertions above over any file."""
    blocks = env_example_blocks()
    assert len(blocks) >= 150, f"only {len(blocks)} prose blocks read"
    paired = {env for b in blocks for env, _ln, _v in b.run}
    for must in ("TIME_STOP_LIVE_AUTO_CLOSE", "LIVE_PERFORMANCE_GOVERNOR_ENABLED",
                 "CONFIDENCE_CALIBRATION_ENABLED", "AUTO_CONFIRM_LIVE_ENABLED"):
        assert must in paired, f"{must} is not paired with any prose block"


class TestTheEnvExampleRules:
    """Planted: the real file is at zero, so a mutation of a rule changes no
    verdict against it."""

    def _claims(self, text):
        return [(f.env, f.claimed) for f in env_example_findings(text, PLANTED_DECL)]

    def _overrides(self, text):
        return [f.env for f in env_example_silent_overrides(text, PLANTED_DECL)]

    def test_off_over_a_true_flag_is_a_finding(self):
        assert self._claims("# Does a thing. Default OFF.\n# FLAG_A=false\n") == [("FLAG_A", "off")]

    def test_on_over_a_false_flag_is_a_finding(self):
        assert self._claims("# Does a thing (default ON).\nFLAG_B=true\n") == [("FLAG_B", "on")]

    def test_a_block_that_agrees_is_not_a_finding(self):
        assert self._claims("# Does a thing. Default ON.\n# FLAG_A=false\n") == []

    def test_a_claim_wrapped_across_two_lines_is_caught(self):
        assert self._claims("# Does a thing (opt-in, default\n# OFF).\n# FLAG_A=true\n") == [("FLAG_A", "off")]

    def test_an_example_line_ends_the_block_it_does_not_join_it(self):
        # Joined, the second block would be read as part of the first and the
        # first's claim paired with FLAG_B: an accusation about the wrong flag.
        text = ("# First thing. Default ON.\n# FLAG_A=false\n"
                "# Second thing. Default OFF.\n# FLAG_B=true\n")
        assert self._claims(text) == []

    def test_a_block_claiming_both_defaults_is_a_finding(self):
        # Over a TRUE flag on purpose: there the off/on comparison alone reads
        # the block as agreeing, so only the "both" clause can refuse it.
        text = "# A thing. Default ON. Default OFF.\n# FLAG_A=false\n"
        assert self._claims(text) == [("FLAG_A", "both")]

    def test_a_run_over_two_flags_is_not_paired(self):
        assert self._claims("# Two things. Default OFF.\n# FLAG_A=x\n# FLAG_B=x\n") == []

    def test_a_knob_before_the_flag_still_pairs(self):
        text = "# A thing. Default OFF.\n# FLAG_A_WINDOW=20\n# FLAG_A=false\n"
        assert self._claims(text) == [("FLAG_A", "off")]

    def test_a_blank_line_breaks_the_pairing(self):
        # Stated as a miss rather than guessed at: prose separated from its
        # example by a blank line is not certainly about it.
        assert self._claims("# A thing. Default OFF.\n\n# FLAG_A=false\n") == []

    def test_a_quoted_retraction_is_not_a_claim(self):
        text = '# A thing. This said "default OFF" once. Default ON.\n# FLAG_A=false\n'
        assert self._claims(text) == []

    def test_a_silent_live_override_is_a_finding(self):
        assert self._overrides("# A thing. Default ON.\nFLAG_A=false\n") == ["FLAG_A"]

    def test_a_live_override_that_says_so_is_not(self):
        text = "# A thing. Default ON. THE LINE BELOW SETS IT OFF.\nFLAG_A=false\n"
        assert self._overrides(text) == []

    def test_saying_the_wrong_direction_does_not_acquit(self):
        text = "# A thing. Default OFF. THE LINE BELOW SETS IT ON.\nFLAG_B=false\n"
        assert self._overrides(text) == []            # agrees with its default
        text = "# A thing. THE LINE BELOW SETS IT ON.\nFLAG_A=false\n"
        assert self._overrides(text) == ["FLAG_A"]    # says ON over a line setting OFF

    def test_a_commented_example_is_not_an_override(self):
        assert self._overrides("# A thing.\n# FLAG_A=false\n") == []

    def test_a_live_line_matching_its_default_is_not_an_override(self):
        assert self._overrides("# A thing.\nFLAG_A=true\n") == []

    def test_an_override_under_no_prose_is_still_found(self):
        # Keyed on the LINE: a live override with only an unrelated header
        # above it is the silent case at its plainest.
        assert self._overrides("# -- Section --\nOTHER=1\nFLAG_A=0\n") == ["FLAG_A"]

    def test_an_override_with_no_comment_above_it_at_all_is_found(self):
        # The first draft of the walk started only at a `#` line, so this run
        # was never visited: the quiet direction, in the rule about silence.
        assert self._overrides("OTHER=1\n\nFLAG_A=false\n") == ["FLAG_A"]


def test_the_live_auto_close_docstring_claims_no_default():
    """Pinned by NAME, the `LearningConfig` precedent: docstrings are outside
    the reader rule, and this one said "Gated (default OFF)" and "the latter
    defaults False ... byte-identical until an operator opts in" over a flag
    that ships ON -- on the method that closes live positions at market. It
    now points at the declaration instead of restating it."""
    import inspect

    from bot.core.engine import RuneClawEngine
    from tests.default_comments import _claim
    doc = inspect.getdoc(RuneClawEngine._evaluate_live_smart_exits) or ""
    assert "live_auto_close_enabled" in doc, "the docstring no longer names its gate"
    assert _claim(doc) is None, (
        "the live auto-close docstring states a default again; the default "
        "lives in bot/config.py alone")
