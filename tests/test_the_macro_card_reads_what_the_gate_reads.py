"""The macro calendar card names the condition behind the state, and reads
what the entry gate reads.

`MacroCalendarSkill` rendered `snap.state` alone. `MacroCalendar.evaluate()`
reports BLACKOUT for two different facts — the hardcoded schedule is
EXHAUSTED (every event is in the past; `snapshot.stale=True`, written so the
monitor can alert) and the evaluation RAISED (fail-closed) — and the card
printed "⚫ Blackout" for both with no sentence, or, with fail-closed switched
off, "🟢 Normal" over an exhausted schedule with no events listed. `if
upcoming:` had no else, so "no future event remains" looked like a short
list. Every event's icon came from `getattr(ev, "severity", "medium")`, a
field MacroEvent does not have, so every row was yellow from a default, and
`ev.timestamp` does not exist either (`scheduled_utc` does), so the day never
printed. And the card read only the hardcoded calendar, while the risk engine
sizes entries off `macro_provider.get_context()` — a seed the card never
looked at, so it could say "Normal" while the gate refused entries off a stale
seed.

`macro_state_words` is the one reading of the state for every surface that
prints it (the card, the risk status pane, the header strip, /status);
`render_macro_calendar` is pure and shows the state with its condition, the
schedule (or why there is none), and the gate's own reading — with an
unreadable size multiplier rendered as unknown, never as full size.
`CheckEventRiskSkill` loses a fallback branch no source could reach whose
default was that full size.

Plant the state, read the card, with a red herring in each block: a healthy
calendar with real events prints a plain "Normal" and lists them; a measured
1.0 multiplier prints as 1.0; the live event row's impact is the record's.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from bot.compat import UTC
from bot.macro.calendar import MacroCalendar, build_2026_calendar, macro_state_words
from bot.macro.models import MacroEvent, MacroEventType, MacroRiskState, MacroStateSnapshot
from bot.skills import macro_skills
from bot.skills.macro_skills import CheckEventRiskSkill, MacroBriefSkill
from bot.skills.skill_registry import MacroCalendarSkill, render_macro_calendar
from bot.skills.telegram_handler import TelegramHandler
from tests.source_scan import code_only
from tests.test_macro_calendar_staleness import EXHAUSTED_NOW, LIVE_NOW, _cal, _clock

FOMC = MacroEvent(event_type=MacroEventType.FOMC_DECISION, scheduled_utc=datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
                  label="FOMC decision", impact="HIGH")
CPI = MacroEvent(event_type=MacroEventType.CPI, scheduled_utc=datetime(2026, 7, 15, 12, 30, tzinfo=UTC),
                 label="CPI (June)", impact="MEDIUM")


def _snap(state, **kw):
    return MacroStateSnapshot(state=state, **kw)


def _check(out, must_say, must_not_say):
    for phrase in must_say:
        assert phrase in out, f"omitted {phrase!r}\n---\n{out}"
    for phrase in must_not_say:
        assert phrase not in out, f"wrongly claimed {phrase!r}\n---\n{out}"


class _Ctx:
    """A v2 provider context; fields are set per test so an absent one is
    genuinely absent, not None."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ── the one reading of the state ────────────────────────────────────────────

class TestStateWords:
    def test_the_four_conditions_have_four_sentences(self):
        assert macro_state_words(_snap(MacroRiskState.NORMAL)) == "Normal"
        assert macro_state_words(_snap(MacroRiskState.PRE_EVENT_CAUTION)) == "Pre Event Caution"
        ex = macro_state_words(_snap(MacroRiskState.BLACKOUT, stale=True))
        assert ex.startswith("Blackout (calendar EXHAUSTED") and "no future event remains" in ex
        ex_open = macro_state_words(_snap(MacroRiskState.NORMAL, stale=True))
        assert ex_open.startswith("Normal (calendar EXHAUSTED"), ex_open
        un = macro_state_words(_snap(MacroRiskState.BLACKOUT, unreadable=True))
        assert "evaluation FAILED" in un and "nothing was measured" in un and "EXHAUSTED" not in un
        empty = macro_state_words(_snap(MacroRiskState.NORMAL), has_events=False)
        assert "no calendar loaded" in empty
        # RED HERRING: nobody asked about the schedule -> the plain label
        assert macro_state_words(_snap(MacroRiskState.NORMAL), has_events=None) == "Normal"
        assert macro_state_words(_snap(MacroRiskState.NORMAL), has_events=True) == "Normal"

    def test_a_snapshot_whose_state_cannot_be_read_is_unread(self):
        assert "could not be read" in macro_state_words(NS())
        assert macro_state_words(NS(state="risk_off")) == "Risk Off"

    def test_the_real_calendar_reports_its_own_exhaustion_and_a_crash(self):
        assert "EXHAUSTED" in macro_state_words(_cal(EXHAUSTED_NOW).evaluate())
        assert macro_state_words(_cal(EXHAUSTED_NOW, fail_closed_when_stale=False).evaluate()).startswith(
            "Normal (calendar EXHAUSTED")

        def boom():
            raise RuntimeError("clock unreadable")

        crashed = MacroCalendar(events=build_2026_calendar(), now_fn=boom).evaluate()
        assert crashed.state == MacroRiskState.BLACKOUT and crashed.unreadable is True and crashed.stale is False
        assert "evaluation FAILED" in macro_state_words(crashed)
        # RED HERRING: a quiet window on a loaded calendar is a plain Normal
        live = _cal(LIVE_NOW).evaluate()
        assert live.unreadable is False and macro_state_words(live) == "Normal"
        assert _cal(LIVE_NOW).has_events() is True
        empty = MacroCalendar(now_fn=_clock(LIVE_NOW))
        empty._events = []
        assert empty.has_events() is False


# ── the card ────────────────────────────────────────────────────────────────

def _card(snap, upcoming=(), gate="absent", has_events=True):
    ctx = None if gate == "absent" else gate
    return render_macro_calendar(snap, list(upcoming), ctx, has_events=has_events)


class TestTheCard:
    def test_an_exhausted_schedule_is_named_with_no_events_and_a_lapsed_protection(self):
        for state in (MacroRiskState.BLACKOUT, MacroRiskState.NORMAL):
            out = _card(_snap(state, stale=True), upcoming=[])
            _check(out, ["calendar EXHAUSTED", "Upcoming</b>: none — the schedule is exhausted",
                         "protection from this calendar has lapsed"],
                   ["<b>Normal</b>", "<b>Blackout</b>", "none scheduled", "could not be read"])

    def test_a_crashed_evaluation_says_nothing_was_measured(self):
        out = _card(_snap(MacroRiskState.BLACKOUT, unreadable=True), upcoming=[FOMC])
        _check(out, ["evaluation FAILED", "Upcoming</b>: could not be read", "do not say the schedule is clear"],
               ["EXHAUSTED", "FOMC decision", "<b>Blackout</b>"])

    def test_an_empty_calendar_is_not_a_normal_one(self):
        out = _card(_snap(MacroRiskState.NORMAL), upcoming=[], has_events=False)
        _check(out, ["no calendar loaded", "protects against nothing"], ["<b>Normal</b>", "none scheduled"])

    def test_a_loaded_calendar_lists_its_events_from_the_record(self):
        # RED HERRING: the plain label IS right here, and the impact is the
        # event's own field — never a "medium" from a default.
        snap = _snap(MacroRiskState.PRE_EVENT_CAUTION, next_event=CPI, time_until_next=timedelta(hours=5))
        out = _card(snap, upcoming=[CPI, FOMC])
        _check(out, ["<b>Pre Event Caution</b>", "Next event in: <code>5.0h</code>",
                     "<b>CPI (June)</b> (medium impact)", "<b>FOMC decision</b> (high impact)",
                     "Wed Jul 15 • <code>2026-07-15 12:30 UTC</code>", "2026-07-15 08:30 ET",
                     "Wed Jul 29"],
               ["EXHAUSTED", "not on record", "none scheduled", "could not be read"])
        bare = MacroEvent(event_type=MacroEventType.NFP, scheduled_utc=FOMC.scheduled_utc, label="NFP", impact="")
        _check(_card(_snap(MacroRiskState.NORMAL), upcoming=[bare]), ["<b>NFP</b> (impact not on record)"],
               ["medium"])

    def test_an_active_lockdown_names_the_event(self):
        snap = _snap(MacroRiskState.EVENT_LOCKDOWN, active_event=FOMC, time_until_next=timedelta(minutes=20))
        out = _card(snap, upcoming=[FOMC])
        _check(out, ["<b>Event Lockdown</b>", "Active: <code>FOMC decision</code>",
                     "Next event in: <code>20min</code>"], [])

    def test_a_loaded_calendar_with_nothing_ahead_says_none_scheduled(self):
        out = _card(_snap(MacroRiskState.NORMAL), upcoming=[], has_events=True)
        _check(out, ["Upcoming</b>: none scheduled"], ["EXHAUSTED", "no calendar loaded"])

    def test_the_gate_section_reads_the_provider(self):
        ctx = _Ctx(risk_state="REDUCE_SIZE", size_multiplier=0.5, explanation="CPI in 40 minutes", is_stale=False,
                   is_blind=False)
        out = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC], gate=ctx)
        _check(out, ["Entry gate: risk state <code>REDUCE_SIZE</code>, size multiplier <code>0.5</code>",
                     "<i>CPI in 40 minutes</i>"],
               ["unavailable", "BLIND", "STALE"])
        # RED HERRING: a measured 1.0 is printed as 1.0
        full = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC],
                     gate=_Ctx(risk_state="CLEAR", size_multiplier=1.0, explanation=""))
        assert "size multiplier <code>1.0</code>" in full

    def test_an_unreadable_multiplier_is_unknown_never_full_size(self):
        out = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC], gate=_Ctx(risk_state="CLEAR", explanation=""))
        _check(out, ["size multiplier <code>unknown</code>"], ["<code>1.0</code>"])
        none = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC],
                     gate=_Ctx(risk_state="CLEAR", size_multiplier=None, explanation=""))
        assert "size multiplier <code>none</code>" in none

    def test_a_stale_or_blind_gate_is_flagged(self):
        stale = _Ctx(risk_state="BLOCK_NEW_ENTRIES", size_multiplier=0.0, is_stale=True, is_blind=False,
                     explanation="Macro calendar is stale (generated 2026-01-01, max age 48h) — blocking new entries.")
        out = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC], gate=stale)
        _check(out, ["risk state <code>BLOCK_NEW_ENTRIES</code>", "size multiplier <code>0.0</code>",
                     "gate's calendar is STALE", "blocking new entries"], ["BLIND"])
        blind = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC],
                      gate=_Ctx(risk_state="BLOCK_NEW_ENTRIES", size_multiplier=0.0, is_blind=True))
        assert "gate's calendar is BLIND" in blind

    def test_a_gate_that_could_not_be_read_or_is_not_wired_says_which(self):
        raised = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC], gate=RuntimeError("seed unreadable"))
        _check(raised, ["Entry gate: <code>could not be read</code>", "do not tell the user the gate is clear"],
               ["seed unreadable", "unavailable"])
        absent = _card(_snap(MacroRiskState.NORMAL), upcoming=[FOMC], gate="absent")
        _check(absent, ["Entry gate: <code>unavailable</code>", "no v2 macro provider is wired"], ["could not be read"])

    def test_the_explanation_is_escaped_and_bounded(self):
        out = _card(_snap(MacroRiskState.NORMAL), upcoming=[], gate=_Ctx(risk_state="CLEAR", size_multiplier=1.0,
                                                                          explanation="<b>x</b>" + "y" * 400))
        assert "&lt;b&gt;x&lt;/b&gt;" in out and "y" * 200 not in out


# ── the skill, driven ───────────────────────────────────────────────────────

class TestTheSkill:
    def _run(self, engine):
        return asyncio.run(MacroCalendarSkill().execute(engine))

    def test_the_real_exhausted_calendar_is_told_as_exhausted(self):
        eng = NS(macro_calendar=_cal(EXHAUSTED_NOW), macro_provider=None)
        out = self._run(eng)
        _check(out, ["Blackout (calendar EXHAUSTED", "schedule is exhausted", "Entry gate: <code>unavailable</code>"],
               ["<b>Blackout</b>", "<b>Normal</b>"])
        # fail-open: the state says Normal and the card still says exhausted
        out = self._run(NS(macro_calendar=_cal(EXHAUSTED_NOW, fail_closed_when_stale=False), macro_provider=None))
        _check(out, ["Normal (calendar EXHAUSTED"], ["<b>Normal</b>"])

    def test_a_crashing_calendar_is_told_as_unreadable(self):
        def boom():
            raise RuntimeError("clock")

        out = self._run(NS(macro_calendar=MacroCalendar(events=build_2026_calendar(), now_fn=boom),
                           macro_provider=None))
        _check(out, ["evaluation FAILED", "Upcoming</b>: could not be read"], ["EXHAUSTED", "FOMC"])

    def test_an_empty_real_calendar_is_told_as_empty(self):
        # A MacroCalendar with no events evaluates NORMAL and is not stale
        # (it never HAD events) — the skill has to ask has_events() itself.
        empty = MacroCalendar(now_fn=_clock(LIVE_NOW))
        empty._events = []
        assert empty.evaluate().state == MacroRiskState.NORMAL
        out = self._run(NS(macro_calendar=empty, macro_provider=None))
        _check(out, ["Normal (no calendar loaded", "protects against nothing"], ["<b>Normal</b>", "none scheduled"])

    def test_a_listing_that_raises_is_unread_not_empty(self):
        # evaluate() answers; upcoming() raises. The old skill would have
        # rendered "none scheduled" off the [] it substituted.
        class Cal:
            def evaluate(self):
                return _snap(MacroRiskState.NORMAL)

            def upcoming(self, limit=5):
                raise RuntimeError("listing unreadable")

            def has_events(self):
                return True

        out = self._run(NS(macro_calendar=Cal(), macro_provider=None))
        _check(out, ["<b>Normal</b>", "Upcoming</b>: could not be read"],
               ["none scheduled", "listing unreadable", "EXHAUSTED"])

    def test_a_live_calendar_and_a_gate_reading_both_print(self):
        # RED HERRING: the operator's real provider raising must not blank
        # the schedule — the two sections are read separately.
        class Provider:
            def get_context(self, symbol=None):
                return _Ctx(risk_state="CLEAR", size_multiplier=1.0, explanation="", is_stale=False, is_blind=False)

        out = self._run(NS(macro_calendar=_cal(LIVE_NOW), macro_provider=Provider()))
        _check(out, ["<b>Normal</b>", "Upcoming</b>",
                     "risk state <code>CLEAR</code>, size multiplier <code>1.0</code>"],
               ["EXHAUSTED", "unavailable"])

        class Raising:
            def get_context(self, symbol=None):
                raise RuntimeError("seed unreadable")

        out = self._run(NS(macro_calendar=_cal(LIVE_NOW), macro_provider=Raising()))
        _check(out, ["<b>Normal</b>", "Upcoming</b>", "Entry gate: <code>could not be read</code>"],
               ["seed unreadable"])


# ── the other three surfaces read the same words ───────────────────────────

class TestTheOtherSurfaces:
    def test_status_bias_names_the_condition(self):
        from tests.test_status_survives_an_unreadable_macro_calendar import _host
        assert "EXHAUSTED" in _host(_cal(EXHAUSTED_NOW))._status_market_bias()
        assert _host(_cal(LIVE_NOW))._status_market_bias() == "Normal"

    def test_the_header_strip_names_the_condition(self, monkeypatch):
        import bot.skills.telegram_handler as th
        monkeypatch.setattr(th, "entry_gate",
                            lambda engine, *a, **kw: {"blocked": False, "unknown": False, "reasons": []})
        import bot.core.live_readiness as lr
        monkeypatch.setattr(lr, "mode_label", lambda *a, **kw: "PAPER")
        h = TelegramHandler.__new__(TelegramHandler)
        h.engine = NS(macro_calendar=_cal(EXHAUSTED_NOW),
                      user_portfolios=NS(all_portfolios=lambda: [], combined_snapshot=lambda: None,
                                         total_open_positions=lambda: 0))
        out = h._banner()
        assert "macro: blackout (calendar exhausted" in out, out
        h.engine.macro_calendar = _cal(LIVE_NOW)
        assert h._banner().endswith("macro: normal")

    def test_the_risk_pane_reads_the_seam(self):
        import bot.skills.skill_registry as reg
        src = code_only(inspect.getsource(reg.CheckRiskSkill._status))
        assert "macro_state_words(macro)" in src
        assert 'macro.state.value.replace("_", " ").title()' not in src


# ── the sibling cards: no full-size default anywhere ───────────────────────

class TestEventRisk:
    def test_the_unreachable_fallback_is_gone_and_its_default_with_it(self):
        src = code_only(inspect.getsource(CheckEventRiskSkill.execute))
        assert "check_risk" not in src and '("size_multiplier", 1.0)' not in src and "1.0" not in src
        brief = code_only(inspect.getsource(MacroBriefSkill.execute))
        assert 'getattr(ctx, "size_multiplier", 1.0)' not in brief and '_present(ctx, "size_multiplier")' in brief

    def test_a_source_with_only_a_check_risk_method_is_told_the_gate_cannot_be_read(self):
        class Legacy:
            def check_risk(self, symbol):
                return NS(size_multiplier=0.25)

        eng = NS(macro_provider=Legacy(), macro_calendar=None)
        out = asyncio.run(CheckEventRiskSkill().execute(eng, symbol="BTC/USDT"))
        _check(out, ["no <code>get_context()</code> method"], ["1.0", "0.25", "Size multiplier"])

    def test_the_brief_renders_an_absent_multiplier_as_unknown(self):
        class Source:
            def get_context(self, symbol=None):
                return _Ctx(risk_state="CLEAR", explanation="", is_stale=False, is_blind=False)

            def upcoming(self, limit=5):
                return []

        out = asyncio.run(MacroBriefSkill().execute(NS(macro_provider=Source(), macro_calendar=None)))
        assert "Size multiplier: <code>unknown</code>" in out and "1.0" not in out
        assert macro_skills._present(_Ctx(size_multiplier=1.0), "size_multiplier") == "1.0"


def test_the_skill_renders_through_the_pure_card():
    src = code_only(inspect.getsource(MacroCalendarSkill.execute))
    assert "render_macro_calendar(" in src and "get_context()" in src and "has_events" in src
    assert "severity" not in src and "timestamp" not in src


@pytest.mark.parametrize("state", list(MacroRiskState))
def test_every_state_has_an_icon(state):
    out = _card(_snap(state), upcoming=[FOMC])
    assert "<b>" in out and "⚪" not in out.split("\n")[2]
