"""A comment that says "default OFF" beside `_env_bool(..., True)` is worse than none.

WHERE THIS CAME FROM

Chasing why the confidence calibrator had **0 samples** while its sibling
learner had 81 trades, the comment on `LEARN_FROM_PAPER_CLOSES` said
"(opt-in, default OFF)" — so that flag looked like the culprit. It was not; the
default is `True` and has been. The real gate was
`LEARN_CALIBRATION_FROM_PAPER` immediately below it. The stale sentence sent
the investigation at the wrong flag entirely.

Five flags carried a claim their code contradicted. Four opened with the same
copy-pasted audit annotation asserting the reverse of their own closing line —
somebody flipped a default, appended "Default ON" at the bottom, and left the
top alone. The fifth was worse and had no correction anywhere:

    class LearningConfig:
        \"\"\"... Default OFF: it changes live entry behavior, so it is opt-in.\"\"\"
        adaptive_confidence_enabled = _env_bool("ADAPTIVE_CONFIDENCE_ENABLED", True)

An operator reading that believed the nudge was inert until switched on. It was
adjusting live entry confidence on every trade.

CLAUDE.md already names this: "a number in prose is the part that rots first",
and `tests/test_claude_md_accuracy.py` pins that document against its own gates
for the same reason. This does it for the flags.

WHAT THIS DELIBERATELY DOES NOT DO

An earlier, broader version scanned a 14-line window and consulted the class
docstring when a flag had no comment of its own. It reported 5 contradictions,
of which **3 were false**: `LIVE_OPEN_TO_KEY_HOLDERS` says "OFF restores the
staged rollout (opt-in allowlist)" — describing what the OFF state MEANS, not
its default — and `PAPER_AUTO_ACCEPT` inherited a "Default OFF" belonging to a
different flag in the same class.

A checker with a 60% false-positive rate gets muted, and a muted checker is
worse than none. So this matches only the narrow, unambiguous signature: the
audit-annotation boilerplate in a flag's OWN contiguous comment block, against
a `True` default. That found 4 of the 5 by itself; the docstring case is pinned
separately by name because a general rule for docstrings could not be made
trustworthy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / "bot" / "config.py"

#: `name: bool = _env_bool("ENV_NAME", True/False)`
FLAG = re.compile(
    r"^\s*(\w+):\s*bool\s*=\s*_env_bool\(\s*[\"'](\w+)[\"']\s*,\s*(True|False)\s*\)")

#: The stale audit boilerplate. Narrow on purpose — see the module docstring.
STALE_OFF_CLAIM = re.compile(
    r"opt-?in,\s*default\s+OFF|default\s+OFF;\s*deep-audit", re.I)


def _flags_with_own_comment():
    """(line, attr, env, default, comment) for every bool flag.

    The comment is the CONTIGUOUS `#` block directly above the flag and nothing
    else — no window, no docstring fallback. Comment markers are stripped and
    the block is joined into one string, because these comments wrap: the
    annotation that started all this reads "(opt-in, default" on one line and
    "OFF; deep-audit medium)" on the next, and a per-line match misses it.
    """
    lines = CONFIG.read_text(encoding="utf-8").split("\n")
    for i, ln in enumerate(lines):
        m = FLAG.match(ln)
        if not m:
            continue
        block, j = [], i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            block.append(lines[j].strip().lstrip("#").strip())
            j -= 1
        yield i + 1, m.group(1), m.group(2), m.group(3) == "True", " ".join(reversed(block))


def test_the_scan_actually_reaches_the_flags() -> None:
    """A scan that matches nothing passes every assertion while checking none."""
    found = list(_flags_with_own_comment())
    assert len(found) >= 100, (
        f"only {len(found)} boolean flags found in config.py; the pattern has "
        "drifted and this file is asserting nothing")


def test_no_flag_defaults_ON_while_its_comment_says_it_is_opt_in() -> None:
    offenders = [
        (line, env) for line, _attr, env, default, comment in _flags_with_own_comment()
        if default and STALE_OFF_CLAIM.search(comment)
    ]
    assert offenders == [], (
        "these flags default to True while their own comment calls them opt-in "
        "or default-OFF. An operator reads the prose, believes the feature is "
        "inert, and never looks again:\n  "
        + "\n  ".join(f"config.py:{line}  {env}" for line, env in offenders))


def test_the_checker_catches_a_planted_instance(tmp_path: Path) -> None:
    """A check that cannot fail has not been tested.

    Verified against history too: run over the commit before the fix, this
    reports exactly four — DAILY_LOSS_BREAKER_AUTORESET,
    LLM_FALLBACK_COST_ACCOUNTING, LLM_CACHE_SCOPED_KEY and
    LEARN_FROM_PAPER_CLOSES — and nothing else.
    """
    planted = tmp_path / "config.py"
    planted.write_text(
        "class C:\n"
        "    # Something useful (opt-in, default OFF; deep-audit medium). More\n"
        "    # words about what it does when it is on.\n"
        '    thing_enabled: bool = _env_bool("THING_ENABLED", True)\n',
        encoding="utf-8")
    lines = planted.read_text(encoding="utf-8").split("\n")
    hits = []
    for i, ln in enumerate(lines):
        m = FLAG.match(ln)
        if not m or m.group(3) != "True":
            continue
        block, j = [], i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            block.append(lines[j].strip().lstrip("#").strip())
            j -= 1
        if STALE_OFF_CLAIM.search(" ".join(reversed(block))):
            hits.append(m.group(2))
    assert hits == ["THING_ENABLED"], hits


def test_a_wrapped_claim_is_still_caught(tmp_path: Path) -> None:
    """The line-wrap that hid LEARN_FROM_PAPER_CLOSES from the first scan.

    "(opt-in, default" ended one line and "OFF; deep-audit medium)" began the
    next, so a per-line regex saw neither. Joining the block is what fixed it,
    and this is the case that proves the join is still happening.
    """
    lines = [
        "    # Feed things into the loop (opt-in, default",
        "    # OFF; deep-audit medium). Longer explanation follows.",
        '    wrapped_enabled: bool = _env_bool("WRAPPED_ENABLED", True)',
    ]
    block = " ".join(x.strip().lstrip("#").strip() for x in lines[:2])
    assert STALE_OFF_CLAIM.search(block), (
        "a claim split across two comment lines is no longer detected")


def test_a_flag_describing_its_OFF_state_is_not_a_false_positive() -> None:
    """`LIVE_OPEN_TO_KEY_HOLDERS` says "OFF restores the staged rollout (opt-in
    allowlist)" — describing what OFF MEANS, not what the default is. An earlier
    version of this check flagged it, and acting on that would have rewritten a
    correct comment on a flag that decides who can trade with real money.

    Pinned so a future widening of the pattern has to fail here first.
    """
    for line, _attr, env, default, comment in _flags_with_own_comment():
        if env == "LIVE_OPEN_TO_KEY_HOLDERS":
            assert default is True
            assert "opt-in allowlist" in comment, "the comment changed; re-verify"
            assert not STALE_OFF_CLAIM.search(comment), (
                "the checker widened and now flags a correct comment")
            return
    pytest.fail("LIVE_OPEN_TO_KEY_HOLDERS is gone — re-verify this exemption")


# ── the docstring case, pinned by name ────────────────────────────────────

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

    assert "Default OFF: it changes live entry behavior" not in doc, (
        "the docstring claims OFF for a flag that defaults to True")
    assert re.search(r"DEFAULTS?\s+\*?\*?ON", doc), (
        "the docstring no longer states the real default")
    assert "ADAPTIVE_CONFIDENCE_ENABLED=false" in doc, (
        "the docstring does not say how to actually turn it off")
