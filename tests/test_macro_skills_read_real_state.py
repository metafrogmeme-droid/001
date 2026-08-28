"""The /macro and /compliance cards, driven by REAL collaborators.

Every skill in `bot/skills/macro_skills.py` was written against an imagined
"v2" API. Seven attribute probes, seven names that do not exist on the objects
they were aimed at:

    upcoming_events   ->  MacroEventProvider.get_upcoming_events
    current_window    ->  MacroContext.window (on the context, not the source)
    consent_ledger    ->  ComplianceEngine.get_consent_ledger
    compliance.profile -> nothing; profiles are arguments to authorize()
    circuit_breaker   ->  nothing; the halt lives on engine.risk
    audit.seal        ->  AuditChain.append / seal_decision
    token.expires_utc ->  ApprovalToken.expires_at

Each miss rendered as a confident negative, because every branch treated "the
attribute was not there" as "the thing is not there". A calendar holding 40
events with Nonfarm Payrolls a week out printed **"No upcoming events
loaded."** — the exact phrasing that tells an operator of a fail-closed macro
system that the calendar is MISSING, which is the one condition they are told
to stop trading on. A ledger holding real authorization decisions, including
denials, printed **"No consent ledger available."**

Nothing caught it because nothing could run it: all five skills are registered
and dispatched by no transport (tests/test_registered_skills_are_reachable.py
holds that half). Unrunnable is exactly WHY the probes were wrong — the
module's tests, if any, would have been its only caller.

So these tests plant real state and assert what the card says, per CLAUDE.md,
rather than scanning the source for the fixed names. A source scan here would
pass against a card that reads the right attribute and then prints the wrong
sentence about it, which is most of what went wrong.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from bot.compliance.compliance_engine import (
    ComplianceEngine,
    Permission,
    SubjectProfile,
)
from bot.core.macro_events import MacroEventProvider
from bot.macro.calendar import MacroCalendar
from bot.skills.macro_skills import (
    CheckEventRiskSkill,
    ComplianceStatusSkill,
    KillSwitchSkill,
    MacroBriefSkill,
    RequestLiveApprovalSkill,
)
from bot.utils.audit_chain import AuditChain

SEED = Path(__file__).resolve().parents[1] / "config" / "macro_calendar.seed.json"


def _run(skill, engine, **kwargs) -> str:
    return asyncio.run(skill.execute(engine, **kwargs))


class _Engine:
    """A bare stand-in; each test attaches only what it is about."""

    macro_provider = None
    macro_calendar = None
    compliance = None


def _loaded_provider() -> MacroEventProvider:
    p = MacroEventProvider(seed_path=SEED)
    if not p.get_upcoming_events(hours=24 * 365):
        pytest.skip("seed calendar holds no forward events to assert on")
    return p


def _populated_compliance() -> ComplianceEngine:
    """A ledger with three grants and one real jurisdiction denial."""
    eng = ComplianceEngine()
    perms = list(Permission)
    ok = SubjectProfile(subject_id="op", permissions=set(perms), jurisdiction="US")
    for i in range(3):
        eng.authorize(action=perms[0], profile=ok, live_mode=False,
                      risk_passed=True, macro_ok=True, notional_usd=100.0,
                      trade_id=f"T{i}")
    blocked = SubjectProfile(subject_id="x", permissions=set(perms),
                             jurisdiction="RU")
    eng.authorize(action=perms[0], profile=blocked, live_mode=False,
                  risk_passed=True, macro_ok=True, notional_usd=1.0,
                  trade_id="T-DENY")
    return eng


# ---------------------------------------------------------------------------
# /compliance
# ---------------------------------------------------------------------------

def test_a_populated_ledger_is_not_reported_as_absent():
    """The headline defect: 4 real decisions, card said none existed."""
    eng = _Engine()
    eng.compliance = _populated_compliance()
    assert len(eng.compliance.get_consent_ledger()) == 4  # the state is real

    out = _run(ComplianceStatusSkill(), eng)

    assert "No consent ledger available" not in out
    assert "T-DENY" in out
    assert "DENIED" in out          # the outcome, which the old card never showed
    assert "GRANTED" in out
    assert "4 decision" in out


def test_a_denial_shows_which_lock_failed_and_why():
    eng = _Engine()
    eng.compliance = _populated_compliance()
    out = _run(ComplianceStatusSkill(), eng)
    assert "jurisdiction" in out
    assert "RU" in out


def test_an_empty_ledger_and_an_unreadable_one_do_not_read_alike():
    """The whole point of the sentinel. Same shape, opposite meanings."""
    empty = _Engine()
    empty.compliance = ComplianceEngine()          # genuinely nothing recorded
    empty_out = _run(ComplianceStatusSkill(), empty)

    class Exploding:
        _restricted = {"KP"}

        def get_consent_ledger(self):
            raise RuntimeError("ledger store offline")

    broken = _Engine()
    broken.compliance = Exploding()
    broken_out = _run(ComplianceStatusSkill(), broken)

    assert "no decisions recorded yet" in empty_out
    assert "could not read" not in empty_out

    assert "could not read" in broken_out
    assert "no decisions recorded yet" not in broken_out


def test_the_card_does_not_report_a_missing_profile_that_never_existed():
    """`ComplianceEngine` holds no profile — authorize() takes one per call.

    "No compliance profile loaded." described a design as a fault, on a
    permissions panel, unconditionally.
    """
    eng = _Engine()
    eng.compliance = _populated_compliance()
    out = _run(ComplianceStatusSkill(), eng)

    assert "No compliance profile loaded" not in out
    # What IS held gets shown instead.
    assert "Restricted jurisdictions" in out
    for code in ("KP", "IR", "SY", "CU", "RU"):
        assert code in out


# ---------------------------------------------------------------------------
# /macro
# ---------------------------------------------------------------------------

def test_a_loaded_calendar_is_never_reported_as_no_events_loaded():
    eng = _Engine()
    eng.macro_provider = _loaded_provider()
    assert eng.macro_provider.event_count > 0     # the state is real

    out = _run(MacroBriefSkill(), eng)

    assert "No upcoming events loaded" not in out
    # Whatever the horizon holds, the next event is named one way or the other.
    nxt = eng.macro_provider.get_upcoming_events(hours=24 * 365)[0]
    assert str(nxt["label"]) in out


def test_a_blind_provider_never_claims_the_calendar_is_clear():
    """`get_upcoming_events` answers [] when blind — for the opposite reason."""
    eng = _Engine()
    eng.macro_provider = MacroEventProvider()      # no seed, nothing loaded
    assert eng.macro_provider.event_count == 0

    out = _run(MacroBriefSkill(), eng)

    assert "unknown" in out
    assert "none scheduled" not in out
    assert "No macro events in the next" not in out


def test_an_estimated_event_date_is_not_printed_as_a_confirmed_one():
    """A heuristic is never a verdict — including a heuristic timestamp."""
    eng = _Engine()
    eng.macro_provider = _loaded_provider()
    events = eng.macro_provider.get_upcoming_events(hours=24 * 365)
    if not any(str(e.get("date_confidence", "")).lower() == "estimated"
               for e in events):
        pytest.skip("seed calendar carries no estimated dates to assert on")

    out = _run(MacroBriefSkill(), eng)
    assert "~estimated" in out


def test_a_source_that_answers_nothing_reports_unknown_not_empty():
    class Mute:
        pass

    eng = _Engine()
    eng.macro_provider = Mute()
    out = _run(MacroBriefSkill(), eng)

    assert "unknown" in out
    assert "No active macro window." not in out   # unfalsifiable when unread
    assert "No macro events" not in out


def test_the_active_window_is_read_from_the_context_not_the_source():
    """`current_window` was never an attribute of any macro source.

    The old probe resolved to nothing on every call, so "No active macro
    window" printed identically whether a CPI blackout was live or the
    provider had fallen over. Driving a REAL blackout is what distinguishes a
    card that reads ctx.window from one that always says no.
    """
    eng = _Engine()
    eng.macro_provider = _loaded_provider()
    ctx = eng.macro_provider.get_context()
    out = _run(MacroBriefSkill(), eng)

    if ctx.window:
        assert str(ctx.window) in out
    else:
        assert "No active macro window." in out


def test_the_calendar_shaped_source_renders_with_its_own_field_names():
    """`source = provider or calendar` — so BOTH shapes must render.

    They disagree on names. MacroEventProvider yields dicts keyed
    `type`/`severity`; MacroCalendar yields MacroEvent dataclasses with
    `event_type`/`impact`. Reading only the first pair printed
    "[severity unknown]" beside a HIGH-impact FOMC decision — the fix for one
    source quietly not covering the other, which is the mistake this file
    exists to document.
    """
    from bot.macro.calendar import build_2026_calendar

    eng = _Engine()
    eng.macro_calendar = MacroCalendar(events=build_2026_calendar())
    out = _run(MacroBriefSkill(), eng)

    assert "No upcoming events loaded" not in out
    assert "severity unknown" not in out
    assert "HIGH" in out


def test_a_heading_never_claims_a_horizon_the_source_did_not_apply():
    """`MacroCalendar.upcoming()` takes a `limit`, not `hours`, and returns
    events months out. A "next 7d" heading over that output is a claim about
    timing made by the card rather than by the data."""
    from bot.macro.calendar import build_2026_calendar

    eng = _Engine()
    eng.macro_calendar = MacroCalendar(events=build_2026_calendar())
    out = _run(MacroBriefSkill(), eng)

    listed = [ln for ln in out.splitlines() if ln.strip().startswith("- ")]
    assert listed, "no events rendered to check the heading against"
    assert "Next events (next" not in out
    assert "Next events:" in out


# ---------------------------------------------------------------------------
# /eventrisk — an unreadable size multiplier is not "full size"
# ---------------------------------------------------------------------------

def test_an_unreadable_size_multiplier_is_not_rendered_as_full_size():
    """`getattr(result, "size_multiplier", 1.0)`: absent read as 1.0, which on
    a macro risk card means TRADE AT FULL SIZE. The fail-open direction on a
    control whose entire job is to shrink positions before a print."""
    class Ctx:
        risk_state = "CLEAR"
        explanation = ""
        is_stale = False
        is_blind = False
        # deliberately no severity / window / size_multiplier

    class Source:
        def get_context(self, symbol=None):
            return Ctx()

    eng = _Engine()
    eng.macro_provider = Source()
    out = _run(CheckEventRiskSkill(), eng, symbol="BTC/USDT")

    assert "Size multiplier: <code>unknown</code>" in out
    assert "1.0" not in out


def test_a_genuinely_clear_reading_says_none_not_unknown():
    """CLEAR means severity and window really are None. That is a measurement,
    and must not share a rendering with "the field was not there"."""
    eng = _Engine()
    eng.macro_provider = _loaded_provider()
    ctx = eng.macro_provider.get_context(symbol="BTC/USDT")
    if ctx.severity is not None or ctx.window is not None:
        pytest.skip("provider is not in a clear state right now")

    out = _run(CheckEventRiskSkill(), eng, symbol="BTC/USDT")
    assert "Severity:        <code>none</code>" in out
    assert "unknown" not in out


# ---------------------------------------------------------------------------
# /kill — the card that must never overclaim
# ---------------------------------------------------------------------------

def test_a_kill_switch_that_stopped_nothing_says_so():
    """The old card printed "All positions frozen. Circuit breaker is OPEN."
    on a fixed template. Whether anything stopped is the only fact on it."""
    class Nothing:
        pass

    out = _run(KillSwitchSkill(), Nothing())
    assert "NOTHING WAS STOPPED" in out
    assert "ACTIVATED" not in out


def test_a_kill_switch_whose_halt_raised_never_reports_a_kill():
    class Jammed:
        class circuit_breaker:
            @staticmethod
            def trip(reason=None):
                raise RuntimeError("breaker jammed")

    out = _run(KillSwitchSkill(), Jammed())
    assert "NOTHING WAS STOPPED" in out
    assert "ACTIVATED" not in out


def test_an_unsealed_kill_is_reported_as_unsealed():
    """`AuditChain.seal` never existed, so the seal was a permanent no-op
    under a card that announced the kill as recorded. An emergency stop that
    left no audit record is a fact the operator needs."""
    tripped = []

    class Breaker:
        def trip(self, reason=None):
            tripped.append(reason)

    class NoAudit:
        circuit_breaker = Breaker()

    out = _run(KillSwitchSkill(), NoAudit())
    assert tripped, "the breaker was never tripped"
    assert "ACTIVATED" in out
    assert "NOT sealed" in out

    class Chain:
        def __init__(self):
            self.rows = []

        def append(self, event, payload, **kw):
            self.rows.append((event, payload))

    class WithAudit:
        circuit_breaker = Breaker()
        audit_chain = Chain()

    eng = WithAudit()
    out2 = _run(KillSwitchSkill(), eng)
    assert "Audit chain: <code>sealed</code>" in out2
    assert eng.audit_chain.rows and eng.audit_chain.rows[0][0] == "KILL_SWITCH"


# ---------------------------------------------------------------------------
# /approve — an invented token authorizes nothing
# ---------------------------------------------------------------------------

def test_an_unreadable_token_id_is_never_replaced_by_an_invented_one():
    """It used to fall back to `str(uuid4())[:8]` — a fresh identifier printed
    to the operator as the token they must quote to authorize a live trade."""
    class Manager:
        @staticmethod
        def issue_token(trade_id=None):
            return object()          # a token with nothing readable on it

    class Engine:
        approval_manager = Manager()

    out = _run(RequestLiveApprovalSkill(), Engine(), trade_id="T-1")
    assert "no readable id" in out
    assert "Token:" not in out


def test_an_unknown_single_use_flag_is_not_asserted_as_single_use():
    """`getattr(token, "one_time", True)` answered the safest-sounding thing
    about a security control it could not read."""
    class Token:
        token_id = "abc123"
        expires_at = "2026-01-01T00:00:00Z"
        # no `one_time` attribute at all

    class Manager:
        @staticmethod
        def issue_token(trade_id=None):
            return Token()

    class Engine:
        approval_manager = Manager()

    out = _run(RequestLiveApprovalSkill(), Engine(), trade_id="T-1")
    assert "One-time:  <code>unknown</code>" in out
    assert "abc123" in out


# ---------------------------------------------------------------------------
# The anti-drift half
# ---------------------------------------------------------------------------

# Every name-tuple `_read()` is called with, and the target it is aimed at. A
# probe missing from this table is the defect that started this file, so the
# scan below refuses to let one be added without declaring its target.
#
# Targets are FACTORIES, not classes, and that is load-bearing: the first draft
# of this test checked `hasattr(ComplianceEngine, ...)` and failed on
# `_restricted`, which is assigned in `__init__` and so does not exist on the
# class at all. The probe was correct — the card renders the jurisdictions —
# and the assertion was wrong. Held state is normally instance state, so an
# instance is the only honest thing to ask.
PROBE_CONTRACT: dict[tuple[str, ...], tuple] = {
    ("get_upcoming_events", "upcoming_events", "upcoming"):
        (MacroEventProvider, lambda: MacroCalendar(events=[])),
    ("get_consent_ledger", "consent_ledger"): (ComplianceEngine,),
    ("restricted_jurisdictions", "_restricted"): (ComplianceEngine,),
}

MODULE = Path(__file__).resolve().parents[1] / "bot" / "skills" / "macro_skills.py"


def _read_probes() -> list[tuple[str, ...]]:
    """Every literal name-tuple passed as `_read()`'s second argument."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    found: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_read"):
            continue
        assert len(node.args) >= 2, f"_read() call with no names at line {node.lineno}"
        names = node.args[1]
        assert isinstance(names, ast.Tuple), (
            f"_read() at line {node.lineno} must take a literal tuple of names "
            "so this test can check them against the real classes"
        )
        found.append(tuple(ast.literal_eval(e) for e in names.elts))
    return found


def test_every_probe_resolves_on_at_least_one_real_class():
    """The check that would have caught all seven misses in one run."""
    probes = _read_probes()
    assert probes, "no _read() probes found — has the helper been renamed?"

    for names in probes:
        targets = PROBE_CONTRACT.get(names)
        assert targets is not None, (
            f"_read(..., {names!r}) is not in PROBE_CONTRACT. Declare which "
            "class it is aimed at, so this test can verify the names exist "
            "on it — an undeclared probe is how this module came to ask for "
            "seven attributes that were never there."
        )
        for factory in targets:
            obj = factory()
            label = type(obj).__name__
            hits = [n for n in names if hasattr(obj, n)]
            assert hits, (
                f"none of {names!r} exists on {label}. This is the "
                f"original defect: {label} would answer nothing and "
                "the card would render the miss as a confident negative."
            )


def test_the_audit_chain_seal_method_the_kill_switch_calls_exists():
    """`AuditChain.seal` never existed, so the kill switch's seal was a
    permanent silent no-op under a card announcing the kill as recorded."""
    assert not hasattr(AuditChain, "seal"), (
        "AuditChain grew a seal() — revisit KillSwitchSkill, which was "
        "changed to call append() precisely because seal() did not exist"
    )
    assert hasattr(AuditChain, "append")
