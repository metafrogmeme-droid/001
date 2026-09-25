"""Trade QUALITY chooses a size and a leverage within the bounds.

#199 made the live bounds a function of the account; #201 made the leverage
the risk gate reduces reach the VENUE. Neither read the one thing the
analyzer measured about THIS trade. Driven before this slice, a 0.58 idea and
a 0.92 idea were sized identically on every account without a Kelly estimate
(twenty closes on record), and the leverage they were set at never saw
confidence at all: `_high_conviction_margin` is binary and flat, and
`_standard_leverage` takes a SYMBOL.

WHAT IS DRIVEN HERE
-------------------
* the reading is three-valued, and a MANUAL ticket's confidence is a stamp:
  `build_manual_idea` writes 1.0 on every one, so it cleared every floor by
  construction and takes NO rung now
* the rungs, AT their floors (a fixture either side of a boundary measures
  nothing about the comparison that decides it)
* a table is refused rather than clamped: a multiplier above 1.0 is growth,
  and the defaults are used with a note on the check line
* the size half tightens the CAP as well as the pre-cap figure -- the
  USER_RISK_PREF lesson: the cap binds on ~every crypto trade, so a pre-cap
  multiply alone is clamped straight back and the flag tightens nothing
* the leverage half rides on the idea and reaches the venue's `set_leverage`,
  and the margin-risk verdict is measured at THAT leverage; the two writers
  of one attribute keep the lower
* both halves are SHADOW when off: byte-identical size, one audit record
* Kelly and the high-conviction target read the same reading, so a manual
  ticket's stamp cannot scale either
* the size TRACE: which step decided the figure, on the check and on both
  fill cards, reset per evaluation, a no-op step never recorded, and a
  change the trace did not see said as one
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import os
import pathlib
import re
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot.core.engine as engine_mod
import bot.core.session_aware as session_aware
import bot.risk.risk_engine as rem
from bot.compat import UTC
from bot.config import CONFIG
from bot.core import live_executor as lx
from bot.core import market_scanner
from bot.core import order_rules as orr
from bot.core.engine import RuneClawEngine
from bot.core.flag_status import audit_flag_report
from bot.core.leverage import RISK_CAP_ATTR, set_margin_risk_cap, tighten_leverage_cap
from bot.core.size_trace import (
    TRACE_ATTR,
    note_size_step,
    reset_size_trace,
    size_basis,
    size_path,
    size_steps,
)
from bot.risk import quality_ladder as ql
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeExecution, TradeIdea, TradeStatus
from tests.leverage_drive import drive_ensure_leverage, lev

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Tuesday 10:00 UTC is the London session: size multiplier 1.0, and not a
# Friday. The dollar figures in this file are that session's figures. Asian
# hours (00:00-08:00 UTC) multiply by 0.75, which is how a $1,000 pre-cap
# fixture published $750 and a $50 ceiling published $37.50 on the 2026-09-22
# main run — the ladder was in shadow the whole time.
_LONDON = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _measure_at_london(monkeypatch):
    """A caller that passes no bar time is measured at London.

    ``as_of`` still wins, so a test can ask for Asian and get the cut.
    The import of ``get_current_session`` is inside ``evaluate``, so the
    patch is the module the function imports from.
    """
    real = session_aware.get_current_session

    def _at(now=None):
        return real(_LONDON if now is None else now)

    monkeypatch.setattr(session_aware, "get_current_session", _at)


def _engine(balance=10_000.0):
    state = os.path.join(tempfile.mkdtemp(prefix="rc-ql-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


def _idea(conf=0.72, source="scan", stop=97.0, tp=109.0):
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=stop, take_profit=tp, confidence=conf,
        reasoning="quality", source=source, timestamp=datetime.now(UTC),
    )


def _cfg(size=False, lev=False, rungs=None, **exchange):
    """The engine's module-level CONFIG with the ladder flags set."""
    kw = dict(quality_ladder_size_enabled=size, quality_ladder_leverage_enabled=lev)
    if rungs is not None:
        kw["quality_ladder_rungs"] = rungs
    c = dataclasses.replace(CONFIG, risk=dataclasses.replace(CONFIG.risk, **kw))
    if exchange:
        c = dataclasses.replace(c, exchange=dataclasses.replace(c.exchange, **exchange))
    return c


def _lines(check, prefix="QUALITY_LADDER"):
    return [ln for ln in check.checks_passed if ln.startswith(prefix)]


@pytest.fixture
def risk_audits(monkeypatch):
    rows = []

    def _rec(log, msg, **kw):
        rows.append((msg, kw))

    monkeypatch.setattr(rem, "audit", _rec)
    return rows


# ── the reading ─────────────────────────────────────────────────────────────

class TestTheReadingIsThreeValued:
    def test_an_analyzer_idea_is_measured_at_its_confidence(self):
        r = ql.quality_reading(_idea(0.72))
        assert (r.measured, r.confidence) == (True, 0.72)
        assert "0.72" in r.why

    @pytest.mark.parametrize("stamp", [1.0, 0.6, 0.0])
    def test_a_manual_ticket_is_unmeasured_whatever_the_stamp_holds(self, stamp):
        """`build_manual_idea` writes confidence=1.0; nothing measured it."""
        r = ql.quality_reading(_idea(stamp, source="manual"))
        assert r.measured is False and r.confidence is None
        assert "stamp" in r.why

    @pytest.mark.parametrize("raw", [None, "abc", float("nan"), float("inf"), 1.5, -0.1, True])
    def test_an_unreadable_confidence_is_unmeasured_with_its_own_reason(self, raw):
        r = ql.quality_reading(SimpleNamespace(confidence=raw, source="scan"))
        assert r.measured is False
        assert "stamp" not in r.why, "an unreadable field is not a manual ticket"

    def test_no_idea_is_unread(self):
        assert ql.quality_reading(None).measured is False


class TestTheRungsAtTheirFloors:
    @pytest.mark.parametrize("conf,rung", [
        (1.0, "A"), (0.85, "A"),      # AT the floor counts
        (0.8499, "B"), (0.70, "B"),
        (0.6999, "C"), (0.0, "C"),
    ])
    def test_the_default_table(self, conf, rung):
        v = ql.ladder_verdict(_idea(conf), ql.DEFAULT_RUNGS)
        assert v.rung == rung, v
        assert v.why == f"rung {rung} at confidence {conf:.2f}"

    def test_the_top_rung_is_the_bounds_themselves(self):
        v = ql.ladder_verdict(_idea(0.9), ql.DEFAULT_RUNGS)
        assert (v.size_mult, v.leverage_mult) == (1.0, 1.0)

    def test_an_unmeasured_quality_takes_no_rung(self):
        v = ql.ladder_verdict(_idea(1.0, source="manual"), ql.DEFAULT_RUNGS)
        assert (v.measured, v.rung, v.size_mult, v.leverage_mult) == (False, None, 1.0, 1.0)
        assert v.why.endswith("; no rung")

    def test_every_default_rung_tightens_or_leaves_alone(self):
        assert all(r.size_mult <= 1.0 and r.leverage_mult <= 1.0 for r in ql.DEFAULT_RUNGS)


class TestATableIsRefusedNotClamped:
    @pytest.mark.parametrize("text,note", [
        ("A:0.9:1.3:1.0,B:0.0:0.5:0.5", "outside (0, 1]"),      # growth is not a rung
        ("A:0.9:1.0:1.2,B:0.0:0.5:0.5", "outside (0, 1]"),
        ("A:0.5:1:1,B:0.7:0.5:0.5", "not strictly descending"),
        ("A:0.9:1:1,B:0.1:0.5:0.5", "last rung's floor must be 0.0"),
        ("A:0.9:1:1,A:0.0:0.5:0.5", "duplicate rung names"),
        ("A:0.9:1", "malformed rung"),
        ("A:x:1:1,B:0:1:1", "unreadable number"),
        ("A:1.5:1:1,B:0:1:1", "outside [0, 1]"),
        ("A:0.9:0:1,B:0:1:1", "outside (0, 1]"),                 # a zero multiplier is a refusal, not a size
    ])
    def test_a_refused_table_is_the_defaults_with_a_note(self, text, note):
        rungs, why = ql.parse_rungs(text)
        assert rungs == ql.DEFAULT_RUNGS
        assert note in why and "defaults in use" in why

    def test_growth_is_refused_rather_than_clamped_to_one(self):
        """A table whose author wrote 1.3 wanted growth. Handing them 1.0
        silently is a second answer about what the table says."""
        rungs, why = ql.parse_rungs("A:0.9:1.3:1.0,B:0.0:0.5:0.5")
        assert not any(r.name == "A" and r.size_mult == 1.0 and r.floor == 0.9 for r in rungs)
        assert why

    def test_an_empty_text_is_the_defaults_with_no_note(self):
        assert ql.parse_rungs("") == (ql.DEFAULT_RUNGS, "")

    def test_a_valid_custom_table_parses(self):
        rungs, why = ql.parse_rungs("X:0.5:0.9:0.9,Y:0.0:0.4:0.4")
        assert why == ""
        assert [r.name for r in rungs] == ["X", "Y"]
        assert ql.rung_for(ql.quality_reading(_idea(0.5)), rungs).name == "X"
        assert ql.rung_for(ql.quality_reading(_idea(0.49)), rungs).name == "Y"

    def test_the_config_default_is_the_leafs_default(self):
        """Two spellings of one table, pinned equal. `bot/config.py` cannot
        import the leaf (the import would cycle), so the default is written
        twice and this is what keeps the copies one answer."""
        tree = ast.parse((ROOT / "bot" / "config.py").read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_env" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "QUALITY_LADDER_RUNGS"):
                found.append(node.args[1].value)
        assert found == [ql.DEFAULT_RUNGS_TEXT], found

    def test_the_config_reader_uses_the_engine_config_it_is_handed(self):
        rungs, note = ql.rungs_from_config(SimpleNamespace(quality_ladder_rungs="Q:0.0:0.5:0.5"))
        assert [r.name for r in rungs] == ["Q"] and note == ""
        rungs, note = ql.rungs_from_config(SimpleNamespace(quality_ladder_rungs="nope"))
        assert rungs == ql.DEFAULT_RUNGS and "defaults in use" in note


class TestTheLeverageRung:
    @pytest.mark.parametrize("standard,conf,floor,expect", [
        (5, 0.72, 1, 4),    # B: int(5 * 0.8)
        (5, 0.60, 1, 3),    # C: int(5 * 0.6)
        (5, 0.90, 1, 5),    # A: untouched
        (3, 0.72, 1, 2),    # int(2.4)
        (2, 0.60, 2, 2),    # int(1.2) = 1, floored at 2
        (1, 0.60, 2, 1),    # never above the standard, whatever the floor
    ])
    def test_reduce_only_never_under_the_floor(self, standard, conf, floor, expect):
        v = ql.ladder_verdict(_idea(conf), ql.DEFAULT_RUNGS)
        assert ql.ladder_leverage(standard, v, floor=floor) == expect

    def test_an_unmeasured_quality_leaves_the_leverage_alone(self):
        v = ql.ladder_verdict(_idea(1.0, source="manual"), ql.DEFAULT_RUNGS)
        assert ql.ladder_leverage(5, v, floor=2) == 5


class TestKellyReadsTheSameReading:
    def test_measured_is_the_confidence_and_manual_is_unscaled(self):
        assert ql.kelly_confidence_factor(_idea(0.72))[0] == 0.72
        f, why = ql.kelly_confidence_factor(_idea(0.6, source="manual"))
        assert f == 1.0 and "unscaled" in why

    def test_the_engines_kelly_sizer_hands_kelly_the_reading_not_the_stamp(self):
        """Patch the Kelly formula and read the confidence it was handed."""
        eng = _engine()
        hist = eng._portfolio._history
        for i in range(15):
            hist.append(TradeExecution(trade_id=f"W{i}", asset="BTC/USDT", direction=Direction.LONG,
                                       entry_price=100.0, stop_loss=98.0, take_profit=106.0,
                                       exit_price=105.0, quantity=1.0, status=TradeStatus.EXECUTED, pnl=50.0))
        for i in range(5):
            hist.append(TradeExecution(trade_id=f"L{i}", asset="BTC/USDT", direction=Direction.LONG,
                                       entry_price=100.0, stop_loss=98.0, take_profit=106.0,
                                       exit_price=98.0, quantity=1.0, status=TradeStatus.EXECUTED, pnl=-20.0))
        handed = []
        eng.kelly_position_size = lambda conf, wr, aw, al: handed.append(conf) or 0.1
        eng._kelly_size_usd(_idea(0.72), 10_000.0)
        eng._kelly_size_usd(_idea(0.6, source="manual"), 10_000.0)   # a stamp of 0.6, not 1.0
        eng.get_recommended_size(_idea(0.6, source="manual"))
        assert handed == [0.72, 1.0, 1.0], handed


# ── the risk gate ───────────────────────────────────────────────────────────

class TestTheSizeHalf:
    def test_london_is_the_session_these_figures_name_and_asia_still_cuts(self, monkeypatch):
        """The wall clock is not the fixture. Patching ``datetime.now`` to
        04:00 UTC must leave an untimed evaluation at $1,000; deleting the
        London default would publish $750. An explicit Asian ``as_of`` still
        cuts, so the default is not a sizer that was switched off."""
        class _Clock:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 9, 22, 4, 0, tzinfo=UTC)

        monkeypatch.setattr(session_aware, "datetime", _Clock)
        london = session_aware.get_current_session()
        asian = session_aware.get_current_session(datetime(2026, 9, 22, 4, 0, tzinfo=UTC))
        assert (london.session_name, london.size_multiplier) == ("london", 1.0)
        assert asian.size_multiplier == 0.75
        eng = _engine()
        idea = dict(stop=80.0, tp=160.0)
        at_london = eng.evaluate(_idea(**idea), atr=2.0).position_size_usd
        at_asia = eng.evaluate(
            _idea(**idea), atr=2.0, as_of=datetime(2026, 9, 22, 4, 0, tzinfo=UTC),
        ).position_size_usd
        assert at_london == pytest.approx(1_000.0)
        assert at_asia == pytest.approx(750.0)

    def test_off_is_byte_identical_and_the_shadow_says_what_it_would_have_done(
            self, monkeypatch, risk_audits):
        eng = _engine()
        baseline = eng.evaluate(_idea(), atr=2.0).position_size_usd
        risk_audits.clear()     # the baseline evaluation shadows too; count the next one alone
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=False, lev=False))
        check = eng.evaluate(_idea(), atr=2.0)
        assert check.position_size_usd == baseline
        assert not _lines(check)
        shadow = [kw for msg, kw in risk_audits if kw.get("action") == "quality_ladder"]
        assert len(shadow) == 1 and shadow[0]["result"] == "SHADOW"
        data = shadow[0]["data"]
        assert data["rung"] == "B" and data["would_be_usd"] == round(baseline * 0.75, 2)
        assert data["size_enabled"] is False and data["leverage_enabled"] is False

    def test_rung_b_is_three_quarters_because_the_cap_is_tightened_too(self, monkeypatch):
        """Pre-cap alone would be clamped straight back to the same figure:
        the fixed-fractional size here is ~5x the notional cap, so a
        multiply above the cap changes nothing and the flag tightens nothing
        -- the first version of USER_RISK_PREF, which its own test caught."""
        eng = _engine()
        off = eng.evaluate(_idea(), atr=2.0).position_size_usd
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        check = eng.evaluate(_idea(), atr=2.0)
        assert check.position_size_usd == pytest.approx(off * 0.75, rel=1e-9)
        assert "QUALITY_LADDER: size x0.75 (rung B at confidence 0.72)" in check.checks_passed
        assert "quality ladder x0.75" in check.size_basis
        assert any(p.startswith("quality ladder rung B x0.75") for p in check.size_path)

    def test_the_pre_cap_half_applies_when_the_cap_does_not_bind(self, monkeypatch):
        """A 20% stop sizes fixed-fractional at 10% of equity, under the 13%
        cap -- so here the PRE-CAP multiply is the one that decides, and a
        ladder that tightened only the cap would change nothing."""
        eng = _engine()
        off = eng.evaluate(_idea(stop=80.0, tp=160.0), atr=2.0).position_size_usd
        assert off == pytest.approx(1_000.0), "the fixture must sit under the cap"
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        check = eng.evaluate(_idea(stop=80.0, tp=160.0), atr=2.0)
        assert check.position_size_usd == pytest.approx(750.0)
        assert check.size_basis.startswith("quality ladder rung B x0.75 decided $750.00")

    def test_the_top_rung_keeps_full_size_and_says_so(self, monkeypatch, risk_audits):
        eng = _engine()
        off = eng.evaluate(_idea(0.9), atr=2.0).position_size_usd
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        check = eng.evaluate(_idea(0.9), atr=2.0)
        assert check.position_size_usd == off
        assert "QUALITY_LADDER: full size (rung A at confidence 0.90)" in check.checks_passed
        assert not [kw for _, kw in risk_audits if kw.get("action") == "quality_ladder"], \
            "nothing would have tightened, so there is no shadow to report"

    def test_a_manual_ticket_takes_no_rung_and_both_lines_say_why(self, monkeypatch, risk_audits):
        eng = _engine()
        off = eng.evaluate(_idea(1.0, source="manual"), atr=2.0).position_size_usd
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        idea = _idea(1.0, source="manual")
        check = eng.evaluate(idea, atr=2.0)
        assert check.position_size_usd == off
        lines = _lines(check)
        assert any(ln.startswith("QUALITY_LADDER: size not applied (manual ticket") for ln in lines)
        assert any(ln.startswith("QUALITY_LADDER: leverage not capped (manual ticket") for ln in lines)
        assert getattr(idea, RISK_CAP_ATTR, None) is None
        assert not [kw for _, kw in risk_audits if kw.get("action") == "quality_ladder"]

    def test_rung_c_halves_the_size(self, monkeypatch):
        eng = _engine()
        off = eng.evaluate(_idea(0.6), atr=2.0).position_size_usd
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        check = eng.evaluate(_idea(0.6), atr=2.0)
        assert check.verdict.value == "APPROVED"
        assert check.position_size_usd == pytest.approx(off * 0.5, rel=1e-9)

    def test_a_refused_table_sizes_by_the_defaults_and_says_so(self, monkeypatch):
        eng = _engine()
        off = eng.evaluate(_idea(0.95), atr=2.0).position_size_usd
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True, rungs="A:0.9:1.3:1.0,B:0.0:0.5:0.5"))
        check = eng.evaluate(_idea(0.95), atr=2.0)
        assert check.position_size_usd == off, "growth was refused, not applied"
        assert any("defaults in use" in ln for ln in _lines(check))

    def test_the_size_half_alone_still_shadows_the_leverage_half(self, monkeypatch, risk_audits):
        eng = _engine()
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=False))
        idea = _idea()
        check = eng.evaluate(idea, atr=2.0)
        assert getattr(idea, RISK_CAP_ATTR, None) is None
        assert not any(ln.startswith("QUALITY_LADDER: leverage") for ln in _lines(check))
        shadow = [kw for _, kw in risk_audits if kw.get("action") == "quality_ladder"]
        assert len(shadow) == 1 and "would_be_usd" not in shadow[0]["data"], \
            "the size half was applied, so a would-be size would be a second figure"


class TestTheLeverageHalf:
    def test_the_cap_rides_on_the_idea_and_the_margin_risk_is_measured_at_it(self, monkeypatch):
        eng = _engine()
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        idea = _idea()
        check = eng.evaluate(idea, atr=2.0)
        assert getattr(idea, RISK_CAP_ATTR) == 4
        assert "QUALITY_LADDER: leverage 5x→4x (rung B at confidence 0.72)" in check.checks_passed
        mr = [ln for ln in check.checks_passed if ln.startswith("MARGIN_RISK")]
        assert mr and "× 4x" in mr[0], mr

    def test_the_venue_is_set_to_the_rungs_leverage(self, monkeypatch):
        """The end the cap has to reach. Sized at 4x with the venue at 5x
        bounds nothing -- the sizing leverage cancels out of the ratio."""
        eng = _engine()
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        idea = _idea()
        eng.evaluate(idea, atr=2.0)
        out = drive_ensure_leverage([lev(4)], target=5, idea=idea, monkeypatch=monkeypatch)
        assert not out.aborted, out.why
        assert out.set_calls and all(c[0] == 4 for c in out.set_calls), out.set_calls

    def test_the_ladder_runs_first_and_the_lower_cap_wins(self, monkeypatch):
        """Rung B takes 5x to 4x; at an 8% stop 4x is still over the 30%
        margin-risk cap, so the verdict reduces AGAIN, from 4x, to 3x -- and
        the attribute both writers share holds the lower figure."""
        eng = _engine()
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True, dynamic_leverage_enabled=True))
        idea = _idea(stop=92.0)
        check = eng.evaluate(idea, atr=2.0)
        assert "QUALITY_LADDER: leverage 5x→4x (rung B at confidence 0.72)" in check.checks_passed
        mr = [ln for ln in check.checks_passed if ln.startswith("MARGIN_RISK")]
        assert mr and "4x→3x" in mr[0], mr
        assert getattr(idea, RISK_CAP_ATTR) == 3

    def test_a_rung_under_the_floor_stays_and_the_line_says_why(self, monkeypatch):
        eng = _engine()
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True, default_leverage=2))
        idea = _idea(0.6)
        check = eng.evaluate(idea, atr=2.0)
        assert getattr(idea, RISK_CAP_ATTR, None) is None
        line = [ln for ln in _lines(check) if "leverage stays 2x" in ln]
        assert line and "under the 2x floor" in line[0], _lines(check)

    def test_the_two_writers_keep_the_lower_cap(self):
        idea = SimpleNamespace()
        tighten_leverage_cap(idea, 4)
        set_margin_risk_cap(idea, 3)
        tighten_leverage_cap(idea, 5)
        assert getattr(idea, RISK_CAP_ATTR) == 3
        tighten_leverage_cap(idea, None)
        assert getattr(idea, RISK_CAP_ATTR) == 3


class TestTheFlags:
    def test_both_halves_default_off(self):
        assert CONFIG.risk.quality_ladder_size_enabled is False
        assert CONFIG.risk.quality_ladder_leverage_enabled is False

    def test_both_flags_are_on_the_flags_card(self):
        flat = {env: on for _, items in audit_flag_report() for env, _, on in items}
        assert "QUALITY_LADDER_SIZE_ENABLED" in flat
        assert "QUALITY_LADDER_LEVERAGE_ENABLED" in flat

    def test_the_env_example_names_the_three_knobs(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for name in ("QUALITY_LADDER_SIZE_ENABLED", "QUALITY_LADDER_LEVERAGE_ENABLED",
                     "QUALITY_LADDER_RUNGS"):
            assert name in text, name


# ── the size trace ──────────────────────────────────────────────────────────

class TestTheTrace:
    def test_the_check_carries_which_step_decided_the_figure(self):
        check = _engine().evaluate(_idea(), atr=2.0)
        assert check.size_basis.startswith("notional cap 13% of $10,000.00 equity decided $1,300.00")
        assert check.size_path[0].startswith("fixed-fractional (swing risk 2% / stop 3.00%)")
        assert check.size_path[-1].startswith("notional cap")

    def test_a_step_that_changed_nothing_is_not_recorded(self):
        eng = _engine()
        loose = eng.evaluate(_idea(), atr=2.0, max_position_usd=1e9)
        assert not any("execution ceiling" in p for p in loose.size_path), loose.size_path
        tight = eng.evaluate(_idea(), atr=2.0, max_position_usd=50.0)
        assert tight.position_size_usd == 50.0
        assert tight.size_basis.startswith("execution ceiling (per-account bound) decided $50.00")

    def test_the_trace_is_reset_per_evaluation(self):
        eng = _engine()
        idea = _idea()
        eng.evaluate(idea, atr=2.0)
        first = list(getattr(idea, TRACE_ATTR))
        eng.evaluate(idea, atr=2.0)
        assert list(getattr(idea, TRACE_ATTR)) == first, "appended across two evaluations"

    def test_the_leaf_says_when_something_off_the_record_changed_the_figure(self):
        idea = SimpleNamespace()
        assert size_basis(idea) == "" and size_path(idea) == []
        reset_size_trace(idea)
        note_size_step(idea, "fixed-fractional", 666.67)
        assert size_basis(idea) == "fixed-fractional decided $666.67"
        note_size_step(idea, "no-op", 666.67, before=666.67)
        assert len(size_steps(idea)) == 1
        note_size_step(idea, "cap", 100.0, before=666.67)
        assert size_basis(idea, 100.004) == "cap decided $100.00 (2 steps from fixed-fractional $666.67)"
        assert size_basis(idea, 80.0).endswith("; then changed to $80.00 by a step not on record")
        note_size_step(idea, "junk", float("nan"))
        note_size_step(idea, "junk", "abc")
        assert len(size_steps(idea)) == 2

    def test_an_object_that_refuses_the_attribute_carries_no_trace_and_nothing_raises(self):
        class _Frozen:
            __slots__ = ()
        reset_size_trace(_Frozen())
        note_size_step(_Frozen(), "x", 1.0)
        assert size_basis(_Frozen()) == ""


class TestTheCardsPrintTheBasis:
    def test_the_live_card_prints_the_sizing_line_only_when_there_is_a_trace(self):
        ex = lx.LiveExecutor.__new__(lx.LiveExecutor)
        idea = _idea()
        reset_size_trace(idea)
        note_size_step(idea, "fixed-fractional (swing risk 2% / stop 3.00%)", 666.67)
        note_size_step(idea, "per-account bound (balance)", 100.0, before=666.67)
        args = ("buy", 4, True, 100.0, 4.0, 100.0, "oid", "sl", "tp", None, True, True,
                {"failure_stage": "ok"}, 0.0, None, False, "", None)
        card = ex._entry_filled_card(idea, *args, size_usd=100.0)
        line = [ln for ln in card.splitlines() if ln.startswith("- Sizing:")]
        assert line == ["- Sizing: <i>per-account bound (balance) decided $100.00 "
                        "(2 steps from fixed-fractional (swing risk 2% / stop 3.00%) $666.67)</i>"]
        cost = [ln for ln in card.splitlines() if ln.startswith("- Cost:")]
        assert cost, "the sizing line sits under the cost it explains"
        changed = ex._entry_filled_card(idea, *args, size_usd=80.0)
        assert "then changed to $80.00 by a step not on record" in changed
        assert "Sizing" not in ex._entry_filled_card(_idea(), *args, size_usd=100.0)

    def test_the_live_card_escapes_the_basis(self):
        ex = lx.LiveExecutor.__new__(lx.LiveExecutor)
        idea = _idea()
        reset_size_trace(idea)
        note_size_step(idea, "<b>planted</b>", 100.0)
        card = ex._entry_filled_card(idea, "buy", 4, True, 100.0, 4.0, 100.0, "oid", "sl", "tp",
                                     None, True, True, {"failure_stage": "ok"}, 0.0, None, False,
                                     "", None, size_usd=100.0)
        assert "&lt;b&gt;planted&lt;/b&gt;" in card and "<b>planted</b>" not in card

    def test_the_paper_card_prints_the_basis_and_runs_at_the_capped_leverage(self):
        class _PaperEng:
            _simulate_paper_fill = RuneClawEngine._simulate_paper_fill
            _high_conviction_margin = RuneClawEngine._high_conviction_margin
            _high_conviction_ceiling = RuneClawEngine._high_conviction_ceiling

            def __init__(self):
                self._pending_ideas = {}
                self._book = PortfolioTracker(initial_balance=10_000.0)
                self.user_portfolios = SimpleNamespace(get=lambda uid: self._book)
                self.learning = SimpleNamespace(log_decision=lambda **k: None)

            def _per_user_margin_cap(self, uid):
                return None

            def _transition(self, *a, **k):
                pass

        idea = _idea()
        reset_size_trace(idea)
        note_size_step(idea, "fixed-fractional (swing risk 2% / stop 3.00%)", 6666.67)
        note_size_step(idea, "notional cap 13% of $10,000.00 equity (quality ladder x0.75)", 975.0)
        tighten_leverage_cap(idea, 4)
        recheck = SimpleNamespace(position_size_usd=975.0, checks_passed=[])
        card = asyncio.run(_PaperEng()._simulate_paper_fill(idea, recheck, "u1", idea.id))
        assert "@ 4x" in card, card
        assert "Sizing: <i>notional cap 13% of $10,000.00 equity (quality ladder x0.75) decided $975.00" in card
        # No cap on record: the paper fill runs at the standard leverage, as it always has.
        plain = _idea()
        card2 = asyncio.run(_PaperEng()._simulate_paper_fill(plain, recheck, "u1", plain.id))
        assert f"@ {int(CONFIG.exchange.default_leverage)}x" in card2
        assert "Sizing:" not in card2


# ── the high-conviction target ──────────────────────────────────────────────

class _HcEng:
    _high_conviction_margin = RuneClawEngine._high_conviction_margin
    _high_conviction_ceiling = RuneClawEngine._high_conviction_ceiling

    def _per_user_margin_cap(self, user_id):
        return None


_HC_FIELDS = ("high_conviction_enabled", "high_conviction_min_confidence",
              "high_conviction_margin_usd")


@pytest.fixture
def high_conviction_on():
    before = {f: getattr(CONFIG.execution, f) for f in _HC_FIELDS}
    for k, v in dict(high_conviction_enabled=True, high_conviction_min_confidence=0.70,
                     high_conviction_margin_usd=100.0).items():
        object.__setattr__(CONFIG.execution, k, v)
    try:
        yield
    finally:
        for k, v in before.items():
            object.__setattr__(CONFIG.execution, k, v)


class TestTheHighConvictionTargetReadsTheSameReading:
    def test_a_manual_ticket_no_longer_clears_the_floor_on_its_stamp(self, high_conviction_on, monkeypatch):
        rows = []
        monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: rows.append(kw))
        assert _HcEng()._high_conviction_margin(_idea(1.0, source="manual"), 37.5, "u") == 37.5
        assert [r.get("result") for r in rows if r.get("action") == "high_conviction_size"] == ["UNMEASURED"]

    def test_an_analyzer_idea_over_the_floor_still_takes_the_target(self, high_conviction_on, monkeypatch):
        monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: None)
        open_gate = SimpleNamespace(base_multiplier=1.0, base_ceiling_usd=None)
        assert _HcEng()._high_conviction_margin(_idea(0.9), 37.5, "u",
                                                check=open_gate) == 100.0
        assert _HcEng()._high_conviction_margin(_idea(0.6), 37.5, "u") == 37.5

    def test_off_is_untouched_and_audits_nothing(self, monkeypatch):
        rows = []
        monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: rows.append(kw))
        assert _HcEng()._high_conviction_margin(_idea(1.0, source="manual"), 37.5, "u") == 37.5
        assert rows == []


# ── every size assignment on both money paths records itself ────────────────
#
# A SCAN, and stated as one: `_confirm_trade_inner` is a 400-line async
# method behind Telegram, the compliance engine and an exchange, and
# `execute` places an order. The claim is a SHAPE -- an assignment to
# `size_usd` is followed by its `note_size_step` -- which is what keeps the
# live card's "then changed by a step not on record" clause from ever being
# the ordinary reading. A tuple-unpack whose right-hand side is a call to a
# function in the same checked set delegates the note to that function.

def _unnoted_size_assignments(src: str, func_names: set) -> list:
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in func_names}
    assert set(funcs) == set(func_names), f"missing {func_names - set(funcs)}"

    def _callee(value):
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            return value.func.attr
        return None

    def _is_note(st):
        return (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
                and isinstance(st.value.func, ast.Name) and st.value.func.id == "note_size_step")

    problems = []
    for name, fn in funcs.items():
        for node in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                for i, st in enumerate(block):
                    if not isinstance(st, ast.Assign) or len(st.targets) != 1:
                        continue
                    tgt = st.targets[0]
                    elts = tgt.elts if isinstance(tgt, ast.Tuple) else [tgt]
                    if not any(isinstance(t, ast.Name) and t.id == "size_usd" for t in elts):
                        continue
                    if isinstance(tgt, ast.Tuple) and _callee(st.value) in func_names:
                        continue
                    nxt = block[i + 1] if i + 1 < len(block) else None
                    if not _is_note(nxt):
                        problems.append(f"{name}:{st.lineno}")
    return problems


class TestEverySizeStepIsOnTheRecord:
    def test_the_engines_two_money_paths(self):
        src = (ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
        assert _unnoted_size_assignments(src, {"_confirm_trade_inner", "_simulate_paper_fill"}) == []

    def test_the_executors_three(self):
        src = (ROOT / "bot" / "core" / "live_executor.py").read_text(encoding="utf-8")
        assert _unnoted_size_assignments(
            src, {"execute", "_apply_order_rules", "_recalculate_limit_entry"}) == []

    def test_the_rule_can_fail(self):
        """A rule no input can reach is a claim that there is a check."""
        planted = (
            "def f(idea, size_usd):\n"
            "    size_usd = size_usd * 0.5\n"
            "    x = 1\n"
            "    if x:\n"
            "        size_usd = 3\n"
            "        note_size_step(idea, 'ok', size_usd)\n"
            "    a, size_usd = self.g(size_usd)\n"
            "    b, size_usd = self.h(size_usd)\n"
            "def g(idea, size_usd):\n"
            "    return 1, size_usd\n"
        )
        assert _unnoted_size_assignments(planted, {"f", "g"}) == ["f:2", "f:8"]

    def test_the_executor_records_the_bound_only_when_it_binds(self, monkeypatch):
        """`execute` driven to a PLANTED preflight refusal: far enough for the
        per-account bound and the order rules to have run on the trace, and
        not one line further. The executor works on a shallow copy of the
        idea and the copy shares the list, which is the whole reason the
        engine's idea can carry the executor's steps.

        The stand-in preflight takes ``**kw`` and RECORDS what it was handed:
        its first draft spelled three parameters, and the full gate refused
        the bounds-shadow slice on it when `execute` grew a fourth
        (``size_before_bound``, the size held BEFORE the clamp) -- a
        hand-written stand-in that must remember each parameter is one that
        will forget the next. Recording it makes that argument a drive
        rather than a signature the fixture has to keep in step."""
        ex = lx.LiveExecutor.__new__(lx.LiveExecutor)
        ex._persistence_broken = False
        seen = []

        async def _avail():
            return 50.0

        async def _funding(idea):
            return None

        ex.available_margin = _avail
        ex._note_funding_rate = _funding
        ex._note_settlement_clock = lambda idea: None
        ex._preflight_check = lambda size_usd, symbol="", available_usd=None, **kw: (
            seen.append((size_usd, kw.get("size_before_bound"))) or "planted refusal")
        monkeypatch.setattr(lx, "audit", lambda log, msg, **kw: None)

        bound = lx.size_bounds_for(50.0).per_trade_usd
        idea = _idea()
        reset_size_trace(idea)
        note_size_step(idea, "fixed-fractional", bound * 10)
        out = asyncio.run(ex.execute(idea, bound * 10))
        assert "planted refusal" in out and seen == [(bound, bound * 10)], \
            "the preflight is handed the clamped size AND the size held before the clamp"
        assert size_basis(idea, bound).startswith(f"per-account bound ({lx.size_bounds_for(50.0).basis}) decided")

        small = _idea()
        reset_size_trace(small)
        note_size_step(small, "fixed-fractional", bound / 2)
        asyncio.run(ex.execute(small, bound / 2))
        assert [s.label for s in size_steps(small)] == ["fixed-fractional"], \
            "a bound that did not bind must not be recorded as the decider"

    def test_every_weekend_queued_class_is_a_size_reduced_class(self):
        """Why the `before=` on the weekend note is a convention today and not
        a check: `is_weekend_queued` answers True only for classes outside
        `_ALWAYS_OPEN` / `_PRE_IPO`, and every class the classifier can emit
        there is in `_WEEKDAY_ONLY`, which `adjust_size_for_weekend` reduces by
        35%. So the note always records a real change -- the mutation that
        dropped its `before=` was an EQUIVALENT mutant over product inputs.
        The `before=` stays as the uniform rule every clamp follows, and THIS
        pins the property: the day a class joins one set and not the other,
        this fails rather than the note quietly naming a no-op the decider."""
        saturday = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
        assert saturday.weekday() == 5
        emitted = set(re.findall(r'return "([A-Za-z-]+)"',
                                 inspect.getsource(market_scanner._classify_symbol)))
        emitted |= set(getattr(market_scanner, "_HL_BUILDER_CLASS", {}).values())
        placed = set(orr._WEEKDAY_ONLY) | set(orr._ALWAYS_OPEN) | set(orr._PRE_IPO)
        assert emitted and emitted <= placed, emitted - placed
        queued = [c for c in emitted if orr.is_weekend_queued(c, now=saturday)]
        assert queued, "the fixture reaches no weekend-queued class at all"
        for cls in queued:
            assert orr.adjust_size_for_weekend(100.0, cls, True) < 100.0, cls

    def test_the_executor_hands_the_card_its_own_final_figure(self):
        """The `size_usd=` keyword at the one call site -- a card handed no
        figure prints the basis and can never say a step went unrecorded."""
        src = (ROOT / "bot" / "core" / "live_executor.py").read_text(encoding="utf-8")
        calls = re.findall(r"self\._entry_filled_card\((.*?)\)\n", src, re.S)
        assert len(calls) == 1 and "size_usd=size_usd" in calls[0], calls
