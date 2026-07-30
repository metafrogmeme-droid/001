"""Three named symbols, and still no way to tell WHERE they hung.

#998 put the names on the operator surface:

    gave up on 3 of 85 symbols after 90s each
    (FET/USDT, RENDER/USDT, LINK/USDT)

Real progress — and then the next question had no answer. FET, RENDER and
LINK are liquid majors, so "thin market data" was already ruled out; what
remained was exchange fetch vs multi-timeframe vs the LLM call vs entry
refinement, and telling them apart meant reading logs.

The batch stage totals (#988) cannot answer it: they accumulate COMPLETED
stages, and a cancelled analysis completes none. So the stage a hung symbol
was sitting in was the one number nobody had.

Measured, not inferred. "Last completed stage + 1" would be a guess, and a
guess on a diagnosis surface is precisely what this codebase keeps removing.
An unmarked symbol reports its bare name — omit, never invent.
"""
from __future__ import annotations

from types import SimpleNamespace

from bot.core.engine import RuneClawEngine

from tests.source_scan import code_only


def _eng():
    e = SimpleNamespace(_stage_inflight={})
    e._stage_enter = RuneClawEngine._stage_enter.__get__(e)
    e._stage_exit = RuneClawEngine._stage_exit.__get__(e)
    return e


class TestTheMarker:
    def test_entering_a_stage_records_it_per_symbol(self):
        e = _eng()
        e._stage_enter("FET/USDT", "mtf")
        e._stage_enter("LINK/USDT", "fetch")
        assert e._stage_inflight["FET/USDT"] == "mtf"
        assert e._stage_inflight["LINK/USDT"] == "fetch"

    def test_a_later_stage_replaces_the_earlier_one(self):
        # The mark must track the CURRENT stage, not the first one entered.
        e = _eng()
        e._stage_enter("FET/USDT", "fetch")
        e._stage_enter("FET/USDT", "analyze")
        assert e._stage_inflight["FET/USDT"] == "analyze"

    def test_exit_clears_only_that_symbol(self):
        e = _eng()
        e._stage_enter("FET/USDT", "mtf")
        e._stage_enter("LINK/USDT", "fetch")
        e._stage_exit("FET/USDT")
        assert "FET/USDT" not in e._stage_inflight
        assert e._stage_inflight["LINK/USDT"] == "fetch"

    def test_exit_on_an_unmarked_symbol_is_harmless(self):
        e = _eng()
        e._stage_exit("NEVER/SEEN")          # must not raise

    def test_the_map_is_bounded(self):
        # One entry per in-flight analysis, but a cancelled task's finally
        # may not run before the next batch — so it must not grow forever.
        e = _eng()
        for i in range(600):
            e._stage_enter(f"S{i}/USDT", "fetch")
        assert len(e._stage_inflight) <= 500

    def test_instrumentation_never_raises(self):
        # Fail-open: a tick must never die for a diagnostic.
        broken = SimpleNamespace(_stage_inflight=None)
        broken._stage_enter = RuneClawEngine._stage_enter.__get__(broken)
        broken._stage_exit = RuneClawEngine._stage_exit.__get__(broken)
        broken._stage_enter("X/USDT", "fetch")   # recreates the dict
        broken._stage_exit("X/USDT")


class TestItReachesTheTimeoutRecord:
    SRC = code_only(open("bot/core/engine.py", encoding="utf-8").read())
    # Scoped to the batch runner: `except asyncio.TimeoutError:` appears four
    # times in this module and `finally:` more than that, so a whole-file
    # split lands in the wrong function. (It did on the first run of these
    # tests, which is the reason this is written down.)
    import inspect as _inspect
    BATCH = code_only(_inspect.getsource(
        RuneClawEngine._analyze_signals_batched))

    def test_every_stage_marks_its_entry(self):
        # Via the module-level guarded helper: _analyze_signal runs under
        # SimpleNamespace stubs in several suites, so the mark must not
        # require the caller to grow a method (it did on the first run, and
        # broke four of them).
        for stage in ("fetch", "mtf", "analyze", "refine"):
            assert f'_stage_enter_guarded(self, signal.symbol, "{stage}")' in self.SRC, (
                f"the {stage} stage must mark its entry or a hang there is "
                "invisible")

    def test_the_marks_do_not_require_the_caller_to_define_them(self):
        # The hazard itself, pinned: both the entry helper and the exit call
        # must be getattr-guarded.
        assert 'getattr(engine, "_stage_enter", None)' in self.SRC
        assert 'getattr(self, "_stage_exit", None)' in self.SRC

    def test_the_timeout_handler_reads_the_marker(self):
        assert "_stage_inflight" in self.BATCH
        assert '_timed_out.append(f"{_sym} ({_stg})" if _stg else _sym)' in self.BATCH, (
            "an unmarked symbol must report its bare name, never a guessed stage")

    def test_the_marker_is_cleared_after_the_handler_reads_it(self):
        # ORDER is the invariant: the timeout handler reads the marker, and
        # only then does the finally clear it. Clearing first would erase the
        # very fact being reported. Compared by position rather than by
        # slicing a window — code_only() blanks comments to whitespace, so a
        # fixed-size window lands in blanks (it did, on the first run).
        read_at = self.BATCH.find("_stage_inflight")
        clear_at = self.BATCH.find("_stage_exit")
        assert read_at != -1 and clear_at != -1
        assert read_at < clear_at, (
            "the marker is cleared before the timeout handler reads it")

    def test_the_map_resets_per_batch(self):
        # Same epoch discipline as the progress record: a cancelled batch's
        # leftovers must not be read as this batch's hang.
        assert "self._stage_inflight: dict[str, str] = {}" in self.SRC
