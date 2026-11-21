"""The analyzer and the market scanner have a coverage floor of their own.

`scripts/ci_test_gate.py` enforced coverage on the money path only. The two
modules that decide what the bot proposes were measured by nothing. They are
now instrumented on the same first run and judged per module against
`tests/signal_coverage_floor.json`, apart from the money floor -- whose list
(`COV_TARGETS`) the honesty gate derives its money path from, so it stays
exactly what it was.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_test_gate as gate  # noqa: E402


def test_the_money_list_is_unchanged_and_the_signal_list_is_separate():
    assert gate.COV_TARGETS == ["bot.risk", "bot.core.live_executor", "bot.compliance"]
    assert gate.SIGNAL_COV_TARGETS == ["bot.core.analyzer", "bot.core.market_scanner"]
    assert not set(gate.COV_TARGETS) & set(gate.SIGNAL_COV_TARGETS)


def test_both_lists_reach_the_first_run():
    for t in gate.COV_TARGETS + gate.SIGNAL_COV_TARGETS:
        assert f"--cov={t}" in gate.COV_FLAGS, t


def test_the_money_report_is_restricted_to_the_money_modules():
    # Without --include the 60% total would be taken over the signal modules too.
    assert gate._cov_include(gate.COV_TARGETS) == \
        "bot/risk/*,bot/core/live_executor.py,bot/compliance/*"
    assert gate._cov_include(["bot.core.analyzer"]) == "bot/core/analyzer.py"


class _Ran:
    def __init__(self, stdout: str):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def test_each_report_is_run_over_exactly_its_own_modules(monkeypatch):
    # The helper above being right says nothing about whether the reports
    # pass it: dropping --include from the money call left every other test
    # here green. So record the commands the two verdicts actually run.
    ran: list[list[str]] = []

    def fake_run(cmd, **_kw):
        ran.append(list(cmd))
        return _Ran("100.00\n" if "--format=total" in cmd else "TOTAL 90%\n")
    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate, "_load_signal_floors",
                        lambda: {t: 50 for t in gate.SIGNAL_COV_TARGETS})

    assert gate._money_coverage_verdict() == gate.COV_OK
    assert gate._signal_coverage_verdict() == gate.COV_OK
    money, *signal = ran
    assert f"--include={gate._cov_include(gate.COV_TARGETS)}" in money
    assert f"--fail-under={gate.COV_FAIL_UNDER}" in money
    assert [[a for a in c if a.startswith("--include=")] for c in signal] == \
        [[f"--include={gate._cov_include([t])}"] for t in gate.SIGNAL_COV_TARGETS]
    assert all("--format=total" in c for c in signal)


def test_the_recorded_floor_names_each_signal_module_once_with_a_whole_number():
    floors = gate._load_signal_floors()
    assert floors is not None, f"{gate.SIGNAL_COV_FLOOR} did not load"
    assert sorted(floors) == sorted(gate.SIGNAL_COV_TARGETS)
    assert all(isinstance(v, int) and 0 < v <= 100 for v in floors.values())
    raw = json.loads(gate.SIGNAL_COV_FLOOR.read_text(encoding="utf-8"))
    assert raw.get("basis"), "a floor carries the date and run it was measured on"


class TestTheVerdict:
    FLOORS = {"bot.core.analyzer": 70, "bot.core.market_scanner": 60}

    def test_at_or_above_every_floor_is_ok(self):
        verdict, lines = gate.signal_floor_verdict(
            {"bot.core.analyzer": 70.0, "bot.core.market_scanner": 61.5}, self.FLOORS)
        assert verdict == gate.COV_OK
        assert any("can be raised to 61" in ln for ln in lines)

    def test_below_one_floor_fails_and_names_the_module(self):
        verdict, lines = gate.signal_floor_verdict(
            {"bot.core.analyzer": 69.99, "bot.core.market_scanner": 90.0}, self.FLOORS)
        assert verdict == gate.COV_BELOW
        assert any("bot.core.analyzer coverage 69.99% is below its 70% floor" in ln
                   for ln in lines)

    def test_a_module_with_no_data_is_unmeasured_never_zero_and_never_ok(self):
        verdict, lines = gate.signal_floor_verdict(
            {"bot.core.analyzer": None, "bot.core.market_scanner": 90.0}, self.FLOORS)
        assert verdict == gate.COV_UNMEASURED
        assert any("CANNOT CHECK" in ln and "bot.core.analyzer" in ln for ln in lines)

    def test_below_wins_over_unmeasured(self):
        verdict, _ = gate.signal_floor_verdict(
            {"bot.core.analyzer": None, "bot.core.market_scanner": 10.0}, self.FLOORS)
        assert verdict == gate.COV_BELOW

    def test_unreadable_floors_are_unmeasured(self):
        verdict, lines = gate.signal_floor_verdict({"bot.core.analyzer": 99.0}, None)
        assert verdict == gate.COV_UNMEASURED
        assert "CANNOT CHECK" in lines[0]


def test_a_malformed_floor_file_does_not_load(tmp_path, monkeypatch):
    bad = tmp_path / "f.json"
    for body in ('{"floors": {"bot.core.analyzer": 70}}',              # one module missing
                 '{"floors": {"bot.core.analyzer": 70, "bot.core.market_scanner": "60"}}',
                 '{"floors": {"bot.core.analyzer": 0, "bot.core.market_scanner": 60}}',
                 "not json"):
        bad.write_text(body, encoding="utf-8")
        monkeypatch.setattr(gate, "SIGNAL_COV_FLOOR", bad)
        assert gate._load_signal_floors() is None, body
