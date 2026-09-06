"""`/llmstatus` promised a confirmation the running config cannot deliver.

Seen live on 2026-09-02, on an operator's screen, with `LLM_BACKGROUND_SCANS=off`
set on the box:

    ⚪ Brain: untested — no LLM analysis attempted since restart;
       state will confirm on the first scan.

The valve returns BEFORE any provider is attempted (`analyzer.py:3966`), so a
background sweep never touches the brain. The state therefore stays UNTESTED
for the life of the process, and the sentence sends the operator away to wait
for a confirmation that cannot arrive — on the surface they opened precisely
because they did not know whether the brain works.

`_scan_timeout_hint` carried the same promise ("/llmstatus confirms on the
first scan"), inside a diagnostic shown while a scan is timing out.

Neither number was wrong. The claim built on them was: true of the historical
default, false of the configuration in force.

The second half of this file covers `sweep_note`, which was written and tested
for exactly this state and called by NOTHING outside its own tests — the #999
shape at function granularity.

It was tracked, not invisible, and the distinction is worth recording because
the first draft of this file asserted the opposite. `unreachable_baseline.txt`
does only cover MODULES, and `brain_state.py` is imported for the brain icons —
but the repo also runs `test_no_new_unreachable_functions.py` against
`unreachable_functions_baseline.txt`, where `sweep_note` was listed. Wiring it
turned that entry stale and the ratchet failed until it was deleted in the same
commit. The checker was not missing; it was doing its job in both directions.
"""
from __future__ import annotations

import re
from pathlib import Path

from bot.formatters.brain_state import (
    SWEEP_RULES,
    SWEEP_UNKNOWN,
    sweep_mode,
    sweep_note,
    untested_confirmation,
)
from tests.source_scan import code_only

REPO = Path(__file__).resolve().parents[1]

LLM_ON = {"background_scans_llm": True}
LLM_OFF = {"background_scans_llm": False}


# --------------------------------------------------------------------------
# The promise must be one the running configuration can keep.
# --------------------------------------------------------------------------

def test_the_valve_off_does_not_promise_a_scan():
    """The exact live defect: no sweep will ever confirm it."""
    out = untested_confirmation(LLM_OFF)
    assert "first scan" not in out
    assert "no sweep will confirm" in out


def test_the_valve_off_names_an_action_that_works():
    """Naming the failure without the remedy just relocates the dead end."""
    assert "/analyze" in untested_confirmation(LLM_OFF)


def test_the_historical_default_is_unchanged():
    """The common case must not gain a hedge it does not need."""
    assert untested_confirmation(LLM_ON) == "state will confirm on the first scan."


def test_an_unknown_sweep_mode_claims_nothing_about_the_sweep():
    """A snapshot too old to carry the field cannot license either claim.

    The first draft reused the rules-only sentence here and asserted
    "background scans do not ask the LLM" about a mode it could not read —
    the same defect one level down from the one this file exists to fix.
    """
    for health in ({}, None, {"background_scans_llm": None},
                   {"degraded_streak": 0}):
        out = untested_confirmation(health)
        assert sweep_mode(health) == SWEEP_UNKNOWN
        assert "first scan" not in out, "an unknown mode may not promise a scan"
        assert "do not ask the LLM" not in out, \
            "an unknown mode may not assert what the sweep does"
        assert "/analyze" in out, "it must still name something that works"


def test_every_branch_gives_the_operator_something_to_do():
    for health in (LLM_ON, LLM_OFF, {}, None):
        assert untested_confirmation(health).strip()


# --------------------------------------------------------------------------
# The sweep's own state, on a surface, at last.
# --------------------------------------------------------------------------

def test_the_valve_state_is_visible_when_it_is_off():
    note = sweep_note(LLM_OFF)
    assert note, "the operator must be able to see the sweep is on rules"
    assert "rule-engine only" in note


def test_the_common_case_stays_quiet():
    """A note on every status line is a note nobody reads."""
    assert sweep_note(LLM_ON) == ""


def test_an_unreadable_snapshot_is_not_reported_as_llm():
    """Absent is not "yes" — a missing field must not print a confident 🧠."""
    assert sweep_mode({}) == SWEEP_UNKNOWN
    assert sweep_mode(LLM_OFF) == SWEEP_RULES
    assert sweep_note({}) != ""


# --------------------------------------------------------------------------
# Wiring. A renderer nothing calls is indistinguishable from one that is wrong.
# --------------------------------------------------------------------------

def _handler_src() -> str:
    """Every file the handler class is made of, plus the scan-hints leaf:
    /llmstatus lives in the LLM mixin since the handler split and the
    scan-timeout hint in `bot/skills/scan_hints.py`, and the count below
    spans both."""
    from pathlib import Path

    from tests.source_scan import handler_sources
    files = (*handler_sources(), Path("bot/skills/scan_hints.py"))
    return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in files)


def test_sweep_note_has_a_production_caller():
    """It had none. That is the whole reason the state was invisible.

    Anchored on the CALL, not the bare name: the name also appears in the
    import line, and an assertion satisfied by the import of the thing it
    checks proves nothing.
    """
    assert "_sweep_note(" in _handler_src(), \
        "sweep_note is back to having no caller — the valve state is invisible"


def test_both_surfaces_ask_what_will_actually_confirm():
    """/llmstatus and the scan-timeout hint each carried the false promise."""
    src = _handler_src()
    assert src.count("_untested_confirm(") == 2, \
        "both the /llmstatus brain line and _scan_timeout_hint must use it"


def test_the_hardcoded_promise_is_gone_from_the_untested_branches():
    """Anchored on the rendering line, not on the phrase.

    Asserting a short string is ABSENT is the assertion that keeps misfiring
    here, so this reads the two places that render an untested brain rather
    than scanning a 15k-line file for a phrase that may legitimately appear in
    prose about the fix.
    """
    src = _handler_src()
    for marker in ("Brain: untested", "no successful LLM call is"):
        i = src.find(marker)
        assert i > 0, f"the untested branch no longer contains {marker!r}"
        window = src[i:i + 400]
        assert "will confirm on the first scan" not in window, \
            f"the unconditional promise is back near {marker!r}"
        assert "confirms on the first scan" not in window, \
            f"the unconditional promise is back near {marker!r}"


def test_the_valve_note_is_its_own_sentence():
    """Never merged into the brain's line: they are different facts.

    A healthy brain with a rules-only sweep is a real and confusing state —
    one user /analyze keeps the streak at 0, so the brain reads HEALTHY while
    every background signal came from the rule engine.
    """
    src = _handler_src()
    m = re.search(r"_sweep\s*=\s*_sweep_note\(\s*h\s*\)", src)
    assert m, "the note must be computed from the same health snapshot"
    assert re.search(r"health_line\s*\+=", src[m.start():m.start() + 300]), \
        "the note must be APPENDED, not folded into the brain sentence"
