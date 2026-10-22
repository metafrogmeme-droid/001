"""A Telegram call can carry chips, asked for before anything is recorded.

Chips (app/lib/duel_chips.js) ride with a call or not at all, so on Telegram
a pick dealt odds does not record the call: it asks how many chips to put
(none, or a step the stack and the limits allow), and the second tap records
it. Everything here is read from the card's chips block; nothing is computed
in Python, and the website's service checks every limit again.

Held here, each both ways:

1. The token grammar round-trips, and refuses chips on a pass, a zero, a
   leading zero, a non-ASCII digit and anything longer than a count.
2. The keyboard offers odds and the asking token only while the block says
   the layer is on, and only on picks dealt odds.
3. The prompt offers the steps the stack covers, the stack itself when no
   step fits, none when the stack is empty or unreadable, and nothing at all
   for a round that cannot take chips now.
4. The card says a call's chips and the stack only where it has a block; a
   stack that cannot be read is a dash, never 1,000.
5. Both bots are driven: the asking tap records nothing, the counted tap
   records the call with its chips, a public room is offered none and a
   chips tap from one records nothing.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot import duel_bot as db
from bot.formatters.duel_card import (
    CHOOSE,
    button_rows,
    call_line,
    chips_prompt,
    for_room,
    parse_callback_data,
    pick_callback_data,
    render_card,
    render_pick_result,
    stack_line,
)

SITE = "https://site.test"
STACK = {"season": "2026-10", "float": 1000, "available": 850, "on_table": 150, "held": 50,
         "net": 0, "put": 150, "calls_with_chips": 2, "paid": 0, "open": 2, "unread": 0}
BLOCK = {"enabled": True, "float": 1000, "min": 10, "max": 200, "hold_hours": 2,
         "odds_source": "table-2026-10", "stack": STACK}


def _card(block=BLOCK, **stack):
    b = None if block is None else {**block, "stack": {**block["stack"], **stack}}
    return {
        "day": "2026-10-22", "horizon_hours": 24, "open": 2, "chips": b,
        "rounds": [
            {"id": 1, "kind": "direction", "symbol": "BTCUSDT", "callable": True,
             "options": ["long", "short", "pass"], "odds": {"long": 1.9, "short": 1.9}},
            {"id": 3, "kind": "range", "symbol": "BNBUSDT", "threshold_pct": 2, "callable": True,
             "options": ["break", "hold", "pass"], "odds": {"break": 2.2, "hold": 1.7}},
            {"id": 4, "kind": "sports", "symbol": "FD:1", "callable": True,
             "options": ["home", "draw", "away", "pass"], "odds": None,
             "match": {"home": "Arsenal FC", "away": "Chelsea FC", "home_short": "Arsenal",
                       "away_short": "Chelsea", "kickoff": "2026-10-22T19:00:00.000Z"}},
            {"id": 5, "kind": "direction", "symbol": "ETHUSDT", "callable": True, "agent_direction": "short",
             "options": ["long", "short", "pass"], "odds": {"long": 1.9, "short": 1.9},
             "my_call": {"pick": "long", "pending": True,
                         "chips": {"chips": 50, "odds": 1.9, "state": "open", "payout": None}}},
        ],
        "accuracy": {"pct": None, "scored": 0, "correct": 0, "unresolved": 0},
        "marks": {"marks": 0, "pending": 1, "beat_agent": 0}, "streak": {"current": 1},
    }


# ── 1. the token ────────────────────────────────────────────────────────────

def test_a_chips_token_round_trips_and_fits_in_telegram():
    for chips in (None, CHOOSE, 10, 25, 200, 9999):
        data = pick_callback_data(123456, "long", chips)
        assert parse_callback_data(data) == (123456, "long", chips), data
        assert len(data.encode()) <= 64
    assert pick_callback_data(7, "long", CHOOSE) == "duel:7:long:c"
    assert pick_callback_data(7, "hold", 50) == "duel:7:hold:50"


def test_a_malformed_chips_token_is_refused_not_guessed():
    for bad in ("duel:1:pass:c", "duel:1:pass:25", "duel:1:long:0", "duel:1:long:050",
                "duel:1:long:x", "duel:1:long:12345", "duel:1:long:-5", "duel:1:long:",
                "duel:1:long:²", "duel:²:long", "duel:1:long:25:c"):
        assert parse_callback_data(bad) is None, bad


# ── 2. the keyboard ─────────────────────────────────────────────────────────

def test_the_keyboard_offers_odds_and_asks_only_while_the_layer_is_on():
    card = _card()
    rows = button_rows(card["rounds"], card["chips"])
    assert rows == [
        [("BTC LONG ×1.9", "duel:1:long:c"), ("SHORT ×1.9", "duel:1:short:c"), ("PASS", "duel:1:pass")],
        [("BNB BREAKS ±2% ×2.2", "duel:3:break:c"), ("HOLDS ±2% ×1.7", "duel:3:hold:c"), ("PASS", "duel:3:pass")],
        [("Arsenal", "duel:4:home"), ("DRAW", "duel:4:draw"), ("Chelsea", "duel:4:away"), ("PASS", "duel:4:pass")],
    ], "a match dealt without odds, and a pass, record at once"
    for row in rows:
        for _label, data in row:
            assert parse_callback_data(data) is not None, data
    plain = [[("BTC LONG", "duel:1:long"), ("SHORT", "duel:1:short"), ("PASS", "duel:1:pass")]]
    assert button_rows(card["rounds"][:1]) == plain, "no block: the keyboard is what it was"
    assert button_rows(card["rounds"][:1], {**BLOCK, "enabled": False}) == plain, \
        "switched off mid-season: nothing is offered"


# ── 3. the prompt ───────────────────────────────────────────────────────────

def _amounts(prompt):
    _text, rows = prompt
    assert rows[0] == [("No chips", "duel:1:long")], "none is always on offer, and records plainly"
    return [parse_callback_data(d)[2] for row in rows[1:] for _l, d in row]


def test_the_prompt_offers_what_the_stack_and_the_limits_allow():
    text, rows = chips_prompt(_card(), 1, "long")
    assert "*LONG* on *BTCUSDT* at ×1.9" in text
    assert "Nothing is recorded until you choose" in text
    assert "up to 200 chips" in text, "the limit, below the stack"
    assert _amounts((text, rows)) == [25, 50, 100]
    assert _amounts(chips_prompt(_card(available=60), 1, "long")) == [25, 50]
    assert _amounts(chips_prompt(_card(available=15), 1, "long")) == [15], "no step fits: the stack itself"
    empty = chips_prompt(_card(available=5), 1, "long")
    assert _amounts(empty) == [] and "No chips left this season" in empty[0]
    unread = chips_prompt(_card(available=None), 1, "long")
    assert _amounts(unread) == [] and "could not be read" in unread[0], "an unread stack offers nothing, never 1,000"


def test_no_prompt_where_the_round_cannot_take_chips_now():
    card = _card()
    assert chips_prompt(card, 1, "pass") is None
    assert chips_prompt(card, 4, "home") is None, "dealt without odds"
    assert chips_prompt(card, 5, "long") is None, "already called"
    assert chips_prompt(card, 99, "long") is None, "not on the card"
    assert chips_prompt(card, 3, "long") is None, "a word this round does not take"
    closed = _card()
    closed["rounds"][0]["callable"] = False
    assert chips_prompt(closed, 1, "long") is None
    assert chips_prompt(_card(block={**BLOCK, "enabled": False}), 1, "long") is None
    assert chips_prompt(_card(block=None), 1, "long") is None


# ── 4. the card ─────────────────────────────────────────────────────────────

def test_the_card_says_the_chips_and_the_stack_only_where_it_has_a_block():
    out = render_card(_card())
    assert "You called LONG — running · 50 chips on the table at ×1.9" in out
    assert "Chips: 850 to put · 150 on the table · 50 held — play money with no cash value" in out
    assert "tap a pick, then choose how many to put" in out
    room = render_card(for_room(_card()))
    assert "chips" not in room.lower(), "a room is told nothing about a player's chips"
    off = render_card(_card(block={**BLOCK, "enabled": False}))
    assert "Chips: 850 to put" in off, "switched off mid-season: what was put is still shown"
    assert "tap a pick, then choose" not in off


def test_a_stack_that_cannot_be_read_is_a_dash_and_each_state_reads_as_the_website_says_it():
    assert stack_line({"enabled": True, "stack": {"available": None}}) == "Chips: — (your chips could not be read)"
    assert stack_line({"enabled": True}) == "Chips: — (your chips could not be read)"
    assert stack_line(None) is None
    r = {"kind": "direction", "symbol": "BTCUSDT"}
    cases = {
        "held": "50 chips held until the check window closes",
        "returned": "50 chips returned",
        "lost": "lost 50 chips",
        "unread": "50 chips: their odds could not be read",
    }
    def line(call, state):
        return call_line({**r, "my_call": {"pick": "long", **call}, "my_result": {"state": state}}, show_chips=True)

    for state, words in cases.items():
        got = line({"chips": {"chips": 50, "odds": 1.9, "state": state, "payout": None}}, "wrong")
        assert got.endswith(words), (state, got)
    won = line({"chips": {"chips": 50, "odds": 1.9, "state": "won", "payout": 45}}, "correct")
    assert won.endswith("won 45 chips at ×1.9")
    reviewed = line({"review": {"state": "push"}}, "push")
    assert reviewed.endswith("no result · reviewed")
    flagged = line({"flagged": True}, "wrong")
    assert flagged.endswith("wrong · flagged, waiting for a review")


def test_the_reply_after_a_call_names_its_chips_only_when_it_carried_some():
    base = {"ok": True, "kind": "direction", "symbol": "BTCUSDT", "pick": "long", "entry_price": 60000,
            "resolves_at": "2026-10-23T10:00:00.000Z"}
    assert render_pick_result({**base, "chips": 50, "odds": 1.9}).endswith("Chips: 50 on the table at ×1.9")
    assert "Chips" not in render_pick_result(base)


# ── 5. both bots, driven ────────────────────────────────────────────────────

def _update(*, chat_type="private", data=None, user_id=777):
    msg = NS(reply_text=AsyncMock())
    query = NS(data=data, answer=AsyncMock(), message=msg) if data is not None else None
    return NS(effective_user=NS(id=user_id, username="p"),
              effective_chat=NS(id=user_id, type=chat_type),
              effective_message=msg, message=msg, callback_query=query)


def _ctx():
    return NS(args=[], bot=NS(username="RuneclawDuelBot"))


def _sent(update):
    return [(c.args[0], c.kwargs.get("reply_markup")) for c in update.effective_message.reply_text.await_args_list]


def _tokens(markup):
    return [b.callback_data for row in (markup.inline_keyboard if markup else ()) for b in row]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _duel_bot(placed, card=None):
    return db.DuelBot(
        fetch_card=lambda tg: (200, card or _card()),
        place_pick=lambda tg, rid, pick, chips=None: (placed.append((tg, rid, pick, chips)),
                                                      (200, {"ok": True, "kind": "direction", "symbol": "BTCUSDT",
                                                             "pick": pick, "entry_price": 60000.0, "chips": chips,
                                                             "odds": 1.9 if chips else None}))[1],
        site_url=SITE)


def test_the_duel_bot_asks_then_records_the_call_with_its_chips():
    placed: list = []
    bot = _duel_bot(placed)
    u = _update()
    _run(bot.cmd_duel(u, _ctx()))
    (text, markup), = _sent(u)
    assert "Chips: 850 to put" in text
    assert "duel:1:long:c" in _tokens(markup)
    # The asking tap: a prompt, and nothing recorded.
    u = _update(data="duel:1:long:c")
    _run(bot.on_duel_callback(u, _ctx()))
    assert placed == []
    (text, markup), = _sent(u)
    assert "Nothing is recorded until you choose" in text
    assert _tokens(markup) == ["duel:1:long", "duel:1:long:25", "duel:1:long:50", "duel:1:long:100"]
    # The counted tap records the call with its chips.
    u = _update(data="duel:1:long:50")
    _run(bot.on_duel_callback(u, _ctx()))
    assert placed == [("777", 1, "long", 50)]
    assert _sent(u)[0][0].endswith("Chips: 50 on the table at ×1.9")
    # "No chips" records the call plainly: chips absent, never 0.
    u = _update(data="duel:1:long")
    _run(bot.on_duel_callback(u, _ctx()))
    assert placed[-1] == ("777", 1, "long", None)


def test_the_duel_bot_offers_a_room_no_chips_and_records_nothing_from_a_chips_tap_there():
    placed: list = []
    bot = _duel_bot(placed)
    u = _update(chat_type="group")
    _run(bot.cmd_duel(u, _ctx()))
    (text, markup), = _sent(u)
    assert "chips" not in text.lower()
    assert all(not t.endswith(":c") for t in _tokens(markup)) and "duel:1:long" in _tokens(markup)
    for data in ("duel:1:long:c", "duel:1:long:50"):
        u = _update(chat_type="supergroup", data=data)
        _run(bot.on_duel_callback(u, _ctx()))
        assert placed == [], data
        assert "private chat" in _sent(u)[0][0] and "Nothing was recorded" in _sent(u)[0][0]


def test_an_asking_tap_on_a_round_that_cannot_take_chips_records_nothing_and_redraws():
    placed: list = []
    bot = _duel_bot(placed)
    u = _update(data="duel:5:long:c")
    _run(bot.on_duel_callback(u, _ctx()))
    assert placed == []
    texts = [t for t, _m in _sent(u)]
    assert texts[0].startswith("That call cannot take chips now. Nothing was recorded")
    assert "Daily Duel" in texts[1]
    # An unreadable card: said, and nothing recorded.
    down = db.DuelBot(fetch_card=lambda tg: (503, None), place_pick=lambda *a: placed.append(a), site_url=SITE)
    u = _update(data="duel:1:long:c")
    _run(down.on_duel_callback(u, _ctx()))
    assert placed == [] and _sent(u)[0][0].startswith("That call cannot take chips now")


def test_the_main_bot_asks_then_records_through_the_same_formatter(monkeypatch):
    from bot.skills.start_commands import StartCommands
    from bot.utils import duel_pull

    placed: list = []
    monkeypatch.setattr(duel_pull, "fetch_card", lambda tg: (200, _card()))
    monkeypatch.setattr(duel_pull, "place_pick", lambda tg, rid, pick, chips=None: (
        placed.append((tg, rid, pick, chips)), (200, {"ok": True, "kind": "direction", "symbol": "BTCUSDT",
                                                      "pick": pick, "chips": chips, "odds": 1.9}))[1])
    sent: list = []

    class Host(StartCommands):
        def _get_tg_id(self, update):
            return "777"

        async def _send(self, update, text, reply_markup=None, edit=False):
            sent.append((text, reply_markup))

    host = Host.__new__(Host)
    _run(host._handle_duel_callback(_update(), "duel:3:hold:c"))
    assert placed == []
    assert "*HOLDS ±2%* on *BNBUSDT* at ×1.7" in sent[0][0]
    assert _tokens(sent[0][1]) == ["duel:3:hold", "duel:3:hold:25", "duel:3:hold:50", "duel:3:hold:100"]
    sent.clear()
    _run(host._handle_duel_callback(_update(), "duel:3:hold:25"))
    assert placed == [("777", 3, "hold", 25)]
    assert sent[0][0].endswith("Chips: 25 on the table at ×1.9")
    assert "duel:1:long:c" in _tokens(sent[1][1]), "the redrawn card still offers chips"


@pytest.mark.parametrize("chips, sent_chips", [(None, None), (50, 50)])
def test_the_pull_sends_chips_only_when_there_are_some(monkeypatch, chips, sent_chips):
    from bot.utils import duel_pull
    bodies: list = []
    monkeypatch.setattr(duel_pull, "is_configured", lambda: True)
    monkeypatch.setattr(duel_pull, "_call", lambda path, data=None: (bodies.append((path, data)), (200, {}))[1])
    duel_pull.place_pick("777", 1, "long", chips)
    path, body = bodies[0]
    assert path == "/api/bot/sync/duel/pick"
    assert body.get("chips") == sent_chips and (("chips" in body) == (chips is not None))
