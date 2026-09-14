"""The reads only the website's chat answers meet a DOOR on Telegram, and
"replay" stops running a backtest.

`app/routes/chat.js` answers fifteen shapes of question from its own
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

Eight of the nine are COMMANDS since the website's own cards became
fetchable (`tests/test_the_website_cards_are_telegram_commands.py`,
`tests/test_the_public_and_wallet_reads_are_telegram_commands.py`): their
phrasings still route to the same intents (ROWS below), the intents now
dispatch a command rather than a door, and the table holds the one door
left — the price alert, a WRITE the website's push channel does.

Plant the phrase, drive the surface, read the STORE and the words.
"""
from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import AsyncMock

import pytest

from bot.nlp.intent_router import IntentRouter, _is_social_message
from bot.nlp.skill_memory import routed_answer_memory
from bot.nlp.web_reads import WEB_READS, collision_blurb, web_read_notice
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
    ("meme coins", "meme_radar"),
    ("ai agent tokens", "meme_radar"),
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
    ("what are airdrops", "airdrops"),
    ("how do nfts work", "nft"),
    ("my net worth", "spot"),
    ("rwa radar", "spot"),
    ("halt the bot", "replay"),
    ("close my eth", "spot"),
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
    for text in ("what is defi", "what are airdrops", "how do nfts work", "what is a spot market"):
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
    assert set(raw) == set(WEB_READS) and len(WEB_READS) == 1
    assert not {"airdrops", "nft", "spot", "replay", "letter", "defi",
                "venue_router", "meme_radar"} & set(WEB_READS), "commands now, not doors"


@pytest.mark.parametrize("intent", sorted(WEB_READS))
def test_the_example_the_notice_quotes_is_one_telegram_routes_too(intent):
    """The other direction of the JS pin: a linked user who types the
    website's example into Telegram lands on this door, not the greeter."""
    i = IntentRouter().classify_rules(WEB_READS[intent].example)
    assert i is not None and i.matched and i.skill == intent, (intent, getattr(i, "skill", None))


@pytest.mark.parametrize("intent", sorted(WEB_READS))
def test_the_telegram_notice_names_the_surface_the_words_and_says_nothing_was_read(intent):
    r = WEB_READS[intent]
    n = web_read_notice(intent, surface="telegram")
    assert "web app" in n and r.example in n and r.label.lower() in n.lower()
    assert n.endswith("Nothing was read or set here.") or "Nothing was read or set here." in n
    slashes = re.findall(r"(?<![\w/])/[a-z_]{2,}", re.sub(r"<[^>]+>", "", n))
    if r.collides_with:
        assert slashes == [f"/{r.collides_with}"] or set(slashes) == {f"/{r.collides_with}"}, slashes
        assert collision_blurb(r.collides_with) in n
        assert "a different thing" in n
    else:
        assert slashes == [], slashes


@pytest.mark.parametrize("intent", sorted(WEB_READS))
def test_the_web_notice_names_the_words_and_no_command(intent):
    r = WEB_READS[intent]
    n = web_read_notice(intent, surface="web")
    assert r.example in n and "Nothing was read or set" in n
    assert re.findall(r"(?<![\w/])/[a-z_]{2,}", re.sub(r"<[^>]+>", "", n)) == []
    assert "web app" not in n, "on the web, the web is 'this chat'"


def test_the_collision_sentence_is_the_catalogues_own_words():
    from bot.skills.command_catalog import all_entries
    for cmd in ("alerts", "venues", "memeplan"):
        assert collision_blurb(cmd) == all_entries()[cmd][2]
    with pytest.raises(KeyError):
        collision_blurb("no_such_command")


# ── Telegram ─────────────────────────────────────────────────────────────

class TestTelegram:
    @pytest.mark.asyncio
    async def test_a_price_alert_sends_the_door_dispatches_nothing_and_records(self, bot):
        # RED HERRING: "replay every signal with $1k" used to be this test's
        # phrase, and before slice 3 those words dispatched `run_backtest`;
        # the replay is a command now, so the one door left is the alert.
        store = _store(bot)
        await bot._handle_message(_update(OPERATOR, "tell me when BTC drops below 100k"), None)
        assert bot.registry.dispatched == []
        assert bot.sent[-1] == web_read_notice("price_alert", surface="telegram")
        turns = [(m.role, m.content) for m in store.get_recent(str(OPERATOR), limit=5)]
        assert turns[0] == ("user", "tell me when BTC drops below 100k")
        assert turns[1][1] == routed_answer_memory("price_alert", bot.sent[-1])
        assert "no tool ran" in turns[1][1]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text,intent", [
        ("tell me when BTC drops below 100k", "price_alert"),
        ("set an alert for eth at 3000", "price_alert"),
    ])
    async def test_each_read_gets_its_door_and_the_model_never_runs(self, bot, text, intent):
        rec = AsyncMock(return_value="narrated")
        bot._llm_chat = rec
        store = _store(bot)
        await bot._handle_message(_update(OPERATOR, text), None)
        rec.assert_not_awaited()
        assert bot.sent[-1] == web_read_notice(intent, surface="telegram")
        assert routed_answer_memory(intent, bot.sent[-1]) == store.get_recent(str(OPERATOR), limit=5)[-1].content

    @pytest.mark.asyncio
    async def test_a_price_alert_ask_names_the_anomaly_command_as_a_different_thing(self, bot):
        await bot._handle_message(_update(OPERATOR, "my alerts"), None)
        n = bot.sent[-1]
        assert "/alerts" in n and "anomaly alert scope" in n and "different thing" in n
        assert "tell me when BTC drops below 100k" in n


# ── the web ──────────────────────────────────────────────────────────────

class TestTheWeb:
    @pytest.mark.parametrize("text,intent", [
        ("alert me when sol hits 200", "price_alert"), ("my alerts", "price_alert"),
    ])
    def test_the_python_path_answers_with_the_intercepts_words_and_records(self, monkeypatch, text, intent):
        ug, h = _web(monkeypatch)
        resp, body = _turn(ug, h, text)
        assert resp.status == 200
        assert body["intent"] == intent
        assert body["reply_html"] == web_read_notice(intent, surface="web")
        assert _assistant(h) == routed_answer_memory(intent, body["reply_html"])
        assert h._llm_chat.await_count == 0 if hasattr(h._llm_chat, "await_count") else True
