"""A setup the engine re-offers is one call, not a new one every idea TTL.

The engine's pending idea lives PENDING_IDEA_TTL (five minutes). When it
lapses untaken, the tick scans again, reads the same closed hourly candles
(cached 900s) and the same cached thesis, and emits the same setup under a new
TradeIdea id. Each of those was published as a new call: a new outcome-ledger
row scored on its own, a new website row (so the stats and `/signals` counted
it again), a new copy push (the sweep dedups by signal key), a new public
thesis event and a new post on the public channel. One market move was counted
as many calls as the setup survived five-minute windows.

A row the engine publishes on a market and direction it already has a PENDING
call on is a re-offer of that call: not recorded, not sent, and the idea says
which call it re-offers (`repeat_of`), so the public surfaces say the call
once. The private alert is unchanged: the re-offer is a live idea with a live
button.
"""
from __future__ import annotations

import asyncio
import json
import types
from datetime import UTC, datetime

import pytest

import bot.core.agent_feed as af
import bot.core.signal_outcomes as so
from bot.core.engine import RuneClawEngine
from bot.utils import website_sync
from bot.utils.models import Direction, TradeIdea

NOW = datetime.now(UTC).isoformat()


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "signal_outcomes.json"
    monkeypatch.setattr(so, "ledger_path", lambda: p)
    return p


def _row(key, symbol="BTC/USDT", direction="LONG", **over):
    long = direction == "LONG"
    row = {"signal_key": key, "symbol": symbol, "direction": direction,
           "entry_price": 100.0, "stop_loss": 95.0 if long else 105.0,
           "take_profit": 110.0 if long else 90.0, "confidence": 0.7,
           "status": "NEW", "created_at": NOW}
    row.update(over)
    return row


def _entries(ledger):
    return json.loads(ledger.read_text())["signals"]


# ── the ledger ─────────────────────────────────────────────────────────────

class TestOneLiveCall:
    def test_a_second_engine_row_on_a_pending_call_is_a_re_offer(self, ledger):
        sent = []
        got = so.publish_signals([_row("A")], sync_fn=sent.append, producer=so.ENGINE)
        assert got == {}
        got = so.publish_signals([_row("B")], sync_fn=sent.append, producer=so.ENGINE)
        assert got == {"B": "A"}
        assert list(_entries(ledger)) == ["A"]
        assert [[r["signal_key"] for r in batch] for batch in sent] == [["A"]], (
            "the re-offer was pushed to the website as a call of its own")

    def test_the_new_call_says_who_published_it(self, ledger):
        so.record_published([_row("A")], producer=so.ENGINE)
        so.record_published([_row("S")])
        e = _entries(ledger)
        assert e["A"]["producer"] == so.ENGINE
        assert "producer" not in e["S"]

    @pytest.mark.parametrize("spelling", ["BTC/USDT:USDT", "BTC", "btcusdt", "btc/usdt"])
    def test_every_spelling_of_the_market_is_one_market(self, ledger, spelling):
        so.record_published([_row("A")], producer=so.ENGINE)
        got = so.record_published([_row("B", symbol=spelling)], producer=so.ENGINE)
        assert got.reoffers == {"B": "A"} and got.added == 0

    def test_the_opposite_direction_is_a_new_call(self, ledger):
        so.record_published([_row("A")], producer=so.ENGINE)
        got = so.record_published([_row("B", direction="SHORT")], producer=so.ENGINE)
        assert got.reoffers == {} and got.added == 1

    def test_another_market_is_a_new_call(self, ledger):
        so.record_published([_row("A")], producer=so.ENGINE)
        got = so.record_published([_row("B", symbol="ETH/USDT")], producer=so.ENGINE)
        assert got.reoffers == {} and got.added == 1

    def test_an_open_call_is_still_pending(self, ledger):
        so.record_published([_row("A")], producer=so.ENGINE)
        so.apply("A", so.Resolution(so.OPEN, "o", triggered_ms=1), 1)
        got = so.record_published([_row("B")], producer=so.ENGINE)
        assert got.reoffers == {"B": "A"}

    @pytest.mark.parametrize("word", list(so.TERMINAL))
    def test_once_the_call_has_resolved_the_setup_is_a_new_call(self, ledger, word):
        so.record_published([_row("A")], producer=so.ENGINE)
        so.apply("A", so.Resolution(word, "done", r=1.0 if word == so.TARGET else None,
                                    resolved_ms=1), 1)
        got = so.record_published([_row("B")], producer=so.ENGINE)
        assert got.reoffers == {} and got.added == 1

    def test_two_rows_in_one_batch_are_one_call(self, ledger):
        got = so.record_published([_row("A"), _row("B", symbol="BTC/USDT:USDT")],
                                  producer=so.ENGINE)
        assert got.added == 1 and got.reoffers == {"B": "A"}

    def test_a_row_with_no_producer_is_always_its_own_call(self, ledger):
        # The scan cards: each card's verify link points at the key it was
        # published under, so a scan row is recorded as sent.
        so.record_published([_row("A")], producer=so.ENGINE)
        got = so.record_published([_row("S1"), _row("S2")])
        assert got.reoffers == {} and got.added == 2

    def test_an_engine_row_is_not_a_re_offer_of_another_producers_call(self, ledger):
        so.record_published([_row("S")])
        so.record_published([_row("X")], producer="other")
        got = so.record_published([_row("A")], producer=so.ENGINE)
        assert got.reoffers == {} and got.added == 1

    def test_a_row_naming_no_direction_is_no_call_to_re_offer(self, ledger):
        # A row whose direction this ledger cannot read is not a call on a
        # side, so a second one is not a re-offer of the first.
        got = so.record_published([_row("A", direction="FLAT"), _row("B", direction="FLAT")],
                                  producer=so.ENGINE)
        assert got.reoffers == {} and got.added == 2

    @pytest.mark.parametrize("spelling", ["long", "Direction.LONG"])
    def test_every_spelling_of_the_direction_is_one_direction(self, ledger, spelling):
        so.record_published([_row("A")], producer=so.ENGINE)
        got = so.record_published([_row("B", direction=spelling)], producer=so.ENGINE)
        assert got.reoffers == {"B": "A"}

    def test_a_pending_row_that_names_no_market_is_no_call_to_re_offer(self, ledger):
        so.record_published([_row("A", symbol="B T C")], producer=so.ENGINE)
        got = so.record_published([_row("B")], producer=so.ENGINE)
        assert got.reoffers == {} and got.added == 1

    def test_an_unreadable_ledger_sends_every_row_as_it_always_did(self, ledger):
        so.record_published([_row("A")], producer=so.ENGINE)
        ledger.write_text("{not json")
        sent = []
        got = so.publish_signals([_row("B")], sync_fn=sent.append, producer=so.ENGINE)
        assert got == {} and [r["signal_key"] for r in sent[0]] == ["B"]
        assert ledger.read_text() == "{not json"

    def test_a_batch_of_nothing_but_re_offers_sends_nothing(self, ledger):
        so.publish_signals([_row("A")], sync_fn=lambda rows: None, producer=so.ENGINE)
        sent = []
        so.publish_signals([_row("B"), _row("C")], sync_fn=sent.append, producer=so.ENGINE)
        assert sent == []


# ── the engine's publish step, driven ──────────────────────────────────────

def _idea(asset="BTC/USDT", direction=Direction.LONG, entry=100.0):
    long = direction == Direction.LONG
    return TradeIdea(asset=asset, direction=direction, entry_price=entry,
                     stop_loss=entry * (0.95 if long else 1.05),
                     take_profit=entry * (1.10 if long else 0.90),
                     confidence=0.72, blended_confidence_raw=0.72, reasoning="r")


@pytest.fixture()
def engine(ledger, monkeypatch):
    sent, feed = [], []
    monkeypatch.setattr(website_sync, "sync_signals_in_background", sent.append)
    monkeypatch.setattr(af.FEED, "emit", lambda kind, **kw: feed.append(kw))
    stub = types.SimpleNamespace(_outcome_regime=lambda s: "")
    return stub, sent, feed


class TestTheEnginesPublishStep:
    def test_the_same_setup_next_scan_is_one_call(self, engine, ledger):
        stub, sent, feed = engine
        first, again = _idea(), _idea(entry=100.4)
        RuneClawEngine._publish_engine_ideas(stub, [first])
        RuneClawEngine._publish_engine_ideas(stub, [again])
        assert list(_entries(ledger)) == [first.id]
        assert [r["signal_key"] for batch in sent for r in batch] == [first.id]
        assert first.repeat_of is None and again.repeat_of == first.id
        assert len(feed) == 1, "the re-offer's thesis went to the public feed again"

    def test_a_reversal_is_a_new_call_with_its_own_thesis(self, engine, ledger):
        stub, sent, feed = engine
        RuneClawEngine._publish_engine_ideas(stub, [_idea()])
        short = _idea(direction=Direction.SHORT)
        RuneClawEngine._publish_engine_ideas(stub, [short])
        assert short.repeat_of is None and short.id in _entries(ledger)
        assert len(feed) == 2

    def test_a_call_among_re_offers_still_publishes(self, engine, ledger):
        stub, sent, feed = engine
        btc = _idea()
        RuneClawEngine._publish_engine_ideas(stub, [btc])
        again, eth = _idea(), _idea(asset="ETH/USDT")
        RuneClawEngine._publish_engine_ideas(stub, [again, eth])
        assert [r["signal_key"] for r in sent[-1]] == [eth.id]
        assert again.repeat_of == btc.id and eth.repeat_of is None
        assert len(feed) == 2


# ── the public channel ──────────────────────────────────────────────────────

def _forwarder():
    from bot.marketing.channel_forwarder import ChannelForwarder

    fwd = ChannelForwarder.__new__(ChannelForwarder)
    fwd._enabled, fwd._group_ids = True, {1}
    sent = []

    async def _post(msg):
        sent.append(msg)

    fwd._post = _post
    return fwd, sent


def test_the_public_channel_posts_a_call_once():
    fwd, sent = _forwarder()
    call, again = _idea(), _idea()
    again.repeat_of = call.id
    asyncio.run(fwd.post_signal(call))
    asyncio.run(fwd.post_signal(again))
    assert len(sent) == 1 and "RUNECLAW SIGNAL" in sent[0]
