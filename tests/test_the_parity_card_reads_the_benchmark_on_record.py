"""The parity card compares live against a benchmark its own document retracted
twice, asks a question it holds the numbers to answer, and folds eleven
execution aborts into the strategy's win rate.

MEASURED from the 2026-09-21 live card (194 filled · win 35% · PF 0.63 ·
net -$117.56 · "175 close(s) inferred") and from source:

- `format_report` printed ``+0.31% / PF 1.14`` as a STRING LITERAL. The
  benchmark document moved that baseline twice and then recorded, on
  2026-09-11, that the same command on the same frozen data reproduces
  -0.38% / PF 0.63 — "re-baseline from a written file, not from a number
  typed into prose" — and no file existed. The card's own framing (a live PF
  under the benchmark's means EXECUTION is the leak) then pointed the
  operator at execution for a week whose PF was the benchmark's own.
- it ended on "is live in the same ballpark?" and answered nothing.
- ``leverage_overshoot x10`` and ``sl_placement_failed x1`` — post-fill
  flatten guards, not strategy exits — sat inside the 194, the win rate, the
  PF and the net.
- ``PF inf`` (no losing trade) and ``PF 5451.06`` (over one dust loss) were
  printed to two decimals with no sample beside them.
- the weekly digest omitted the 175-of-194 caveat the full card carried, and
  printed ``0.5×`` for a ratio the full card printed as ``0.46×``.
- ``SL HIT 1 tr`` sat beside ``SL HIT (inferred) 51 tr``: one reason, two rows.

Every claim below is DRIVEN — the artefact is planted, the card rendered and
read — except where the honest instrument is a shape: the abort vocabulary is
derived from the executor's own source, because a hand-written set is the
`/setllm` ten-of-eleven shape.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bot.backtest import benchmark_record as br
from bot.backtest import parity
from bot.backtest.benchmark_record import BenchmarkReading, benchmark_on_record, card_line, symbol_base
from bot.utils.close_reason import EXECUTION_ABORT_REASONS, NON_FILL_CLOSE_REASONS, is_execution_abort, is_filled_close

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "bot" / "core" / "live_executor.py"
COMMISSION = 0.1   # per side -> 0.200% round trip modeled


# ── fixtures ────────────────────────────────────────────────────────────
def _t(net, sym="BTC/USDT:USDT", reason="SL HIT (inferred)", fill="ticker_fallback",
       fees=0.3, sig="momentum_confluence", setup="swing"):
    return {"symbol": sym, "entry_price": 100.0, "quantity": 1.0, "cost_usd": 10.0,
            "leverage": 10, "pnl_usd": net, "gross_pnl": None if net is None else net + fees,
            "commission": fees, "signal_type": sig, "strategy_type": setup,
            "close_reason": reason, "fill_source": fill}


def _reading(**over) -> BenchmarkReading:
    """A READ benchmark, planted — never the committed artefact, whose numbers
    move with the code and would make every assertion here a claim about
    today's tree rather than about the rule."""
    base = dict(state="read", reason="", path="planted/result.json",
                dataset="benchmark/majors_1h", dataset_hash="abc123", recorded_at="2026-09-21T07:31:02+00:00",
                code_sha="6f55e090634d", folds_run=6, profitable_folds=1,
                mean_oos_return_pct=-0.38, pooled_trades=112, pooled_wins=57, pooled_losses=55,
                pooled_net_usd=-226.86, pooled_win_rate=0.51, pooled_pf=0.63,
                pooled_mean_net_usd=-2.03, universe=("BTC", "ETH", "SOL"))
    base.update(over)
    return BenchmarkReading(**base)


def _card_shaped_rows(seed=7):
    """The pasted card's shape: 51 inferred stops (all losses), one exchange
    stop, 14 inferred trailing stops, 94 unknowns, 14 manual closes (all
    wins, so PF has no loss to divide by), 9 inferred targets with ONE dust
    loss, 11 execution aborts, 74 never-filled, and a third of the strategy
    exits outside the benchmark's universe: 194 filled rows, 183 of them
    strategy exits, 268 rows in all."""
    import random
    r = random.Random(seed)
    crypto = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
    tradfi = ["NATGAS/USDT:USDT", "CL/USDT:USDT", "AAPL/USDT:USDT"]
    rows = [_t(-round(r.uniform(2, 8), 2), sym=r.choice(crypto + tradfi)) for _ in range(51)]
    rows += [_t(-13.24, reason="SL HIT", fill="exchange_fill_sltp")]
    rows += [_t(round(r.uniform(-3, 2), 2), reason="TRAILING SL HIT (inferred)") for _ in range(14)]
    rows += [_t(round(r.uniform(4, 9), 2), reason="TP HIT (inferred)") for _ in range(8)]
    rows += [_t(-0.01, reason="TP HIT (inferred)")]
    rows += [_t(round(r.uniform(2, 6), 2), reason="manual_nlp", fill="exchange_fill") for _ in range(14)]
    rows += [_t(round(r.uniform(-6, 7), 2), sym=r.choice(crypto + tradfi),
                reason="CLOSED (unknown)") for _ in range(94)]
    rows += [_t(-0.36, reason="leverage_overshoot", fill="exchange_fill") for _ in range(10)]
    rows += [_t(-0.45, reason="sl_placement_failed", fill="exchange_fill")]
    rows += [_t(0.0, reason="expired", fill=None) for _ in range(74)]
    return rows


def _plant(tmp_path, artefact: dict, manifest_hash="deadbeef"):
    """An artefact with the snapshot manifest the reader pins it to beside it.
    ``manifest_hash=None`` plants no manifest at all."""
    p = tmp_path / "result.json"
    p.write_text(json.dumps(artefact))
    if manifest_hash is not None:
        (tmp_path / "manifest.json").write_text(json.dumps({"dataset_hash": manifest_hash}))
    return p


def _artefact(**over) -> dict:
    d = {"mode": "portfolio_walk_forward", "data_source": "frozen_snapshot:deadbeef",
         "recorded_at": "2026-09-21T07:31:02+00:00", "code_sha": "abcdef0123",
         "folds_run": 6, "profitable_folds": 1, "mean_oos_return_pct": -0.38,
         "universe": {"measured": ["ADA/USDT:USDT", "BTC/USDT:USDT"], "dropped": []},
         "pooled": {"trades": 112, "wins": 57, "losses": 55, "flat": 0, "net_usd": -226.86,
                    "win_rate": 0.51, "pf": 0.63, "mean_net_usd": -2.03}}
    d.update(over)
    return d


def _row_line(report: str, label: str) -> str:
    lines = [ln for ln in report.splitlines() if ln.strip().startswith(label)]
    assert len(lines) == 1, f"{label!r}: {len(lines)} rows"
    return lines[0]


# ── 1. the benchmark reading is three-valued ────────────────────────────
class TestTheBenchmarkReading:
    def test_no_artefact_is_none_on_record_and_names_the_command(self, tmp_path):
        r = benchmark_on_record(tmp_path / "missing.json")
        assert r.state == "none"
        line = card_line(r)
        assert br.WRITE_COMMAND in line
        assert not re.search(r"PF \d", line), "a 'none' line names no figure"

    @pytest.mark.parametrize("body, why", [
        ("{not json", "could not parse"),
        ("[1, 2]", "not an object"),
        (json.dumps({"mode": "single"}), "not a walk-forward"),
        (json.dumps({"mode": "portfolio_walk_forward", "data_source": "frozen_snapshot:x",
                     "folds_run": 6}), "no pooled block"),
        (json.dumps({"mode": "portfolio_walk_forward", "data_source": "bitget_real",
                     "folds_run": 6, "pooled": {k: 1 for k in br._POOLED_FIELDS}}),
         "not a frozen snapshot"),
    ])
    def test_a_file_that_is_there_and_cannot_answer_is_unreadable_not_none(self, tmp_path, body, why):
        p = tmp_path / "result.json"
        p.write_text(body)
        r = benchmark_on_record(p)
        assert r.state == "unreadable" and why in r.reason
        line = card_line(r)
        assert "could not be read" in line and 'not "no benchmark"' in line
        assert br.WRITE_COMMAND not in line, "unreadable is not an invitation to overwrite"

    def test_a_read_artefact_carries_its_universe_as_bases(self, tmp_path):
        r = benchmark_on_record(_plant(tmp_path, _artefact()))
        assert r.state == "read" and r.dataset_hash == "deadbeef"
        assert r.universe == ("ADA", "BTC")
        line = card_line(r)
        assert "recorded 2026-09-21 at abcdef0" in line and "PF 0.63" in line and "1/6 folds" in line

    def test_no_fold_ran_is_said_and_never_a_zero_percent(self):
        line = card_line(_reading(folds_run=0, mean_oos_return_pct=None, pooled_trades=0))
        assert "NO FOLD RAN" in line
        assert not re.search(r"[+-]?\d+\.\d+\s*%", line), line  # no return figure at all

    def test_a_benchmark_with_no_losing_trade_prints_no_ratio(self):
        line = card_line(_reading(pooled_pf=None, pooled_losses=0))
        assert "PF — (no losing trade)" in line

    def test_an_artefact_off_another_snapshot_is_refused_and_says_which(self, tmp_path):
        # The dataset was re-frozen after the artefact was recorded: a card
        # comparing live against it would name a benchmark that is not on
        # disk. The reason carries both hashes, because "regenerate it" over
        # a file that parses sends a reader to look for a parse error.
        r = benchmark_on_record(_plant(tmp_path, _artefact(), manifest_hash="0123456789abcdef"))
        assert r.state == "unreadable"
        assert "deadbeef" in r.reason and "0123456789ab" in r.reason and "regenerate" in r.reason
        assert "could not be read" in card_line(r) and br.WRITE_COMMAND not in card_line(r)

    def test_an_artefact_with_no_manifest_beside_it_cannot_be_pinned(self, tmp_path):
        # A pin nobody can read is not a pin that held: the artefact is not
        # read as the benchmark, and the sentence says why -- neither
        # "regenerate" (the artefact may be right) nor "no benchmark".
        r = benchmark_on_record(_plant(tmp_path, _artefact(), manifest_hash=None))
        assert r.state == "unreadable"
        assert "cannot be pinned" in r.reason and "manifest.json" in r.reason
        assert "regenerate" not in r.reason
        (tmp_path / "manifest.json").write_text("{not json")
        r2 = benchmark_on_record(tmp_path / "result.json")
        assert r2.state == "unreadable" and "cannot be pinned" in r2.reason

    @pytest.mark.parametrize("sym, base", [
        ("BTC/USDT:USDT", "BTC"), ("BTCUSDT", "BTC"), ("eth/usd", "ETH"),
        ("NATGAS/USDT:USDT", "NATGAS"), ("XAUUSDC", "XAU"), ("", ""), ("USDT", "USDT"),
    ])
    def test_symbol_base_reads_both_spellings(self, sym, base):
        assert symbol_base(sym) == base


# ── 1b. the pooled block the runner writes is the block the card reads ──
class TestThePooledBlock:
    """``pooled_stats`` is the ONE reading behind the printed report and the
    artefact; the card reads the artefact. Driven here because a block the
    guard only reads back from the committed file is a block whose
    arithmetic nothing checks."""

    def _trade(self, net):
        return NS(net_pnl_usd=net)

    def test_no_losing_trade_is_no_ratio_and_a_flat_is_neither_side(self):
        from bot.backtest.runner import pooled_stats
        s = pooled_stats([self._trade(x) for x in (4.0, 2.0, 0.0)])
        assert s["trades"] == 3 and s["wins"] == 2 and s["losses"] == 0 and s["flat"] == 1
        assert s["pf"] is None, "gross win over no gross loss is not a ratio"
        assert s["win_rate"] == pytest.approx(2 / 3)
        assert s["net_usd"] == pytest.approx(6.0) and s["mean_net_usd"] == pytest.approx(2.0)
        assert all(k in s for k in br._POOLED_FIELDS)

    def test_the_ratio_is_gross_win_over_gross_loss(self):
        from bot.backtest.runner import pooled_stats
        s = pooled_stats([self._trade(x) for x in (6.0, -2.0, 3.0, -1.0)])
        assert s["pf"] == pytest.approx(9.0 / 3.0)
        assert s["wins"] == 2 and s["losses"] == 2 and s["flat"] == 0
        assert s["gross_win_usd"] == pytest.approx(9.0) and s["gross_loss_usd"] == pytest.approx(3.0)

    def test_nothing_pooled_is_none_never_zero(self):
        from bot.backtest.runner import pooled_stats
        s = pooled_stats([])
        assert s["trades"] == 0 and s["win_rate"] is None and s["pf"] is None
        assert s["mean_net_usd"] is None

    def test_the_card_and_the_pooled_block_share_one_profit_factor(self):
        # Two copies of "PF over no loss is None" are two answers the day one
        # is edited; the runner CALLS the leaf's function, read off its AST,
        # and the card's name is that same object.
        import inspect

        from bot.backtest import runner
        assert parity._pf is br.profit_factor
        tree = ast.parse(inspect.getsource(runner.pooled_stats))
        calls = {ast.unparse(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
        assert "profit_factor" in calls, calls
        assert "gross_win / gross_loss" not in inspect.getsource(runner.pooled_stats)

    def test_the_code_sha_is_the_head_or_none(self):
        from bot.backtest.runner import _code_sha
        sha = _code_sha()
        assert sha is None or re.fullmatch(r"[0-9a-f]{40}", sha), sha


# ── 2. the committed artefact is the one the manifest describes ─────────
class TestTheCommittedArtefact:
    def test_it_is_on_record_read_and_pinned_to_the_manifest(self):
        r = benchmark_on_record()
        assert r.state == "read", r.reason
        assert r.dataset_hash == br.manifest_hash(), \
            "the artefact on record measured other data than the manifest names"
        assert r.code_sha and re.fullmatch(r"[0-9a-f]{7,40}", r.code_sha)
        assert r.recorded_at and r.recorded_at[:4].isdigit()

    def test_its_pooled_counts_close(self):
        d = json.loads(br.BENCHMARK_RESULT.read_text())
        p = d["pooled"]
        assert p["wins"] + p["losses"] + p["flat"] == p["trades"] == d["pooled_trades"]

    def test_the_document_names_the_command_that_writes_it(self):
        doc = (ROOT / "docs" / "FROZEN_BENCHMARK.md").read_text()
        assert br.WRITE_COMMAND in doc, "the 'none on record' sentence and the doc must agree"
        assert str(br.BENCHMARK_RESULT.relative_to(ROOT)) in doc


# ── 3. the card's benchmark line comes from the file ────────────────────
class TestTheCardReadsTheFile:
    def test_two_plantings_two_lines_each_the_readings_own(self):
        rows = _card_shaped_rows()
        a = _reading(mean_oos_return_pct=-9.99, pooled_pf=0.42)
        b = _reading(mean_oos_return_pct=+3.21, pooled_pf=1.77, profitable_folds=5)
        ra = parity.format_report(parity.parity_summary(rows, COMMISSION, benchmark=a))
        rb = parity.format_report(parity.parity_summary(rows, COMMISSION, benchmark=b))
        assert card_line(a) in ra and card_line(b) in rb
        assert "-9.99%" in ra and "+3.21%" in rb and "5/6 folds" in rb

    def test_none_on_record_reaches_the_card_as_the_command_and_the_verdict_as_no_comparison(self):
        s = parity.parity_summary(_card_shaped_rows(), COMMISSION,
                                  benchmark=benchmark_on_record(Path("/no/such/result.json")))
        report = parity.format_report(s)
        assert br.WRITE_COMMAND in report
        assert s["verdict"]["ballpark"] == "no_benchmark"
        assert "no benchmark on record to compare against" in report
        # the live-edge verdict does not need a benchmark, so it is still made
        assert s["verdict"]["edge"] in ("positive", "negative", "straddles")

    def test_an_unreadable_artefact_is_no_comparison_and_not_no_benchmark(self, tmp_path):
        p = tmp_path / "result.json"
        p.write_text("{oops")
        s = parity.parity_summary(_card_shaped_rows(), COMMISSION, benchmark=benchmark_on_record(p))
        assert s["verdict"]["ballpark"] == "benchmark_unreadable"
        assert 'not "no benchmark"' in s["verdict"]["ballpark_sentence"]

    def test_the_default_reading_is_the_one_on_record(self, monkeypatch):
        planted = _reading(mean_oos_return_pct=-7.77)
        monkeypatch.setattr(parity, "benchmark_on_record", lambda: planted)
        s = parity.parity_summary(_card_shaped_rows(), COMMISSION)
        assert s["benchmark"]["mean_oos_return_pct"] == -7.77


# ── 4. the verdict: live edge, with its instrument and its floor ─────────
class TestTheEdgeVerdict:
    def _edge(self, nets, **kw):
        rows = [_t(n) for n in nets]
        return parity.parity_verdict(rows, _reading(), **kw)

    def test_positive_when_the_whole_interval_clears_zero(self):
        v = self._edge([5.0 + (i % 3) * 0.1 for i in range(20)])
        assert v["edge"] == "positive" and "POSITIVE" in v["edge_sentence"]
        assert v["interval"][0] > 0

    def test_negative_when_the_whole_interval_is_below_zero(self):
        v = self._edge([-5.0 - (i % 3) * 0.1 for i in range(20)])
        assert v["edge"] == "negative" and "NEGATIVE" in v["edge_sentence"]

    def test_a_losing_mean_whose_interval_reaches_zero_is_not_a_verdict(self):
        # the arb verdict's own recorded corpus gap: a negative point estimate
        # is not "negative" when the interval straddles zero
        nets = [-1.0, 4.0, -3.0, 2.5, -2.0, 1.0, -4.0, 3.0, -1.5, 0.5, -2.5, 2.5]
        v = self._edge(nets)
        assert sum(nets) < 0
        assert v["edge"] == "straddles" and "straddling zero" in v["edge_sentence"]
        assert "too thin" not in v["edge_sentence"], "a full sample that straddles is not thin"

    def test_a_winning_mean_whose_interval_reaches_zero_is_not_a_verdict(self):
        # the mirror of the losing case, and the arb verdict's own recorded
        # corpus gap: every straddling fixture there had a POSITIVE mean, so
        # a mutant deciding POSITIVE off the point estimate changed no
        # verdict. This is the input that separates the interval from the mean.
        nets = [3.0, -2.0, 4.0, -3.0, 2.0, -1.0, 5.0, -4.0, 1.0, -2.0, 3.0, -1.0]
        v = self._edge(nets)
        assert sum(nets) > 0
        assert v["edge"] == "straddles" and "straddling zero" in v["edge_sentence"]
        assert "POSITIVE" not in v["edge_sentence"]

    def test_the_floor_is_at_the_bar_not_beside_it(self):
        bar = parity.MIN_VERDICT_TRADES
        assert self._edge([5.0] * (bar - 2) + [5.1])["edge"] == "thin"  # bar-1 rows, with variance
        assert self._edge([5.0] * (bar - 1))["edge"] == "thin"
        assert self._edge([5.0 + (i % 2) * 0.1 for i in range(bar)])["edge"] == "positive"

    def test_nothing_on_record_is_its_own_word(self):
        v = self._edge([])
        assert v["edge"] == "nothing" and "nothing to score" in v["edge_sentence"]

    def test_the_ticker_priced_share_qualifies_the_verdict_by_name(self):
        rows = [_t(5.0, fill="ticker_fallback")] * 15 + [_t(5.1, fill="exchange_fill")] * 5
        v = parity.parity_verdict(rows, _reading())
        assert v["inferred"] == 15
        assert "15 of 20 strategy exits are ticker-priced" in v["inferred_sentence"]
        assert "inferred" in v["inferred_sentence"]
        clean = parity.parity_verdict([_t(5.0 + (i % 2) * 0.1, fill="exchange_fill") for i in range(20)], _reading())
        assert "inferred_sentence" not in clean, "no caveat over a record with nothing to caveat"


class TestTheBallparkVerdict:
    def _rows(self, k_in, wins_in, k_out=0):
        rows = [_t(5.0 if i < wins_in else -5.0, sym="BTC/USDT:USDT") for i in range(k_in)]
        rows += [_t(5.0, sym="AAPL/USDT:USDT") for _ in range(k_out)]
        return rows

    def test_in_the_ballpark_when_the_live_interval_holds_the_benchmark_rate(self):
        v = parity.parity_verdict(self._rows(40, 20), _reading(pooled_win_rate=0.51))
        assert v["ballpark"] == "in" and v["in_universe"] == 40
        assert "in the ballpark on hit rate" in v["ballpark_sentence"]

    def test_below_when_the_whole_live_interval_is_under_it(self):
        v = parity.parity_verdict(self._rows(40, 4), _reading(pooled_win_rate=0.51))
        assert v["ballpark"] == "below" and "BELOW" in v["ballpark_sentence"]

    def test_above_when_the_whole_live_interval_is_over_it(self):
        v = parity.parity_verdict(self._rows(40, 38), _reading(pooled_win_rate=0.51))
        assert v["ballpark"] == "above" and "ABOVE" in v["ballpark_sentence"]

    def test_only_the_benchmarks_universe_is_compared_and_the_rest_is_named(self):
        v = parity.parity_verdict(self._rows(40, 20, k_out=30), _reading(pooled_win_rate=0.51))
        assert v["in_universe"] == 40 and v["outside"] == 30
        assert "30 of 70 live strategy exits are outside the benchmark's universe" in v["outside_sentence"]
        # every outside row is a win; a comparison over all 70 would have read ABOVE
        assert v["ballpark"] == "in"

    def test_a_thin_sample_whose_interval_holds_the_rate_is_in_whatever_its_point_estimate_says(self):
        # 4 of 10 is 40% against 51%: eleven points off, and the Wilson
        # interval on ten trades (17%..69%) still holds the benchmark's rate.
        # A point estimate with any tolerance a reader would accept reads
        # BELOW; the interval says the sample cannot tell.
        v = parity.parity_verdict(self._rows(10, 4), _reading(pooled_win_rate=0.51))
        assert v["ballpark"] == "in", v["ballpark_sentence"]
        lo, hi = v["live_wr_interval"]
        assert lo < 0.51 < hi

    def test_a_wide_sample_four_points_off_is_below_because_its_interval_excludes_the_rate(self):
        # 470 of 1000 is 47%: four points under 51%, and the interval on a
        # thousand trades (44%..50%) excludes it. The same four-point gap on
        # ten trades would have been "in"; the sample decides, not the gap.
        v = parity.parity_verdict(self._rows(1000, 470), _reading(pooled_win_rate=0.51))
        assert v["ballpark"] == "below", v["ballpark_sentence"]
        assert v["live_wr_interval"][1] < 0.51

    def test_the_universe_floor_is_at_the_bar(self):
        bar = parity.MIN_VERDICT_TRADES
        assert parity.parity_verdict(self._rows(bar - 1, bar - 1, k_out=50), _reading())["ballpark"] == "thin"
        assert parity.parity_verdict(self._rows(bar, bar // 2), _reading())["ballpark"] != "thin"

    def test_a_benchmark_that_measured_nothing_is_no_comparison(self):
        v = parity.parity_verdict(self._rows(40, 20), _reading(pooled_trades=0, pooled_win_rate=None))
        assert v["ballpark"] == "thin" and "measured no trades" in v["ballpark_sentence"]

    def test_the_pfs_are_printed_beside_the_rate_and_never_rounded_to_a_word(self):
        v = parity.parity_verdict(self._rows(40, 20), _reading(pooled_win_rate=0.51, pooled_pf=0.63))
        assert re.search(r"PF \S+ live vs 0\.63 benchmark", v["ballpark_sentence"])
        assert "no interval instrument" in v["ballpark_sentence"]

    def test_the_wilson_interval_is_the_readiness_modules_own(self):
        from bot.learning.readiness import wilson_lower_bound
        lo, hi = parity._wilson(20, 40)
        assert lo == wilson_lower_bound(20, 40)
        assert hi == pytest.approx(1.0 - wilson_lower_bound(20, 40))
        assert parity._wilson(0, 0) is None


# ── 5. execution aborts are kept apart ──────────────────────────────────
class TestTheAbortsAreKeptApart:
    def test_the_partition_closes(self):
        rows = _card_shaped_rows()
        parts = parity.partition(rows)
        assert sum(len(v) for v in parts.values()) == len(rows) == 268
        assert len(parts["aborts"]) == 11 and len(parts["non_fills"]) == 74
        assert len(parts["strategy"]) == 194 - 11

    def test_the_headline_is_over_strategy_exits_only(self):
        rows = _card_shaped_rows()
        s = parity.parity_summary(rows, COMMISSION, benchmark=_reading())
        assert s["trades"] == 183 and s["aborts"]["trades"] == 11
        assert s["aborts"]["net"] == pytest.approx(-(10 * 0.36 + 0.45), abs=0.01)
        strat = parity.strategy_exits(rows)
        assert s["net_pnl"] == pytest.approx(sum(t["pnl_usd"] for t in strat), abs=0.01)
        assert s["wins"] + s["losses"] + s["flat"] == s["trades"]
        # the aborts are eleven losses; folding them in moved the win rate
        folded = sum(1 for t in strat if t["pnl_usd"] > 0) / (len(strat) + 11)
        assert s["win_rate"] > folded

    def test_an_unscored_row_is_counted_nowhere_but_its_own_bucket(self):
        # The first mutation round's one survivor: with the None filter
        # dropped from `partition`, `_group` still skipped the row (it has its
        # own guard) and every net was still filtered -- what moved was the
        # ticker-priced COUNT and the abort COUNT, which read the row's
        # fill_source and close_reason without asking whether it was scored.
        # No fixture held an unscored row that could change a count.
        rows = [_t(5.0 + (i % 2) * 0.1, fill="exchange_fill") for i in range(20)]
        rows += [_t(None, fill="ticker_fallback"),
                 _t(None, reason="leverage_overshoot", fill="exchange_fill")]
        s = parity.parity_summary(rows, COMMISSION, benchmark=_reading())
        assert s["trades"] == 20 and s["unscored_pnl"] == 2
        assert s["inferred_fills"] == 0, "an unscored ticker-priced row is not a ticker-priced exit"
        assert s["aborts"]["trades"] == 0, "an unscored abort is not a scored abort"
        assert s["verdict"]["inferred"] == 0 and "inferred_sentence" not in s["verdict"]
        assert parity.aborts_line(s) == ""

    def test_the_card_and_the_digest_say_it_in_one_sentence(self):
        s = parity.parity_summary(_card_shaped_rows(), COMMISSION, benchmark=_reading())
        line = parity.aborts_line(s)
        assert line.startswith("Execution aborts kept apart: 11 (net $-4.05)")
        assert "leverage_overshoot 10" in line and "sl_placement_failed 1" in line
        assert "not strategy exits" in line
        assert line in parity.format_report(s)

    def test_no_aborts_no_sentence(self):
        s = parity.parity_summary([_t(5.0), _t(-3.0)], COMMISSION, benchmark=_reading())
        assert parity.aborts_line(s) == ""
        assert "Execution aborts" not in parity.format_report(s)

    def test_an_abort_is_a_filled_close_and_is_not_a_strategy_exit(self):
        for r in EXECUTION_ABORT_REASONS:
            assert is_filled_close(r, -0.36), "capital was at risk — the fees are real"
            assert is_execution_abort(r) and is_execution_abort(r.upper())
        assert not is_execution_abort("SL HIT (inferred)") and not is_execution_abort(None)
        assert not (EXECUTION_ABORT_REASONS & NON_FILL_CLOSE_REASONS), \
            "a row cannot be both never-filled and flattened after filling"

    def test_the_asset_class_bucket_is_over_the_same_rows(self):
        rows = _card_shaped_rows()
        from bot.core.market_scanner import category_for_symbol
        filled = parity.strategy_exits(rows)
        for tr in filled:
            tr["asset_class"] = category_for_symbol(tr.get("symbol", "") or "")
        bucket = parity._group(filled, "asset_class")
        assert sum(g["trades"] for g in bucket.values()) == 183


def executor_abort_literals(src: str) -> set[str]:
    """Every literal ``reason="…"`` the executor hands its OWN close_position —
    the derivation the vocabulary is pinned against."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "close_position"):
            for kw in node.keywords:
                if (kw.arg == "reason" and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)):
                    out.add(kw.value.value)
    return out


class TestTheAbortVocabularyIsDerived:
    def test_the_set_is_exactly_what_the_executor_writes(self):
        found = executor_abort_literals(EXECUTOR.read_text(encoding="utf-8"))
        assert found == set(EXECUTION_ABORT_REASONS), (
            f"executor writes {sorted(found)}, vocabulary holds "
            f"{sorted(EXECUTION_ABORT_REASONS)} — a flatten guard added or removed "
            f"moves BOTH, or the parity card counts it as a losing trade")

    def test_the_walk_sees_a_fourth_literal_and_ignores_a_variable(self):
        planted = (
            "class E:\n"
            "    async def a(self):\n"
            "        await self.close_position(t, reason=\"leverage_overshoot\")\n"
            "        await self.close_position(t, reason=\"new_guard\")\n"
            "        await self.close_position(t, reason=why)\n"
            "        audit(log, reason=\"not_a_close\")\n")
        assert executor_abort_literals(planted) == {"leverage_overshoot", "new_guard"}


# ── 6. a ratio over nothing is not a ratio ──────────────────────────────
class TestProfitFactor:
    def test_no_losing_trade_is_none_never_inf(self):
        assert parity._pf([]) is None and parity._pf([1.0, 2.0]) is None
        assert parity._pf([10.0, -5.0]) == 2.0
        assert parity._pf_str(None) == "—" and parity._pf_str(2.0) == "2.00"

    def test_the_card_prints_the_sample_beside_every_pf(self):
        report = parity.format_report(parity.parity_summary(_card_shaped_rows(), COMMISSION, benchmark=_reading()))
        manual = _row_line(report, "manual_nlp")
        assert re.search(r"PF —\s+14W/0L", manual), manual
        tp = _row_line(report, "TP HIT")
        assert re.search(r"PF \d+\.\d\d\s+8W/1L", tp), tp
        head = _row_line(report, "Live realized:")
        assert re.search(r"\(\d+W/\d+L\)", head), head

    def test_a_measured_flat_is_counted_only_when_present(self):
        assert parity._sample({"wins": 2, "losses": 1, "flat": 0}) == "2W/1L"
        assert parity._sample({"wins": 2, "losses": 1, "flat": 3}) == "2W/1L/3F"


# ── 7. one reason, one row ──────────────────────────────────────────────
class TestReasonBuckets:
    @pytest.mark.parametrize("raw, want", [
        ("SL HIT (inferred)", ("SL HIT", "inferred")),
        ("TP HIT (exchange)", ("TP HIT", "exchange")),
        ("SL HIT (exchange, combined TPSL)", ("SL HIT", "exchange")),
        ("TRAILING SL HIT (inferred)", ("TRAILING SL HIT", "inferred")),
        ("manual_nlp", ("manual_nlp", "")),
        ("MANUAL CLOSE", ("CLOSED (unknown)", "")),
        ("CLOSED (unknown)", ("CLOSED (unknown)", "")),
        (None, (parity.NO_REASON, "")),
        ("", (parity.NO_REASON, "")),
    ])
    def test_exit_reason_splits_reason_from_provenance(self, raw, want):
        assert parity.exit_reason({"close_reason": raw}) == want

    def test_the_two_spellings_of_a_stop_are_one_row_with_the_provenance_counted(self):
        s = parity.parity_summary(_card_shaped_rows(), COMMISSION, benchmark=_reading())
        rows = s["by_exit_reason"]
        assert "SL HIT (inferred)" not in rows
        assert rows["SL HIT"]["trades"] == 52 and rows["SL HIT"]["inferred"] == 51
        line = _row_line(parity.format_report(s), "SL HIT ")
        assert "52 tr" in line and "51 reason inferred" in line

    def test_a_missing_reason_is_not_an_unknown_close(self):
        s = parity.parity_summary([_t(1.0, reason=None), _t(2.0, reason="CLOSED (unknown)")],
                                  COMMISSION, benchmark=_reading())
        assert set(s["by_exit_reason"]) == {parity.NO_REASON, "CLOSED (unknown)"}


# ── 8. the digest carries what the card carries ─────────────────────────
def _digest_for(rows, monkeypatch, tmp_path, benchmark=None):
    from datetime import datetime as _dt

    import bot.core.proactive_monitor as pm
    now = _dt.now(pm.UTC)
    monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
    monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
    if benchmark is not None:
        monkeypatch.setattr(parity, "benchmark_on_record", lambda: benchmark)
    f = tmp_path / "closed.json"
    f.write_text(json.dumps(rows))
    eng = NS(live_executor=NS(_closed_trades_file=str(f)))
    alerts = pm.ProactiveMonitor(eng)._check_parity_digest()
    assert len(alerts) == 1
    return alerts[0].body


class TestTheDigest:
    def test_it_carries_the_ticker_priced_share_the_aborts_and_the_verdict(self, monkeypatch, tmp_path):
        body = _digest_for(_card_shaped_rows(), monkeypatch, tmp_path, benchmark=_reading())
        assert "Strategy exits: <b>183</b>" in body
        assert re.search(r"⚠ \d+ of 183 strategy exits are ticker-priced", body)
        assert "Execution aborts kept apart: 11" in body
        assert "Verdict: " in body and "on hit rate" in body

    def test_no_caveat_over_a_record_that_needs_none(self, monkeypatch, tmp_path):
        rows = [_t(5.0, fill="exchange_fill"), _t(-3.0, fill="exchange_fill")]
        body = _digest_for(rows, monkeypatch, tmp_path, benchmark=_reading())
        assert "ticker-priced" not in body and "Execution aborts" not in body

    def test_what_the_artefact_carries_is_escaped_at_the_digest_boundary(self, monkeypatch, tmp_path):
        # The ballpark sentence lists the benchmark's universe, which is read
        # off a FILE; Telegram's HTML parser refuses a whole message over one
        # stray tag and the send chokepoint's fallback then strips every tag,
        # so the digest arriving without its bold is the quiet failure.
        rows = [_t(5.0 + (i % 2) * 0.1, fill="exchange_fill") for i in range(20)]
        bench = _reading(universe=("<B>BTC", "ETH"))
        body = _digest_for(rows, monkeypatch, tmp_path, benchmark=bench)
        assert "&lt;B&gt;BTC" in body and "<B>BTC" not in body

    def test_two_decimals_on_the_ratio_the_full_card_prints_with_two(self, monkeypatch, tmp_path):
        # The pasted card's own 0.46x: 0.092% realized per round trip against
        # the live box's 0.200% modeled (COMMISSION_PCT=0.1). The digest reads
        # the rate off CONFIG, and this box may run a different one, so the
        # fee is DERIVED for the rate the digest will read rather than typed
        # for the live box's -- a fixture written for one deployment's rate
        # measures nothing on another.
        from bot.config import CONFIG
        modeled_round_trip = 2.0 * CONFIG.risk.commission_pct / 100.0
        fee = 0.46 * modeled_round_trip * 100.0        # on a notional of 100 per row
        rows = [_t(1.0, fees=fee, fill="exchange_fill") for _ in range(4)]
        body = _digest_for(rows, monkeypatch, tmp_path, benchmark=_reading())
        assert "<code>0.46×</code>" in body
        assert "0.5×" not in body, "the digest used to round the card's 0.46x to 0.5x"

    def test_a_pf_with_no_losing_trade_is_a_dash_on_the_digest_too(self, monkeypatch, tmp_path):
        rows = [_t(1.0, fill="exchange_fill") for _ in range(4)]
        body = _digest_for(rows, monkeypatch, tmp_path, benchmark=_reading())
        assert "PF <code>—</code>" in body


# ── 9. /parity's asset-class bucket is over the headline's rows ─────────
class TestTheParityCommand:
    def test_the_bucket_counts_strategy_exits_not_every_fill(self, tmp_path):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        f = tmp_path / "closed.json"
        f.write_text(json.dumps(_card_shaped_rows()))
        sent = []

        class Host:
            engine = NS(live_executor=NS(_closed_trades_file=str(f)))

            def _is_admin(self, update):
                return True

            def _lang(self, update):
                return "en"

            async def _send(self, update, text, **kw):
                sent.append(text)

        asyncio.run(EngineOpsCommands._cmd_parity(Host(), NS(), NS()))
        assert len(sent) == 1
        text = sent[0]
        assert "By asset class:" in text
        rows = [ln for ln in text.splitlines()
                if re.match(r"\s+(Crypto|Stock|Commodity|ETF|Metal|Forex|Index)\s", ln)]
        counted = sum(int(re.search(r"(\d+) tr", ln).group(1)) for ln in rows)
        assert counted == 183, f"asset-class rows sum to {counted}, headline is 183"


# ── 10. the web section: the words travel, the dollars do not ───────────
class TestTheWebSection:
    def test_verdict_words_and_benchmark_figures_travel_without_a_dollar(self, monkeypatch, tmp_path):
        from bot.core import web_reports as wr
        monkeypatch.setattr(parity, "benchmark_on_record", lambda: _reading())
        f = tmp_path / "closed.json"
        f.write_text(json.dumps(_card_shaped_rows()))
        sec = wr._parity_section(NS(live_executor=NS(_closed_trades_file=str(f))))
        assert sec["trades"] == 183 and sec["aborts"] == 11
        assert sec["verdict"]["edge"] in ("positive", "negative", "straddles")
        assert sec["verdict"]["ballpark"] in ("in", "below", "above")
        assert sec["benchmark"]["state"] == "read" and sec["benchmark"]["pooled_pf"] == 0.63
        assert "$" not in json.dumps(sec), "a public section carries words and ratios only"
        assert "pooled_net_usd" not in sec["benchmark"] and "net_pnl" not in sec
