"""The DOCSTRING half of "a comment must not name a default the flag lacks".

WHERE THIS CAME FROM

Chasing why the confidence calibrator had **0 samples** while its sibling
learner had 81 trades, the comment on `LEARN_FROM_PAPER_CLOSES` said
"(opt-in, default OFF)" — so that flag looked like the culprit. It was not; the
default is `True` and has been. The real gate was
`LEARN_CALIBRATION_FROM_PAPER` immediately below it. The stale sentence sent the
investigation at the wrong flag entirely.

Five flags carried a claim their code contradicted. Four opened with the same
copy-pasted audit annotation asserting the reverse of their own closing line —
somebody flipped a default, appended "Default ON" at the bottom, and left the
top alone. The fifth was worse and had no correction anywhere:

    class LearningConfig:
        \"\"\"... Default OFF: it changes live entry behavior, so it is opt-in.\"\"\"
        adaptive_confidence_enabled = _env_bool("ADAPTIVE_CONFIDENCE_ENABLED", True)

An operator reading that believed the nudge was inert until switched on. It was
adjusting live entry confidence on every trade.

=== AND THE NARROWNESS THIS FILE CHOSE WAS WHERE EIGHTEEN MORE LIVED ===

This file used to carry the whole rule, and deliberately matched only two
boilerplate spellings in `bot/config.py`. The reasoning was sound and is worth
keeping: an earlier, broader draft reported 5 contradictions of which **3 were
false**, and *"a checker with a 60% false-positive rate gets muted, and a muted
checker is worse than none."*

Driven over the whole tree, that stated limit had eighteen instances inside it:

  * **Reader comments were never scanned at all** — this file reads
    `bot/config.py` only. Twelve sat above USE sites, five of them on the live
    sizing path (the live-performance governor, correlation sizing, Kelly, the
    volatility-targeted cap), and one above `self._circuit_open = False`, so a
    reader was told the daily-loss breaker latches until a human runs `/reset`
    when by default it clears itself at the UTC day rollover.
  * **Only two OFF spellings counted.** "Default OFF makes this byte-identical
    to prior behaviour", "Default OFF → byte-identical until enabled" and
    "Default OFF → no scan runs" all passed — the last of those being the
    Guardian firewall line CLAUDE.md has documented as a defect since 2026-07
    and which nobody had fixed.
  * **The rule was ONE-WAY.** Nothing here could see a comment promising a
    protection is ON while the flag ships `False`, which is the more dangerous
    direction, and which `MODE_MIN_CONFIDENCE_ENABLED` and
    `STRUCTURE_TRAIL_ENABLED` both did.

`tests/default_comments.py` is the reading now, and its two false-accusation
cases are the two this file avoided by narrowing — SOLVED rather than sidestepped:
requiring the word "default" separates a claim about the DEFAULT from a
description of a STATE (which is what acquits `LIVE_OPEN_TO_KEY_HOLDERS`, by a
rule rather than by name), and a LOCAL `_env_bool` beats a `CONFIG.` reference
beside it (which acquits the four "Own env flag, default OFF" comments and finds
`chart_patterns.py`, whose comment sits one line above the declaration it
contradicts). `tests/test_a_comment_names_the_default_the_flag_has.py` enforces
it whole-tree, two-way, with no baseline.

WHAT STAYS HERE. The `#`-comment rule does not read DOCSTRINGS, and no general
docstring rule could be made trustworthy — the attempt produced two false
positives out of three. So the docstring case keeps being pinned by name, which
is the honest form for a judgement a rule cannot make.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.default_comments import _OFF, declared_defaults

CONFIG = Path(__file__).resolve().parents[1] / "bot" / "config.py"


def test_adaptive_confidence_docstring_states_its_real_default() -> None:
    """The worst of the five, and the only one with no correction anywhere.

    `LearningConfig`'s docstring said "Default OFF: it changes live entry
    behavior, so it is opt-in" above a flag reading `True`. Pinned by name
    rather than by rule because no general docstring rule could be made
    trustworthy — the attempt produced two false positives out of three.
    """
    src = CONFIG.read_text(encoding="utf-8")
    at = src.index("class LearningConfig:")
    doc = src[at:src.index("adaptive_confidence_enabled", at)]
    # Whitespace-normalised, because the docstring NARRATES the sentence it
    # replaced ("this docstring said ... until 2026-08-25") and the narration is
    # line-wrapped: `"Default\n    OFF: it changes ..."`. A raw `in` check
    # therefore passed by accident of where the line broke, which is the trap
    # this repo records as "a comment that quotes the string it forbids".
    flat = " ".join(doc.split())
    assert flat.count("Default OFF: it changes live entry behavior") <= 1, (
        "the docstring claims OFF for a flag that defaults to True "
        "(one occurrence is the retraction naming what it corrected)")
    assert re.search(r"DEFAULTS?\s+\*?\*?ON", doc), (
        "the docstring no longer states the real default")
    assert "ADAPTIVE_CONFIDENCE_ENABLED=false" in doc, (
        "the docstring does not say how to actually turn it off")


def test_describing_the_off_state_is_still_not_a_default_claim() -> None:
    """The false accusation that made this file narrow, kept as a REAL fixture.

    `LIVE_OPEN_TO_KEY_HOLDERS` says "OFF restores the staged rollout (opt-in
    allowlist)" — describing what OFF MEANS, not what ships. Acting on that
    accusation would have rewritten a correct comment on the flag that decides
    who can trade with real money.

    The shared reading acquits it by RULE now (a default claim must spell
    "default") rather than by an exemption naming this flag, so a future
    widening has to fail here first — and the planted twin in
    `test_a_comment_names_the_default_the_flag_has.py` proves the rule rather
    than this one instance.
    """
    by_env = {env: dflt for env, dflt in declared_defaults().values()}
    assert by_env.get("LIVE_OPEN_TO_KEY_HOLDERS") is True, \
        "the flag changed — re-verify this exemption"

    lines = CONFIG.read_text(encoding="utf-8").split("\n")
    for i, ln in enumerate(lines):
        if "LIVE_OPEN_TO_KEY_HOLDERS" not in ln or "_env_bool" not in ln:
            continue
        j, block = i - 1, []
        while j >= 0 and lines[j].strip().startswith("#"):
            block.append(lines[j].strip().lstrip("#").strip())
            j -= 1
        comment = " ".join(reversed(block))
        assert "opt-in allowlist" in comment, "the comment changed; re-verify"
        assert not _OFF.search(comment), (
            "the shared reading widened and now flags a correct comment that "
            "describes the OFF STATE rather than claiming a default")
        return
    pytest.fail("LIVE_OPEN_TO_KEY_HOLDERS is gone — re-verify this exemption")
