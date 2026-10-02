"""The reads only the website's chat answers meet a DOOR on Telegram, and
"replay" stops running a backtest.

`app/routes/chat.js` answers one shape of question from its own
intercepts. Six had nothing on Telegram (replay, letter, airdrops, nft,
spot, defi) and three share a word with a Telegram command that does
something else (/alerts is the anomaly-alert scope, /venues picks the venues
that trade, /memeplan is a buy preflight). Typed on Telegram, "replay every
signal with $1k" ran a SYNTHETIC BACKTEST — the backtest rule carried a bare
`replay` — and the other eight were greeted by the social gate or reached a
model told nothing about the website. A read the product has on one surface
and not the other gets a door, never a narrator: the notice names the surface
that answers it, the words it accepts, the same-named command when there is
one, and ends by saying nothing was read. Both surfaces answer from
`bot/nlp/web_reads.py`, whose table the Node side pins against the
intercepts' own patterns (`app/test/web_reads_examples_reach_the_intercepts.test.js`).

All nine are COMMANDS now: eight since the website's own cards became
fetchable (`tests/test_the_website_cards_are_telegram_commands.py`,
`tests/test_the_public_and_wallet_reads_are_telegram_commands.py`) and the
price alert since the website's alert engine gained a Telegram delivery
(`tests/test_a_price_alert_is_armed_and_delivered_on_telegram.py`). Their
phrasings still route to the same intents (ROWS below); the intents dispatch
a command rather than a door, and the table is empty — idle yield left it
for the shared card. Telegram's `/idleyield` stays the operator's exchange
scan, a different reading, so these words do not reach that command.

Plant the phrase, drive the surface, read the STORE and the words.
"""
from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import AsyncMock

import pytest

from bot.nlp.intent_router import IntentRouter, _is_social_message
from bot.nlp.skill_memory import card_shown_memory
from bot.nlp.web_reads import WEB_READS, collision_blurb
from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
from tests.test_a_routed_answer_is_in_the_transcript import _store
from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update
from tests.test_the_web_intercept_phrasings_reach_the_same_read_on_telegram import _assistant, _turn, _web

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(name="bot")
def _bot(tmp_path):
    yield from _halt_bot.__wrapped__(tmp_path)


# ── the router ───────────────────────────────────────────────────────────

ROWS = [
    ("replay every signal with $1k", "replay"),
    ("what if i'd taken every trade with $500", "replay"),
    ("what-if replay", "replay"),
    ("replay", "replay"),
    ("show me this week's letter", "letter"),
    ("weekly letter", "letter"),
    ("last week's letter please", "letter"),
    ("airdrops", "airdrops"),
    ("airdrop radar", "airdrops"),
    ("any testnets worth farming?", "airdrops"),
    ("nft radar", "nft"),
    ("opensea", "nft"),
    ("which nfts are trending", "nft"),
    ("floor price of pudgy penguins", "nft"),
    ("spot market", "spot"),
    ("spot vs perp", "spot"),
    ("spot basis", "spot"),
    ("my defi positions", "defi"),
    ("aave health", "defi"),
    ("health factor", "defi"),
    ("defi", "defi"),
    ("my exposure", "exposure"),
    ("what's my total exposure", "exposure"),
    ("am I overexposed", "exposure"),
    ("how exposed am i", "exposure"),
    ("tell me when BTC drops below 100k", "price_alert"),
    ("set an alert for eth at 3000", "price_alert"),
    ("alert me when sol hits 200", "price_alert"),
    ("my alerts", "price_alert"),
    ("price alert", "price_alert"),
    ("best venue for BTC", "venue_router"),
    ("cheapest exchange to short eth", "venue_router"),
    ("venue router", "venue_router"),
    ("meme radar", "meme_radar"),
    ("dexscreener", "meme_radar"),
    ("degen", "meme_radar"),
    ("meme coins", "meme_radar"),
    ("ai agent tokens", "meme_radar"),
    # Idle yield left the intercept table. Both doors route these words to
    # the shared card. /idleyield here is still the operator's account, and
    # these words do not reach that command.
    ("idle yield", "idleyield"),
    ("idle-yield optimizer", "idleyield"),
    ("my idle usdc", "idleyield"),
    ("put my idle cash to work", "idleyield"),
    ("put my usdc to work", "idleyield"),
    ("best yield for my idle stables", "idleyield"),
    ("best apy for usdc", "idleyield"),
    ("where can i earn yield", "idleyield"),
    ("earn more on my stables", "idleyield"),
    ("what to do with my idle cash", "idleyield"),
    ("is my capital idle", "idleyield"),
]

#: Phrases that must NOT reach any of the nine — each a neighbour that was
#: routed elsewhere before this slice and still is, or a price question the
#: web's wider `spot` claim would have swallowed.
DECOYS = [
    ("spot price of btc", "spot"),
    ("what's the price of btc", "spot"),
    ("backtest it", "replay"),
    ("run a backtest", "replay"),
    ("test the strategy", "replay"),
    # A replay phrasing NEITHER surface claims: the web's regex needs
    # "replay every/all/each signal", and so does this router. Under the old
    # backtest rule the bare word inside it ran a backtest for this.
    ("replay the last week", "run_backtest"),
    ("anomaly alerts every 2h", "price_alert"),
    ("what is defi", "defi"),
    ("what is exposure", "exposure"),
    ("what is overexposed", "exposure"),
    ("whats my drawdown", "exposure"),
    ("am i exposed", "exposure"),
    ("check my risk", "exposure"),
    ("whats my max exposure", "exposure"),
    ("what are airdrops", "airdrops"),
    ("how do nfts work", "nft"),
    ("what is opensea", "nft"),
    ("what is a floor price", "nft"),
    ("my net worth", "spot"),
    ("rwa radar", "spot"),
    ("halt the bot", "replay"),
    ("close my eth", "spot"),
    # The web's idle-yield regex takes a bare "idle" and "stake my …"; here
    # "stake my usdc" is a request to ACT that /stake's confirm card owns, an
    # education question is the model's, and "yield radar" is this chat's
    # own /yield word. None reaches the door.
    ("stake my usdc", "idleyield"),
    ("what is idle yield", "idleyield"),
    ("what is yield farming", "idleyield"),
    ("how do i earn yield", "idleyield"),
    ("yield radar", "idleyield"),
]

#: Routes that stay exactly where they were.
UNCHANGED = [
    ("run a backtest", "run_backtest"),
    ("backtest btc", "run_backtest"),
    ("test the strategy", "run_backtest"),
    ("my net worth", "networth"),
    ("rwa radar", "rwa"),
    ("halt the bot", "halt"),
    ("close my eth", "close_position"),
]


@pytest.mark.parametrize("text,intent", ROWS, ids=[t for t, _ in ROWS])
def test_the_websites_phrasing_routes_to_its_door(text, intent):
    i = IntentRouter().classify_rules(text)
    assert i is not None and i.matched and i.skill == intent, (text, getattr(i, "skill", None))
    assert i.confidence >= 0.8


@pytest.mark.parametrize("text,not_intent", DECOYS, ids=[t for t, _ in DECOYS])
def test_a_neighbour_does_not_reach_the_door(text, not_intent):
    i = IntentRouter().classify_rules(text)
    got = i.skill if (i is not None and i.matched) else None
    assert got != not_intent, (text, got)
    assert got not in WEB_READS or got is None or (text, got) in [(t, s) for t, s in ROWS], (text, got)


@pytest.mark.parametrize("text,intent", UNCHANGED, ids=[t for t, _ in UNCHANGED])
def test_the_neighbours_still_route_where_they_did(text, intent):
    i = IntentRouter().classify_rules(text)
    assert i is not None and i.matched and i.skill == intent, (text, getattr(i, "skill", None))


def test_replay_no_longer_runs_a_backtest():
    """RED HERRING: `run_backtest` still exists and still answers "run a
    backtest"; only the bare word left it. The third line is the one that
    sees it leave: a replay phrasing the new rule does NOT claim used to fall
    to the backtest rule's bare `replay`, and reaches no rule now."""
    r = IntentRouter()
    assert r.classify_rules("replay every signal with $1k").skill == "replay"
    assert r.classify_rules("run a backtest").skill == "run_backtest"
    i = r.classify_rules("replay the last week")
    assert not (i is not None and i.matched), getattr(i, "skill", None)


def test_an_education_question_is_the_models_not_the_greeters():
    """"what is defi" is three words with no rule; without its noun in the
    social gate's vocabulary it was answered "hey!"."""
    for text in ("what is defi", "what are airdrops", "how do nfts work",
                 "what is opensea", "what is a floor price",
                 "what is a spot market", "what is degen", "what is a meme coin",
                 "what is exposure", "what is overexposed"):
        assert not _is_social_message(text), text


# ── the table and the notice ─────────────────────────────────────────────

def test_every_row_names_a_real_intercept_and_a_real_library():
    js = (REPO / "app" / "routes" / "chat.js").read_text()
    block = js[js.index("const INTERCEPTS = ["):]
    block = block[:block.index("\n];")]
    rows = set(re.findall(r"^\s*\['([a-z]+)',", block, re.M))
    for r in WEB_READS.values():
        assert r.row in rows, r
        assert (REPO / "app" / "lib" / f"{r.lib}.js").exists(), r.lib
    raw = json.loads((REPO / "bot" / "nlp" / "web_reads.json").read_text())
    assert set(raw) == set(WEB_READS) and len(WEB_READS) == 0
    assert not {"airdrops", "nft", "spot", "replay", "letter", "wallet", "defi",
                "exposure", "research", "networth", "venue_router", "meme_radar",
                "price_alert", "idleyield", "idle_yield"} & set(WEB_READS), (
        "commands now, not doors")


def test_the_collision_sentence_is_the_catalogues_own_words():
    from bot.skills.command_catalog import all_entries
    for cmd in ("alerts", "venues", "memeplan"):
        assert collision_blurb(cmd) == all_entries()[cmd][2]
    with pytest.raises(KeyError):
        collision_blurb("no_such_command")


# ── Telegram ─────────────────────────────────────────────────────────────

class TestTelegram:
    @pytest.mark.asyncio
    async def test_the_idle_yield_ask_sends_the_card_not_the_operator_scan(self, bot, monkeypatch):
        # The operator's /idleyield reads the exchange account. These words
        # render the caller's wallet card and dispatch nothing, including
        # that command.
        import bot.utils.web_data_pull as wdp
        monkeypatch.setattr(wdp, "fetch_idleyield", lambda tg: {
            "reply_html": "💤→💸 <b>Idle-yield</b><br><span>note</span>",
            "intent": "idleyield",
        })
        bot._cmd_idleyield = AsyncMock()
        store = _store(bot)
        await bot._handle_message(_update(OPERATOR, "put my idle cash to work"), None)
        assert bot.registry.dispatched == []
        assert bot._cmd_idleyield.await_count == 0
        assert "Idle-yield" in bot.sent[-1]
        assert "<span" not in bot.sent[-1] and "\n" in bot.sent[-1] and "note" in bot.sent[-1]
        turns = [(m.role, m.content) for m in store.get_recent(str(OPERATOR), limit=5)]
        assert turns[0] == ("user", "put my idle cash to work")
        assert turns[1][1] == card_shown_memory("idleyield")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", [
        "put my idle cash to work",
        "my idle usdc",
    ])
    async def test_each_read_gets_the_card_and_the_model_never_runs(self, bot, monkeypatch, text):
        import bot.utils.web_data_pull as wdp
        monkeypatch.setattr(wdp, "fetch_idleyield", lambda tg: {
            "reply_html": "<b>Idle-yield</b><br>wallet", "intent": "idleyield",
        })
        rec = AsyncMock(return_value="narrated")
        bot._llm_chat = rec
        bot._cmd_idleyield = AsyncMock()
        store = _store(bot)
        await bot._handle_message(_update(OPERATOR, text), None)
        rec.assert_not_awaited()
        assert bot._cmd_idleyield.await_count == 0
        assert "Idle-yield" in bot.sent[-1] and "<br" not in bot.sent[-1]
        assert card_shown_memory("idleyield") == store.get_recent(str(OPERATOR), limit=5)[-1].content

    @pytest.mark.asyncio
    async def test_a_refused_gate_reads_nothing(self, bot):
        bot.idleyield_card_text = AsyncMock(return_value="<b>Idle-yield</b>")
        bot._guard = AsyncMock(return_value=False)
        bot._cmd_idleyield = AsyncMock()
        await bot._handle_message(_update(OPERATOR, "idle yield"), None)
        assert bot._guard.await_args.args[1] == "idleyield"
        assert bot.idleyield_card_text.await_count == 0
        assert bot._cmd_idleyield.await_count == 0
        assert not [s for s in bot.sent if "Idle-yield" in s]


# ── the web ──────────────────────────────────────────────────────────────

class TestTheWeb:
    @pytest.mark.parametrize("text", ["best apy for usdc", "my idle usdc"])
    def test_the_python_path_renders_the_card_and_records(self, monkeypatch, text):
        ug, h = _web(monkeypatch)
        resp, body = _turn(ug, h, text)
        assert resp.status == 200
        assert body["intent"] == "idleyield"
        assert h.idleyield_card_text.await_count == 1
        assert h.idleyield_card_text.await_args.kwargs == {"surface": "web"}
        assert body["reply_html"] == h.idleyield_card_text.return_value
        assert "[idleyield] result:" in _assistant(h)
        assert h._llm_chat.await_count == 0 if hasattr(h._llm_chat, "await_count") else True
