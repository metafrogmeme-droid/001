"""The learning nudge reads each decision once, reads only what it uses, and
does it off the scan lane's loop.

10 October, the operator's `/status`: "Slowest tick phase: analyze 274s peak of
300s (91%) · last run 273s", every cycle, with 37 stock and ETF symbols already
skipped. The engine's confidence nudge ran once per trade idea, synchronously
on the scan lane, and asked for the whole learning context: a full re-read and
re-validation of `decision_memory.jsonl` (a row per idea the risk gate rejects,
never rotated), and three more file reads (patterns, model agreement,
feedback) for figures it threw away. Measured: 0.6 s a call at 5,000 rows,
3.5 s at 20,000. While it read, every analysis in flight on the lane stood
still, and the stall was booked as the other symbols' fetch time.

Now the store parses each line of the file once across reads, the nudge asks
for the setup record alone, and it asks in a worker thread.

Driven: the real `LearningStore` over a real file, the real
`LearningOrchestrator`, and the engine's own `_learning_nudge`.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.learning.models import DecisionMemory
from bot.learning.orchestrator import LearningOrchestrator
from bot.learning.store import LearningStore
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only


def _full_read(store: LearningStore, symbol="", regime="", direction="", window=500, limit=10):
    """What similar-setup selection answered before: the whole file parsed
    (`get_decisions`, which still reads it all), then filtered."""
    rows = store.get_decisions(symbol=symbol, limit=window)
    return [r.model_dump() for r in rows
            if (not regime or r.market_regime == regime)
            and (not direction or r.direction == direction)
            and r.pnl_result is not None][-limit:]


def _dumps(rows) -> list:
    return [r.model_dump() for r in rows]


def _row(symbol, pnl, direction="LONG", regime="", decision="OUTCOME"):
    return DecisionMemory(symbol=symbol, direction=direction, market_regime=regime,
                          pnl_result=pnl, decision=decision)


def _outcome(store, symbol, pnl, direction="LONG", regime=""):
    store.record_decision(_row(symbol, pnl, direction, regime))


ASKS = [(sym, regime, side, window, limit)
        for sym in ("", "A/USDT", "B/USDT", "Z/USDT")
        for regime in ("", "TREND_UP")
        for side in ("", "LONG", "SHORT")
        for window, limit in ((500, 10), (5, 3), (500, 1))]


def _same(store):
    for sym, regime, side, window, limit in ASKS:
        got = store.similar_decisions(sym, regime, side, window=window, limit=limit)
        assert _dumps(got) == _full_read(store, sym, regime, side, window, limit), \
            (sym, regime, side, window, limit)


@pytest.fixture
def counted(monkeypatch):
    """Every JSON validation of a decision row, counted."""
    calls: list = []
    real = DecisionMemory.model_validate_json

    def _counting(cls, data, *a, **k):
        calls.append(1)
        return real(data, *a, **k)

    monkeypatch.setattr(DecisionMemory, "model_validate_json", classmethod(_counting))
    return calls


# ── the store ───────────────────────────────────────────────────────────────

def test_each_line_is_parsed_once_across_asks(tmp_path, counted):
    store = LearningStore(str(tmp_path))
    for i in range(300):
        store.record_decision(_row(("A/USDT", "B/USDT", "C/USDT")[i % 3], float(i % 7 - 3),
                                   decision="REJECTED" if i % 4 else "OUTCOME"))
    store.similar_decisions("A/USDT", "", "LONG")
    assert len(counted) == 300 + 10
    for sym in ("A/USDT", "B/USDT", "C/USDT") * 20:
        before = len(counted)
        got = store.similar_decisions(sym, "", "LONG")
        assert len(counted) - before == len(got), "the file was re-parsed on a re-ask"
    for _ in range(5):
        _outcome(store, "A/USDT", 1.0)
    before = len(counted)
    got = store.similar_decisions("A/USDT", "", "LONG")
    assert len(counted) - before == 5 + len(got), "only the appended lines are parsed"


def test_the_index_holds_no_parsed_rows(tmp_path):
    """110,529 parsed rows held 502 MB; the index keeps where a row sits."""
    store = LearningStore(str(tmp_path))
    for i in range(50):
        _outcome(store, "A/USDT", float(i))
    store.similar_decisions("A/USDT", "", "LONG")
    index = store._similar
    kept = list(index._all) + [k for ks in index._by_symbol.values() for k in ks]
    assert kept and all(type(k).__name__ == "_DecisionKey" for k in kept)


def test_the_index_answers_what_the_full_read_answered(tmp_path):
    store = LearningStore(str(tmp_path))
    path = tmp_path / "decision_memory.jsonl"
    for i in range(40):
        store.record_decision(_row("A/USDT" if i % 2 else "B/USDT",
                                   None if i % 5 == 0 else float(i - 20),
                                   direction="LONG" if i % 3 else "SHORT",
                                   regime="TREND_UP" if i % 4 == 0 else "RANGE"))
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n   \n{not json}\n")                      # a blank line and a corrupt one
        f.write(_row("A/USDT", -1.0).model_dump_json())      # a whole row, no newline yet
    _same(store)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n")                                        # the last line made whole
    _outcome(store, "B/USDT", 4.0, regime="TREND_UP")
    _same(store)
    # Replaced (another inode) with fewer rows.
    tmp = tmp_path / "rewrite.jsonl"
    tmp.write_text(_row("A/USDT", 2.0).model_dump_json() + "\n")
    os.replace(tmp, path)
    _same(store)
    # Rewritten in place, same inode, longer than before, a different start.
    with open(path, "w", encoding="utf-8") as f:
        for i in range(30):
            f.write(_row("B/USDT", float(i), direction="SHORT").model_dump_json() + "\n")
    _same(store)
    path.unlink()
    assert store.similar_decisions("B/USDT", "", "SHORT") == []
    assert store.similar_decisions("", "", "") == []


def test_a_file_replaced_or_cut_short_behind_the_same_first_line_is_read_again(tmp_path):
    """Each check on its own: a different file (inode) and a shorter one
    (size), both starting with the line the index already holds."""
    store = LearningStore(str(tmp_path))
    path = tmp_path / "decision_memory.jsonl"
    first = _row("A/USDT", 1.0).model_dump_json() + "\n"
    path.write_text(first + "".join(_row("A/USDT", float(i)).model_dump_json() + "\n"
                                    for i in range(2, 8)))
    _same(store)
    # Another file, the same first line, longer: only the inode tells.
    tmp = tmp_path / "next.jsonl"
    tmp.write_text(first + "".join(_row("A/USDT", -float(i), direction="SHORT").model_dump_json()
                                   + "\n" for i in range(20)))
    os.replace(tmp, path)
    _same(store)
    # The same file cut back to its first three lines: only the size tells.
    with open(path, "rb+") as f:
        keep = sum(len(ln) for ln in f.readlines()[:3])
        f.truncate(keep)
    assert len(store.similar_decisions("A/USDT", "", "")) == 3
    _same(store)


def test_a_line_still_being_written_is_read_once_it_is_whole(tmp_path):
    store = LearningStore(str(tmp_path))
    _outcome(store, "A/USDT", 1.0)
    line = _row("A/USDT", 2.0).model_dump_json()
    path = tmp_path / "decision_memory.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line[:20])
    assert [r.pnl_result for r in store.similar_decisions("A/USDT", "", "")] == [1.0]
    with open(path, "a", encoding="utf-8") as f:
        f.write(line[20:] + "\n")
    assert [r.pnl_result for r in store.similar_decisions("A/USDT", "", "")] == [1.0, 2.0]


def test_a_row_changed_by_its_reader_does_not_change_the_next_answer(tmp_path):
    store = LearningStore(str(tmp_path))
    _outcome(store, "A/USDT", -3.0)
    (row,) = store.similar_decisions("A/USDT", "", "")
    row.pnl_result = 999.0
    assert store.similar_decisions("A/USDT", "", "")[0].pnl_result == -3.0


# ── one reading of the setup record ─────────────────────────────────────────

def test_the_setup_record_reads_through_the_index(tmp_path, counted):
    orch = LearningOrchestrator(str(tmp_path))
    for i in range(200):
        if i % 2:      # a rejected idea: a row with no outcome
            orch.store.record_decision(_row("A/USDT", None, decision="REJECTED"))
        else:
            orch.record_closed_outcome(symbol="A/USDT", direction="LONG",
                                       pnl_result=float(i % 5 - 2))
    orch.setup_record(symbol="A/USDT", direction="LONG")
    before = len(counted)
    n, _avg = orch.setup_record(symbol="A/USDT", direction="LONG")
    assert n == 10 and len(counted) - before == n, "the record re-read the file"


def test_the_setup_record_counts_the_side_s_completed_setups(tmp_path):
    """None with no setups, never 0.0: no record is not a break-even one."""
    orch = LearningOrchestrator(str(tmp_path))
    assert orch.setup_record(symbol="A/USDT", direction="LONG") == (0, None)
    for pnl in (-4.0, -2.0, 3.0):
        orch.record_closed_outcome(symbol="A/USDT", direction="LONG", pnl_result=pnl)
    orch.record_closed_outcome(symbol="A/USDT", direction="SHORT", pnl_result=50.0)
    orch.store.record_decision(_row("A/USDT", None, decision="REJECTED"))
    assert orch.setup_record(symbol="A/USDT", direction="LONG") == (3, pytest.approx(-1.0))


def test_the_context_nothing_read_is_gone():
    """Its one caller was the nudge; its pattern lookup had no other."""
    from bot.learning.patterns import PatternLearner
    assert not hasattr(LearningOrchestrator, "get_learning_context")
    assert not hasattr(PatternLearner, "get_relevant_patterns")


# ── the engine's nudge ──────────────────────────────────────────────────────

def _engine(learning):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.learning = learning
    eng.risk = NS(_current_regime="")
    return eng


def _idea(asset="A/USDT", confidence=0.70):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=106.0, confidence=confidence,
                     reasoning="nudge", source="scan", timestamp=datetime.now(UTC))


def test_the_nudge_moves_confidence_as_before_and_reads_only_the_record(tmp_path):
    assert CONFIG.learning.adaptive_confidence_enabled
    orch = LearningOrchestrator(str(tmp_path))
    unused: list = []
    for part, name in ((orch.patterns, "get_relevant_patterns"),
                       (orch.models, "get_accuracy_summary"),
                       (orch.feedback, "get_feedback_summary")):
        setattr(part, name, lambda *a, _n=name, **k: unused.append(_n))
    need = CONFIG.learning.adaptive_confidence_min_samples
    for _ in range(need):
        orch.record_closed_outcome(symbol="LOSE/USDT", direction="LONG", pnl_result=-5.0)
        orch.record_closed_outcome(symbol="WIN/USDT", direction="LONG", pnl_result=+5.0)
    for _ in range(need - 1):
        orch.record_closed_outcome(symbol="FEW/USDT", direction="LONG", pnl_result=-5.0)
    eng = _engine(orch)
    lose, win, few = _idea("LOSE/USDT"), _idea("WIN/USDT"), _idea("FEW/USDT")
    for idea in (lose, win, few):
        asyncio.run(eng._learning_nudge(idea))
    assert lose.confidence == pytest.approx(0.70 - CONFIG.learning.adaptive_confidence_max_penalty)
    assert win.confidence == pytest.approx(0.70 + CONFIG.learning.adaptive_confidence_max_boost)
    assert few.confidence == 0.70, "a setup under the sample floor was nudged"
    assert unused == [], f"the nudge read what it does not use: {unused}"


def test_the_nudge_does_not_stall_the_loop(tmp_path):
    """A read that takes 0.3 s: the loop keeps running other analyses."""
    def _slow(**kw):
        time.sleep(0.3)
        return 0, None

    eng = _engine(NS(setup_record=_slow))

    async def main():
        ticks = 0
        done = asyncio.Event()

        async def other_analysis():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(other_analysis())
        await asyncio.sleep(0)
        await eng._learning_nudge(_idea())
        done.set()
        await task
        return ticks

    assert asyncio.run(main()) >= 10, "the lane stood still while the nudge read"


def test_the_analysis_awaits_the_nudge():
    """`_analyze_signal` is 700 lines no unit test drives; the call is the
    shape a test cannot reach otherwise."""
    src = code_only(inspect.getsource(RuneClawEngine._analyze_signal))
    assert src.count("await self._learning_nudge(idea)") == 1
    assert "get_learning_context" not in src
