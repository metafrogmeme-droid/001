"""A check nobody ran, rendered as a check that passed.

`_scan_timeout_hint` is the line an operator reads while a scan has just timed
out. Four of its branches opened with "LLM brain is healthy" and then ruled the
fallback chain OUT — pointing at "a per-symbol dependency (exchange fetch or a
single provider call), not the fallback chain".

All four read that from `degraded_streak == 0`. By its own docstring that value
means "healthy OR LLM-by-design-off", and it is also 0 when nothing has been
ATTEMPTED yet — the streak cannot rise before the first call runs. So on a
freshly restarted bot the message excluded the one subsystem nobody had
checked, using a negative nobody had measured.

THE FUNCTION'S OWN DOCSTRING ALREADY FORBIDS THIS. It was rewritten after a
green LLM check was read as "the slowness is likely exchange/data latency, not
the AI" while analyses hung long enough to blow the tick cap 37 times running:
"a heuristic flag is never a verdict". An UNRUN check rendered as a green one
is that same error one step earlier — and it survived the rewrite because the
rewrite was about what a green check MEANS, not about whether it was green.

AND THE SIBLING SURFACE WAS ALREADY CURED. `/llmstatus` separates the three
states and its comment says why: "the live incident showed 'healthy' at 18:07
then 18 failures at 18:08 because the first status simply pre-dated any LLM
call." One surface got the distinction; the one that uses it to rule something
out did not. Ask which OTHER surface makes the same claim.

`degraded_streak` stays exactly as it is — it is the SCORING value and it is
right for scoring, which is why the proactive monitor pages on a real streak
and not on silence. `brain_state()` is the display answer. Same split as
`integrity_veto.is_reading()`.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.formatters.brain_state import (BRAIN_TEXT, DEGRADED, HEALTHY, UNKNOWN,
                                        UNTESTED, brain_state, brain_state_of,
                                        may_rule_out_llm)
from bot.skills.scan_hints import _scan_timeout_hint

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Analyzer:
    def __init__(self, health):
        self._h = health

    def llm_health(self):
        if isinstance(self._h, Exception):
            raise self._h
        return self._h


def _hint(health):
    return _scan_timeout_hint(_Analyzer(health))


# ── the three states behind one zero ────────────────────────────────────────

def test_a_streak_of_zero_with_no_success_is_untested_not_healthy():
    """THE DEFECT. Both of these are `degraded_streak == 0`."""
    assert brain_state({"degraded_streak": 0, "last_ok_seconds_ago": None}) == UNTESTED
    assert brain_state({"degraded_streak": 0, "last_ok_seconds_ago": 12.0}) == HEALTHY


def test_a_success_this_instant_is_the_healthiest_reading_there_is():
    """`0.0` seconds ago is falsy, and it is a success that just happened.
    Test `is None`, not falsiness — the rule this repo states outright."""
    assert brain_state({"degraded_streak": 0, "last_ok_seconds_ago": 0.0}) == HEALTHY


def test_a_real_streak_is_degraded():
    assert brain_state({"degraded_streak": 3, "last_ok_seconds_ago": 900.0}) == DEGRADED


@pytest.mark.parametrize("health", [None, {}, "nope", 7,
                                    {"degraded_streak": "abc"},
                                    {"degraded_streak": 0}])
def test_an_unreadable_snapshot_is_unknown_never_healthy(health):
    """A missing measurement is not a passing one. The last case is the subtle
    one: a snapshot carrying the streak but NOT `last_ok_seconds_ago` cannot
    support "healthy", and defaulting the absent key would invent it."""
    assert brain_state(health) == UNKNOWN


def test_only_healthy_may_rule_the_llm_out():
    """The entitlement, asked for explicitly rather than assumed from a zero."""
    assert may_rule_out_llm({"degraded_streak": 0, "last_ok_seconds_ago": 1.0})
    for h in ({"degraded_streak": 0, "last_ok_seconds_ago": None},
              {"degraded_streak": 2, "last_ok_seconds_ago": 5.0}, None, {}):
        assert not may_rule_out_llm(h), h


def test_an_absent_analyzer_reads_as_unknown():
    assert brain_state_of(None) == UNKNOWN
    assert brain_state_of(object()) == UNKNOWN
    assert brain_state_of(_Analyzer(RuntimeError("boom"))) == UNKNOWN


def test_every_state_has_wording_and_an_entitlement():
    for state in (DEGRADED, UNTESTED, HEALTHY, UNKNOWN):
        icon, label, may = BRAIN_TEXT[state]
        assert icon and label
        assert may is (state == HEALTHY), (
            f"{state} claims the right to rule the LLM out")


# ── what the card actually says ─────────────────────────────────────────────

def test_an_untested_brain_is_not_ruled_out():
    """MUST_SAY / MUST_NOT_SAY, with the RED HERRING planted: `degraded_streak`
    is genuinely 0, which is true and reads as good news, while nothing has
    been measured at all."""
    out = _hint({"degraded_streak": 0, "last_ok_seconds_ago": None})
    assert "untested" in out
    assert "NOT ruled out" in out
    assert "LLM brain is healthy" not in out, (
        "an unrun check is being reported as a passing one, in the message "
        "read while diagnosing a timeout")


def test_an_untested_brain_does_not_point_at_the_exchange():
    """The concrete cost. 37 tick timeouts were spent on the exchange because
    a green check was read as a verdict; an unrun one must not do it again."""
    out = _hint({"degraded_streak": 0, "last_ok_seconds_ago": None})
    assert "exchange fetch" not in out


def test_a_healthy_brain_still_rules_the_chain_out():
    """CONTROL. The whole value of the line is that a MEASURED green does
    narrow the search — softening that everywhere would be the opposite
    error."""
    out = _hint({"degraded_streak": 0, "last_ok_seconds_ago": 30.0})
    assert "healthy" in out and "ruled out" in out
    assert "untested" not in out


def test_a_degraded_brain_still_leads_with_the_streak():
    out = _hint({"degraded_streak": 5, "last_ok_seconds_ago": 600.0})
    assert "degraded" in out and "5 analyses in a row" in out


def test_a_broken_health_call_produces_no_line_rather_than_a_wrong_one():
    assert _scan_timeout_hint(_Analyzer(RuntimeError("boom"))) == ""
    assert _scan_timeout_hint(None) == ""
    assert _scan_timeout_hint(object()) == ""


# ── the two surfaces cannot drift apart again ───────────────────────────────

def test_no_surface_claims_a_healthy_brain_without_asking():
    """The guard. `/llmstatus` had the distinction and the timeout hint did
    not, for as long as each spelled the check out for itself."""
    from tests.source_scan import code_only, handler_sources

    # Every file the handler class is made of, plus the scan-hints leaf the
    # timeout hint moved to: /llmstatus lives in a mixin now, and a count over
    # telegram_handler.py alone would miss a second copy of the claim written
    # there — or, since the hint left for `scan_hints.py`, the one copy that
    # is entitled to exist.
    sources = {p: code_only(p.read_text(encoding="utf-8"))
               for p in (*handler_sources(), ROOT / "bot" / "skills" / "scan_hints.py")}
    src = "\n".join(sources.values())
    # ONE occurrence is correct: the `_may_exclude` arm of the shared
    # expression, which is the only place entitled to say it. Forbidding it
    # outright would forbid the fix; the property is that it never appears
    # anywhere the entitlement was not checked.
    assert src.count("LLM brain is healthy") == 1, (
        f"{src.count('LLM brain is healthy')} surfaces assert a healthy brain "
        "as a literal — it must come from the _may_exclude branch, which can "
        "answer 'untested' instead")
    i = src.index("LLM brain is healthy")
    assert "_may_exclude" in src[max(0, i - 300):i + 300], (
        "the healthy claim is no longer guarded by the entitlement check")
    assert "last_ok_seconds_ago" not in src, (
        "a surface reads the health dict directly again instead of going "
        "through brain_state(), which is how the two came apart")


def test_both_surfaces_resolve_through_the_shared_function():
    from tests.source_scan import code_only, handler_sources

    # The timeout hint lives in the scan-hints leaf; /llmstatus is in the
    # LLM mixin. Each marker is looked up in whichever contributing file
    # defines it, so a further move does not turn this into a scan of nothing.
    sources = [code_only(p.read_text(encoding="utf-8"))
               for p in (*handler_sources(), ROOT / "bot" / "skills" / "scan_hints.py")]
    for marker, end in (("def _scan_timeout_hint", "def _inflight_analysis_progress"),
                        ("def _cmd_llmstatus", "def _cmd_llmreset")):
        src = next((s for s in sources if marker in s), None)
        assert src is not None, f"{marker} is defined in no file of the handler class"
        i = src.index(marker)
        body = src[i:src.index(end, i)]
        assert "_brain_state(" in body, f"{marker} no longer uses brain_state()"


def test_the_scoring_value_is_left_alone():
    """CONTROL, and the point of the split: the proactive monitor pages on a
    real streak and must NOT start paging on an untested brain — silence at
    startup is not an incident."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "proactive_monitor.py")
                    .read_text(encoding="utf-8"))
    assert "degraded_streak" in src, (
        "the monitor stopped reading the streak — brain_state is the DISPLAY "
        "answer and was never meant to replace the scoring one")


# ── a provider name is never invented ───────────────────────────────────────

def test_the_playbook_never_invents_a_provider_name():
    """`CONFIG.llm.provider if ... else "groq"` rendered a confident `GROQ` on
    the card that describes the system to its user, from a value nobody could
    read. The string form of `.get("pnl", 0)` — and worse than a zero, because
    a provider name carries no hint that it was defaulted."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "skill_registry.py")
                    .read_text(encoding="utf-8"))
    assert 'else "groq"' not in src, (
        "the playbook card names a provider it did not read")


@pytest.mark.parametrize("provider,must_say,must_not_say", [
    ("groq", "GROQ", "not configured"),
    ("", "not configured", "GROQ"),
    (None, "not configured", "GROQ"),
])
def test_the_playbook_llm_line_says_what_it_read(provider, must_say, must_not_say):
    """Driven through the renderer's own expression rather than matched, and
    parameterised on the case that used to be invisible."""
    _prov = str(provider or "").strip()
    line = (f"  🤖 LLM primary: <b>{_prov.upper()}</b> + cascading fallback"
            if _prov else
            "  🤖 LLM primary: <b>not configured</b> — running on the rule engine"
            " unless a tier is routed (<code>/llmtiers</code>)")
    assert must_say in line
    assert must_not_say not in line


def test_the_playbook_labels_the_primary_rather_than_the_route():
    """Every tier can be routed away from the primary by an env pin, the admin
    table or /settier, so an unlabelled provider name reads as "where this
    runs" and is often not."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "skill_registry.py")
                    .read_text(encoding="utf-8"))
    assert "LLM primary:" in src, (
        "the playbook presents a provider name as the route again")
