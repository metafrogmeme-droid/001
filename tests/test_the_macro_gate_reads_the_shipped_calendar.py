"""The macro gate keeps its windows on the calendar the engine actually loads.

`RuneClawEngine` builds `MacroEventProvider(seed_path=config/macro_calendar.seed.json)`
and the risk engine asks it first. The seed names an event's kind `type` and spells
two of them `CPI_RELEASE` / `NFP_RELEASE`; the provider classified by `event_type`
in its own spelling. Every event fell through to LOW, whose blackout is the
scheduled instant alone. Driven on 2026-10-15 against the shipped seed, the FOMC
decision of 2026-10-28 read CLEAR at full size twenty minutes before and ten
minutes after, and blocked only at 18:00:00 exactly. The docs, the risk manifest
and the window table in `macro_events.py` all described windows the gate did not
keep.

These tests drive the real provider on the real seed, at offsets on both sides of
every window edge, so the fixture can tell a FOMC window from a CPI window and
either from LOW.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bot.core.macro_events import MacroEventProvider, MacroSeverity

SEED = Path(__file__).resolve().parents[1] / "config" / "macro_calendar.seed.json"


def _events():
    return json.loads(SEED.read_text(encoding="utf-8"))["events"]


def _at(event: dict) -> datetime:
    return datetime.fromisoformat(event["scheduled_utc"].replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def provider():
    mp = MacroEventProvider(seed_path=SEED)
    assert mp.event_count == len(_events()), "the seed did not load whole"
    return mp


def _isolated(kind: str) -> dict:
    """An event of this kind with no other seed event within six hours, so each
    reading below is about this event's window and nothing else's."""
    evs = _events()
    for e in evs:
        if e["type"] != kind:
            continue
        others = [abs((_at(o) - _at(e)).total_seconds()) for o in evs if o is not e]
        if min(others) > 6 * 3600:
            return e
    raise AssertionError(f"no isolated {kind} event in the seed")


def test_every_seed_event_is_classified_as_the_seed_declares_it(provider):
    """The seed carries its own `severity`; the provider's one reading must agree
    with it for every event. All three kinds are present, so a map that agrees on
    one kind and not another is caught."""
    kinds = {e["type"] for e in _events()}
    assert kinds == {"FOMC_DECISION", "CPI_RELEASE", "NFP_RELEASE"}
    loaded = provider._events
    assert len(loaded) == len(_events())
    for ev in loaded:
        declared = next(e["severity"] for e in _events()
                        if e["label"] == ev["label"] and e["scheduled_utc"] == ev["scheduled_utc"]
                        .strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert provider._severity_for(ev) is MacroSeverity(declared), ev["label"]


# (minutes from the event, risk_state, size multiplier). Each window edge is read
# on both sides.
FOMC = [
    (-360, "CLEAR", 1.0),
    (-181, "CLEAR", 1.0),
    (-180, "REDUCE", 0.5),
    (-61, "REDUCE", 0.5),
    (-60, "BLOCK_NEW_ENTRIES", 0.0),
    (-20, "BLOCK_NEW_ENTRIES", 0.0),
    (0, "BLOCK_NEW_ENTRIES", 0.0),
    (30, "BLOCK_NEW_ENTRIES", 0.0),
    (31, "REDUCE", 0.5),
    (60, "REDUCE", 0.5),
    (61, "CLEAR", 1.0),
]
HIGH = [
    (-121, "CLEAR", 1.0),
    (-120, "REDUCE", 0.5),
    (-31, "REDUCE", 0.5),
    (-30, "BLOCK_NEW_ENTRIES", 0.0),
    (15, "BLOCK_NEW_ENTRIES", 0.0),
    (16, "REDUCE", 0.5),
    (45, "REDUCE", 0.5),
    (46, "CLEAR", 1.0),
]


@pytest.mark.parametrize("kind,table", [
    ("FOMC_DECISION", FOMC), ("CPI_RELEASE", HIGH), ("NFP_RELEASE", HIGH)])
def test_each_kind_keeps_its_window(provider, kind, table):
    at = _at(_isolated(kind))
    got = []
    for minutes, _, _ in table:
        ctx = provider.get_context(now=at + timedelta(minutes=minutes))
        got.append((minutes, ctx.risk_state, ctx.size_multiplier))
    assert got == table


def test_the_harness_reads_the_shipped_calendar_at_a_quiet_instant(provider):
    """`tests/conftest.py` moves a no-time reading of the shipped calendar to a
    fixed instant, so tests that build the real engine do not depend on the
    date. That instant must be quiet, or every such test would be reading a
    window."""
    ctx = provider.get_context()
    assert (ctx.risk_state, ctx.size_multiplier, ctx.window, ctx.active_event) == (
        "CLEAR", 1.0, None, None), ctx.explanation
