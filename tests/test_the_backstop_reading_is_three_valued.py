"""The risk backstop, as the scan payload publishes it for the website.

Nothing under ``app/`` could reach the live drawdown, the halt threshold, the
slot cap or the gate state, and every ``drawdown`` the website holds is the
historical drawdown of its own trade curve — a different quantity under the
same name. ``circuit_breaker.backstop`` carries the engine's own figures now,
and three things about its shape are pinned here because each was an
objection to the design's first draft.

**The slot count comes from where it exists.** The design built the count
inside a risk-engine method from ``live_open_count`` — a PARAMETER of
``evaluate()`` that no method on the engine can see — so on a live deployment
it would have published the PAPER book's count against the LIVE binding cap:
the defect ``drawdown_status`` was cured of, one field over. ``slot_status``
takes the caller's count, and an unread count stays None.

**A raised aggregator is not a single venue.** ``_person_totals`` folds an
exception into None, which the gate fails closed on and a card would have
printed as an exact ``2 / 5`` over a count whose cross-venue half failed.
``_person_totals_state`` keeps the third word.

**A composer fault is a marker, not an absence.** The key is present in every
payload this build makes; ``{"unreadable": true}`` is a fault; an ABSENT key
means an older build — a redeploy instruction that a fault must not be
reported as.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

from bot.formatters import risk_backstop as rb
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from tests.source_scan import code_only
from tests.test_the_scan_payload_says_what_it_could_not_read import (
    CATEGORY,
    DRIVER_TEXT,
    _cfg,
    _engine,
    _payload,
    _risk,
)

# The web's scrub drops any key carrying one of these tokens for an anonymous
# caller (app/lib/flight.js DOLLAR_KEY). The block must survive it whole.
DOLLAR_KEY = re.compile(
    r"(usd|equity|balance|notional|margin|collateral|dollars?|account_value|pnl|cash|funds|wallet_value)", re.I)


def _risk_engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-backstop-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _totals(open_positions=0, unreadable=()):
    return NS(open_positions=open_positions, unreadable=tuple(unreadable))


# ── slot_status: the count is the caller's, the cap is the binding one ────────

class TestSlotStatus:
    def test_the_count_is_the_callers_and_an_unread_count_stays_unread(self):
        eng = _risk_engine()
        s = eng.slot_status(2)
        assert s == {"used": 2, "floor": False, "note": "", "cap": s["cap"], "person": "unset"}
        assert eng.slot_status(None)["used"] is None, "the paper book is never substituted for an unread live count"
        assert eng.slot_status(0)["used"] == 0, "a measured zero is a reading"
        for junk in (True, -1, "2", 2.0):
            assert eng.slot_status(junk)["used"] is None, repr(junk)

    def test_the_person_total_tightens_only(self):
        eng = _risk_engine()
        eng.set_person_totals_fn(lambda: _totals(open_positions=4))
        s = eng.slot_status(2)
        assert s["used"] == 4 and s["floor"] is False and s["person"] == "read"
        eng.set_person_totals_fn(lambda: _totals(open_positions=1))
        assert eng.slot_status(3)["used"] == 3, "a person total below this book's count never lowers it"
        assert eng.slot_status(None)["used"] == 1, "an unread book beside a read person total is the person's count"
        # A total the aggregator did not carry is not a total of zero: the
        # caller's count stands, and an unread one stays unread.
        eng.set_person_totals_fn(lambda: NS(unreadable=()))
        assert eng.slot_status(2)["used"] == 2
        assert eng.slot_status(None)["used"] is None, "no person total + no book count is unread, not 0"
        eng.set_person_totals_fn(lambda: NS(open_positions=None, unreadable=()))
        assert eng.slot_status(None)["used"] is None

    def test_a_floor_names_the_venue_it_could_not_see(self):
        eng = _risk_engine()
        eng.set_person_totals_fn(lambda: _totals(open_positions=2, unreadable=("bybit",)))
        s = eng.slot_status(2)
        assert s["floor"] is True and "bybit" in s["note"] and s["used"] == 2

    def test_a_raised_aggregator_is_a_floor_and_not_a_single_venue(self):
        eng = _risk_engine()

        def boom():
            raise RuntimeError("aggregator down")
        eng.set_person_totals_fn(boom)
        s = eng.slot_status(2)
        assert s["person"] == "raised"
        assert s["floor"] is True, "a count whose cross-venue half failed is a floor, not an exact count"
        assert "could not be read" in s["note"]
        assert s["used"] == 2
        # And the gate's own reader still folds it — that is its contract.
        assert eng._person_totals() is None
        assert eng._person_totals_incomplete() == ""

    def test_the_cap_is_the_binding_one_in_live_mode_only(self):
        eng = _risk_engine()
        with patch("bot.risk.risk_engine.CONFIG") as cfg:
            cfg.risk.max_open_positions = 5
            cfg.execution.max_live_open_positions = 3
            cfg.is_live.return_value = False
            assert eng.slot_status(1)["cap"] == 5
            cfg.is_live.return_value = True
            assert eng.slot_status(1)["cap"] == 3
            cfg.execution.max_live_open_positions = 9
            assert eng.slot_status(1)["cap"] == 5, "the lower of the two refuses first"
            cfg.risk.max_open_positions = "nope"
            assert eng.slot_status(1)["cap"] is None, "an unreadable cap is None, never a default"

    def test_the_risk_card_reads_the_cap_through_the_one_home(self):
        src = code_only(open("bot/skills/portfolio_commands.py", encoding="utf-8").read())
        i = src.index("async def _cmd_risk")
        body = src[i:src.index("\n    @guard", i + 1) if "\n    @guard" in src[i + 1:] else len(src)]
        assert "slot_status(open_count)" in body
        assert "max_live_open_positions" not in src, "the min() has one home now: RiskEngine.slot_status"


# ── backstop_block: existing readings, never re-derived; a fault is a marker ──

def _stub_risk(st, slots=None, slot_raises=False):
    def slot_status(count=None):
        if slot_raises:
            raise RuntimeError("slots unreadable")
        return {"used": count, "floor": False, "note": "", "cap": 5, "person": "unset"} if slots is None else slots
    return NS(drawdown_status=lambda: st, slot_status=slot_status)


HEALTHY = {"drawdown_pct": 3.2, "drawdown_source": "live", "effective_limit_pct": 7.0,
           "config_live_limit_pct": 7.0, "override_pct": None, "live_hardening": True}


class TestBackstopBlock:
    def test_a_healthy_read_is_the_card_readings_and_only_percent_count_flag(self):
        b = rb.backstop_block(NS(risk=_stub_risk(HEALTHY)), open_count=2,
                              gate={"blocked": False, "unknown": False, "reasons": []})
        assert b == {"drawdown_pct": 3.2, "limit_pct": 7.0, "source": "live", "verdict": "Healthy",
                     "override_pct": None, "default_limit_pct": 7.0, "hardening": True,
                     "slots_used": 2, "slots_cap": 5, "slots_floor": False, "slots_note": "",
                     "slots_person": "unset", "gate": {"blocked": False, "unknown": False, "reasons": []}}
        assert tuple(b.keys()) == rb.KEYS
        for k in b:
            assert not DOLLAR_KEY.search(k), f"{k} would be dropped by the web's anonymous scrub"
        assert "$" not in json.dumps(b)

    def test_an_empty_drawdown_status_is_an_unread_drawdown_beside_a_read_gate(self):
        # `{}` is drawdown_status's ONE failure signal — not a composer fault.
        b = rb.backstop_block(NS(risk=_stub_risk({})), open_count=1,
                              gate={"blocked": True, "unknown": False, "reasons": ["kill switch engaged"]})
        assert "unreadable" not in b
        assert b["drawdown_pct"] is None and b["limit_pct"] is None and b["source"] is None
        assert b["verdict"] == "Unknown"
        assert b["hardening"] is None and b["override_pct"] is None
        assert b["gate"]["blocked"] is True and b["slots_used"] == 1

    def test_the_drawdown_triple_is_validated_by_the_card_seams_not_here(self):
        nan = dict(HEALTHY, drawdown_pct=float("nan"))
        b = rb.backstop_block(NS(risk=_stub_risk(nan)), open_count=0, gate=None)
        assert b["drawdown_pct"] is None and b["verdict"] == "Unknown"
        zero_limit = dict(HEALTHY, effective_limit_pct=0)
        b = rb.backstop_block(NS(risk=_stub_risk(zero_limit)), open_count=0, gate=None)
        assert b["drawdown_pct"] == 3.2 and b["limit_pct"] is None and b["verdict"] == "Unknown", \
            "a zero threshold is rejected, not used — it would score every book Critical"
        crit = dict(HEALTHY, drawdown_pct=7.5)
        assert rb.backstop_block(NS(risk=_stub_risk(crit)), open_count=0, gate=None)["verdict"] == "Critical"
        warn = dict(HEALTHY, drawdown_pct=5.0)
        assert rb.backstop_block(NS(risk=_stub_risk(warn)), open_count=0, gate=None)["verdict"] == "Warning"
        odd = dict(HEALTHY, drawdown_source="venue")
        assert rb.backstop_block(NS(risk=_stub_risk(odd)), open_count=0, gate=None)["source"] is None

    def test_a_measured_zero_drawdown_is_a_reading(self):
        flat = dict(HEALTHY, drawdown_pct=0.0)
        b = rb.backstop_block(NS(risk=_stub_risk(flat)), open_count=0, gate=None)
        assert b["drawdown_pct"] == 0.0 and b["verdict"] == "Healthy"

    def test_only_a_bool_is_a_hardening_reading_and_only_a_number_an_override(self):
        st = dict(HEALTHY, live_hardening="yes", override_pct="3")
        b = rb.backstop_block(NS(risk=_stub_risk(st)), open_count=0, gate=None)
        assert b["hardening"] is None and b["override_pct"] is None
        st = dict(HEALTHY, live_hardening=False, override_pct=3.0, config_live_limit_pct=math.inf)
        b = rb.backstop_block(NS(risk=_stub_risk(st)), open_count=0, gate=None)
        assert b["hardening"] is False and b["override_pct"] == 3.0 and b["default_limit_pct"] is None

    def test_the_gate_is_the_builders_own_block_and_a_malformed_one_is_none(self):
        eng = NS(risk=_stub_risk(HEALTHY))
        assert rb.backstop_block(eng, open_count=0, gate=None)["gate"] is None
        assert rb.backstop_block(eng, open_count=0, gate={"blocked": "no", "unknown": False})["gate"] is None
        assert rb.backstop_block(eng, open_count=0, gate={"blocked": False})["gate"] is None
        g = rb.backstop_block(eng, open_count=0, gate={"blocked": False, "unknown": True, "reasons": "x"})["gate"]
        assert g == {"blocked": False, "unknown": True, "reasons": []}

    def test_the_slots_are_the_engines_reading(self):
        slots = {"used": 6, "floor": True, "note": "could not read bybit", "cap": 5, "person": "read"}
        b = rb.backstop_block(NS(risk=_stub_risk(HEALTHY, slots=slots)), open_count=2, gate=None)
        assert (b["slots_used"], b["slots_cap"], b["slots_floor"], b["slots_note"], b["slots_person"]) == \
            (6, 5, True, "could not read bybit", "read")
        b = rb.backstop_block(NS(risk=_stub_risk(HEALTHY, slots={})), open_count=2, gate=None)
        assert (b["slots_used"], b["slots_cap"], b["slots_floor"]) == (None, None, None), \
            "slot_status's {} is its failure signal: nothing is read off it"

    def test_a_composer_fault_is_the_marker_never_an_absence_and_never_a_raise(self):
        assert rb.backstop_block(None, open_count=0, gate=None) == {"unreadable": True}
        assert rb.backstop_block(NS(), open_count=0, gate=None) == {"unreadable": True}
        raising = NS(risk=_stub_risk(HEALTHY, slot_raises=True))
        assert rb.backstop_block(raising, open_count=0, gate=None) == {"unreadable": True}
        assert rb.backstop_block(NS(risk=NS()), open_count=0, gate=None) == {"unreadable": True}
        assert rb.backstop_block(NS(risk=_stub_risk("junk")), open_count=0, gate=None)["verdict"] == "Unknown", \
            "a non-dict status is an unread drawdown, not a fault"
        # The marker is a fresh dict each time: a caller mutating one cannot
        # poison the next payload.
        a = rb.backstop_block(None, open_count=0, gate=None)
        a["x"] = 1
        assert rb.backstop_block(None, open_count=0, gate=None) == {"unreadable": True}
        # ...on the fault path too, which is the one that returns from the except.
        b = rb.backstop_block(NS(risk=NS()), open_count=0, gate=None)
        b["x"] = 1
        assert rb.backstop_block(NS(risk=NS()), open_count=0, gate=None) == {"unreadable": True}
        assert rb.UNREADABLE == {"unreadable": True}, "the module's own marker was never mutated"


# ── the payload carries it, with the count the slot chip reads ────────────────

def _full_risk(**kw):
    r = _risk(**kw)
    r.drawdown_status = lambda: dict(HEALTHY)
    r.slot_status = lambda count=None: {"used": count, "floor": False, "note": "", "cap": 5, "person": "unset"}
    return r


class TestThePayloadCarriesIt:
    def test_present_in_every_payload_this_build_makes(self):
        cb = _payload(_engine(risk=_full_risk()), _cfg(live=False))["circuit_breaker"]
        assert "backstop" in cb
        assert cb["backstop"]["verdict"] == "Healthy" and cb["backstop"]["drawdown_pct"] == 3.2
        assert cb["backstop"]["gate"] == cb["gate"], "one gate read, one answer"

    def test_the_slot_count_is_the_chips_count(self):
        # Paper: the paper snapshot's count.
        cb = _payload(_engine(risk=_full_risk(), open_positions=3), _cfg(live=False))["circuit_breaker"]
        assert cb["backstop"]["slots_used"] == 3
        # Live with the venue read: the venue's count.
        live = {"equity": 1234.0, "net_pnl": 1.0, "win_rate": 50.0, "total_trades": 2, "open_count": 2,
                "open_positions": [], "closed_trades": []}
        cb = _payload(_engine(risk=_full_risk(), open_positions=3), _cfg(live=True), live_data=live)["circuit_breaker"]
        assert cb["backstop"]["slots_used"] == 2
        # Live with NO venue read and no balance cache: unread, never the
        # paper book's 3 under the live cap.
        eng = _engine(risk=_full_risk(), open_positions=3, cache_total=None)
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        assert cb["live_unavailable"] is True
        assert cb["backstop"]["slots_used"] is None

    def test_an_older_risk_engine_or_a_fault_is_the_marker_and_the_rest_of_the_payload_survives(self):
        cb = _payload(_engine(risk=_risk()), _cfg(live=False))["circuit_breaker"]
        assert cb["backstop"] == {"unreadable": True}
        assert cb["gate"] == {"blocked": False, "unknown": False, "reasons": []}
        assert any(r["label"].startswith("Circuit Breaker") for r in cb["rules"])

    def test_no_engine_is_the_marker(self):
        import bot.skills.scan_skill as ss
        with patch("bot.config.CONFIG", _cfg(live=False)), \
             patch.object(ss, "_fetch_live_exchange_data", return_value=None), \
             patch.object(ss, "_build_features_block", return_value={}):
            cb = ss._build_scan_payload([], None)["circuit_breaker"]
        assert cb["backstop"] == {"unreadable": True}

    def test_no_driver_text_and_no_dollar_reaches_the_block(self):
        eng = _engine(risk=_full_risk(), auth_ok=False, detail=DRIVER_TEXT)
        cb = _payload(eng, _cfg(live=True))["circuit_breaker"]
        wire = json.dumps(cb["backstop"])
        assert CATEGORY in wire
        for leak in ("https://", "api.bitget.com", "apiKey", "ABC123", "$"):
            assert leak not in wire, leak
        for k in cb["backstop"]:
            assert not DOLLAR_KEY.search(k), k
