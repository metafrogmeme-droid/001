"""Scored surface scenarios: plant a known state, assert what the card SAYS.

Every diagnosis defect fixed on 2026-07-30 — nineteen PRs — was found by the
operator hitting it live, not by a test. The reason is structural. Our ~5,400
tests verify that code RUNS. Almost none verify that a surface TELLS THE
TRUTH when the system is in a particular broken state.

The idea is borrowed from OpenSRE's "scored synthetic RCA suites that check
root-cause accuracy, required evidence, and adversarial red herrings". Three
axes, and the third is the one that matters here:

  MUST_SAY      the fact the operator needs
  MUST_NOT_SAY  the wrong conclusion the state invites
  RED HERRING   a true-but-misleading signal planted alongside

The red herring is not decoration. `_scan_timeout_hint` carries a comment
recording that a GREEN LLM health check was read as "the exchange is slow"
while analyses hung badly enough to blow the tick cap thirty-seven times. A
green sub-check that rules ONE cause out is exactly an adversarial signal,
and no test had ever planted one.

Two of tonight's four defects would have failed here on the first run:
#999's adoption detail (rendered nothing, forever) and the unlabelled
lifetime PnL. Both were caught by screenshots instead.
"""
from __future__ import annotations

import time
from types import SimpleNamespace as NS

import pytest

from bot.formatters.rich_cards import render_adoption_card, render_open_positions
from bot.skills.telegram_handler import _scan_timeout_hint


def _pos(**over):
    base = dict(origin="adopted", symbol="UNI/USDT", status="open",
                stop_loss=6.51, take_profit=7.89, unprotected=False)
    base.update(over)
    return NS(**base)


# ── Scenario table ────────────────────────────────────────────────────────
# (id, render callable, must_say, must_not_say, why this state is a trap)

ADOPTION_SCENARIOS = [
    pytest.param(
        lambda: render_adoption_card(["UNI/USDT"], [_pos()]),
        ["UNI/USDT", "SL", "6.51", "active"],
        [],
        id="protected-position-names-its-levels",
    ),
    pytest.param(
        lambda: render_adoption_card(["UNI/USDT"], [_pos(unprotected=True)]),
        ["UNPROTECTED", "Set one NOW"],
        ["active"],
        id="unprotected-is-loud-not-buried",
    ),
    pytest.param(
        # THE #999 TRAP: the card is handed a symbol it cannot match. It must
        # still name the position — dropping an adoption notice is worse than
        # rendering one without levels — but must NOT imply protection.
        lambda: render_adoption_card(["UNI/USDT"], []),
        ["UNI/USDT"],
        ["active", "UNPROTECTED"],
        id="unmatched-symbol-renders-bare-never-invents",
    ),
    pytest.param(
        # A non-adopted position with the same symbol must not lend it levels.
        lambda: render_adoption_card(["UNI/USDT"], [_pos(origin="bot")]),
        ["UNI/USDT"],
        ["active"],
        id="a-bot-opened-twin-does-not-lend-its-stops",
    ),
    pytest.param(
        # A CLOSED adopted twin likewise.
        lambda: render_adoption_card(["UNI/USDT"], [_pos(status="closed")]),
        ["UNI/USDT"],
        ["active"],
        id="a-closed-twin-does-not-lend-its-stops",
    ),
]


@pytest.mark.parametrize("render,must_say,must_not_say", ADOPTION_SCENARIOS)
def test_adoption_card_scenarios(render, must_say, must_not_say):
    out = render()
    for phrase in must_say:
        assert phrase in out, f"card omitted {phrase!r}\n---\n{out}"
    for phrase in must_not_say:
        assert phrase not in out, f"card wrongly claimed {phrase!r}\n---\n{out}"


# ── The red herring that cost thirty-seven ticks ──────────────────────────

def _analyzer(degraded_streak: int):
    return NS(llm_health=lambda: {"degraded_streak": degraded_streak})


class TestGreenSubCheckIsNotAVerdict:
    """A healthy LLM rules ONE cause out. It names none."""

    def test_green_llm_never_concludes_the_exchange_is_at_fault(self):
        eng = NS(_last_analysis_timeout=None, _analyze_progress=None)
        out = _scan_timeout_hint(_analyzer(0), eng)
        # The exact wording that shipped and misled, and anything like it.
        assert "not the AI" not in out
        assert "likely exchange" not in out
        assert "does not identify the cause" in out

    def test_measured_progress_outranks_the_green_check(self):
        eng = NS(_last_analysis_timeout=None,
                 _analyze_progress={"of": 40, "done": 12,
                                    "started": time.monotonic() - 25.0, "seq": 1})
        out = _scan_timeout_hint(_analyzer(0), eng)
        assert "12 of 40" in out
        assert "slow, not hung" in out

    def test_zero_progress_is_not_reported_as_slow(self):
        # "Slow" and "blocked" call for different next moves.
        eng = NS(_last_analysis_timeout=None,
                 _analyze_progress={"of": 40, "done": 0,
                                    "started": time.monotonic() - 30.0, "seq": 1})
        out = _scan_timeout_hint(_analyzer(0), eng)
        assert "blocked dependency" in out
        assert "slow, not hung" not in out

    def test_a_degraded_brain_is_named_over_everything_else(self):
        eng = NS(_last_analysis_timeout=None,
                 _analyze_progress={"of": 40, "done": 12,
                                    "started": time.monotonic() - 25.0, "seq": 1})
        out = _scan_timeout_hint(_analyzer(5), eng)
        assert "LLM brain degraded" in out
        assert "slow, not hung" not in out


# ── A total whose window is not stated ────────────────────────────────────

class TestNumbersCarryTheirWindow:
    def test_open_positions_card_never_implies_a_pnl_window(self):
        # The positions card shows per-position PnL only; a lifetime total
        # appearing here unlabelled is the /portfolio trap one surface over.
        out = render_open_positions([{
            "pair": "INJUSDT", "direction": "LONG", "entry": 10.0,
            "current": 10.5, "pnl_pct": 5.0, "pnl_usd": 2.5,
            "sl": 9.5, "tp": 11.0, "size_usd": 51.18, "hold_hours": 1.0,
        }])
        assert "INJ" in out
        assert "Realized PnL" not in out


# ── The status headline must not contradict its own gauge ────────────────

from bot.formatters.rich_cards import render_status_card
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
import os as _os
import tempfile as _tempfile


def _status(**over):
    base = dict(mode="LIVE", active=True, equity=545.29, open_positions=1,
                daily_pnl=0.7, drawdown=0.0, max_drawdown=7.0,
                market_bias="Normal")
    base.update(over)
    return render_status_card(**base)


class TestHaltedIsNeverGreen:
    """17:40 live: '⛔ Trading blocked: circuit breaker (drawdown)' printed
    directly above 'Drawdown: +0.0% / +7.0%' with a GREEN gauge. The verdict
    and its evidence disagreed on the same card, because the breaker state
    persists to disk and the high-water mark it tripped on does not."""

    def test_a_blocked_engine_never_renders_the_active_headline(self):
        out = _status(active=False)
        assert "HALTED" in out.upper()
        assert "\U0001f7e2 " + "ACTIVE" not in out.upper()

    def test_an_active_engine_does_not_render_halted(self):
        assert "HALTED" not in _status(active=True).upper()


class TestBreakerScenarios:
    """trading_blocked_by must answer 'can we trade', not 'is ONE breaker
    open'. #990: the warning-rate breaker rejected live trades while
    circuit_breaker_active read false on every operator surface."""

    def _eng(self):
        sf = _os.path.join(_tempfile.mkdtemp(prefix="rc-scen-"), "risk.json")
        return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=sf)

    def test_warning_rate_trip_is_reported_though_the_narrow_flag_is_false(self):
        eng = self._eng()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "engine_tick_failure"
        # The red herring: the flag every surface used to read.
        assert eng.circuit_breaker_active is False
        assert eng.trading_blocked_by == "warning_rate:engine_tick_failure"

    def test_a_clear_engine_reports_no_blocker(self):
        assert self._eng().trading_blocked_by == ""

    def test_a_drawdown_trip_names_drawdown_not_a_generic_halt(self):
        eng = self._eng()
        eng._circuit_open = True
        eng._circuit_trip_cause = "drawdown"
        assert eng.trading_blocked_by == "drawdown"


class TestTheTripCardNamesEveryCause:
    """Tonight's halt was a phantom: equity moved by a TRANSFER, not losses.
    The hint compares against REALIZED PnL only, so an underwater book
    produces identical arithmetic — it must not name only one cause."""

    def _eng(self):
        sf = _os.path.join(_tempfile.mkdtemp(prefix="rc-hint-"), "risk.json")
        return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=sf)

    def test_the_live_shape_names_all_three_and_refuses_to_conclude(self):
        eng = self._eng()
        eng._live_equity_peak = 568.68
        eng._live_daily_pnl = 6.36          # today: POSITIVE realized
        out = eng._drawdown_transfer_hint(517.58)
        assert "withdrawal" in out          # what it was
        assert "OPEN positions" in out      # the one it cannot see
        assert "balance reading" in out     # the third
        assert "Do not resume until you know which" in out

    def test_a_real_losing_streak_keeps_the_plain_message(self):
        eng = self._eng()
        eng._live_equity_peak = 568.68
        eng._live_daily_pnl = -51.10        # losses fully explain the drop
        assert eng._drawdown_transfer_hint(517.58) == ""


class TestStaleBalanceIsNeverServedAsCurrent:
    """#995: five surfaces read the balance cache with no age check, so a
    dead venue connection served an hours-old number as the live account.
    The red herring is that the stale value is entirely plausible."""

    def _eng(self, age_s):
        e = NS(_live_balance_cache={"total": 884.31, "free": 512.0},
               _live_balance_cache_ts=time.monotonic() - age_s)
        from bot.core.engine import RuneClawEngine
        e.live_balance_cached = RuneClawEngine.live_balance_cached.__get__(e)
        return e

    def test_a_fresh_reading_is_served(self):
        assert self._eng(30.0).live_balance_cached()["total"] == 884.31

    def test_a_plausible_but_stale_reading_is_refused(self):
        assert self._eng(3600.0).live_balance_cached() is None

    def test_never_stamped_is_refused_regardless_of_host_uptime(self):
        # monotonic()'s epoch is unspecified; on a fresh host the age
        # arithmetic alone called a never-stamped cache "fresh" (CI caught it).
        from bot.core.engine import RuneClawEngine
        e = NS(_live_balance_cache={"total": 5.0}, _live_balance_cache_ts=0.0)
        e.live_balance_cached = RuneClawEngine.live_balance_cached.__get__(e)
        assert e.live_balance_cached() is None


# ── /risk scored HEALTHY while the engine was HALTED ─────────────────────

from bot.warroom.warroom_bot import render_risk


def _risk(**over):
    base = dict(current_drawdown=0.0, drawdown_limit=7.0, daily_loss_limit=5.0,
                max_open_trades=5, open_trades=1, leverage_cap=10)
    base.update(over)
    return render_risk(base)["text"]


class TestRiskCardKnowsWhenTradingIsBlocked:
    """The 17:40 state, exactly. The engine was HALTED on a drawdown breaker,
    /status said so — and this card computed `healthy` from the drawdown
    reading alone, which the restart-erased high-water mark had left at 0.0%.
    It would have printed HEALTHY at 100%.

    #990 widened the PNG stats-card tile beside this one and missed this
    renderer — the text fallback the operator gets whenever the image fails.
    Half a surface fixed is a surface that disagrees with itself."""

    def test_the_exact_1740_state_is_not_scored_healthy(self):
        out = _risk(current_drawdown=0.0, trading_blocked_by="drawdown")
        assert "HEALTHY" not in out
        assert "BLOCKED (drawdown)" in out

    def test_a_blocked_engine_never_scores_full_health(self):
        # 100% risk health beside a halted engine is the contradiction this
        # card exists to avoid.
        out = _risk(current_drawdown=0.0, trading_blocked_by="drawdown")
        assert "100%" not in out

    def test_the_warning_rate_breaker_reaches_this_card_too(self):
        out = _risk(trading_blocked_by="warning_rate:engine_tick_failure")
        assert "engine_tick_failure" in out
        assert "HEALTHY" not in out

    def test_a_clear_engine_still_reads_healthy(self):
        out = _risk()
        assert "HEALTHY" in out
        assert "BLOCKED" not in out

    def test_drawdown_over_its_limit_still_warns_without_a_blocker(self):
        # The pre-existing behaviour must survive: a drawdown at/over the cap
        # is a WARNING even when nothing is formally blocked yet.
        out = _risk(current_drawdown=8.0)
        assert "WARNING" in out
        assert "HEALTHY" not in out

    def test_an_absent_key_leaves_old_callers_unchanged(self):
        # No trading_blocked_by supplied => byte-identical to before.
        assert "HEALTHY" in _risk()
