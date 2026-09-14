"""The scan sync payload: what it publishes, to whom, and what it could not read.

`_build_scan_payload` rides every scan to POST /api/bot/sync/scan and is served
back by GET /api/bot/sync/scan, which is `optionalAuth` and never 401s — an
anonymous reader gets it, with `app/lib/flight.js` stripping dollar amounts
from the `circuit_breaker` section and nothing else. Four things were wrong
with what it carried.

**It published the venue's own error text unauthenticated.** The gate chip
called `entry_gate(engine)` with the default `include_detail=True`, whose
venue-auth reason appends `_safe_detail` of the credential preflight's
exception — the venue's host and path, scrubbed of keys but not of anything
else. `trade_gate`'s docstring says why the public form exists ("Scrubbed is
not the same as public") and /health already asks for it; the scan payload
did not. `gate_block` asks for the category.

**It threw the gate's `unknown` away.** `entry_gate` answers three things and
the chip kept `reasons`, so a gate that could not be read rendered as a green
✓. `circuit_breaker.gate` carries all three now, and the chip's `active` is
three-valued: True (a positive blocker), False (READ clear), None.

**Its daily-loss chip printed the PAPER book's dollars beside the LIVE
equity.** `f"Daily PnL: ${state.daily_pnl:+.2f}"` off `engine.portfolio` —
which live fills never touch, so in live mode it read `$+0.00`, a flat day on
an account that may have lost 4% — and the `$+` spelling walked through the
web's dollar scrub, whose pattern allowed a minus and not a plus. The chip is
the RATIO the breaker measures now, off the breaker's own live accumulator in
live mode (`live_daily_pnl_today`, a read with the writer's UTC-day rule), and
says "realized" because that is all the accumulator holds.

**Its macro block could not tell a crashed calendar from a blackout.** A
crashed evaluation is BLACKOUT `unreadable`, an exhausted schedule is BLACKOUT
`stale`, and an EMPTY calendar is NORMAL with `has_events()` False — a
confident all-clear from no data, by calendar.py's own docstring. The block
published the state alone. It carries the condition and `macro_state_words`'
sentence for it now, so the browser prints one vocabulary.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest

import bot.macro.calendar as cal
import bot.skills.scan_skill as ss
from bot.compat import UTC
from bot.macro.calendar import MacroCalendar, build_2026_calendar
from bot.macro.models import MacroRiskState
from tests.test_macro_calendar_staleness import EXHAUSTED_NOW, LIVE_NOW, _clock

# A ccxt-shaped preflight message: host, path, a query the key sits in.
DRIVER_TEXT = ("bitget GET https://api.bitget.com/api/v2/mix/account/accounts"
               "?productType=USDT-FUTURES&apiKey=bg_live_ABC123 401 Unauthorized"
               " {\"code\":\"40012\",\"msg\":\"apikey/password is incorrect\"}")
CATEGORY = "venue auth marked down"


def _cfg(live: bool, cap_pct=5.0, max_open=5):
    cfg = MagicMock()
    cfg.simulation_mode = not live
    cfg.live_trading_enabled = live
    cfg.is_live.return_value = live
    cfg.risk.max_daily_loss_pct = cap_pct
    cfg.risk.max_open_positions = max_open
    return cfg


def _risk(blocked_by="", cb_active=False, live_daily=None):
    r = NS(trading_blocked_by=blocked_by, circuit_breaker_active=cb_active)
    if live_daily is not None:
        r.live_daily_pnl_today = lambda: live_daily
    return r


def _engine(*, risk=None, auth_ok=True, detail="", halted=False, paper_daily=0.0,
            paper_equity=1000.0, open_positions=0, cache_total=1000.0):
    risk = risk if risk is not None else _risk()
    eng = NS(
        _halted=halted, risk=risk, risk_for=lambda uid="": risk,
        live_auth_healthy=lambda uid="": auth_ok,
        _live_auth_detail={"": detail} if detail else {},
        portfolio=NS(snapshot=lambda: NS(equity_usd=paper_equity, open_positions=open_positions,
                                         daily_pnl=paper_daily),
                     _history=[]),
        live_executor=NS(open_positions=[]),
    )
    eng.live_balance_cached = (lambda: {"total": cache_total}) if cache_total is not None else (lambda: None)
    return eng


def _payload(engine, cfg, live_data=None):
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=live_data), \
         patch.object(ss, "_build_features_block", return_value={}):
        return ss._build_scan_payload([], engine)


def _rule(payload, prefix):
    hits = [r for r in payload["circuit_breaker"]["rules"] if r["label"].startswith(prefix)]
    assert len(hits) == 1, (prefix, payload["circuit_breaker"]["rules"])
    return hits[0]


# ── the venue's text never reaches the published payload ─────────────────────

class TestNoDriverTextIsPublished:
    def test_a_marked_down_venue_is_named_by_category_only(self):
        eng = _engine(auth_ok=False, detail=DRIVER_TEXT)
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        wire = json.dumps(cb)
        assert CATEGORY in wire, "the halt itself must still be reported"
        for leak in ("https://", "api.bitget.com", "/api/v2/", "apiKey", "ABC123",
                     "40012", "incorrect"):
            assert leak not in wire, f"{leak!r} reached the anonymous scan payload"
        assert cb["gate"] == {"blocked": True, "unknown": False,
                              "reasons": [CATEGORY + ", a restart re-runs the check"]}
        assert _rule(cb and {"circuit_breaker": cb}, "Blocked:")["active"] is True

    def test_the_control_the_detail_form_would_have_leaked(self):
        """Proves the fixture reproduces the leak: the gated form of the same
        gate carries the host. Without this the test above could pass against
        a detail string nothing ever quoted."""
        from bot.core.trade_gate import entry_gate
        eng = _engine(auth_ok=False, detail=DRIVER_TEXT)
        gated = "; ".join(entry_gate(eng, live=True)["reasons"])
        assert "api.bitget.com" in gated and "ABC123" not in gated
        public = "; ".join(entry_gate(eng, live=True, include_detail=False)["reasons"])
        assert "api.bitget.com" not in public and CATEGORY in public

    def test_the_scan_payload_asks_for_the_public_form(self):
        from tests.source_scan import code_only
        src = code_only(open("bot/skills/scan_skill.py", encoding="utf-8").read())
        assert "entry_gate(engine, include_detail=False)" in src
        assert "entry_gate(engine)" not in src, "a second, detailed call would leak again"


# ── the gate's three answers reach the wire, and the chip reads them ─────────

class TestTheGateIsThreeValued:
    def test_blocked_names_the_cause_and_goes_red(self):
        # The 2026-07-29 breaker: warning-rate, NOT the narrow flag.
        eng = _engine(risk=_risk(blocked_by="warning_rate:api_errors", cb_active=False))
        cb = _payload(eng, _cfg(live=False))["circuit_breaker"]
        assert cb["gate"]["blocked"] is True and cb["gate"]["unknown"] is False
        chip = _rule({"circuit_breaker": cb}, "Blocked:")
        assert chip["active"] is True and "warning_rate:api_errors" in chip["label"]

    def test_clear_is_read_clear(self):
        cb = _payload(_engine(), _cfg(live=False))["circuit_breaker"]
        assert cb["gate"] == {"blocked": False, "unknown": False, "reasons": []}
        chip = _rule({"circuit_breaker": cb}, "Circuit Breaker")
        assert chip == {"label": "Circuit Breaker", "active": False}

    def test_unknown_is_neither_green_nor_red(self):
        # No _halted attribute and a risk engine whose fields cannot be read:
        # entry_gate answers unknown. The old chip rendered this as ✓.
        class Opaque:
            @property
            def trading_blocked_by(self):
                raise RuntimeError("streak probe unreadable")

            @property
            def circuit_breaker_active(self):
                raise RuntimeError("state unreadable")
        eng = _engine(risk=Opaque())
        del eng._halted
        cb = _payload(eng, _cfg(live=False))["circuit_breaker"]
        assert cb["gate"]["unknown"] is True and cb["gate"]["blocked"] is False
        chip = _rule({"circuit_breaker": cb}, "Circuit Breaker")
        assert chip["active"] is None and "unreadable" in chip["label"]

    def test_the_narrow_flag_alone_still_turns_it_red(self):
        eng = _engine(risk=_risk(blocked_by="", cb_active=True))
        cb = _payload(eng, _cfg(live=False))["circuit_breaker"]
        assert _rule({"circuit_breaker": cb}, "Circuit Breaker")["active"] is True

    def test_a_gate_that_cannot_be_asked_is_none_not_clear(self, monkeypatch):
        import bot.core.trade_gate as tg
        monkeypatch.setattr(tg, "entry_gate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
        cb = _payload(_engine(), _cfg(live=False))["circuit_breaker"]
        assert cb["gate"] is None
        chip = _rule({"circuit_breaker": cb}, "Circuit Breaker")
        assert chip["active"] is None and "unreadable" in chip["label"]

    def test_a_failed_snapshot_no_longer_drops_the_gate(self):
        eng = _engine(risk=_risk(blocked_by="drawdown"))
        eng.portfolio = NS(snapshot=lambda: (_ for _ in ()).throw(RuntimeError("book unreadable")),
                           _history=[])
        cb = _payload(eng, _cfg(live=False))["circuit_breaker"]
        assert cb["gate"]["blocked"] is True, "one raise used to delete every chip"
        assert _rule({"circuit_breaker": cb}, "Blocked:")["active"] is True
        daily = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert daily["active"] is None and "unread" in daily["label"]

    def test_the_wire_is_json(self):
        cb = _payload(_engine(), _cfg(live=False))["circuit_breaker"]
        json.dumps(cb)
        assert set(cb["gate"]) == {"blocked", "unknown", "reasons"}
        assert all(set(r) == {"label", "active"} for r in cb["rules"])
        assert all(r["active"] in (True, False, None) for r in cb["rules"])


# ── the daily-loss chip is a ratio, and reads the book it stands beside ──────

class TestTheDailyLossChip:
    def test_paper_prints_the_ratio_the_cap_is_on_and_no_dollar(self):
        cb = _payload(_engine(paper_daily=-60.0, paper_equity=1000.0), _cfg(live=False))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip == {"label": "Daily PnL: -6.0% of equity (cap 5%)", "active": True}
        assert "$" not in json.dumps(cb["rules"])

    def test_a_positive_day_carries_no_plus_dollar_either(self):
        # `$+12.34` is the spelling the web scrub could not see.
        cb = _payload(_engine(paper_daily=12.34, paper_equity=1000.0), _cfg(live=False))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip["label"] == "Daily PnL: +1.2% of equity (cap 5%)" and chip["active"] is False
        assert "$" not in chip["label"]

    def test_live_reads_the_breakers_own_accumulator_not_the_paper_book(self):
        # ASYMMETRIC on purpose: the paper book says +50%, the live accumulator
        # says -4% of the live equity. A chip reading the wrong book cannot
        # pass this, and a symmetric fixture could not tell them apart.
        eng = _engine(risk=_risk(live_daily=-40.0), paper_daily=500.0, paper_equity=1000.0,
                      cache_total=1000.0)
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip == {"label": "Daily PnL (realized today): -4.0% of equity (cap 5%)",
                        "active": False}

    def test_live_with_no_readable_equity_is_unmeasured(self):
        eng = _engine(risk=_risk(live_daily=-40.0), cache_total=None)
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        assert cb["live_unavailable"] is True
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip["active"] is None and "unmeasured" in chip["label"]
        assert "-40" not in chip["label"] and "$" not in chip["label"]

    def test_live_with_no_accumulator_on_the_risk_engine_is_unread(self):
        eng = _engine(risk=_risk(), cache_total=1000.0)   # no live_daily_pnl_today
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip["active"] is None and chip["label"].endswith(": unread")

    def test_a_zero_equity_is_not_a_flat_day(self):
        cb = _payload(_engine(paper_daily=-60.0, paper_equity=0.0), _cfg(live=False))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip["active"] is None and "unmeasured" in chip["label"]

    def test_an_unreadable_cap_keeps_the_ratio_and_withholds_the_verdict(self):
        cb = _payload(_engine(paper_daily=-60.0, paper_equity=1000.0),
                      _cfg(live=False, cap_pct=None))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Daily PnL")
        assert chip["label"] == "Daily PnL: -6.0% of equity (cap unread)" and chip["active"] is None

    def test_the_pure_rule_is_three_valued(self):
        assert ss.daily_pnl_rule(None, 100.0, 5.0, realized_only=False)["active"] is None
        assert ss.daily_pnl_rule(-5.0, 100.0, 5.0, realized_only=False)["active"] is True
        assert ss.daily_pnl_rule(-4.99, 100.0, 5.0, realized_only=False)["active"] is False
        assert ss.daily_pnl_rule(0.0, 100.0, 5.0, realized_only=True) == {
            "label": "Daily PnL (realized today): +0.0% of equity (cap 5%)", "active": False}


# ── the slot chip does not count a book nobody read ──────────────────────────

class TestTheOpenPositionsChip:
    def test_live_and_unread_says_so(self):
        eng = _engine(cache_total=None)
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        chip = _rule({"circuit_breaker": cb}, "Open Positions")
        assert chip == {"label": "Open Positions: unread/5", "active": None}

    def test_a_read_count_is_a_count(self):
        eng = _engine(open_positions=2)
        cb = _payload(eng, _cfg(live=False))["circuit_breaker"]
        assert _rule({"circuit_breaker": cb}, "Open Positions") == {
            "label": "Open Positions: 2/5", "active": False}
        assert ss.open_positions_rule(5, 5.0)["active"] is True
        assert ss.open_positions_rule(0, None) == {"label": "Open Positions: 0/?", "active": None}


# ── the live accumulator's reader applies the writer's day rule ──────────────

class TestLiveDailyPnlToday:
    @staticmethod
    def _risk_engine():
        import os
        import tempfile

        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        state = os.path.join(tempfile.mkdtemp(prefix="rc-scan-daily-"), "risk_state.json")
        return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)

    def test_todays_accumulator_is_returned(self):
        eng = self._risk_engine()
        eng._live_daily_pnl = -11.3
        eng._live_daily_day = time.strftime("%Y-%m-%d", time.gmtime())
        assert eng.live_daily_pnl_today() == -11.3

    def test_yesterdays_total_is_not_todays(self):
        # The writer rolls the day over on the NEXT close; a reader before it
        # would be handed yesterday's total under today's name.
        eng = self._risk_engine()
        eng._live_daily_pnl = -11.3
        eng._live_daily_day = "2020-01-01"
        assert eng.live_daily_pnl_today() == 0.0

    def test_the_writer_reader_and_reset_share_one_day_rule(self):
        from tests.source_scan import code_only
        src = code_only(open("bot/risk/risk_engine.py", encoding="utf-8").read())
        assert src.count('time.strftime("%Y-%m-%d", time.gmtime(int(self._now())))') == 1, (
            "the UTC-day rule must live in _utc_day and nowhere else")
        assert src.count("self._utc_day()") >= 4


# ── the macro block names the condition behind its state ─────────────────────

def _plant(monkeypatch, calendar):
    monkeypatch.setattr(cal, "MacroCalendar", lambda *a, **k: calendar)


class TestTheMacroBlockNamesItsCondition:
    def test_a_crashed_evaluation_is_unreadable_not_a_blackout(self, monkeypatch):
        def boom():
            raise RuntimeError("clock unreadable")
        _plant(monkeypatch, MacroCalendar(events=build_2026_calendar(), now_fn=boom))
        m = ss._macro_block()
        assert m["state"] == MacroRiskState.BLACKOUT.value
        assert m["unreadable"] is True and m["stale"] is False and m["has_events"] is True
        assert "FAILED" in m["reading"] and "nothing was measured" in m["reading"]
        json.dumps(m)

    def test_an_exhausted_schedule_is_stale_and_says_so(self, monkeypatch):
        _plant(monkeypatch, MacroCalendar(events=build_2026_calendar(), now_fn=_clock(EXHAUSTED_NOW)))
        m = ss._macro_block()
        assert m["state"] == MacroRiskState.BLACKOUT.value
        assert m["stale"] is True and m["unreadable"] is False and m["has_events"] is True
        assert "EXHAUSTED" in m["reading"]
        assert m["next_event"] is None and m["active_event"] is None

    def test_an_empty_calendar_is_not_a_normal_one(self, monkeypatch):
        empty = MacroCalendar(now_fn=_clock(LIVE_NOW))
        empty._events = []
        _plant(monkeypatch, empty)
        m = ss._macro_block()
        assert m["state"] == MacroRiskState.NORMAL.value
        assert m["has_events"] is False and m["unreadable"] is False and m["stale"] is False
        assert "no calendar loaded" in m["reading"]

    def test_a_quiet_loaded_calendar_is_a_plain_reading(self, monkeypatch):
        # RED HERRING: a real quiet window must not be accused of anything.
        _plant(monkeypatch, MacroCalendar(events=build_2026_calendar(), now_fn=_clock(LIVE_NOW)))
        m = ss._macro_block()
        assert m["state"] == MacroRiskState.NORMAL.value
        assert m["reading"] == "Normal"
        assert m["has_events"] is True and m["unreadable"] is False and m["stale"] is False
        assert m["next_event"] is not None

    def test_a_lockdown_names_its_event(self, monkeypatch):
        ev = build_2026_calendar()[0]
        _plant(monkeypatch, MacroCalendar(events=build_2026_calendar(), now_fn=_clock(ev.scheduled_utc)))
        m = ss._macro_block()
        assert m["state"] == MacroRiskState.EVENT_LOCKDOWN.value
        assert m["active_event"]["label"] == ev.label
        assert m["reading"] == "Event Lockdown"

    def test_the_words_are_the_shared_reading(self, monkeypatch):
        # One vocabulary: the chat card, the risk pane, /status and this block.
        seen = {}

        def words(snap, has_events=None):
            seen["args"] = (snap.state, has_events)
            return "THE READING"
        monkeypatch.setattr(cal, "macro_state_words", words)
        _plant(monkeypatch, MacroCalendar(events=build_2026_calendar(), now_fn=_clock(LIVE_NOW)))
        assert ss._macro_block()["reading"] == "THE READING"
        assert seen["args"] == (MacroRiskState.NORMAL, True)


@pytest.mark.parametrize("when", [LIVE_NOW, EXHAUSTED_NOW, datetime(2026, 1, 1, tzinfo=UTC)])
def test_every_macro_block_is_json_and_carries_the_three_fields(monkeypatch, when):
    _plant(monkeypatch, MacroCalendar(events=build_2026_calendar(), now_fn=_clock(when)))
    m = ss._macro_block()
    json.dumps(m)
    assert {"unreadable", "has_events", "reading"} <= set(m)
    assert isinstance(m["reading"], str) and m["reading"]
