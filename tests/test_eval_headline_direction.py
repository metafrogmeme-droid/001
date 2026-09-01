"""The yardstick must read the format the model actually writes.

runeclaw_eval.py extracted `direction` only from a LABELLED line —
"Direction: LONG", "Setup: SHORT". v12 writes neither. It puts the call in
the headline, "TRADE IDEA: BTC/USDT LONG", so the parser found no direction,
and a correct call was graded as a miss.

That is the yardstick being wrong about the model, not the model being wrong
about the trade — the same drift the "Risk Check:" verdict comment in that
file already warns about, repeated one field along.

The red herring is prose. "We would take a long position here" must NOT
yield LONG: an explanation is not a call, and a parser that harvests one
from a sentence invents a verdict to grade.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "ollama" / "runeclaw_eval.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("runeclaw_eval", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHeadlineDirection:
    def test_headline_with_pair(self, ev):
        got = ev.extract_json("TRADE IDEA: BTC/USDT LONG\nEntry: 59500")
        assert got["direction"] == "LONG"

    def test_headline_with_ticket_id(self, ev):
        got = ev.extract_json("TRADE IDEA [TI-4412] SOL/USDT SHORT\nEntry: 180")
        assert got["direction"] == "SHORT"

    def test_headline_without_space(self, ev):
        got = ev.extract_json("TradeIdea: ETH/USDT LONG\nEntry: 2400")
        assert got["direction"] == "LONG"

    def test_direction_on_the_line_below(self, ev):
        got = ev.extract_json("TRADE IDEA\nBTC/USDT SHORT\nEntry: 59500")
        assert got["direction"] == "SHORT"

    def test_a_labelled_direction_still_wins(self, ev):
        # The labelled pattern runs first; the headline is only a fallback.
        got = ev.extract_json(
            "TRADE IDEA: BTC/USDT LONG\nDirection: SHORT\nEntry: 59500")
        assert got["direction"] == "SHORT"


class TestWhatMustNotBeRead:
    def test_lowercase_prose_is_not_a_call(self, ev):
        got = ev.extract_json(
            "TRADE IDEA: BTC/USDT\nWe would take a long position here.\n"
            "Entry: 59500") or {}
        assert "direction" not in got, \
            "an explanation was graded as a directional call"

    def test_no_headline_means_no_direction(self, ev):
        got = ev.extract_json("Market looks LONG overdue for a pullback.") or {}
        assert "direction" not in got

    def test_absent_direction_stays_absent(self, ev):
        got = ev.extract_json("Entry: 59500\nStop Loss: 58500") or {}
        assert "direction" not in got, \
            "unreadable must not become a guess — the eval grades on this"
