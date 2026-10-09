"""An autonomous live order needs an eligibility record, not only a flag.

A non-human live confirm (the tick's auto-confirm, the ``/forcescan`` loop,
a skill dispatch) was minted the compliance Lock-5 token whenever
``AUTO_CONFIRM_LIVE_ENABLED`` was on, and that flag ships ON in the code. The
``/forcescan`` loop never read the tick's suppression, so the mint was its
only barrier. Nothing in code asked for evidence that the running strategy
works before it placed real orders on its own.

The mint asks now: the flag AND a committed eligibility record for the
running strategy (``bot/core/live_eligibility.py``). A human confirm is not
asked. Both loops ask the same reading first, so a deployment with no record
runs no confirm pipeline only to be denied at the end, and the audit says why.
"""
from __future__ import annotations

import ast
import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.engine as eng
import bot.core.live_eligibility as le
from bot.compat import UTC
from bot.config import CONFIG
from bot.core.confirm_result import placed_nothing
from bot.core.engine import RuneClawEngine
from bot.utils.models import Direction, TradeIdea

ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64


def _record(root: Path, strategy: str = HASH, **fields) -> Path:
    body = {"schema": 1, "strategy_hash": strategy, "verdict": "survives",
            "stage": "minimum"}
    body.update(fields)
    path = le.record_path(strategy, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return path


# ---------------------------------------------------------------------------
# The reading.
# ---------------------------------------------------------------------------

class TestTheRecord:
    def test_no_record_is_missing(self, tmp_path):
        got = le.read_eligibility(HASH, tmp_path)
        assert got.state == le.MISSING and not got.eligible
        assert HASH[:12] in got.reason

    def test_an_empty_file_is_missing(self, tmp_path):
        path = le.record_path(HASH, tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("")
        assert le.read_eligibility(HASH, tmp_path).state == le.MISSING

    def test_a_record_that_will_not_read_is_unreadable(self, tmp_path):
        path = le.record_path(HASH, tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        got = le.read_eligibility(HASH, tmp_path)
        assert got.state == le.UNREADABLE and not got.eligible
        assert "JSONDecodeError" in got.reason

    def test_a_record_that_is_not_an_object_is_unreadable(self, tmp_path):
        path = le.record_path(HASH, tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2]")
        assert le.read_eligibility(HASH, tmp_path).state == le.UNREADABLE

    @pytest.mark.parametrize("fields", [
        {"schema": 2}, {"schema": True}, {"schema": "1"}, {"schema": 1.0},
        {"strategy_hash": ""}, {"strategy_hash": 7},
        {"verdict": "yes"}, {"verdict": None},
        {"stage": "all of it"}, {"stage": None},
    ])
    def test_a_record_of_another_shape_is_malformed(self, tmp_path, fields):
        _record(tmp_path, **fields)
        got = le.read_eligibility(HASH, tmp_path)
        assert got.state == le.MALFORMED and not got.eligible, got

    def test_a_record_naming_another_strategy_is_a_mismatch(self, tmp_path):
        """Filed under this strategy's name, naming another inside it."""
        _record(tmp_path, strategy_hash="b" * 64)
        got = le.read_eligibility(HASH, tmp_path)
        assert got.state == le.MISMATCH and not got.eligible
        assert "b" * 12 in got.reason

    def test_a_record_for_another_strategy_is_not_read(self, tmp_path):
        _record(tmp_path, strategy="b" * 64)
        assert le.read_eligibility(HASH, tmp_path).state == le.MISSING

    @pytest.mark.parametrize("verdict, words", [
        ("does_not", "does not survive"), ("inconclusive", "inconclusive")])
    def test_a_verdict_that_is_not_survives_denies(self, tmp_path, verdict, words):
        _record(tmp_path, verdict=verdict)
        got = le.read_eligibility(HASH, tmp_path)
        assert got.state == le.NOT_SURVIVES and words in got.reason

    def test_a_record_granting_no_stage_denies(self, tmp_path):
        _record(tmp_path, stage="none")
        got = le.read_eligibility(HASH, tmp_path)
        assert got.state == le.NO_STAGE and not got.eligible

    @pytest.mark.parametrize("stage", ["minimum", "25%", "100%"])
    def test_a_surviving_record_granting_a_stage_is_eligible(self, tmp_path, stage):
        _record(tmp_path, stage=stage)
        got = le.read_eligibility(HASH, tmp_path)
        assert got.eligible and got.stage == stage

    def test_the_reason_names_no_path(self, tmp_path):
        """It reaches the trade log and the confirm's answer."""
        for fields in ({}, {"verdict": "does_not"}, {"schema": 2}):
            _record(tmp_path, **fields)
            got = le.read_eligibility(HASH, tmp_path)
            assert str(tmp_path) not in got.reason and ".json" not in got.reason


class TestTheStrategyHash:
    def _tree(self, root: Path) -> None:
        (root / "bot" / "core").mkdir(parents=True)
        (root / "bot" / "core" / "a.py").write_text("x = 1\n")
        (root / "bot" / "b.py").write_text("y = 2\n")

    def test_it_is_stable(self, tmp_path):
        self._tree(tmp_path)
        assert le.strategy_hash(tmp_path) == le.strategy_hash(tmp_path)

    def test_a_code_change_moves_it(self, tmp_path):
        self._tree(tmp_path)
        before = le.strategy_hash(tmp_path)
        (tmp_path / "bot" / "b.py").write_text("y = 3\n")
        assert le.strategy_hash(tmp_path) != before

    def test_a_rename_moves_it(self, tmp_path):
        self._tree(tmp_path)
        before = le.strategy_hash(tmp_path)
        (tmp_path / "bot" / "b.py").rename(tmp_path / "bot" / "c.py")
        assert le.strategy_hash(tmp_path) != before

    def test_bytecode_does_not_move_it(self, tmp_path):
        self._tree(tmp_path)
        before = le.strategy_hash(tmp_path)
        (tmp_path / "bot" / "__pycache__").mkdir()
        (tmp_path / "bot" / "__pycache__" / "b.py").write_text("junk\n")
        (tmp_path / "bot" / "b.pyc").write_bytes(b"\0")
        assert le.strategy_hash(tmp_path) == before

    def test_the_repo_hash_is_the_running_one(self):
        assert le.strategy_hash() == le.strategy_hash(ROOT)
        assert len(le.strategy_hash()) == 64

    def test_the_engine_takes_the_hash_at_start(self, monkeypatch):
        """Taken lazily, the hash described whatever was on DISK at the first
        autonomous confirm: a `git reset --hard` landing new code and its
        record before then would let the old process read the new record as
        its own. Construction takes it now, so the cache holds the code the
        process started with."""
        seen: list = []
        real = le.strategy_hash

        def _counting(root=None):
            seen.append(root)
            return real(root)

        monkeypatch.setattr(le, "_HASH_CACHE", None)
        monkeypatch.setattr(le, "strategy_hash", _counting)
        RuneClawEngine()
        assert seen == [None], seen
        assert le._HASH_CACHE == real(ROOT)

    def test_a_hash_that_raises_at_start_does_not_stop_the_engine(self, monkeypatch):
        """The mint refuses on its own when the hash cannot be taken, so start
        says so and goes on."""
        def _boom(root=None):
            raise OSError("disk")

        monkeypatch.setattr(le, "strategy_hash", _boom)
        RuneClawEngine()

    def _deployed(self, tmp_path, monkeypatch):
        """A repo root whose code on disk has an eligible record filed for it:
        what a deploy that lands new code and its record looks like to a
        process that is still running the old code."""
        (tmp_path / "bot").mkdir()
        (tmp_path / "bot" / "a.py").write_text("x = 2\n")
        monkeypatch.setattr(le, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(le, "_HASH_CACHE", None)
        monkeypatch.setattr(le, "record_path", lambda s, root=None: tmp_path / f"{s}.json")
        _record(tmp_path, le.strategy_hash(tmp_path))
        real = le.strategy_hash
        failing = {"on": True}

        def _flaky(root=None):
            if failing["on"]:
                raise OSError("disk")
            return real(root)

        monkeypatch.setattr(le, "strategy_hash", _flaky)
        return failing

    def test_a_hash_that_failed_at_start_is_never_taken_from_the_disk_later(
            self, tmp_path, monkeypatch):
        """It was: the first autonomous confirm hashed the NEW code on disk,
        found the record filed for it, and minted a live order for the OLD
        code still running."""
        failing = self._deployed(tmp_path, monkeypatch)
        assert le.take_strategy_hash_at_start() == "OSError"
        failing["on"] = False            # the disk reads fine by the confirm
        got = le.read_eligibility()
        assert got.state == le.UNREADABLE and not got.eligible
        assert got.reason == ("the running strategy was not identified when the bot "
                              "started (OSError); a restart takes it again")
        assert le._HASH_CACHE is None

    def test_a_hash_taken_at_start_reads_the_record_filed_for_it(self, tmp_path, monkeypatch):
        """The other arm: the same deploy, a take that worked, an eligible record."""
        failing = self._deployed(tmp_path, monkeypatch)
        failing["on"] = False
        assert le.take_strategy_hash_at_start() is None
        assert le.read_eligibility().state == le.ELIGIBLE

    def test_the_engine_keeps_a_failed_take_for_the_mint(self, tmp_path, monkeypatch):
        failing = self._deployed(tmp_path, monkeypatch)
        RuneClawEngine()
        failing["on"] = False
        _flag(monkeypatch, True)
        assert RuneClawEngine._autonomous_live_refusal() == (
            "the running strategy was not identified when the bot started (OSError); "
            "a restart takes it again")

    def test_a_second_take_keeps_the_first_failure(self, monkeypatch):
        monkeypatch.setattr(le, "_HASH_CACHE", None)
        monkeypatch.setattr(le, "_START_FAILURE", "OSError")
        assert le.take_strategy_hash_at_start() == "OSError"
        with pytest.raises(le.HashNotTakenAtStart):
            le.strategy_hash()

    def test_no_record_ships_for_the_running_strategy(self):
        """Every deployment today is ineligible, by construction: a record is
        added by a reviewed commit, never by the bot."""
        assert le.read_eligibility().state == le.MISSING


# ---------------------------------------------------------------------------
# The engine's reading, which both loops and the mint ask.
# ---------------------------------------------------------------------------

def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(eng, "CONFIG", replace(CONFIG, auto_confirm_live_enabled=on))


def _eligible(monkeypatch) -> list:
    asked: list = []

    def _read(*a, **k):
        asked.append(1)
        return le.Eligibility(le.ELIGIBLE, "planted", HASH, "minimum")

    monkeypatch.setattr(le, "read_eligibility", _read)
    return asked


class TestTheRefusal:
    def test_the_flag_off_refuses_before_the_record_is_read(self, monkeypatch):
        _flag(monkeypatch, False)
        asked = _eligible(monkeypatch)
        assert RuneClawEngine._autonomous_live_refusal() == "AUTO_CONFIRM_LIVE_ENABLED is off"
        assert asked == []

    def test_the_flag_on_with_no_record_refuses_with_the_records_sentence(self, monkeypatch):
        _flag(monkeypatch, True)
        monkeypatch.setattr(le, "read_eligibility", lambda *a, **k: le.Eligibility(
            le.MISSING, "PLANTED missing sentence", HASH))
        assert RuneClawEngine._autonomous_live_refusal() == "PLANTED missing sentence"

    def test_the_flag_on_with_a_record_allows(self, monkeypatch):
        _flag(monkeypatch, True)
        _eligible(monkeypatch)
        assert RuneClawEngine._autonomous_live_refusal() is None

    def test_a_reading_that_raises_refuses_by_class(self, monkeypatch):
        _flag(monkeypatch, True)

        def _boom(*a, **k):
            raise OSError("SECRETVALUE /some/path")

        monkeypatch.setattr(le, "read_eligibility", _boom)
        why = RuneClawEngine._autonomous_live_refusal()
        assert why is not None and "OSError" in why and "SECRETVALUE" not in why


# ---------------------------------------------------------------------------
# The mint, driven through the real confirm path.
# ---------------------------------------------------------------------------

FILLED = "\U0001f7e2 LIVE LONG BTC/USDT opened @ $65,000.0000"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _no_website_sync(monkeypatch):
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda *a, **k: None)


def _engine(tmp_path):
    from bot.utils.audit_chain import AuditChain
    engine = RuneClawEngine()
    engine.risk._state_file = "/dev/null"
    engine.risk._circuit_open = False
    engine.risk._consecutive_losses = 0
    engine.risk._last_loss_time = None
    engine._cooldown_until = 0.0
    if engine.macro_provider is not None:
        engine.macro_provider._calendar_stale = False
        engine.macro_provider._calendar_blind = False
    engine.portfolio.balance = 50000.0
    engine.portfolio._peak_equity = 50000.0
    engine._live_balance_cache = {"total": 50000.0, "free": 50000.0}
    engine._live_balance_cache_ts = time.monotonic()
    # An ENGINE idea, registered the way the engine registers its own: a
    # manual ticket confirmed under "auto" is a state the product never
    # produces (auto-confirm refuses a stamp before it gets here).
    idea = TradeIdea(
        id="TI-ELIG1", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=65000, stop_loss=63050, take_profit=68900,
        confidence=0.9, reasoning="engine setup", signals_used=["momentum"],
        timestamp=datetime.now(UTC), order_type="market",
    )
    engine._register_engine_idea(idea)
    engine._pending_atr[idea.id] = 500.0
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": idea.entry_price})
    engine.scanner._get_exchange = AsyncMock(return_value=ex)
    engine.scanner._get_futures_exchange = AsyncMock(return_value=ex)
    engine.live_executor._positions = {}
    engine.live_executor.execute = AsyncMock(return_value=FILLED)
    engine.compliance.issue_approval_token = MagicMock(return_value="tok")
    # Lock 5's rule, planted: granted only when a token was minted.
    engine.compliance.authorize = MagicMock(side_effect=lambda **kw: SimpleNamespace(
        granted=kw.get("approval_token") is not None,
        reasons=[] if kw.get("approval_token") is not None
        else ["No human approval token provided"],
        locks_failed=[] if kw.get("approval_token") is not None else ["human_approval"],
        locks_passed=["L1"]))
    engine._live_execution_vetoed_by_simulation = lambda: False
    engine.learning.log_decision = MagicMock()
    engine._sync_flight_records = lambda: None
    engine.audit_chain = AuditChain(str(tmp_path / "chain.jsonl"))
    return engine, idea


def _confirm(engine, idea, user_id):
    with patch.object(type(CONFIG), "is_live", return_value=True), \
         patch("bot.core.engine.get_exchange_position_count", new=AsyncMock(return_value=0)), \
         patch("bot.core.engine.invalidate_position_count_cache"):
        return _run(engine.confirm_trade(idea.id, user_id=user_id))


class TestTheMint:
    def test_an_autonomous_confirm_with_no_record_places_nothing(self, tmp_path, monkeypatch):
        _flag(monkeypatch, True)
        engine, idea = _engine(tmp_path)
        answer = _confirm(engine, idea, "auto")
        engine.live_executor.execute.assert_not_awaited()
        engine.compliance.issue_approval_token.assert_not_called()
        assert placed_nothing(answer), answer
        assert answer.startswith("Execution denied: this live order had no human "
                                 "confirm, and no eligibility record exists"), answer

    def test_the_denial_is_sealed_with_its_cause(self, tmp_path, monkeypatch):
        _flag(monkeypatch, True)
        engine, idea = _engine(tmp_path)
        _confirm(engine, idea, "auto")
        rows = [json.loads(ln) for ln in (tmp_path / "chain.jsonl").read_text().splitlines()]
        denied = [r for r in rows if r.get("event_type") == "AUTH_DENIED"]
        assert len(denied) == 1, rows
        assert "no eligibility record exists" in json.dumps(denied[0])

    def test_the_flag_off_still_places_nothing(self, tmp_path, monkeypatch):
        _flag(monkeypatch, False)
        engine, idea = _engine(tmp_path)
        answer = _confirm(engine, idea, "auto")
        engine.live_executor.execute.assert_not_awaited()
        assert "AUTO_CONFIRM_LIVE_ENABLED is off" in answer

    def test_an_unattended_empty_caller_is_asked_too(self, tmp_path, monkeypatch):
        _flag(monkeypatch, True)
        engine, idea = _engine(tmp_path)
        answer = _confirm(engine, idea, "")
        engine.live_executor.execute.assert_not_awaited()
        assert placed_nothing(answer)

    def test_an_autonomous_confirm_with_a_record_is_minted(self, tmp_path, monkeypatch):
        """The other arm: without it the refusal above passes against a mint
        that never mints anything."""
        _flag(monkeypatch, True)
        _eligible(monkeypatch)
        engine, idea = _engine(tmp_path)
        answer = _confirm(engine, idea, "auto")
        engine.compliance.issue_approval_token.assert_called_once()
        engine.live_executor.execute.assert_awaited_once()
        assert answer.startswith(FILLED), answer

    def test_a_human_confirm_is_not_asked(self, tmp_path, monkeypatch):
        _flag(monkeypatch, True)

        def _never(*a, **k):
            raise AssertionError("a human confirm read the eligibility record")

        monkeypatch.setattr(le, "read_eligibility", _never)
        engine, idea = _engine(tmp_path)
        answer = _confirm(engine, idea, "123456")
        engine.live_executor.execute.assert_awaited_once()
        assert answer.startswith(FILLED), answer


# ---------------------------------------------------------------------------
# /forcescan, driven end to end on a stand-in host.
# ---------------------------------------------------------------------------

def _measured(asset="ETH/USDT:USDT", confidence=0.95):
    return TradeIdea(
        asset=asset, direction=Direction.LONG, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, confidence=confidence,
        blended_confidence_raw=confidence, reasoning="engine",
        signals_used=["x"], source="unknown", timestamp=datetime.now(UTC),
    )


def _forcescan_host(confirmed: list):
    engine = SimpleNamespace()

    async def _scan():
        return [SimpleNamespace(symbol="ETH/USDT:USDT")]

    async def _batched(signals, lightweight=False):
        return [_measured()]

    async def _confirm(tid, user_id=""):
        confirmed.append((tid, user_id))
        return "ok"

    engine._pending_ideas = {}
    engine._pending_atr = {}
    engine._pending_timing = {}
    engine._pending_pyramid = {}
    engine._cooldown_until = 0.0
    engine._last_scan_signals = []
    engine.scanner = SimpleNamespace(scan=_scan)
    engine._transition = lambda *a, **k: None
    engine._analyze_signals_batched = _batched
    engine.confirm_trade = _confirm
    engine._auto_confirm_notify_callback = None
    engine.analyzer = None
    engine._engine_idea_ids = set()
    for name in ("_force_scan_locked", "_auto_confirm_gate_value",
                 "_auto_confirm_suppressed", "_engine_pending_ids",
                 "_register_engine_idea", "_autonomous_live_refusal",
                 "_suppress_autonomous_live"):
        # Through the class __dict__, so a staticmethod binds as one.
        setattr(engine, name, RuneClawEngine.__dict__[name].__get__(engine, RuneClawEngine))
    return engine


def _audits(monkeypatch) -> list:
    seen: list = []
    monkeypatch.setattr(eng, "audit", lambda *a, **kw: seen.append(
        {"msg": a[1] if len(a) > 1 else "", **kw}))
    return seen


class TestForceScan:
    @pytest.fixture(autouse=True)
    def _live_at_the_default_bar(self, monkeypatch):
        from bot.config import RUNTIME
        monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 0.85)
        with patch.object(type(CONFIG), "is_live", return_value=True):
            yield

    def test_no_record_confirms_nothing_and_says_why(self, monkeypatch):
        _flag(monkeypatch, True)
        seen = _audits(monkeypatch)
        confirmed: list = []
        summary = _run(_forcescan_host(confirmed)._force_scan_locked())
        assert confirmed == []
        assert summary["auto_confirmed"] == 0
        # "Auto-confirmed: 0" alone reads as "nothing cleared the bar".
        assert summary["auto_withheld"] == 1
        assert "no eligibility record exists" in summary["auto_withheld_why"]
        sup = [r for r in seen if r.get("result") == "SUPPRESSED_LIVE"]
        assert len(sup) == 1, seen
        assert "no eligibility record exists" in sup[0]["data"]["why"]

    def test_a_record_lets_the_engines_idea_through(self, monkeypatch):
        """The other arm: without it the refusal passes against a loop that
        confirms nothing at all."""
        _flag(monkeypatch, True)
        _eligible(monkeypatch)
        confirmed: list = []
        summary = _run(_forcescan_host(confirmed)._force_scan_locked())
        assert [u for _, u in confirmed] == ["auto"]
        assert summary["auto_confirmed"] == 1
        assert summary["auto_withheld"] == 0
        assert summary["auto_withheld_why"] is None

    def test_the_flag_off_confirms_nothing(self, monkeypatch):
        _flag(monkeypatch, False)
        seen = _audits(monkeypatch)
        confirmed: list = []
        _run(_forcescan_host(confirmed)._force_scan_locked())
        assert confirmed == []
        assert any(r.get("data", {}).get("why") == "AUTO_CONFIRM_LIVE_ENABLED is off"
                   for r in seen), seen

    def test_paper_mode_asks_nothing(self, monkeypatch):
        _flag(monkeypatch, True)

        def _never(*a, **k):
            raise AssertionError("paper mode read the eligibility record")

        monkeypatch.setattr(le, "read_eligibility", _never)
        confirmed: list = []
        with patch.object(type(CONFIG), "is_live", return_value=False):
            _run(_forcescan_host(confirmed)._force_scan_locked())
        assert [u for _, u in confirmed] == ["auto"]


# ---------------------------------------------------------------------------
# The tick. A SCAN, stated as one: `_tick` is 434 lines behind a scanner, an
# analyzer and an exchange. What the tick hands `confirm_trade` is refused by
# the mint driven above; the claim here is only that the tick asks first.
# ---------------------------------------------------------------------------

def _fn(name):
    tree = ast.parse((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(found) == 1, name
    return found[0]


def _call_lines(node, attr):
    return [n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


@pytest.mark.parametrize("fn", ["_tick", "_force_scan_locked"])
def test_each_loop_asks_before_it_confirms(fn):
    node = _fn(fn)
    asked = _call_lines(node, "_autonomous_live_refusal")
    confirmed = _call_lines(node, "confirm_trade")
    assert asked and confirmed, (fn, asked, confirmed)
    assert min(asked) < min(confirmed)


def test_the_mint_asks_only_for_a_caller_that_is_not_a_human():
    node = _fn("_confirm_trade_inner")
    src = ast.unparse(node)
    assert "if not human:\n            _auto_refusal = self._autonomous_live_refusal()" in src
    assert "if human or _auto_refusal is None:" in src


# ---------------------------------------------------------------------------
# The two operator cards say what an auto-confirm does now.
# ---------------------------------------------------------------------------

from bot.skills import engine_ops_commands as eoc  # noqa: E402


class TestTheCards:
    def test_live_with_no_record_says_nothing_is_placed_and_why(self, monkeypatch):
        _flag(monkeypatch, True)
        line = eoc.autoconfirm_placement_line(RuneClawEngine, is_live=True)
        assert "no order is placed without a tap" in line
        assert "no eligibility record exists" in line
        assert "auto-execute" not in line

    def test_live_with_a_record_says_it_is_placed(self, monkeypatch):
        _flag(monkeypatch, True)
        _eligible(monkeypatch)
        line = eoc.autoconfirm_placement_line(RuneClawEngine, is_live=True)
        # The stock/ETF exception may follow it while STOCK_TRADING_ENABLED is
        # off (the default): both arms are in test_the_house_does_not_auto_trade_stocks.
        assert line.startswith("Live: an idea that clears the bar is placed with no tap.")

    def test_the_flag_off_is_named(self, monkeypatch):
        _flag(monkeypatch, False)
        line = eoc.autoconfirm_placement_line(RuneClawEngine, is_live=True)
        assert "AUTO_CONFIRM_LIVE_ENABLED is off" in line

    def test_paper_mode_places_nothing_and_asks_nothing(self):
        def _never():
            raise AssertionError("paper mode read the live gate")

        line = eoc.autoconfirm_placement_line(
            SimpleNamespace(_autonomous_live_refusal=_never), is_live=False)
        assert "places nothing" in line

    def test_a_gate_that_raises_is_said_not_passed(self):
        def _boom():
            raise RuntimeError("x")

        line = eoc.autoconfirm_placement_line(
            SimpleNamespace(_autonomous_live_refusal=_boom), is_live=True)
        assert "could not be read (RuntimeError)" in line
        assert "is placed with no tap" not in line

    def test_the_reason_is_escaped(self):
        line = eoc.autoconfirm_placement_line(
            SimpleNamespace(_autonomous_live_refusal=lambda: "<b>x</b>"), is_live=True)
        assert "<b>x</b>" not in line and "&lt;b&gt;" in line

    @pytest.mark.parametrize("result", [
        {}, {"auto_withheld": 0}, {"auto_withheld": True},
        {"auto_withheld": "2"}, {"auto_withheld": -1},
    ])
    def test_no_line_when_nothing_was_held_back(self, result):
        assert eoc.forcescan_withheld_line(result) is None

    def test_the_held_back_line_counts_and_says_why(self):
        line = eoc.forcescan_withheld_line(
            {"auto_withheld": 2, "auto_withheld_why": "no <record>"})
        assert "<b>2</b>" in line and "no &lt;record&gt;" in line

    def test_the_held_back_line_with_no_reason(self):
        line = eoc.forcescan_withheld_line({"auto_withheld": 1})
        assert "<b>1</b>." in line

    def test_the_status_card_reads_runtime_not_the_boot_config(self, monkeypatch):
        """`/autoconfirm 0.75` and the adaptive threshold move RUNTIME; the
        card read the frozen CONFIG value and showed the boot default."""
        from bot.config import RUNTIME
        monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 0.72)
        sent: list = []

        async def _send(update, text, **kw):
            sent.append(text)

        host = SimpleNamespace(
            engine=SimpleNamespace(_autonomous_live_refusal=lambda: "nope"),
            _send=_send, _guard=AsyncMock(return_value=True),
            _get_tg_id=lambda u: "1")
        fn = eoc.EngineOpsCommands._cmd_autoconfirm
        fn = getattr(fn, "__wrapped__", fn)
        with patch.object(type(CONFIG), "is_live", return_value=True):
            _run(fn(host, SimpleNamespace(), SimpleNamespace(args=[])))
        assert len(sent) == 1, sent
        assert "72%" in sent[0], sent[0]
        assert "auto-execute" not in sent[0]
        assert "no order is placed without a tap, because nope" in sent[0]
