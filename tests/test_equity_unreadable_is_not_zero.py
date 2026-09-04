"""In LIVE mode with an empty balance cache, the engine returned PAPER equity.

    def get_effective_equity(self, user_id=""):
        if CONFIG.is_live() and self._live_balance_cache:
            return self._live_balance_cache.get("total", 0.0)
        portfolio = ...
        return portfolio.snapshot().equity_usd     # <- LIVE falls through here

An empty cache is the ordinary state after a restart, so this is not an edge:
a live surface asked for the account's equity and got the paper portfolio's,
with no way to tell. `resolve_display_equity_sync` was written to answer this
honestly and had sat fifty lines below, unused by all three callers.

Downstream it compounded. `/twin` catches the failure as `equity = 0.0`, and
`simulate_scenario` does `drawdown_pct = ... if eq > 0 else 0.0`, so EVERY
stress scenario reported a 0% drawdown and `_scenario_risk(0.0, 0)` returned
`"none"` — the calmest of the verdicts, on the screen whose entire job is to
say how bad things could get. `twin_payload` then sealed
`worst_drawdown_pct: 0.0` to the tamper-evident chain.
"""
import pytest

from bot.guardian.digital_twin import (
    _scenario_risk,
    run,
    simulate_scenario,
    twin_payload,
)

BOOK = [{"symbol": "BTC/USDT", "entry": 60000.0, "qty": 0.01,
         "direction": "LONG", "leverage": 10, "group": "majors"}]
SAFE = [{"symbol": "BTC/USDT", "entry": 60000.0, "qty": 0.001,
         "direction": "LONG", "leverage": 1, "group": "majors"}]


class TestTheVerdict:
    def test_an_unknown_drawdown_is_not_the_calmest_verdict(self):
        assert _scenario_risk(None, 0) == "unknown"
        assert _scenario_risk(None, 0) != "none"

    def test_a_measured_flat_scenario_is_still_none(self):
        # 0.0 drawdown with nothing liquidating is a real, calm reading.
        assert _scenario_risk(0.0, 0) == "none"

    def test_liquidation_outranks_an_unreadable_equity(self):
        # THE POINT OF THE SPLIT: a position liquidates at a price derived
        # from its own entry and leverage. That answer survives an unreadable
        # balance, so the alarm still fires.
        assert _scenario_risk(None, 2) == "high"

    @pytest.mark.parametrize("dd,expected", [(0.0, "none"), (1.0, "low"),
                                             (12.0, "medium"), (40.0, "high")])
    def test_known_drawdowns_score_exactly_as_before(self, dd, expected):
        assert _scenario_risk(dd, 0) == expected


class TestTheScenario:
    def test_an_unread_equity_yields_no_percentage(self):
        s = simulate_scenario(BOOK, None, {"name": "x", "shocks": {"*": -0.3}})
        assert s["drawdown_pct"] is None
        assert s["projected_equity_usd"] is None

    def test_the_book_pnl_is_still_measured(self):
        # P&L under the shock comes from the positions, not from equity.
        s = simulate_scenario(BOOK, None, {"name": "x", "shocks": {"*": -0.3}})
        assert s["projected_pnl_usd"] < 0

    def test_a_known_equity_is_unchanged(self):
        s = simulate_scenario(BOOK, 1000.0, {"name": "x", "shocks": {"*": -0.3}})
        assert s["drawdown_pct"] == pytest.approx(18.0)


class TestTheReport:
    def test_an_unread_equity_does_not_report_risk_none(self):
        r = run(SAFE, None)
        assert r["risk"] == "unknown"
        assert r["equity_known"] is False
        assert r["equity_usd"] is None

    def test_a_read_equity_still_scores(self):
        r = run(SAFE, 100000.0)
        assert r["risk"] in ("none", "low", "medium", "high")
        assert r["equity_known"] is True

    def test_a_crash_is_not_a_calm_book(self):
        # `run` degrades to an empty report on any exception; it used to say
        # risk "none" there — a verdict assembled from a fault.
        r = run(None, None)          # positions=None drives the except arm
        assert r["risk"] == "unknown"
        assert r["equity_known"] is False

    def test_worst_prefers_a_measurable_scenario(self):
        # An unknown drawdown must not win the "worst" slot over a real one
        # merely by comparing greater.
        r = run(BOOK, 1000.0)
        assert r["worst"]["drawdown_pct"] is not None


class TestTheChainRecord:
    def test_it_no_longer_seals_a_manufactured_zero(self):
        # `.get("drawdown_pct", 0.0)` wrote a measured-looking 0.00% for a run
        # that computed none, into the one record whose value is that it
        # cannot be argued with later.
        assert twin_payload(SAFE, None)["worst_drawdown_pct"] is None

    def test_it_still_seals_a_real_one(self):
        assert twin_payload(BOOK, 1000.0)["worst_drawdown_pct"] is not None

    def test_it_records_whether_equity_was_known(self):
        assert twin_payload(SAFE, None)["equity_known"] is False
        assert twin_payload(SAFE, 100000.0)["equity_known"] is True


class TestTheBoundary:
    def _engine_code(self):
        import io

        from tests.source_scan import code_only
        return code_only(io.open("bot/core/engine.py", encoding="utf-8").read())

    def test_the_getter_is_optional_and_delegates(self):
        code = self._engine_code()
        assert "def get_effective_equity(self, user_id: str = \"\") -> Optional[float]:" in code
        i = code.index("def get_effective_equity(self")
        # Sliced to the next method, matching `async def` too — a first draft
        # searched for "    def " and so ran straight past
        # `get_effective_equity_async` into `resolve_display_equity_sync`,
        # which legitimately reads the cache. That failed on true code.
        import re
        m = re.search(r"\n    (?:async )?def ", code[i + 40:])
        body = code[i:i + 40 + m.start()]
        # It must not re-implement the live/paper choice; one place decides.
        assert "self.resolve_display_equity_sync(user_id)" in body
        assert "_live_balance_cache" not in body

    def test_no_caller_folds_the_failure_to_zero(self):
        code = self._engine_code()
        assert "equity = 0.0" not in code

    def test_the_dashboard_pusher_no_longer_gates_on_greater_than_zero(self):
        import io

        from tests.source_scan import code_only
        code = code_only(io.open("bot/core/dashboard_pusher.py",
                                 encoding="utf-8").read())
        # `if live_eq > 0` could not tell a live balance from the paper one.
        assert "if live_eq > 0" not in code
        assert "None if live_eq is None else round(live_eq, 2)" in code


class TestFlatIsNotUnreadable:
    """`_twin_positions` said `[]` for both, and both cards printed "flat"."""

    def _engine_code(self):
        import io

        from tests.source_scan import code_only
        return code_only(io.open("bot/core/engine.py", encoding="utf-8").read())

    def test_the_reader_can_say_unreadable(self):
        code = self._engine_code()
        assert 'def _twin_positions(self, user_id: str = "") -> Optional[list]:' in code

    def test_all_three_consumers_split_the_cases(self):
        # twin, sentinel and escape each get `positions is None` before the
        # falsy check. Escape already documented this fix for ITSELF; making
        # the reader three-valued would have re-entered the bug there.
        assert self._engine_code().count("if positions is None:") == 3

    def test_a_flat_book_still_scores_none(self):
        from bot.core.engine import _FLAT_BOOK
        assert _FLAT_BOOK["risk"] == "none"
        assert _FLAT_BOOK["flat_book"] is True
