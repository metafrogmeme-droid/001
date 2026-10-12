"""The bot sends the website what the fees leave of each idea's R:R.

The Telegram cards say "6.47 (5.74 after fees)" (`trade_costs.rr_words`).
The website printed the price ratio alone on every card, and it cannot work
the net figure out for itself: the fee table is the bot's, and the order type
is not on every record. So each payload a page renders an idea from carries
`trade_costs.rr_fields`: ``net_rr``, and ``rr_fee_losing`` to say which
absence a null ``net_rr`` is. The page side is
`app/test/an_idea_card_says_what_the_fees_leave_of_its_ratio.test.js`.

The payloads: the web trade card (`_idea_payload`), the chat's setup hint
(`_setup_from_new_idea`, which also carries the idea's order type now: its
"Trade this" re-proposed every setup as a limit), the engine's signal-stream
row (`build_signal_payload`), the scan's entry cards and their stream rows,
and the sealed flight record (`decision_idea_payload`).
"""
from __future__ import annotations

import types

import pytest

from bot.core import trade_costs as tc
from bot.utils.models import Direction, TradeIdea

#: The TIA long of 12 October.
TIA = dict(id="T1", asset="TIA/USDT", direction=Direction.LONG,
           entry_price=0.58820, stop_loss=0.58164, take_profit=0.63065,
           confidence=0.71, reasoning="r", strategy_type="swing")


def _idea(**kw) -> TradeIdea:
    return TradeIdea(**dict(TIA, **kw))


def _net(order_type: str) -> float:
    costed = tc.net_reward_risk(0.58820, 0.58164, 0.63065, order_type=order_type)
    assert costed is not None and costed.net is not None
    return round(costed.net, 2)


# ── the fields ────────────────────────────────────────────────────────────


def test_the_fields_name_each_of_the_three_states():
    assert tc.rr_fields(tc.net_reward_risk(0.58820, 0.58164, 0.63065, order_type="market")) == {
        "net_rr": 5.74, "rr_fee_losing": False}
    assert tc.rr_fields(tc.net_reward_risk(0.58820, 0.58164, 0.58850)) == {
        "net_rr": None, "rr_fee_losing": True}
    assert tc.rr_fields(None) == {"net_rr": None, "rr_fee_losing": None}


def test_an_idea_is_priced_at_its_own_order_type():
    market = tc.idea_rr_fields(_idea(order_type="market"))
    limit = tc.idea_rr_fields(_idea(order_type="limit"))
    assert market["net_rr"] == _net("market") == 5.74
    assert limit["net_rr"] == _net("limit")
    assert limit["net_rr"] > market["net_rr"], "a maker entry pays less than a taker one"


# ── the web trade card and the chat's setup hint ─────────────────────────


@pytest.mark.parametrize("order_type", ["market", "limit"])
def test_the_web_trade_card_carries_it(monkeypatch, order_type):
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_trade_mode", lambda *a: ("PAPER", False, ""))
    p = ug._idea_payload(None, None, "1", _idea(order_type=order_type), None)
    assert (p["rr"], p["net_rr"], p["rr_fee_losing"]) == (6.47, _net(order_type), False)
    assert p["order_type"] == order_type


@pytest.mark.parametrize("order_type", ["market", "limit"])
def test_the_chat_setup_hint_carries_it_and_its_order_type(order_type):
    from bot.web.user_gateway import _setup_from_new_idea
    engine = types.SimpleNamespace(_pending_ideas={"T1": _idea(order_type=order_type)})
    s = _setup_from_new_idea(engine, set())
    assert s is not None
    assert (s["rr"], s["net_rr"], s["rr_fee_losing"]) == (6.47, _net(order_type), False)
    assert s["order_type"] == order_type


# ── the public signal stream ─────────────────────────────────────────────


def test_the_engine_signal_row_carries_it_at_the_ideas_order_type():
    from bot.utils.website_sync import build_signal_payload
    row = build_signal_payload("k", _idea(order_type="market"))
    assert (row["net_rr"], row["rr_fee_losing"]) == (5.74, False)
    # A dict-shaped idea is read the same way.
    as_dict = dict(TIA, direction="LONG", order_type="limit")
    assert build_signal_payload("k", as_dict)["net_rr"] == _net("limit")


def test_a_fee_losing_or_unreadable_row_says_which():
    from bot.utils.website_sync import build_signal_payload
    losing = build_signal_payload("k", dict(TIA, direction="LONG", take_profit=0.58850))
    assert (losing["net_rr"], losing["rr_fee_losing"]) == (None, True)
    unread = build_signal_payload("k", dict(TIA, direction="LONG", stop_loss=None))
    assert (unread["net_rr"], unread["rr_fee_losing"]) == (None, None)


def _scan_row(**over):
    r = {"sym": "TIA/USDT", "price": 0.5900, "dir": "LONG", "score": 0.8, "rsi": 55.0,
         "atr": 0.004, "vol_ratio": 1.5, "patterns": []}
    r.update(over)
    return r


def _engine():
    return types.SimpleNamespace(
        risk=types.SimpleNamespace(trading_blocked_by=None, circuit_breaker_active=False),
        health=types.SimpleNamespace(snapshot=lambda: types.SimpleNamespace(status="HEALTHY")),
    )


def test_a_scan_entry_card_is_priced_as_the_resting_limit_it_is():
    from bot.skills.scan_skill import _build_scan_payload
    card = _build_scan_payload([_scan_row()], _engine())["entry_cards"][0]
    costed = tc.net_reward_risk(float(card["entry"]), float(card["stop_loss"]),
                                float(card["tp1"]), order_type="limit")
    assert costed is not None and costed.net is not None
    assert (card["net_rr"], card["rr_fee_losing"]) == (round(costed.net, 2), False)
    taker = tc.net_reward_risk(float(card["entry"]), float(card["stop_loss"]),
                               float(card["tp1"]), order_type="market")
    assert taker is not None and card["net_rr"] != round(taker.net or 0, 2)


def test_the_stream_row_carries_the_cards_figures_and_an_old_card_carries_none():
    from bot.skills.scan_skill import _scan_signal_rows
    card = {"symbol": "TIA", "direction": "LONG", "entry": "0.5888", "stop_loss": "0.58",
            "tp1": "0.602", "rr": "1.5", "score": 0.8}
    payload = {"timestamp": "2026-10-12 10:35 UTC",
               "entry_cards": [dict(card, net_rr=1.38, rr_fee_losing=False), card]}
    new, old = _scan_signal_rows(payload)
    assert (new["net_rr"], new["rr_fee_losing"]) == (1.38, False)
    assert (old["net_rr"], old["rr_fee_losing"]) == (None, None)
    # The figures are beside the key, never in it: the same call re-pushed by
    # a bot that sends them keeps the key it was sealed under.
    assert new["signal_key"] == old["signal_key"]


# ── the sealed flight record ─────────────────────────────────────────────


def test_a_new_flight_record_seals_the_figure_and_the_order_type_it_was_priced_at():
    from bot.guardian.flight_recorder import decision_idea_payload
    d = decision_idea_payload(_idea(order_type="market"))
    assert (d["rr"], d["net_rr"], d["rr_fee_losing"], d["order_type"]) == (
        6.47, 5.74, False, "market")
    d = decision_idea_payload(dict(TIA, direction="LONG", take_profit=0.58850))
    assert (d["net_rr"], d["rr_fee_losing"], d["order_type"]) == (None, True, None)
