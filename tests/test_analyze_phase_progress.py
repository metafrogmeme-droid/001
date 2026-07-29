"""A cancelled phase must be able to say what it was doing.

Production, 2026-07-29, after both attempted fixes had shipped and deployed:

    - Tick phase timed out: analyze (exceeded its 300s, x5)

That is the whole account. It says the phase died. It does not say whether 4
of 200 signals had finished or 194 had — and those are OPPOSITE diagnoses
calling for opposite fixes:

    barely started -> something is stuck; find it and drop it
    nearly done    -> the budget is too small for the universe; resize

Without that number the next step is a guess, which is exactly how two fixes
(#970's per-analysis cap, #971's chain bounding) shipped against a cause that
was never confirmed — and the phase is still timing out.

So the batch records progress as it goes, the timeout handler reads it after
the batch's own frames are gone, and /status shows it.
"""

import time
from types import SimpleNamespace

import pytest

from bot.formatters.rich_cards import render_status_card
from tests.source_scan import code_only

ENGINE = code_only(open("bot/core/engine.py", encoding="utf-8").read())


# ── the batch records how far it got ──────────────────────────────────────

def test_progress_is_initialised_with_the_batch_size():
    block = ENGINE[ENGINE.index("async def _analyze_signals_batched"):
                   ENGINE.index("async def _analyze_signal(self")]
    assert '"of": len(signals)' in block
    assert '"done": 0' in block
    assert '"started": time.monotonic()' in block


def test_every_outcome_counts_toward_progress():
    """The question is "how far did the batch get", not "how many
    succeeded" — so it increments in `finally`, past every return path."""
    block = ENGINE[ENGINE.index("async def _analyze_signals_batched"):
                   ENGINE.index("async def _analyze_signal(self")]
    assert "finally:" in block
    tail = block[block.index("finally:"):]
    assert '_p["done"] += 1' in tail


def test_the_counter_cannot_break_the_batch():
    block = ENGINE[ENGINE.index("async def _analyze_signals_batched"):
                   ENGINE.index("async def _analyze_signal(self")]
    tail = block[block.index("finally:"):]
    assert "except Exception:" in tail, (
        "a counter that can raise inside `finally` would replace the "
        "analysis's own outcome with its own failure")


def test_it_lives_on_the_engine_not_the_batch_frame():
    """wait_for cancels the gather; the batch's locals are gone by the time
    the timeout handler runs. The record has to outlive them."""
    block = ENGINE[ENGINE.index("async def _analyze_signals_batched"):
                   ENGINE.index("async def _analyze_signal(self")]
    assert "self._analyze_progress = {" in block


# ── the timeout handler reports it ────────────────────────────────────────

def _phase_block() -> str:
    b = ENGINE[ENGINE.index("async def _phase(self, coro"):
               ENGINE.index("def _record_phase_duration")]
    return b[b.index("except asyncio.TimeoutError:"):]


def test_the_timeout_line_reports_the_share_and_the_elapsed():
    b = _phase_block()
    assert "finished {_done} of {_of} signals" in b
    assert "{_done / _of:.0%}" in b, "a share, not just a count"
    assert "_elapsed" in b


def test_it_projects_what_the_batch_would_have_needed():
    """The single most actionable number: at the rate it was going, how long
    would the whole universe have taken? That is what says whether the cap or
    the universe is the wrong size."""
    b = _phase_block()
    assert "_rate" in b and "_of / _rate" in b


def test_no_progress_means_no_claim():
    b = _phase_block()
    assert '_detail = ""' in b
    assert 'if isinstance(_prog, dict) and _prog.get("of")' in b, (
        "an absent record must produce no sentence rather than a zero")


def test_only_the_analyze_phase_is_annotated():
    """positions and scan do not run this batch; attaching its progress to
    them would be a number from the wrong place."""
    b = _phase_block()
    assert 'if what == "analyze" else None' in b


def test_the_record_travels_with_the_timeout():
    b = _phase_block()
    assert '"progress": (dict(_prog) if isinstance(_prog, dict) else None)' in b, (
        "a copy, so a later batch mutating the live dict cannot rewrite what "
        "the last failure reported")


# ── it reaches the operator ───────────────────────────────────────────────

def _card(**kw):
    base = dict(mode="LIVE", active=True, equity=883.18, open_positions=0,
                daily_pnl=0.5, drawdown=0.0, max_drawdown=7.0,
                market_bias="caution")
    base.update(kw)
    return render_status_card(**base)


def test_status_shows_how_far_it_got():
    out = _card(phase_timeout={"phase": "analyze", "cap_s": 300.0, "count": 5,
                               "progress": {"of": 200, "done": 41,
                                            "started": 0.0}})
    line = [l for l in out.split("\n") if "↳" in l]
    assert line, "the progress line is missing from /status"
    assert "41/200" in line[0]


def test_status_omits_the_line_when_there_is_no_record():
    out = _card(phase_timeout={"phase": "analyze", "cap_s": 300.0, "count": 5})
    assert "↳" not in out, "absent is not zero — say nothing"


def test_status_omits_it_for_a_record_without_a_size():
    out = _card(phase_timeout={"phase": "scan", "cap_s": 300.0, "count": 1,
                               "progress": {"done": 0}})
    assert "↳" not in out


def test_the_label_is_translated():
    from bot.utils.i18n import t
    for lang in ("en", "zh"):
        assert t("val_signals_done", lang) != "val_signals_done"


# ── the property, exercised ───────────────────────────────────────────────

def test_a_cancelled_batch_still_knows_its_count():
    """Driven directly: a counter incremented in `finally` survives the
    cancellation that tears the gather down."""
    import asyncio

    async def _run():
        prog = {"of": 6, "done": 0, "started": time.monotonic()}

        async def _one(delay):
            try:
                await asyncio.sleep(delay)
            finally:
                prog["done"] += 1

        try:
            await asyncio.wait_for(
                asyncio.gather(*[_one(d) for d in (0.01, 0.01, 0.02, 5, 5, 5)]),
                timeout=0.2)
        except asyncio.TimeoutError:
            pass
        return prog

    prog = asyncio.run(_run())
    assert prog["done"] == 6, (
        "cancellation runs `finally` in every task, so the count is complete "
        "even though only three finished their work")
    assert prog["of"] == 6


def test_the_engine_declares_the_attribute_up_front():
    assert "self._analyze_progress: dict | None = None" in ENGINE, (
        "an attribute created only inside the batch would be missing on an "
        "engine whose first analyze phase has not started")
    _ = SimpleNamespace, pytest


# ── a stale batch must not write into a live record ───────────────────────
#
# Production, minutes after this shipped:
#
#     ↳ 47/40 signals analysed before it was cancelled
#
# 47 of 40. The slot is engine-level and a CANCELLED batch keeps unwinding
# after _phase has returned — its tasks' `finally` blocks fire while the next
# tick has already installed a fresh record, and they incremented THAT one.
#
# An instrument reporting a number larger than its own denominator is not
# reporting anything, and this one was being read to diagnose a live trading
# fault. Worse than no instrument: the first reading was used to argue about
# where the phase was dying.

def test_the_record_is_tagged_with_its_batch():
    block = ENGINE[ENGINE.index("async def _analyze_signals_batched"):
                   ENGINE.index("async def _analyze_signal(self")]
    assert "_analyze_batch_seq" in block
    assert '"seq": _seq' in block


def test_only_the_owning_batch_increments():
    block = ENGINE[ENGINE.index("async def _analyze_signals_batched"):
                   ENGINE.index("async def _analyze_signal(self")]
    tail = block[block.index("finally:"):]
    assert '_p.get("seq") == _seq' in tail, (
        "without the check, a cancelled batch's unwinding tasks corrupt the "
        "next batch's count")


def test_the_sequence_starts_defined():
    assert "self._analyze_batch_seq: int = 0" in ENGINE


def test_a_stale_batch_cannot_corrupt_the_live_count():
    """The property, driven directly rather than asserted."""
    class _E:
        def __init__(self):
            self._analyze_progress = None
            self._analyze_batch_seq = 0

        def start(self, n):
            self._analyze_batch_seq += 1
            seq = self._analyze_batch_seq
            self._analyze_progress = {"of": n, "done": 0, "started": 0.0,
                                      "seq": seq}
            return seq

        def finished_one(self, seq):
            p = self._analyze_progress
            if p is not None and p.get("seq") == seq:
                p["done"] += 1

    e = _E()
    old = e.start(70)
    for _ in range(9):
        e.finished_one(old)          # nine complete before the cancellation
    new = e.start(40)                # next tick installs a fresh record
    for _ in range(38):
        e.finished_one(old)          # the cancelled batch keeps unwinding
    for _ in range(7):
        e.finished_one(new)          # and the new batch makes real progress

    assert e._analyze_progress["done"] == 7, "only the owning batch counts"
    assert e._analyze_progress["of"] == 40
    assert e._analyze_progress["done"] <= e._analyze_progress["of"], (
        "done can never exceed of — that is what 47/40 meant")
