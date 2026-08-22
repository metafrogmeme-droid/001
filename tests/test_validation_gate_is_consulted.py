"""The validation gate is consulted, and the third answer is why it can be.

`bot/core/validation_gate.py` opened by saying, about itself:

    NOTE: Stub implementation — not yet wired to backtest harness.
    record_validation() must be called manually; no automated pipeline invokes it.
    The engine (bot/core/engine.py) does not consult this gate before executing
    trades.

A gate nothing consults is a claim with no mechanism. But it could not simply be
consulted either, and the reason is the subject of this file:

  * storage was in-memory, so every restart emptied it;
  * nothing called `record_validation`, so it was empty anyway;
  * `is_validated()` answered False for a strategy nobody had ever tested.

Those three together mean fail-closed is a trading halt on every redeploy — one
no operator would attribute to this file — while fail-open is a gate that never
blocks, which is the decorative all-clear this repo spends its guards
preventing. Neither is "the engine consults it".

So the verdict is THREE-VALUED. `PASSED` and `FAILED` are measurements;
`NEVER_TESTED` is the absence of one, and keeping them apart is what lets an
operator choose the untested case deliberately instead of discovering it as a
halt. The mode ladder (`off` / `shadow` / `enforce`, default shadow) is the same
one `integrity_veto` and `authority` already use, for the same reason: a control
whose first production signal is a refused trade has not been observed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.core.validation_gate import (
    BacktestValidationGate, FAILED, NEVER_TESTED, PASSED,
)


def _good(gate, name="swing"):
    gate.record_validation(name, sharpe=1.4, max_drawdown=8.0, win_rate=0.55,
                           total_trades=40, walk_forward_score=0.8)


def _bad(gate, name="scalp"):
    gate.record_validation(name, sharpe=0.1, max_drawdown=30.0, win_rate=0.3,
                           total_trades=40, walk_forward_score=0.1)


# ── The distinction the old API could not express ─────────────────────────

def test_never_tested_is_not_failed():
    gate = BacktestValidationGate()
    _bad(gate, "scalp")
    assert gate.verdict("scalp") == FAILED
    assert gate.verdict("swing") == NEVER_TESTED, (
        "a strategy nobody measured must not report the same verdict as one "
        "that was measured and lost — the gate cannot act on the difference, "
        "and neither can the operator reading it")


def test_a_thin_sample_is_a_measured_failure_not_a_pass():
    # 3 trades at a great Sharpe is not evidence; it is a small sample.
    gate = BacktestValidationGate()
    gate.record_validation("lucky", sharpe=3.0, max_drawdown=1.0, win_rate=1.0,
                           total_trades=3, walk_forward_score=0.9)
    assert gate.verdict("lucky") == FAILED


def test_a_never_tested_strategy_reports_no_sharpe_rather_than_zero():
    """`0.0` is a real, achievable Sharpe.

    The old status dict returned `"sharpe": 0.0` for a strategy nobody had
    measured — the `.get("pnl", 0)` shape from CLAUDE.md's table, an absent
    reading rendered as a measurement. None forces the caller to decide.
    """
    st = BacktestValidationGate().get_validation_status("nothing")
    assert st["sharpe"] is None, f"got {st['sharpe']!r}"
    assert st["badge"] == "NEVER TESTED"
    assert st["verdict"] == NEVER_TESTED


def test_is_validated_still_means_passed_only():
    gate = BacktestValidationGate()
    _good(gate); _bad(gate)
    assert gate.is_validated("swing") is True
    assert gate.is_validated("scalp") is False
    assert gate.is_validated("never-heard-of-it") is False


# ── Persistence: a restart must not silently change policy ────────────────

def test_validations_survive_a_restart(tmp_path):
    """Without this, enabling `enforce` means a halt at every redeploy.

    The store empties, every strategy reads NEVER_TESTED, and nothing an
    operator changed that day explains it.
    """
    p = tmp_path / "validations.json"
    _good(BacktestValidationGate(p))
    reopened = BacktestValidationGate(p)
    assert reopened.verdict("swing") == PASSED
    assert reopened.has_any_records()


def test_a_corrupt_store_reads_as_untested_not_as_validated(tmp_path):
    """Fail-open on read, but toward the SAFE answer.

    An unreadable store must not resolve to "everything is validated". It
    resolves to NEVER_TESTED, which is the truth: nothing could be read.
    """
    p = tmp_path / "validations.json"
    p.write_text("{ this is not json", encoding="utf-8")
    gate = BacktestValidationGate(p)
    assert gate.verdict("swing") == NEVER_TESTED
    assert not gate.has_any_records()


def test_a_malformed_record_does_not_poison_the_rest(tmp_path):
    p = tmp_path / "validations.json"
    good = BacktestValidationGate(p)
    _good(good, "swing")
    raw = json.loads(p.read_text())
    raw["broken"] = {"strategy_name": "broken"}          # missing fields
    p.write_text(json.dumps(raw), encoding="utf-8")
    gate = BacktestValidationGate(p)
    assert gate.verdict("swing") == PASSED
    assert gate.verdict("broken") == NEVER_TESTED


def test_has_any_records_is_the_is_reading_seam():
    """Empty store vs. per-strategy verdict are different questions.

    `integrity_veto.is_reading()` exists because `assess({})` returning "clear"
    is a confident all-clear over no data. Same split: every strategy reading
    NEVER_TESTED is correct per-strategy and misleading as a headline.
    """
    gate = BacktestValidationGate()
    assert not gate.has_any_records()
    assert gate.verdict("anything") == NEVER_TESTED     # correct per-strategy
    _good(gate)
    assert gate.has_any_records()


# ── The engine actually consults it ───────────────────────────────────────

RISK = Path(__file__).resolve().parents[1] / "bot" / "risk" / "risk_engine.py"


@pytest.fixture
def gate_flags():
    """Flip the frozen CONFIG.risk switches and put them back.

    `CONFIG.risk` is a frozen dataclass; `object.__setattr__` is how
    tests/test_authority_engine_bridge.py flips the sibling switch.
    """
    from bot.config import CONFIG
    saved = {}

    def apply(**flags):
        for k, v in flags.items():
            saved.setdefault(k, getattr(CONFIG.risk, k))
            object.__setattr__(CONFIG.risk, k, v)

    yield apply
    for k, v in saved.items():
        object.__setattr__(CONFIG.risk, k, v)


def _evaluate(strategy_type, gate, monkeypatch, tmp_path, gate_flags,
              mode="enforce", allow_untested=False):
    """Drive the REAL RiskEngine.evaluate() and return its RiskCheck.

    Driven rather than grepped. The whole reason this file exists is that the
    gate was present and unreached — a source scan proving the hook is written
    would repeat exactly the mistake it is here to fix.
    """
    import io
    import contextlib
    import logging
    from bot.core.red_team import _make_idea
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine

    monkeypatch.setattr("bot.core.validation_gate.get_validation_gate", lambda: gate)
    gate_flags(validation_gate_enabled=True, validation_gate_mode=mode,
               validation_gate_allow_untested=allow_untested)

    engine = RiskEngine(PortfolioTracker(), state_file=str(tmp_path / "s.json"))
    sink = io.StringIO()
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return engine.evaluate(_make_idea(strategy_type=strategy_type,
                                              tp=51500.0))
    finally:
        logging.disable(logging.NOTSET)


def test_the_engine_reaches_the_hook_and_rejects_a_failed_strategy(
        monkeypatch, tmp_path, gate_flags):
    gate = BacktestValidationGate(tmp_path / "v.json")
    _bad(gate, "scalp")
    check = _evaluate("scalp", gate, monkeypatch, tmp_path, gate_flags)
    hits = [r for r in check.checks_failed if "VALIDATION" in r]
    assert hits, ("the hook never ran — no VALIDATION line in checks_failed. "
                  f"failed={check.checks_failed[:5]}")
    assert "failed backtest validation" in hits[0]


def test_a_passed_strategy_is_not_rejected_by_this_gate(monkeypatch, tmp_path, gate_flags):
    gate = BacktestValidationGate(tmp_path / "v.json")
    _good(gate, "swing")
    check = _evaluate("swing", gate, monkeypatch, tmp_path, gate_flags)
    assert any("VALIDATION" in r and "passed" in r for r in check.checks_passed)
    assert not [r for r in check.checks_failed if "VALIDATION" in r], (
        "a validated strategy was rejected BY THE VALIDATION GATE — it can "
        "only ever tighten, and this one has evidence")


def test_never_tested_is_decided_explicitly_not_by_accident(monkeypatch, tmp_path, gate_flags):
    """The same strategy, both ways, on one switch.

    This is the case that makes the gate wirable at all: an empty store means
    every strategy is NEVER_TESTED, so whether that blocks has to be a decision
    somebody made rather than a consequence of the store being new.
    """
    gate = BacktestValidationGate(tmp_path / "v.json")
    _good(gate, "swing")                       # store is populated but lacks 'position'

    blocked = _evaluate("position", gate, monkeypatch, tmp_path, gate_flags,
                        allow_untested=False)
    assert [r for r in blocked.checks_failed if "never been backtested" in r]

    allowed = _evaluate("position", gate, monkeypatch, tmp_path, gate_flags,
                        allow_untested=True)
    assert not [r for r in allowed.checks_failed if "VALIDATION" in r]
    assert [r for r in allowed.checks_passed if "never_tested" in r]


def test_shadow_records_what_it_would_reject_and_blocks_nothing(
        monkeypatch, tmp_path, gate_flags):
    gate = BacktestValidationGate(tmp_path / "v.json")
    _bad(gate, "scalp")
    check = _evaluate("scalp", gate, monkeypatch, tmp_path, gate_flags, mode="shadow")
    assert not [r for r in check.checks_failed if "VALIDATION" in r], (
        "shadow mode rejected a trade — the point of shadow is that it does not")
    assert [r for r in check.checks_passed if "shadow — would reject" in r], (
        "shadow blocked nothing AND recorded nothing, which is indistinguishable "
        "from the gate being off")


def test_a_gate_fault_never_affects_a_trade(monkeypatch, tmp_path, gate_flags):
    """Fail-open bridge, like its neighbours. A gate bug cannot halt the engine."""
    class _Exploding:
        def verdict(self, *_a, **_k):
            raise RuntimeError("boom")

        def has_any_records(self):
            raise RuntimeError("boom")

    check = _evaluate("scalp", _Exploding(), monkeypatch, tmp_path, gate_flags)
    assert not [r for r in check.checks_failed if "VALIDATION" in r]
    assert [r for r in check.checks_passed if "VALIDATION: skipped" in r]


def test_an_idea_with_no_strategy_is_skipped_not_scored(monkeypatch, tmp_path, gate_flags):
    """Nothing to look up is not the same as nothing found."""
    gate = BacktestValidationGate(tmp_path / "v.json")
    _good(gate, "swing")
    check = _evaluate("", gate, monkeypatch, tmp_path, gate_flags, allow_untested=False)
    assert [r for r in check.checks_passed if "names no strategy" in r]
    assert not [r for r in check.checks_failed if "VALIDATION" in r]


def test_the_hook_can_only_tighten():
    """Veto-only, like its neighbours. A passed backtest is not a reason to trade.

    Bounded by code at both ends so this reads the hook and not the file.
    """
    src = RISK.read_text(encoding="utf-8")
    i = src.index("validation_gate_enabled")
    j = src.index("VALIDATION: skipped (error", i)
    block = src[i:j]
    assert "failed.append" in block, "the hook cannot reject at all"
    # It must never remove a rejection or approve anything.
    for forbidden in ("failed.remove", "failed.clear", "failed = [", "return True"):
        assert forbidden not in block, (
            f"the validation hook contains {forbidden!r} — it can only ever add "
            "a rejection, never loosen one")


def test_enforce_is_not_the_default():
    """Shadow first. The first run after enabling sees an EMPTY store.

    In enforce that is a full halt attributable to nothing an operator changed
    that day, so the default observes and records what it would have refused.
    """
    from bot.config import CONFIG
    assert CONFIG.risk.validation_gate_enabled is False, (
        "the gate is on by default; it should be opt-in like intent_policy and "
        "the authority envelope")
    assert CONFIG.risk.validation_gate_mode == "shadow"
    assert CONFIG.risk.validation_gate_allow_untested is True, (
        "refusing every unmeasured strategy by default is the empty-store halt "
        "made permanent")


# ── The recorder, and the one run it must refuse ──────────────────────────

class _Trade:
    def __init__(self, setup, pnl):
        self.setup, self.pnl_usd = setup, pnl


class _Result:
    sharpe_ratio = 1.5
    max_drawdown_pct = 7.0

    def __init__(self, trades):
        self.trades = trades


def test_a_real_run_records_per_strategy(monkeypatch, tmp_path, gate_flags):
    from bot.backtest import runner
    gate = BacktestValidationGate(tmp_path / "v.json")
    monkeypatch.setattr("bot.core.validation_gate.get_validation_gate", lambda: gate)
    res = _Result([_Trade("swing", 10.0) for _ in range(12)]
                  + [_Trade("scalp", -1.0) for _ in range(11)])
    runner._record_validations(res, used_synthetic=False, data_source="bitget_real")
    # Keyed by strategy_type — the same vocabulary the risk engine looks up.
    assert gate.verdict("swing") == PASSED
    assert gate.get_validation_status("swing")["details"]["total_trades"] == 12
    assert gate.get_validation_status("scalp")["details"]["total_trades"] == 11


def test_a_synthetic_run_records_nothing(monkeypatch, tmp_path, capsys):
    """THE MOST IMPORTANT ASSERTION HERE.

    The runner already prints "these numbers come from SYNTHETIC data — NOT a
    real backtest". Recording them would put a fabricated Sharpe behind a gate
    whose whole job is answering "has this been shown to work on real data".
    A refused recording leaves the strategy NEVER_TESTED, which is true.
    """
    from bot.backtest import runner
    gate = BacktestValidationGate(tmp_path / "v.json")
    monkeypatch.setattr("bot.core.validation_gate.get_validation_gate", lambda: gate)
    res = _Result([_Trade("swing", 10.0) for _ in range(50)])
    runner._record_validations(res, used_synthetic=True, data_source="synthetic")
    assert gate.verdict("swing") == NEVER_TESTED, (
        "a synthetic backtest validated a strategy")
    assert not gate.has_any_records()
    assert "NOT recorded" in capsys.readouterr().out


def test_trades_without_a_strategy_are_not_recorded_under_a_blank_name(
        monkeypatch, tmp_path, gate_flags):
    from bot.backtest import runner
    gate = BacktestValidationGate(tmp_path / "v.json")
    monkeypatch.setattr("bot.core.validation_gate.get_validation_gate", lambda: gate)
    runner._record_validations(_Result([_Trade("", 1.0), _Trade(None, 2.0)]),
                               used_synthetic=False, data_source="bitget_real")
    assert not gate.has_any_records()


@pytest.mark.parametrize("verdict_name,expected", [
    ("swing", PASSED), ("scalp", FAILED), ("unseen", NEVER_TESTED)])
def test_round_trip_through_disk_preserves_every_verdict(tmp_path, verdict_name, expected):
    p = tmp_path / "v.json"
    g = BacktestValidationGate(p)
    _good(g, "swing"); _bad(g, "scalp")
    assert BacktestValidationGate(p).verdict(verdict_name) == expected
