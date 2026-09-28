"""The circuit-breaker trip card and the drawdown tier card read what the gate read.

Driven on the real `RiskEngine` and the real `ProactiveMonitor`, a live trip
at 8.59% against a 7.00% limit (the card an operator pasted) printed:

    - Drawdown: see cause          `drawdown_status()` answered 8.59% / live / 7.0
    - Daily loss: -0.09%           on a +$8 WINNING day: the gate's MAGNITUDE
                                   (`abs()`) behind a hard-coded minus
    - Open Positions: 0            for a book nobody read (a raising executor,
                                   or none)
    - Triggered At: <boot time>    on the monitor's FIRST pass after a restart
                                   with the breaker persisted open

and beside it `DRAWDOWN AT 85% OF LIMIT ... halts all entries at 100% ...
Consider reducing size` for the same 8.59%: the tier label was fixed at 85 for
any fraction past 0.85, under a breaker that had already tripped.

The readings are the engine's (`circuit_trip_at`, persisted and restored by
its own helper; `last_daily_pnl_reading`, signed, with its basis), the two
cards are pure (`bot/formatters/breaker_card.py`), and the monitor's first
pass is a first pass (`_last_cb_state` starts as None).
"""
from __future__ import annotations

import json
import math
import time
import types
from datetime import datetime, timezone

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor
from bot.formatters import breaker_card
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

SIM_AT = 1_700_000_000.0            # 2023-11-14 22:13:20 UTC
SIM_STAMP = "22:13:20 UTC"
SIM_DAY_STAMP = "2023-11-14 22:13:20 UTC"


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


@pytest.fixture
def paper(monkeypatch):
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: False)


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=0.8,
                     reasoning="t", risk_reward_ratio=3.0)


def _risk(tmp_path, name="risk.json"):
    pt = PortfolioTracker(initial_balance=1000.0,
                          state_file=str(tmp_path / f"pf-{name}"))
    return RiskEngine(pt, state_file=str(tmp_path / name))


class _Book:
    """An operator executor holding three open positions and one resting order."""
    open_positions = ([types.SimpleNamespace(status="open")] * 3
                      + [types.SimpleNamespace(status="pending_fill")])


class _Raising:
    @property
    def open_positions(self):
        raise RuntimeError("apiKey=SECRETVALUE")


def _engine(risk, executor=_Book()):
    return types.SimpleNamespace(risk=risk, live_executor=executor,
                                 portfolio=types.SimpleNamespace(open_positions=[]))


def _trip_on_drawdown(risk, *, day_pnl=None):
    """Peak $10,000, equity $9,141: 8.59% against the 7.0% live limit."""
    if day_pnl is not None:
        risk._live_daily_pnl = float(day_pnl)
        risk._live_daily_day = risk._utc_day()
    risk._live_equity_peak = 10000.0
    risk.evaluate(_idea(), live_equity=9141.0)
    assert risk.circuit_breaker_active and risk.circuit_trip_cause == "drawdown"


def _line(body, label):
    for ln in body.split("\n"):
        if ln.startswith(f"- {label}:"):
            return ln
    raise AssertionError(f"no {label!r} line in:\n{body}")


# ── the trip card ─────────────────────────────────────────────────────────

class TestTheTripCard:

    def test_a_monitor_built_before_the_trip_announces_a_trip(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        assert m._check_circuit_breaker() == [], "a closed breaker on the first pass says nothing"
        _trip_on_drawdown(risk)
        (card,) = m._check_circuit_breaker()
        assert card.alert_type == "CIRCUIT_BREAKER" and card.severity == "CRITICAL"
        assert card.dedup_key == "cb_tripped"
        assert "CIRCUIT BREAKER TRIPPED" in card.body and "OPEN AT STARTUP" not in card.body
        assert m._check_circuit_breaker() == [], "unchanged: nothing more"

    def test_the_drawdown_is_the_gates_figure_with_its_source_and_limit(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        (card,) = m._check_circuit_breaker()
        assert _line(card.body, "Drawdown") == (
            "- Drawdown: <code>8.59% (live equity high-water mark), limit 7.00%</code>")
        assert "see cause" not in card.body

    def test_a_winning_day_is_not_a_daily_loss(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk, day_pnl=+8.0)
        (card,) = m._check_circuit_breaker()
        assert _line(card.body, "Daily P&L") == (
            "- Daily P&L: <code>+0.09% of equity (realized live closes)</code>")
        assert "Daily loss" not in card.body and "-0.09" not in card.body

    def test_a_losing_day_keeps_its_sign(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk, day_pnl=-8.0)
        (card,) = m._check_circuit_breaker()
        assert "<code>-0.09% of equity (realized live closes)</code>" in card.body

    def test_a_measured_flat_day_is_a_figure_not_unread(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk, day_pnl=0.0)
        (card,) = m._check_circuit_breaker()
        assert "<code>+0.00% of equity (realized live closes)</code>" in card.body
        assert "N/A" not in card.body and "unread" not in _line(card.body, "Daily P&L")

    def test_open_positions_count_the_operators_book_apart_from_resting(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        (card,) = m._check_circuit_breaker()
        assert _line(card.body, "Open positions") == "- Open positions: <code>3 (+1 resting order)</code>"

    @pytest.mark.parametrize("executor", [None, _Raising()], ids=["none", "raises"])
    def test_a_book_nobody_read_is_unread_not_zero(self, live, tmp_path, executor):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk, executor))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        (card,) = m._check_circuit_breaker()
        assert _line(card.body, "Open positions") == "- Open positions: <code>unread</code>"
        assert "SECRETVALUE" not in card.body

    def test_the_trip_time_is_the_trips_own_not_the_monitors(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        risk._sim_now = SIM_AT
        _trip_on_drawdown(risk)
        assert risk.circuit_trip_at == SIM_AT
        (card,) = m._check_circuit_breaker()
        assert _line(card.body, "Tripped at") == f"- Tripped at: <code>{SIM_STAMP}</code>"
        assert "Noticed at" not in card.body and "Triggered At" not in card.body

    def test_a_fresh_trip_with_no_time_on_record_is_labelled_noticed(self, live):
        risk = types.SimpleNamespace(circuit_breaker_active=True,
                                     circuit_trip_cause="manual",
                                     drawdown_status=lambda: {})
        m = ProactiveMonitor(_engine(risk))
        m._last_cb_state = False
        (card,) = m._check_circuit_breaker()
        ln = _line(card.body, "Noticed at")
        assert "(the trip's own time is not on record)" in ln
        assert "Tripped at" not in card.body

    def test_a_paper_trip_reads_the_paper_book(self, paper, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        risk.emergency_halt("test")
        (card,) = m._check_circuit_breaker()
        assert "(paper snapshot), limit 10.00%" in _line(card.body, "Drawdown")
        # The paper book is empty: a measured zero, not "unread".
        assert _line(card.body, "Open positions") == "- Open positions: <code>0</code>"


# ── a restart with the breaker persisted open ─────────────────────────────

class TestAnOpenBreakerAtStartup:

    def _restart(self, tmp_path, *, live_mode=True):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        risk._live_equity_peak = 10000.0
        risk.evaluate(_idea(), live_equity=9141.0)
        assert risk.circuit_breaker_active
        risk2 = _risk(tmp_path)                 # same state file: a restart
        assert risk2.circuit_breaker_active and risk2.circuit_trip_at == SIM_AT
        return risk2

    def test_the_first_pass_says_open_at_startup_with_the_recorded_time(self, live, tmp_path):
        risk2 = self._restart(tmp_path)
        m = ProactiveMonitor(_engine(risk2))
        (card,) = m._check_circuit_breaker()
        assert card.title == "Circuit Breaker OPEN at startup"
        assert card.dedup_key == "cb_open_at_boot"
        assert "OPEN AT STARTUP" in card.body and "TRIPPED</b>" not in card.body
        assert "tripped before this process started" in card.body
        assert _line(card.body, "Tripped at") == f"- Tripped at: <code>{SIM_DAY_STAMP}</code>"
        assert time.strftime("%H:%M:%S UTC", time.gmtime()) not in card.body or SIM_STAMP in card.body

    def test_in_live_mode_the_paper_fallback_is_unread(self, live, tmp_path):
        """After a restart no live equity has been read, and `drawdown_status`
        falls back to the paper snapshot: a live trip's card printed
        `0.00% (paper snapshot)`."""
        risk2 = self._restart(tmp_path)
        (card,) = ProactiveMonitor(_engine(risk2))._check_circuit_breaker()
        assert _line(card.body, "Drawdown") == (
            "- Drawdown: <code>unread (no live equity has been read since this "
            "process started), limit 7.00%</code>")
        assert "paper snapshot" not in card.body

    def test_a_day_nobody_measured_is_unread(self, live, tmp_path):
        risk2 = self._restart(tmp_path)
        (card,) = ProactiveMonitor(_engine(risk2))._check_circuit_breaker()
        assert _line(card.body, "Daily P&L") == (
            "- Daily P&L: <code>unread (no evaluation has measured today since "
            "this process started)</code>")

    def test_a_trip_a_build_before_this_field_wrote_has_no_time_on_record(self, live, tmp_path):
        risk = _risk(tmp_path)
        risk.emergency_halt("test")
        path = tmp_path / "risk.json"
        data = json.loads(path.read_text())
        del data["circuit_trip_at"]
        path.write_text(json.dumps(data))
        risk2 = _risk(tmp_path)
        assert risk2.circuit_breaker_active and risk2.circuit_trip_at is None
        (card,) = ProactiveMonitor(_engine(risk2))._check_circuit_breaker()
        assert _line(card.body, "Tripped at") == (
            "- Tripped at: <code>not on record (before this process started)</code>")

    def test_a_clear_after_the_startup_card_is_still_announced(self, live, tmp_path):
        risk2 = self._restart(tmp_path)
        m = ProactiveMonitor(_engine(risk2))
        assert m._check_circuit_breaker()
        risk2.reset_circuit_breaker()
        (card,) = m._check_circuit_breaker()
        assert card.severity == "INFO" and "CLEARED" in card.body

    def test_a_closed_breaker_on_the_first_pass_is_not_a_clear(self, live, tmp_path):
        m = ProactiveMonitor(_engine(_risk(tmp_path)))
        assert m._check_circuit_breaker() == []


# ── the trip time is the engine's, persisted ──────────────────────────────

class TestTheTripTime:

    def test_a_clear_forgets_it(self, tmp_path):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        risk.emergency_halt("test")
        assert risk.circuit_trip_at == SIM_AT
        risk.reset_circuit_breaker()
        assert risk.circuit_trip_at is None
        assert risk._export_state_dict()["circuit_trip_at"] is None

    def test_the_combined_state_loader_restores_it(self, tmp_path):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        risk.emergency_halt("test")
        exported = risk._export_state_dict()
        risk2 = _risk(tmp_path, "other.json")
        risk2._load_from_state_dict(exported)
        assert risk2.circuit_breaker_active and risk2.circuit_trip_at == SIM_AT

    @pytest.mark.parametrize("junk", ["junk", True, float("nan"), float("inf"), -1.0, None],
                             ids=["str", "bool", "nan", "inf", "negative", "none"])
    def test_a_value_that_is_not_a_time_is_not_restored(self, tmp_path, junk):
        risk = _risk(tmp_path)
        risk._restore_trip_time({"circuit_trip_at": junk})
        assert risk.circuit_trip_at is None

    def test_a_time_in_the_future_is_not_restored(self, tmp_path):
        risk = _risk(tmp_path)
        risk._restore_trip_time({"circuit_trip_at": time.time() + 3600})
        assert risk.circuit_trip_at is None
        risk._restore_trip_time({"circuit_trip_at": SIM_AT})
        assert risk.circuit_trip_at == SIM_AT

    def test_a_fail_closed_restore_records_when_it_tripped(self, tmp_path):
        """A corrupt state file trips the breaker at boot; that trip has a
        time like any other, or the card would say it is not on record."""
        (tmp_path / "risk.json").write_text("{not json")
        before = time.time()
        risk = _risk(tmp_path)
        assert risk.circuit_breaker_active and risk.circuit_trip_cause == "state_unreadable"
        assert risk.circuit_trip_at is not None and before <= risk.circuit_trip_at <= time.time()

    def test_a_missing_key_leaves_it_alone(self, tmp_path):
        risk = _risk(tmp_path)
        risk._restore_trip_time({})
        assert risk.circuit_trip_at is None


# ── the signed daily reading ──────────────────────────────────────────────

class TestTheDailyReading:

    def test_unmeasured_until_an_evaluation(self, tmp_path):
        assert _risk(tmp_path).last_daily_pnl_reading() == (None, "")

    def test_the_magnitude_the_gate_compares_has_no_sign(self, live, tmp_path):
        """The premise: `last_known_daily_loss_pct` is `abs()`, so a card
        cannot read the day's direction off it."""
        risk = _risk(tmp_path)
        risk._live_daily_pnl = 8.0
        risk._live_daily_day = risk._utc_day()
        risk._live_equity_peak = 10000.0
        risk.evaluate(_idea(), live_equity=10000.0)
        assert risk.last_known_daily_loss_pct == pytest.approx(0.08)
        assert risk.last_daily_pnl_reading() == (pytest.approx(0.08), "live")

    def test_a_paper_evaluation_reads_the_paper_book(self, paper, tmp_path):
        risk = _risk(tmp_path)
        risk.evaluate(_idea())
        pct, basis = risk.last_daily_pnl_reading()
        assert pct == 0.0 and basis == "paper"


# ── the tier card ─────────────────────────────────────────────────────────

def _past_limit(tmp_path):
    """8.59% against 7.0%, breaker NOT tripped: no evaluation has run since."""
    risk = _risk(tmp_path)
    risk._live_equity_peak = 10000.0
    risk._last_live_equity = 9141.0
    return risk


class TestTheTierCard:

    def test_the_header_names_the_measured_fraction(self, live, tmp_path):
        m = ProactiveMonitor(_engine(_past_limit(tmp_path)))
        (card,) = m._check_drawdown_tiers()
        assert card.title == "Drawdown 123% of limit"
        assert "DRAWDOWN AT 123% OF LIMIT" in card.body and "85%" not in card.body
        assert card.severity == "CRITICAL" and card.audience == "admin"
        assert card.dedup_key == "dd_tier_100"

    def test_past_the_limit_with_the_breaker_closed_says_it_trips_next(self, live, tmp_path):
        (card,) = ProactiveMonitor(_engine(_past_limit(tmp_path)))._check_drawdown_tiers()
        assert "That is past the limit." in card.body
        assert "nothing has been halted yet" in card.body
        assert "Consider reducing size" not in card.body
        assert "halts all entries at 100%" not in card.body

    def test_past_the_limit_beside_a_tripped_breaker_sends_no_card(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        assert m._check_drawdown_tiers() == [], "the trip card is the card"
        assert m._last_dd_tier == 100, "the tier is still recorded"
        # Recovery re-arms once the NEXT live reading is in: /resume clears the
        # last live equity with the peak, and until an evaluation reads one
        # the drawdown is unread, which fires nothing and re-arms nothing.
        risk.reset_circuit_breaker()
        assert m._check_drawdown_tiers() == [] and m._last_dd_tier == 100
        risk._live_equity_peak = 9141.0
        risk._last_live_equity = 9141.0
        assert m._check_drawdown_tiers() == [] and m._last_dd_tier == 0

    def test_below_the_limit_the_card_is_the_early_warning(self, live, tmp_path):
        risk = _past_limit(tmp_path)
        risk._last_live_equity = 9400.0          # 6.00% of 7.00%: 86%
        (card,) = ProactiveMonitor(_engine(risk))._check_drawdown_tiers()
        assert card.title == "Drawdown 86% of limit"
        assert "halts all entries at 100% of the limit" in card.body
        assert "Consider reducing size" in card.body
        assert "past the limit" not in card.body

    def test_the_tiers_fire_once_each_up_to_the_limit(self, live, tmp_path):
        risk = _past_limit(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        risk._last_live_equity = 9400.0
        assert [c.dedup_key for c in m._check_drawdown_tiers()] == ["dd_tier_85"]
        assert m._check_drawdown_tiers() == []
        risk._last_live_equity = 9141.0
        assert [c.dedup_key for c in m._check_drawdown_tiers()] == ["dd_tier_100"]
        assert m._check_drawdown_tiers() == []

    def test_a_breaker_that_cannot_be_read_gets_the_louder_sentence(self, live, tmp_path):
        risk = _past_limit(tmp_path)

        class _Hostile(type(risk)):
            @property
            def circuit_breaker_active(self):
                raise RuntimeError("nope")

        risk.__class__ = _Hostile
        (card,) = ProactiveMonitor(_engine(risk))._check_drawdown_tiers()
        assert "nothing has been halted yet" in card.body

    def test_both_cards_read_one_drawdown(self, live, tmp_path):
        """Patch the monitor's one reading: both cards print the planted figure."""
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        m._enforced_drawdown_reading = lambda: (4.2, "live", 7.0)
        (tier,) = m._check_drawdown_tiers()
        assert "Drawdown 60% of limit" == tier.title
        risk.emergency_halt("test")
        (trip,) = m._check_circuit_breaker()
        assert "4.20% (live equity high-water mark), limit 7.00%" in trip.body

    def test_drawdown_tier_is_the_highest_reached(self):
        assert [breaker_card.drawdown_tier(f) for f in (0.49, 0.5, 0.74, 0.75, 0.85, 0.99, 1.0, 1.23)] == \
            [0, 50, 0 + 50, 75, 85, 85, 100, 100]


# ── the pure lines ────────────────────────────────────────────────────────

class TestThePureLines:

    def test_a_stand_in_figure_is_unread(self):
        from unittest.mock import MagicMock
        assert breaker_card.daily_pnl_line(MagicMock()).startswith("unread")
        assert breaker_card.daily_pnl_line((MagicMock(), "live")).startswith("unread")
        assert breaker_card.drawdown_line(MagicMock()).startswith("unread")
        assert breaker_card.positions_line(MagicMock()) == "unread"

    @pytest.mark.parametrize("v", [True, "1.5", float("nan"), None])
    def test_num_refuses_what_is_not_a_finite_number(self, v):
        assert breaker_card._num(v) is None

    def test_positions_line_pluralises_resting_orders(self):
        assert breaker_card.positions_line((0, 0)) == "0"
        assert breaker_card.positions_line((2, 1)) == "2 (+1 resting order)"
        assert breaker_card.positions_line((2, 3)) == "2 (+3 resting orders)"

    def test_an_unread_gate_with_a_readable_limit_says_both(self):
        assert breaker_card.drawdown_line((None, None, 7.0)) == \
            "unread (the gate's reading could not be read), limit 7.00%"
        assert breaker_card.drawdown_line((None, None, None)) == \
            "unread (the gate's reading could not be read)"
        assert breaker_card.drawdown_line((3.0, "live", None)) == \
            "3.00% (live equity high-water mark), limit unread"

    def test_the_tripped_at_stamp_is_utc(self):
        label, when = breaker_card.tripped_at_line(SIM_AT, time.time(), restored=False)
        assert (label, when) == ("Tripped at", SIM_STAMP)
        label, when = breaker_card.tripped_at_line(SIM_AT, time.time(), restored=True)
        assert (label, when) == ("Tripped at", SIM_DAY_STAMP)
        assert datetime.fromtimestamp(SIM_AT, tz=timezone.utc).strftime("%H:%M:%S UTC") == SIM_STAMP

    def test_the_tier_card_past_the_limit_beside_an_open_breaker_is_none(self):
        assert breaker_card.tier_card(frac=1.2, dd=8.4, source="live", limit=7.0,
                                      breaker_open=True) is None
        title, body, sev = breaker_card.tier_card(frac=1.2, dd=8.4, source="live", limit=7.0,
                                                  breaker_open=False)
        assert title == "Drawdown 120% of limit" and sev == "CRITICAL"
        assert not math.isnan(1.2)
