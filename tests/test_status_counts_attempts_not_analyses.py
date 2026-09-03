"""'85/85 signals analysed before it was cancelled' -- on a batch where four gave up.

The operator's /status at 00:34 UTC, 2026-09-03:

    Tick phase timed out: analyze (exceeded its 300s, x33)
      ↳ 85/85 signals analysed before it was cancelled

Half an hour earlier the same engine had reported giving up on four of those
85 after 90 seconds each. Both were true, because `done` counts ATTEMPTS: the
batch's `finally` increments it for a symbol that timed out exactly as it
does for one that finished. "Analysed" was the label's claim, not the
counter's. The label says "attempted" now, and the record counts the
give-ups beside it so the card can say both -- a sum over a set that includes
unreadable rows is a partial total printed as whole, and this was that shape
with a count.
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from bot.formatters.rich_cards import _gave_up_note, render_status_card


def _card(progress):
    return render_status_card(
        mode="LIVE", active=True, equity=643.09, open_positions=1, daily_pnl=0.0,
        drawdown=3.8, max_drawdown=7.0, market_bias="Normal",
        phase_timeout={"phase": "analyze", "cap_s": 300.0, "count": 33,
                       "progress": progress},
    )


def _progress_line(out: str) -> str:
    return next(line for line in out.splitlines() if "↳" in line)


def test_the_operators_card_now_says_attempted_and_how_many_gave_up():
    line = _progress_line(_card({"of": 85, "done": 85, "gave_up": 4}))
    assert "85/85" in line
    assert "attempted" in line
    assert re.search(r"\b4 of them gave up", line), line
    assert "signals analysed" not in line, "the old claim must be gone from this line"


def test_a_clean_batch_says_attempted_and_nothing_about_giving_up():
    line = _progress_line(_card({"of": 40, "done": 20, "gave_up": 0}))
    assert "20/40" in line and "attempted" in line
    assert "gave up" not in line


def test_an_older_record_without_the_field_claims_nothing_about_it():
    """Absent is not zero: no `gave_up` key means we do not know."""
    line = _progress_line(_card({"of": 40, "done": 20}))
    assert "gave up" not in line
    assert _gave_up_note({"of": 40, "done": 20}) == ""
    assert _gave_up_note(None) == ""
    assert _gave_up_note({"gave_up": "four"}) == ""


def test_the_note_renders_in_both_languages():
    for lang in ("en", "zh"):
        note = _gave_up_note({"gave_up": 2}, lang)
        assert note and "2" in note
        assert "val_gave_up" not in note, "untranslated key leaked"


# ── the counter itself ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_symbol_that_times_out_is_counted_as_attempted_and_as_given_up():
    """Drive the real batch method on a bare host: one symbol finishes, one
    sleeps past the per-symbol cap. `done` must say 2 (both attempted) and
    `gave_up` must say 1."""
    import asyncio
    import dataclasses
    from types import SimpleNamespace

    from bot.config import CONFIG
    from bot.core import engine as eng_mod
    from bot.core.engine import RuneClawEngine

    async def _analyze_signal(sig, *a, **k):
        if sig.symbol == "SLOW/USDT:USDT":
            await asyncio.sleep(5)
        return None

    host = SimpleNamespace(
        _analyze_signal=_analyze_signal,
        _symbol_cooldowns={},
        _record_analyze_throughput=lambda *a, **k: None,
    )
    host._analyze_signals_batched = RuneClawEngine._analyze_signals_batched.__get__(host)
    signals = [SimpleNamespace(symbol="FAST/USDT:USDT"),
               SimpleNamespace(symbol="SLOW/USDT:USDT")]
    with patch.object(eng_mod, "CONFIG",
                      dataclasses.replace(CONFIG, analysis_timeout_sec=0.05)):
        await host._analyze_signals_batched(signals)

    prog = host._analyze_progress
    assert prog["of"] == 2
    assert prog["done"] == 2, "both were ATTEMPTED -- that is what done counts"
    assert prog["gave_up"] == 1, "exactly one gave up at the per-symbol cap"


@pytest.mark.asyncio
async def test_a_batch_where_nothing_times_out_reports_zero_give_ups():
    import dataclasses
    from types import SimpleNamespace

    from bot.config import CONFIG
    from bot.core import engine as eng_mod
    from bot.core.engine import RuneClawEngine

    async def _analyze_signal(sig, *a, **k):
        return None

    host = SimpleNamespace(_analyze_signal=_analyze_signal, _symbol_cooldowns={},
                           _record_analyze_throughput=lambda *a, **k: None)
    host._analyze_signals_batched = RuneClawEngine._analyze_signals_batched.__get__(host)
    with patch.object(eng_mod, "CONFIG",
                      dataclasses.replace(CONFIG, analysis_timeout_sec=1.0)):
        await host._analyze_signals_batched([SimpleNamespace(symbol="A/USDT:USDT")])
    assert host._analyze_progress["gave_up"] == 0
    assert host._analyze_progress["done"] == 1
