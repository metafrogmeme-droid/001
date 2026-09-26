"""A drawdown is one account's equity against that account's own peak.

The live drawdown gate compares the equity it is handed with ONE high-water
mark, `_live_equity_peak`, and the engine hands it whichever account it is
trading now. Two ordinary actions change that account under the same engine:

  * the operator's `/venue` switch replaces the executor (it refuses only while
    a position is open), and nothing touched the risk engine's peak;
  * a per-user engine serves every venue its person trades, and `/connect` of
    a second venue makes that venue the active one.

Driven through the real `switch_venue`: bitget at $1,000, then bybit at $300,
read `DRAWDOWN: 70.0% >= 7.0% (this venue)` and tripped the breaker, on a
move of zero. Switching back would have been measured against the wrong high
as well. The peak now belongs to the account it was measured on
(`RiskEngine._select_live_account`), the engine names the account at both
live evaluations (`_executor_account`), and the account being left keeps its
peak for when it is read again.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

import bot.core.engine as engine_mod
import bot.risk.risk_engine as risk_mod
from bot.config import CONFIG
from bot.core import venues
from bot.core.engine import RuneClawEngine, _executor_account
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from tests.test_core import _DEFAULT_ATR, _DEFAULT_MAX_POS, _make_idea
from tests.test_drawdown_peak_persistence import _flag

ROOT = Path(__file__).resolve().parents[1]


def _engine(state_file: str | None = None) -> RiskEngine:
    sf = state_file or os.path.join(tempfile.mkdtemp(prefix="rc-acct-"),
                                    "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=sf)


def _dd(eng: RiskEngine, equity: float, account: str = "") -> tuple[str, bool]:
    """The DRAWDOWN line one live evaluation prints, and whether it refused."""
    chk = eng.evaluate(_make_idea(), atr=_DEFAULT_ATR, live_equity=equity,
                       max_position_usd=_DEFAULT_MAX_POS, live_open_count=0,
                       live_mode=True, live_book=[], live_account=account)
    failed = [x for x in chk.checks_failed if x.startswith("DRAWDOWN")]
    passed = [x for x in chk.checks_passed if x.startswith("DRAWDOWN")]
    assert len(failed) + len(passed) == 1, (failed, passed)
    return (failed or passed)[0], bool(failed)


def _pct(line: str) -> float:
    return float(line.split(":", 1)[1].split("%", 1)[0])


# ── the risk engine ───────────────────────────────────────────────────────

class TestAnAccountIsMeasuredAgainstItsOwnPeak:
    def test_a_second_account_starts_its_own_peak(self):
        eng = _engine()
        assert _pct(_dd(eng, 1000.0, "bitget")[0]) == 0.0
        line, refused = _dd(eng, 300.0, "bybit")
        assert not refused, line
        assert _pct(line) == 0.0, (
            "the second account's balance was measured against the first "
            "account's peak")
        assert not eng.circuit_breaker_active

    def test_returning_resumes_the_peak_it_left(self):
        """Re-seeding on every switch would forget a drawdown the account
        still carries: 5% on bitget, a switch away and back, and it could lose
        another 7% from $950."""
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        line, refused = _dd(eng, 950.0, "bitget")
        assert _pct(line) == pytest.approx(5.0), line
        assert not refused

    def test_an_account_has_one_peak_not_two(self):
        """The account being read holds its peak in `_live_equity_peak` and
        nowhere else: a second copy in the others' store is a second answer
        about the same account."""
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        _dd(eng, 950.0, "bitget")
        assert "bitget" not in eng._live_equity_peaks
        assert eng._live_equity_peaks == {"bybit": 300.0}

    def test_each_account_still_trips_on_its_own_loss(self):
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        _dd(eng, 1000.0, "bitget")
        line, refused = _dd(eng, 240.0, "bybit")
        assert refused, line
        assert _pct(line) == pytest.approx(20.0)
        assert eng.circuit_breaker_active

    def test_an_account_is_matched_case_blind(self):
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        line, _ = _dd(eng, 950.0, " BitGet ")
        assert _pct(line) == pytest.approx(5.0), line

    def test_an_unnamed_evaluation_keeps_the_current_peak(self):
        """A caller that does not know the account changes nothing: it reads
        the peak the last named evaluation left, as every caller did before
        accounts were named."""
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        line, refused = _dd(eng, 500.0, "")
        assert _pct(line) == pytest.approx(50.0) and refused, line
        assert eng._live_peak_account == "bitget"

    def test_a_peak_with_no_owner_is_the_first_named_accounts(self):
        """A peak restored from a build that did not record the account, or
        set before any account was named. It is what every evaluation compared
        to before, so the first named account adopts it rather than dropping
        it (dropping it would re-seed at the current equity: no drawdown)."""
        eng = _engine()
        eng._live_equity_peak = 1000.0
        seen = []
        with patch.object(risk_mod, "audit",
                          lambda *a, **k: seen.append(k.get("action"))):
            line, _ = _dd(eng, 950.0, "bybit")
        assert _pct(line) == pytest.approx(5.0), line
        assert eng._live_peak_account == "bybit"
        assert "live_peak_account" not in seen, (
            "an adoption was audited as a switch from bybit to itself")

    def test_the_switch_is_audited_with_both_accounts(self):
        eng = _engine()
        seen = []
        _dd(eng, 1000.0, "bitget")
        with patch.object(risk_mod, "audit",
                          lambda *a, **k: seen.append((a, k))):
            _dd(eng, 300.0, "bybit")
            _dd(eng, 290.0, "bybit")  # the same account again: no switch
        switched = [k for a, k in seen
                    if k.get("action") == "live_peak_account"]
        assert len(switched) == 1
        assert switched[0]["data"]["from"] == "bitget"
        assert switched[0]["data"]["to"] == "bybit"
        assert switched[0]["data"]["resumed_peak"] is None

    def test_a_reset_reseeds_only_the_account_being_read(self):
        """/resume is a decision about the account in front of the operator.
        Another account's peak was not part of it."""
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        eng.reset_circuit_breaker()
        assert eng._live_equity_peak == 0.0
        line, _ = _dd(eng, 950.0, "bitget")
        assert _pct(line) == pytest.approx(5.0), line

    def test_a_reset_account_stays_reset_after_a_switch_away_and_back(self):
        """The reset cleared bitget's peak. Nothing kept elsewhere may bring
        the cleared value back when bitget is read again."""
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        _dd(eng, 950.0, "bitget")
        eng.reset_circuit_breaker()
        _dd(eng, 300.0, "bybit")
        line, _ = _dd(eng, 950.0, "bitget")
        assert _pct(line) == 0.0, (
            "a peak the operator reset came back from the other accounts' "
            "store: " + line)

    def test_the_reporter_names_the_account(self):
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        st = eng.drawdown_status()
        assert st["live_peak_account"] == "bybit"
        assert st["live_equity_peak"] == pytest.approx(300.0)
        assert st["live_drawdown_pct"] == pytest.approx(0.0)

    def test_an_engine_built_without_init_still_reports(self):
        eng = RiskEngine.__new__(RiskEngine)
        assert eng._live_peak_account == ""


# ── persistence ───────────────────────────────────────────────────────────

class TestTheAccountsAreSavedWithThePeak:
    def _two_accounts(self, sf):
        eng = _engine(sf)
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        eng._save_state()
        return eng

    def test_they_survive_a_restart_when_the_peak_does(self):
        sf = os.path.join(tempfile.mkdtemp(prefix="rc-acct-"), "risk.json")
        self._two_accounts(sf)
        with _flag(True):
            fresh = _engine(sf)
            assert fresh._live_peak_account == "bybit"
            assert fresh._live_equity_peak == pytest.approx(300.0)
            assert fresh._live_equity_peaks == {"bitget": 1000.0}
            line, _ = _dd(fresh, 950.0, "bitget")
        assert _pct(line) == pytest.approx(5.0), line

    def test_nothing_is_restored_without_the_flag(self):
        sf = os.path.join(tempfile.mkdtemp(prefix="rc-acct-"), "risk.json")
        self._two_accounts(sf)
        with _flag(False):
            fresh = _engine(sf)
        assert fresh._live_equity_peak == 0.0
        assert fresh._live_peak_account == ""
        assert fresh._live_equity_peaks == {}

    @pytest.mark.parametrize("account", [123, "Bybit", "by bit", "x" * 33, "",
                                         True, ["bybit"], "bybít"])
    def test_an_account_that_is_not_a_name_is_dropped(self, account):
        sf = os.path.join(tempfile.mkdtemp(prefix="rc-acct-"), "risk.json")
        eng = self._two_accounts(sf)
        data = eng._export_state_dict()
        data["live_peak_account"] = account
        Path(sf).write_text(json.dumps(data))
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_peak_account == ""
        assert not fresh.circuit_breaker_active, (
            "an unreadable account name failed the whole restore closed")

    def test_a_peak_that_is_not_sane_is_dropped_on_its_own(self):
        sf = os.path.join(tempfile.mkdtemp(prefix="rc-acct-"), "risk.json")
        eng = self._two_accounts(sf)
        data = eng._export_state_dict()
        data["live_equity_peaks"] = {
            "okx": 500.0, "gate": "500", "kucoin": True, "bingx": -5.0,
            "hyperliquid": 1e13, "BAD KEY": 400.0, "bybit": 999.0,
        }
        Path(sf).write_text(json.dumps(data))
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_equity_peaks == {"okx": 500.0}, (
            "junk became a peak, or the current account's own peak was held "
            "twice")
        assert not fresh.circuit_breaker_active

    def test_a_peaks_map_that_is_not_a_map_is_dropped(self):
        sf = os.path.join(tempfile.mkdtemp(prefix="rc-acct-"), "risk.json")
        eng = self._two_accounts(sf)
        data = eng._export_state_dict()
        data["live_equity_peaks"] = [["bitget", 1000.0]]
        Path(sf).write_text(json.dumps(data))
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_equity_peaks == {}
        assert fresh._live_peak_account == "bybit"

    def test_the_combined_loader_restores_them_too(self):
        eng = _engine()
        _dd(eng, 1000.0, "bitget")
        _dd(eng, 300.0, "bybit")
        data = eng._export_state_dict()
        fresh = _engine()
        with _flag(True):
            fresh._load_from_state_dict(data)
        assert fresh._live_peak_account == "bybit"
        assert fresh._live_equity_peaks == {"bitget": 1000.0}

    def test_a_bool_peak_is_not_a_peak(self):
        sf = os.path.join(tempfile.mkdtemp(prefix="rc-acct-"), "risk.json")
        eng = _engine(sf)
        data = eng._export_state_dict()
        data["live_equity_peak"] = True
        Path(sf).write_text(json.dumps(data))
        with _flag(True):
            fresh = _engine(sf)
        assert fresh._live_equity_peak == 0.0


# ── the engine names the account ──────────────────────────────────────────

@pytest.fixture
def live_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
    # A real /venue switch persists its choice; kept in this test's own
    # directory, or every later engine in the run starts on bybit.
    monkeypatch.setattr(venues, "VENUE_OVERRIDE_FILE",
                        str(tmp_path / "venue_override.json"))

    async def _count(_eng):
        return 0

    monkeypatch.setattr(engine_mod, "get_exchange_position_count", _count)
    eng = RuneClawEngine()
    yield eng


class TestTheEngineNamesTheAccount:
    def test_a_real_venue_switch_does_not_read_as_a_drawdown(self, live_engine):
        eng = live_engine

        def acct():
            return _executor_account(eng.live_executor)

        assert acct() == "bitget"
        _dd(eng.risk, 1000.0, acct())
        result = asyncio.run(eng.switch_venue("bybit"))
        assert result.startswith("switched"), result
        assert acct() == "bybit"
        line, refused = _dd(eng.risk, 300.0, acct())
        assert not refused and _pct(line) == 0.0, line
        assert not eng.risk.circuit_breaker_active

    def test_the_operator_recheck_names_the_operators_venue(self, live_engine):
        rc = asyncio.run(live_engine._live_recheck_context(""))
        assert rc.account == "bitget"
        asyncio.run(live_engine.switch_venue("bybit"))
        rc = asyncio.run(live_engine._live_recheck_context(""))
        assert rc.account == "bybit"

    def test_a_per_user_recheck_names_that_users_venue(self, live_engine,
                                                       monkeypatch):
        eng = live_engine
        object.__setattr__(CONFIG, "per_user_live_enabled", True)
        try:
            mine = NS(_venue=NS(id="Bybit"), open_positions=[])
            monkeypatch.setattr(eng, "_executor_for", lambda uid, *a, **k: mine)

            async def _bal(_uid):
                return {"total": 300.0, "free": 300.0}

            monkeypatch.setattr(eng, "get_user_live_equity", _bal)
            rc = asyncio.run(eng._live_recheck_context("777"))
        finally:
            object.__setattr__(CONFIG, "per_user_live_enabled", False)
        assert rc.equity == 300.0
        assert rc.account == "bybit"

    def test_an_unreadable_venue_names_nothing(self):
        assert _executor_account(NS()) == ""
        assert _executor_account(NS(_venue=NS(id=None))) == ""
        assert _executor_account(None) == ""

    def test_every_live_evaluation_names_its_account(self):
        """A rule over the call sites, because a new live evaluation that
        forgets the account compares whatever it reads to the last account's
        peak, and nothing else would say so."""
        missing = []
        for path in [*(ROOT / "bot").rglob("*.py"), ROOT / "api_bridge.py",
                     *(ROOT / "scripts").rglob("*.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            missing += [f"{path.relative_to(ROOT)}:{n}"
                        for n in _unnamed_live_evaluations(tree)]
        assert missing == [], missing

    def test_the_rule_sees_what_it_exists_to_refuse(self):
        planted = ast.parse(
            "risk.evaluate(idea, live_equity=eq)\n"
            "risk.evaluate(idea, live_equity=eq, live_account=a)\n"
            "risk.evaluate(idea)\n"
            "calendar.evaluate(live_equity=eq)\n"
            "risk.evaluate(idea, live_equity=eq, live_account='')\n"
            "risk.evaluate(idea, live_equity=eq, live_account=a if b else '')\n")
        assert _unnamed_live_evaluations(planted) == [1, 4, 5]

    def test_the_rule_finds_the_two_real_call_sites(self):
        """Or it measures nothing: a walk that reached no live evaluation
        would pass on any tree."""
        tree = ast.parse((ROOT / "bot/core/engine.py").read_text())
        named = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "evaluate"
                 and "live_equity" in {k.arg for k in n.keywords}]
        assert len(named) == 2


def _unnamed_live_evaluations(tree) -> list[int]:
    """Lines of every `.evaluate(...)` that hands a live equity and no
    account."""
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "evaluate"):
            continue
        kws = {k.arg: k.value for k in node.keywords}
        if "live_equity" not in kws:
            continue
        named = kws.get("live_account")
        # A literal "" names no account, whatever the keyword says.
        if named is None or (isinstance(named, ast.Constant)
                             and not named.value):
            out.append(node.lineno)
    return sorted(out)
