"""The documented benchmark command was the one path with no provenance.

`docs/FROZEN_BENCHMARK.md` names its one-liner:

    python -m bot.backtest.runner --dataset data/benchmark/majors_1h \
        --honest --walk-forward 6

and promises, in the same section, that "the run stamps
``data_source=frozen_snapshot:<dataset_hash>`` so every result is
self-describing about *which* frozen data it measured". With `--dataset` and no
`--symbols` that command is a PORTFOLIO run, and `_run_portfolio` had its own
inline snapshot loader instead of calling `_load_bars` — so it printed the hash
to stdout and left `result.data_source` at the model default, the string
`"unknown"`. Four things followed from that one duplicated reading:

1. the saved JSON could not say which data produced it;
2. the walk-forward branch ``return``ed before the ``if args.output:`` block, so
   ``-o`` was accepted and wrote **nothing**, silently;
3. `_record_validations` — whose own docstring says it is the pipeline that
   stops `validation_gate` reading NEVER_TESTED forever, and which `config.py`
   names as the reason shadow mode exists ("until the backtest runner has
   populated the store") — was never reached;
4. the live-fetch branch dropped a failed symbol with ``print("skipped")``
   four lines under the frozen branch's own comment: *"dropping a symbol would
   change the universe and thus the measured system."* A fetch that merely
   returned nothing fell through both ``if bars:`` and the ``except`` and
   vanished with no line at all.

WHY NO GUARD CAUGHT IT. `tests/test_backtest_data_provenance.py` exists for
exactly this claim — its docstring is about a run that "was indistinguishable
from a real backtest" — and every one of its tests drives `runner._load_bars`.
None drives a runner path, so none can see a caller that does not call it. Its
`test_stamp_round_trips_through_dump` pins that a stamp set on the model
survives `model_dump`: the field WORKS, which is one step short of any run
SETTING it. A provenance guard one caller short, and the caller it missed is
the benchmark.

So every test here drives `_run_portfolio` and reads what it wrote.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

import pytest

import bot.backtest.runner as runner
from bot.backtest import portfolio_engine as pe
from bot.backtest import snapshot as snap
from bot.backtest.models import BacktestResult, BacktestTrade
from bot.compat import UTC

SYMS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
HASH = "a" * 64
MANIFEST = {"dataset_hash": HASH, "timeframe": "1h"}


def _args(**over):
    base = dict(
        symbols=",".join(SYMS), timeframe="1h", balance=10_000.0, commission=0.06,
        slippage=0.05, fill_mode="next_open", breaker_reset_bars=0, use_llm=False,
        use_recorded_llm=False, use_recorded_order_flow=False, of_snapshot_path="",
        dataset=None, limit=720, walk_forward=0, output=None, strict_data=False,
        last_bars=0, honest=False, confidence_threshold=0.0, volume_spike_min=None,
        regime_filter="", rsi_max=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _fake_bars(n=200):
    """Opaque rows — nothing under test inspects a bar."""
    return list(range(n))


def _result(**over):
    base = dict(symbol=SYMS[0], timeframe="1h", start_date="2026-01-01",
                end_date="2026-02-01", initial_balance=10_000.0, final_equity=10_100.0,
                commission_pct=0.06, slippage_pct=0.05, total_return_pct=1.0,
                total_pnl=100.0, total_commission=6.0, total_slippage=5.0,
                net_pnl=100.0, total_trades=4, winning_trades=3, losing_trades=1,
                win_rate=0.75, avg_win_usd=50.0, avg_loss_usd=-20.0,
                largest_win_usd=60.0, largest_loss_usd=-20.0,
                avg_trade_duration_hours=4.4, max_drawdown_pct=2.0,
                max_drawdown_usd=200.0, max_consecutive_losses=1,
                profit_factor=1.6, sharpe_ratio=1.4, sortino_ratio=1.8,
                calmar_ratio=0.5, risk_reward_avg=1.5,
                total_signals_generated=10, total_ideas_generated=8,
                total_ideas_rejected_risk=2, total_ideas_rejected_confidence=2)
    base.update(over)
    return BacktestResult(**base)


class _StubPB:
    """Stands in for PortfolioBacktester: no engine, no bars, one result."""
    per_symbol: dict = {}
    result = None

    def __init__(self, config, symbols=None):
        self.config, self.symbols = config, list(symbols or [])
        self.per_symbol = {s: {"trades": 1, "net_pnl": 1.0, "win_rate": 1.0}
                           for s in self.symbols}

    async def run(self, data):
        return type(self).result or _result()

    def cleanup(self):
        pass


@pytest.fixture
def frozen(monkeypatch):
    """A frozen dataset whose every symbol loads."""
    monkeypatch.setattr(snap, "load_manifest_multi", lambda d: MANIFEST)
    monkeypatch.setattr(snap, "load_symbol_multi", lambda d, s, m: _fake_bars())
    monkeypatch.setattr(pe, "PortfolioBacktester", _StubPB)
    _StubPB.result = None
    return MANIFEST


def _run(args):
    return asyncio.run(runner._run_portfolio(args))


def _written(tmp_path, name="out.json"):
    p = tmp_path / name
    return str(p), p


# ── 1. the stamp the doc promises ───────────────────────────────────────

def test_the_portfolio_result_names_the_frozen_data_it_read(frozen, tmp_path, capsys):
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    saved = json.loads(p.read_text())
    assert saved["data_source"] == f"frozen_snapshot:{HASH}"


def test_the_stamp_is_never_the_model_default(frozen, tmp_path):
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    # "unknown" is BacktestResult's default. A result carrying it after a run
    # that read a named, content-hashed dataset is an absence rendered as a value.
    assert json.loads(p.read_text())["data_source"] != "unknown"


def test_one_reading_shared_with_load_bars(frozen, tmp_path):
    """`_load_bars` and `_run_portfolio` must agree byte for byte.

    Two loaders each deciding "which data is this" is the second-answer shape
    this repo has paid for over the provider->env map three times.
    """
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    from_portfolio = json.loads(p.read_text())["data_source"]
    assert from_portfolio == runner.frozen_source(MANIFEST)


def test_a_live_fetch_portfolio_says_bitget_real(monkeypatch, tmp_path):
    async def ok(**kw):
        return _fake_bars()
    monkeypatch.setattr(runner.DataLoader, "from_bitget", ok)
    monkeypatch.setattr(pe, "PortfolioBacktester", _StubPB)
    _StubPB.result = None
    out, p = _written(tmp_path)
    _run(_args(output=out))
    assert json.loads(p.read_text())["data_source"] == "bitget_real"


# ── 2. --output on the walk-forward path ────────────────────────────────

def _folds(monkeypatch, rows):
    async def wf(data, config, n_folds=6):
        return rows
    monkeypatch.setattr(pe, "portfolio_walk_forward", wf)


def _fold(k, trades=5, ret=0.5, sharpe=1.2, dd=1.0):
    return {"fold": k, "oos_start": "a", "oos_end": "b", "trades": trades,
            "return_pct": ret, "win_rate": 0.6, "max_dd_pct": dd,
            "profit_factor": 1.3, "sharpe": sharpe, "per_symbol": {},
            "_trades": [_trade("swing")] * trades}


def _trade(setup, pnl=1.0):
    """A real BacktestTrade, not a stub.

    The first draft used a hand-rolled object carrying only the two attributes
    these assertions read. It blew up in `_format_result_summary` on
    `net_pnl_usd` — which is the useful half: a stub shaped like the assertion
    rather than like the type can pass for a reason unrelated to the rule.
    """
    return BacktestTrade(
        trade_id=f"t-{setup or 'none'}-{pnl}", symbol=SYMS[0], direction="LONG",
        entry_price=100.0, exit_price=101.0,
        entry_time=datetime(2026, 1, 1, tzinfo=UTC),
        exit_time=datetime(2026, 1, 1, 4, tzinfo=UTC),
        quantity=1.0, size_usd=100.0, pnl_usd=pnl, pnl_pct=1.0,
        commission_usd=0.12, slippage_usd=0.05, net_pnl_usd=pnl,
        exit_reason="TP", confidence=0.8, risk_verdict="APPROVED",
        entry_regime="RANGE", setup=setup, signal_type="momentum_confluence")


def test_output_on_the_walk_forward_path_actually_writes(frozen, monkeypatch, tmp_path):
    """The regression. `-o` was accepted and silently wrote nothing."""
    _folds(monkeypatch, [_fold(0), _fold(1)])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=2, output=out))
    assert p.exists(), "--output was accepted and no file was written"


def test_the_written_walk_forward_file_names_its_dataset(frozen, monkeypatch, tmp_path):
    _folds(monkeypatch, [_fold(0), _fold(1)])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=2, output=out))
    saved = json.loads(p.read_text())
    assert saved["data_source"] == f"frozen_snapshot:{HASH}"
    assert saved["mode"] == "portfolio_walk_forward"
    assert saved["folds_run"] == 2


def test_the_written_file_carries_the_numbers_the_card_printed(frozen, monkeypatch, tmp_path):
    _folds(monkeypatch, [_fold(0, ret=1.0), _fold(1, ret=-2.0)])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=2, output=out))
    saved = json.loads(p.read_text())
    assert saved["mean_oos_return_pct"] == pytest.approx(-0.5)
    assert saved["worst_oos_return_pct"] == pytest.approx(-2.0)
    assert saved["profitable_folds"] == 1


def test_private_fold_keys_do_not_reach_the_file(frozen, monkeypatch, tmp_path):
    """`_trades` carries full trade objects for pooling; it is not a result field."""
    _folds(monkeypatch, [_fold(0)])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=1, output=out))
    assert all(not k.startswith("_") for k in json.loads(p.read_text())["folds"][0])


# ── 3. no fold ran is not a zero ────────────────────────────────────────

def test_no_fold_ran_does_not_crash_the_benchmark(frozen, monkeypatch, tmp_path):
    """`sum(rets)/len(rets)` over an empty fold list was a ZeroDivisionError
    out of the benchmark command. `portfolio_walk_forward` skips a fold whose
    symbols all have <= lookback_size bars, so every fold can be skipped."""
    _folds(monkeypatch, [])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=3, output=out))   # must not raise
    assert p.exists()


def test_no_fold_ran_is_not_reported_as_zero_percent(frozen, monkeypatch, tmp_path, capsys):
    _folds(monkeypatch, [])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=3, output=out))
    saved = json.loads(p.read_text())
    assert saved["mean_oos_return_pct"] is None
    assert saved["worst_oos_return_pct"] is None
    assert saved["folds_run"] == 0
    said = capsys.readouterr().out
    assert "NO FOLD RAN" in said
    # 0.00% is a real, achievable result and it is not this one.
    assert "mean OOS ret +0.00%" not in said


# ── 4. the universe is part of the measured system ──────────────────────

def _fetch_some(ok_syms, *, empty=(), raises=()):
    async def fetch(symbol=None, timeframe=None, limit=None, **kw):
        if symbol in raises:
            raise RuntimeError("venue down")
        if symbol in empty:
            return []
        return _fake_bars() if symbol in ok_syms else _fake_bars()
    return fetch


def test_a_dropped_symbol_is_named_in_the_result(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.DataLoader, "from_bitget",
                        _fetch_some({SYMS[0]}, raises={SYMS[1]}))
    monkeypatch.setattr(pe, "PortfolioBacktester", _StubPB)
    _StubPB.result = None
    out, p = _written(tmp_path)
    _run(_args(output=out))
    uni = json.loads(p.read_text())["universe"]
    assert uni["requested"] == SYMS
    assert uni["measured"] == [SYMS[0]]
    assert [d["symbol"] for d in uni["dropped"]] == [SYMS[1]]
    assert "fetch failed" in uni["dropped"][0]["reason"]


def test_an_empty_fetch_is_a_drop_with_a_reason(monkeypatch, tmp_path, capsys):
    """A fetch returning [] fell through `if bars:` AND the `except` — the
    symbol left the universe with no line of output at all."""
    monkeypatch.setattr(runner.DataLoader, "from_bitget",
                        _fetch_some({SYMS[0]}, empty={SYMS[1]}))
    monkeypatch.setattr(pe, "PortfolioBacktester", _StubPB)
    _StubPB.result = None
    out, p = _written(tmp_path)
    _run(_args(output=out))
    uni = json.loads(p.read_text())["universe"]
    assert [d["symbol"] for d in uni["dropped"]] == [SYMS[1]]
    assert uni["dropped"][0]["reason"], "a drop with no reason is a silent drop"
    assert SYMS[1] in capsys.readouterr().out


def test_a_full_universe_reports_no_drops(frozen, tmp_path):
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    uni = json.loads(p.read_text())["universe"]
    assert uni["dropped"] == []
    assert uni["measured"] == uni["requested"] == SYMS


def test_strict_data_refuses_to_measure_a_changed_universe(monkeypatch, tmp_path):
    """--honest sets --strict-data, whose contract is that an automated run
    never silently measures something other than what it was asked to."""
    monkeypatch.setattr(runner.DataLoader, "from_bitget",
                        _fetch_some({SYMS[0]}, raises={SYMS[1]}))
    monkeypatch.setattr(pe, "PortfolioBacktester", _StubPB)
    _StubPB.result = None
    with pytest.raises(SystemExit) as exc:
        _run(_args(strict_data=True))
    assert exc.value.code == 1


def test_strict_data_does_not_block_a_complete_universe(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.DataLoader, "from_bitget", _fetch_some(set(SYMS)))
    monkeypatch.setattr(pe, "PortfolioBacktester", _StubPB)
    _StubPB.result = None
    out, p = _written(tmp_path)
    _run(_args(strict_data=True, output=out))       # must not exit
    assert json.loads(p.read_text())["universe"]["dropped"] == []


# ── 5. the validation gate's pipeline reaches the benchmark ─────────────

class _Gate:
    def __init__(self):
        self.recorded: list[dict] = []

    def record_validation(self, **kw):
        self.recorded.append(kw)

    def verdict(self, name):
        return "passed"


@pytest.fixture
def gate(monkeypatch):
    g = _Gate()
    import bot.core.validation_gate as vg
    monkeypatch.setattr(vg, "get_validation_gate", lambda: g)
    return g


def test_the_portfolio_single_pass_records_validations(frozen, gate, tmp_path):
    _StubPB.result = _result()
    _StubPB.result.trades = [_trade("swing"), _trade("scalp")]
    out, _p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    assert sorted(r["strategy_name"] for r in gate.recorded) == ["scalp", "swing"]


def test_the_walk_forward_records_oos_evidence(frozen, gate, monkeypatch, tmp_path):
    _folds(monkeypatch, [_fold(0, trades=6), _fold(1, trades=4)])
    out, _p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=2, output=out))
    assert [r["strategy_name"] for r in gate.recorded] == ["swing"]
    assert gate.recorded[0]["total_trades"] == 10      # pooled across folds


def test_the_mean_oos_sharpe_excludes_folds_that_never_traded(frozen, gate, monkeypatch,
                                                              tmp_path):
    """A fold that took no trade reports sharpe 0.0, and nothing measured it.
    Averaging it in drags a real reading toward a fabricated one."""
    _folds(monkeypatch, [_fold(0, trades=4, sharpe=1.0),
                         _fold(1, trades=0, sharpe=0.0),
                         _fold(2, trades=4, sharpe=2.0)])
    out, _p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=3, output=out))
    assert gate.recorded[0]["sharpe"] == pytest.approx(1.5)   # not 1.0


def test_no_fold_traded_records_nothing(frozen, gate, monkeypatch, tmp_path, capsys):
    """NEVER_TESTED is the true answer when nothing was measured, and the
    gate's three-valued verdict only survives if nobody writes a fake row."""
    _folds(monkeypatch, [_fold(0, trades=0, sharpe=0.0)])
    out, _p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=1, output=out))
    assert gate.recorded == []
    assert "NOT recorded" in capsys.readouterr().out


def test_a_trade_with_no_strategy_type_is_not_recorded_under_one(frozen, gate, tmp_path):
    _StubPB.result = _result()
    _StubPB.result.trades = [_trade(""), _trade("  ")]
    out, _p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    assert gate.recorded == []


def test_the_recorded_sharpe_is_labelled_as_a_mean(frozen, gate, monkeypatch,
                                                   tmp_path, capsys):
    """It is not the single-run Sharpe and must not borrow its name — a
    walk-forward has no single Sharpe."""
    _folds(monkeypatch, [_fold(0, trades=3, sharpe=1.0)])
    _run(_args(dataset="ds", walk_forward=1))
    assert "mean OOS sharpe" in capsys.readouterr().out


def test_a_synthetic_source_would_record_nothing(frozen, gate, monkeypatch, tmp_path):
    """`used_synthetic` is derived from the one provenance reading rather than
    a literal False, so the gate's most important rule cannot rot if a
    synthetic path is ever added to this branch."""
    monkeypatch.setattr(snap, "load_manifest_multi",
                        lambda d: {"dataset_hash": HASH})
    monkeypatch.setattr(runner, "frozen_source", lambda man: "synthetic_fallback")
    _StubPB.result = _result()
    _StubPB.result.trades = [_trade("swing")]
    out, _p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    assert gate.recorded == []


# The next two drive `_record_walk_forward_validations` DIRECTLY rather than
# through `_run_portfolio`, and the reason is worth stating: driving them
# through the runner plants a fold dict that `portfolio_walk_forward` cannot
# emit — every field it writes comes off a required, non-Optional
# `BacktestResult` float. The first draft did exactly that and blew up in the
# fold PRINTER (`{f['max_dd_pct']:5.2f}` on a None), which is a crash in a
# renderer for a state its producer cannot produce. Hardening that renderer
# would be a refactor bought with no safety, so it is left alone, and the guard
# under test is named for what it is: the RECORDER's contract about an absent
# reading, not a demonstrated production path. The honesty ratchet's finding
# was about the recorder.

def test_a_fold_with_an_unreadable_drawdown_records_no_drawdown_at_all(gate, capsys):
    """The ratchet failed this commit on `float(f.get("max_dd_pct") or 0.0)` and
    it was right. 0.0 is the BEST value that field can carry — "this strategy
    never drew down" — and reaching it from an absent reading writes a confident
    all-clear into a store an operator reads."""
    bad = _fold(0, trades=5, sharpe=1.5)
    bad["max_dd_pct"] = None
    runner._record_walk_forward_validations([bad], bad["_trades"], "frozen_snapshot:x")
    assert gate.recorded == []
    assert "NOT recorded" in capsys.readouterr().out


def test_a_readable_fold_beside_an_unreadable_one_still_records(gate):
    """Omit, not guard: one unreadable fold must not blank the evidence the
    others carry — the composite-view half of the table in CLAUDE.md."""
    bad = _fold(0, trades=5, sharpe=9.0)
    bad["max_dd_pct"] = None
    good = _fold(1, trades=5, sharpe=1.0, dd=3.0)
    runner._record_walk_forward_validations(
        [bad, good], bad["_trades"] + good["_trades"], "frozen_snapshot:x")
    assert len(gate.recorded) == 1
    # The unreadable fold's 9.0 must not reach the mean, and its absent
    # drawdown must not become the recorded one.
    assert gate.recorded[0]["sharpe"] == pytest.approx(1.0)
    assert gate.recorded[0]["max_drawdown"] == pytest.approx(3.0)


def test_a_boolean_trade_count_is_not_a_trade_count(gate):
    """`True > 0` is True in Python, so a bool in a numeric field reads as one
    trade. `isinstance(x, int)` accepts bools; the reader rejects them."""
    odd = _fold(0, trades=5, sharpe=1.0)
    odd["trades"] = True
    runner._record_walk_forward_validations([odd], odd["_trades"], "frozen_snapshot:x")
    assert gate.recorded == []


def test_the_walk_forward_recorder_refuses_synthetic_evidence(gate, capsys):
    """The sibling recorder's most important line — "SYNTHETIC RUNS RECORD
    NOTHING" — holds on this path too. Unreachable today; a guard that depends
    on that staying true is a guard that rots."""
    f = _fold(0, trades=5, sharpe=2.0)
    runner._record_walk_forward_validations([f], f["_trades"], "synthetic_fallback")
    assert gate.recorded == []
    assert "not real" in capsys.readouterr().out


# ── 6. the gate is a side-effect; the results are the deliverable ───────

class _AngryGate(_Gate):
    def record_validation(self, **kw):
        raise RuntimeError("gate store is read-only")


@pytest.fixture
def angry_gate(monkeypatch):
    g = _AngryGate()
    import bot.core.validation_gate as vg
    monkeypatch.setattr(vg, "get_validation_gate", lambda: g)
    return g


def test_a_recording_fault_does_not_cost_the_result_file(frozen, angry_gate,
                                                         monkeypatch, tmp_path):
    """Both recorders used to run BEFORE anything was printed or written, so a
    fault inside one discarded a finished backtest — minutes of compute and, on
    this path, the `--output` file this commit exists to start writing."""
    _folds(monkeypatch, [_fold(0, trades=5)])
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", walk_forward=1, output=out))
    assert p.exists()
    assert json.loads(p.read_text())["pooled_trades"] == 5


def test_a_recording_fault_does_not_cost_the_single_pass_result(frozen, angry_gate,
                                                                tmp_path):
    _StubPB.result = _result()
    _StubPB.result.trades = [_trade("swing")]
    out, p = _written(tmp_path)
    _run(_args(dataset="ds", output=out))
    assert p.exists()
    assert json.loads(p.read_text())["data_source"] == f"frozen_snapshot:{HASH}"


def test_a_recording_fault_is_said_out_loud(frozen, angry_gate, monkeypatch,
                                            tmp_path, capsys):
    """Omit, out loud. A silent pass would make "recorded nothing" and "could
    not record" look alike — the distinction the gate's three-valued verdict
    is built on."""
    _folds(monkeypatch, [_fold(0, trades=5)])
    _run(_args(dataset="ds", walk_forward=1))
    said = capsys.readouterr().out
    assert "NOT recorded" in said
    assert "read-only" in said, "the reason the recording failed is not reported"
