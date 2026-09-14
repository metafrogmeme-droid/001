"""Three of the website chat's own cards are Telegram commands — /nft, /spot
and /airdrops — off ONE renderer.

Slice 3 gave the nine website-only reads a DOOR on Telegram ("ask the web app
in these words"). Three of the nine are market reads with no account in them
— NFT collections by real 7-day volume, the spot pairs and the spot/perp
basis, the curated airdrop radar — so a door was the honest answer only until
the read could be fetched. It can: `GET /api/bot/sync/card/<name>` answers the
card the web intercept renders, byte for byte, over the bot-secret channel
(`app/test/sync_card_route_is_the_web_intercepts_own_card.test.js` pins that
side), and the three commands render THAT. A second formatter in Python would
be a second answer about one reading; there is none — `_web_card_text` fetches
the card off the event loop and turns the website's `<br>` into the newline
Telegram's HTML parser accepts.

The routed intents keep their rules and move from the door table to the
command table: on Telegram the branch dispatches the guarded command and
records the card the way `rwa` does; on the web the Python path answers from
the seam under the same gate, for the phrasings the Node intercept missed.

Plant the payload, drive the command, the branch on both surfaces and the
tables; read the words and the store.
"""
from __future__ import annotations

import asyncio
import inspect
import textwrap
from types import MethodType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import bot.utils.web_data_pull as wdp
from bot.nlp.intent_router import IntentRouter, routed_skill_names
from bot.nlp.skill_memory import card_shown_memory
from bot.nlp.web_reads import WEB_READS
from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import ROLE_PERMISSIONS
from bot.utils.web_data_pull import WEB_CARDS, fetch_web_card, web_card_text
from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
from tests.test_a_routed_answer_is_in_the_transcript import _store
from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update
from tests.test_the_web_intercept_phrasings_reach_the_same_read_on_telegram import (
    CALLER,
    _assistant,
    _turn,
    _web,
)
from tests.test_web_and_scan_authorization import ROUTED_INTENT_SEAM

CARDS = ("nft", "spot", "airdrops")
WEB_CARD = {"reply_html": "🖼 <b>NFT radar</b> — top collections:<br>• <b>Apes</b> — floor 2.5 ETH<br/>"
                          "<i>Read-only market data.</i>", "intent": "nft"}


@pytest.fixture(name="bot")
def _bot(tmp_path):
    yield from _halt_bot.__wrapped__(tmp_path)


# ── 1. the pull: a fixed name set, the wire never sees anything else ────────

class TestThePull:
    def test_unconfigured_is_none_for_every_card(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "")
        monkeypatch.setattr(wdp, "_request", lambda *a, **k: pytest.fail("must not be called"))
        for name in CARDS:
            assert fetch_web_card(name) is None

    def test_the_paths_and_the_optional_telegram_id(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        calls = []
        monkeypatch.setattr(wdp, "_request", lambda path, body=None: calls.append(path) or {"ok": 1})
        fetch_web_card("nft")
        fetch_web_card("spot")
        fetch_web_card("airdrops", "770001")
        fetch_web_card("airdrops", "")
        assert calls == ["/api/bot/sync/card/nft", "/api/bot/sync/card/spot",
                         "/api/bot/sync/card/airdrops?telegram_id=770001",
                         "/api/bot/sync/card/airdrops"]

    def test_a_name_outside_the_tuple_never_reaches_the_wire(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        monkeypatch.setattr(wdp, "_request", lambda *a, **k: pytest.fail("must not be called"))
        for bad in ("rwa", "nope", "../exposure", "", "__proto__"):
            assert fetch_web_card(bad) is None, bad
        assert WEB_CARDS == CARDS

    def test_the_telegram_id_is_quoted_and_bounded(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        calls = []
        monkeypatch.setattr(wdp, "_request", lambda path, body=None: calls.append(path) or {})
        fetch_web_card("airdrops", "1 2&x=3" + "9" * 40)
        assert calls == ["/api/bot/sync/card/airdrops?telegram_id=1%202%26x%3D3" + "9" * 25]


class TestTheText:
    def test_br_becomes_the_newline_telegram_accepts_and_nothing_else_changes(self):
        assert web_card_text(WEB_CARD) == ("🖼 <b>NFT radar</b> — top collections:\n• <b>Apes</b> — floor 2.5 ETH\n"
                                           "<i>Read-only market data.</i>")
        assert web_card_text({"reply_html": "a<BR />b<br/>c"}) == "a\nb\nc"

    @pytest.mark.parametrize("payload", [None, "html", 3, [], {}, {"reply_html": ""},
                                         {"reply_html": "   "}, {"reply_html": 5},
                                         {"error": "Card unavailable"}, {"nonsense": 1}])
    def test_a_payload_with_no_card_is_none_never_an_empty_card(self, payload):
        assert web_card_text(payload) is None


# ── 2. the seams: the website's card or the channel's own sentence ─────────

def _host():
    """A stand-in host carrying the one attribute the seams reach for
    (`_link_hint`) and the real fetch-and-convert method, BOUND — the
    method is the code under test, so it is driven rather than stubbed."""
    host = NS(_link_hint=TelegramHandler._link_hint)
    host._web_card_text = MethodType(TelegramHandler._web_card_text, host)
    return host


def _seam(name, host, *args, **kw):
    return asyncio.run(getattr(TelegramHandler, f"{name}_card_text")(host, *args, **kw))


class TestTheSeams:
    def test_each_seam_renders_the_websites_card_off_the_event_loop(self, monkeypatch):
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="": seen.append((name, tg)) or dict(WEB_CARD, intent=name))
        h = _host()
        assert _seam("nft", h) == web_card_text(WEB_CARD)
        assert _seam("spot", h) == web_card_text(WEB_CARD)
        assert _seam("airdrops", h, "770001") == web_card_text(WEB_CARD)
        assert seen == [("nft", ""), ("spot", ""), ("airdrops", "770001")]
        assert "to_thread" in inspect.getsource(TelegramHandler._web_card_text)

    def test_the_airdrops_seam_hands_over_the_callers_own_id_and_never_none(self, monkeypatch):
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="": seen.append(tg) or dict(WEB_CARD))
        h = _host()
        _seam("airdrops", h, 12345)
        _seam("airdrops", h, None)
        _seam("airdrops", h, "")
        assert seen == ["12345", "", ""]

    @pytest.mark.parametrize("answer", [None, {"error": "Card unavailable"}, {"reply_html": ""}, "junk"])
    def test_a_channel_that_did_not_answer_is_said_in_the_transports_words(self, monkeypatch, answer):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="": answer)
        h = _host()
        for name, args in (("nft", ()), ("spot", ()), ("airdrops", ("1",))):
            tg = _seam(name, h, *args)
            web = _seam(name, h, *args, surface="web")
            assert tg == TelegramHandler._WEB_LINK_HINT and "/link" in tg
            assert web == TelegramHandler._link_hint("web") and "/link" not in web
            assert "Nothing was read" in web
            for card in (tg, web):
                assert "NFT" not in card and "Spot" not in card and "Airdrop" not in card

    @pytest.mark.asyncio
    async def test_each_command_sends_its_seam_and_the_airdrops_one_names_the_caller(self):
        for name in ("nft", "spot"):
            h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
                   **{f"{name}_card_text": AsyncMock(return_value=f"<b>{name}</b> card")})
            await getattr(TelegramHandler, f"_cmd_{name}")(h, NS(), NS(args=[]))
            assert h._guard.await_args.args[1] == name, "the gate runs under the command's own name"
            getattr(h, f"{name}_card_text").assert_awaited_once_with()
            assert h._send.await_args.args[1] == f"<b>{name}</b> card"
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(), _get_tg_id=lambda u: "770001",
               airdrops_card_text=AsyncMock(return_value="<b>airdrops</b> card"))
        await TelegramHandler._cmd_airdrops(h, NS(), NS(args=[]))
        assert h._guard.await_args.args[1] == "airdrops"
        h.airdrops_card_text.assert_awaited_once_with("770001")
        assert h._send.await_args.args[1] == "<b>airdrops</b> card"
        # And the gate refusing means no read and no send — the refusal is
        # the guard's own sentence, sent by the guard.
        h = NS(_guard=AsyncMock(return_value=False), _send=AsyncMock(), _get_tg_id=lambda u: "1",
               nft_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_nft(h, NS(), NS(args=[]))
        assert h.nft_card_text.await_count == 0 and h._send.await_count == 0


# ── 3. the tables agree, and the three left the door table ─────────────────

class TestTheTables:
    def test_registered_guarded_and_in_the_catalogue(self):
        src = inspect.getsource(TelegramHandler)
        from bot.skills.command_catalog import all_entries
        entries = all_entries()
        for name in CARDS:
            assert f'("{name}", self._cmd_{name})' in src, f"/{name} not registered"
            fn_src = inspect.getsource(getattr(TelegramHandler, f"_cmd_{name}"))
            assert f'@guard("{name}")' in fn_src, f"/{name} must run the role gate under its own name"
            assert f"{name}_card_text" in fn_src, "the command renders the seam"
            title, audience, desc = entries[name]
            assert title == "🌍 Market context" and audience == "user", (name, title, audience)
            assert "read-only" in desc or "guided only" in desc, desc

    def test_the_roles_that_hold_rwa_hold_these(self):
        for role in ("trader", "paper", "viewer"):
            assert set(CARDS) <= ROLE_PERMISSIONS[role], role
        assert not set(CARDS) & ROLE_PERMISSIONS["pending"]

    def test_the_three_tables_agree_and_the_door_table_lost_them(self):
        from bot.web import user_gateway as ug
        assert set(CARDS) <= set(ug._WEB_SEAM)
        assert set(CARDS) <= set(routed_skill_names())
        for name in CARDS:
            assert WEB_ROUTED_PERMISSION[name] == name
            assert ROUTED_INTENT_SEAM[name] == f"{name}_card_text"
            assert ug._WEB_SKILL_PERMISSION[name] == name
        assert not set(CARDS) & set(WEB_READS), "a door notice over a read that exists"

    def test_the_command_branches_sit_above_the_door_branch(self):
        """A read that exists here must never be answered 'ask the web app':
        the three dispatch branches come BEFORE the `WEB_READS` notice in
        `_handle_message`, so a row re-added to the door table by mistake
        would still lose to the command."""
        src = textwrap.dedent(inspect.getsource(TelegramHandler._handle_message))
        door = src.index("if intent.skill in WEB_READS:")
        for name in CARDS:
            assert src.index(f'if intent.skill == "{name}":') < door, name


# ── 4. the router: the same phrasings, now a command's ─────────────────────

ROWS = [
    ("nft radar", "nft"), ("opensea", "nft"), ("which nfts are trending", "nft"),
    ("spot market", "spot"), ("spot vs perp", "spot"), ("spot basis", "spot"),
    ("airdrops", "airdrops"), ("airdrop radar", "airdrops"), ("any testnets worth farming?", "airdrops"),
]


@pytest.mark.parametrize("text,intent", ROWS, ids=[t for t, _ in ROWS])
def test_the_phrasing_routes_to_the_command_intent(text, intent):
    i = IntentRouter().classify_rules(text)
    assert i is not None and i.matched and i.skill == intent and i.confidence >= 0.8


@pytest.mark.parametrize("text", ["spot price of btc", "what is a spot market", "how do nfts work",
                                  "what are airdrops", "rwa radar", "my net worth"])
def test_the_neighbours_do_not_reach_them(text):
    i = IntentRouter().classify_rules(text)
    got = i.skill if (i is not None and i.matched) else None
    assert got not in CARDS, (text, got)


# ── 5. Telegram: the guarded command, the card recorded, no door ────────────

class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text,name", [("nft radar", "nft"), ("spot market", "spot"),
                                           ("airdrop radar", "airdrops")])
    async def test_the_branch_dispatches_the_command_and_records_the_card(self, bot, text, name):
        store = _store(bot)
        for other in CARDS:
            setattr(bot, f"_cmd_{other}", AsyncMock())
        await bot._handle_message(_update(OPERATOR, text), None)
        for other in CARDS:
            assert getattr(bot, f"_cmd_{other}").await_count == (1 if other == name else 0), other
        turns = [(m.role, m.content) for m in store.get_recent(str(OPERATOR), limit=5)]
        assert turns == [("user", text), ("assistant", card_shown_memory(name))]
        assert bot.registry.dispatched == []

    @pytest.mark.asyncio
    async def test_end_to_end_the_websites_card_reaches_the_chat_newlines_and_all(self, bot, monkeypatch):
        # RED HERRING: before this slice the same words sent the door notice
        # ("is handled by the RUNECLAW web app's chat … ask it there").
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="": dict(WEB_CARD))
        await bot._handle_message(_update(OPERATOR, "nft radar"), None)
        assert bot.sent[-1] == web_card_text(WEB_CARD)
        assert "<br" not in bot.sent[-1] and "\n" in bot.sent[-1]
        assert "web app's chat" not in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_end_to_end_a_channel_that_did_not_answer_names_link(self, bot, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="": None)
        await bot._handle_message(_update(OPERATOR, "spot market"), None)
        assert bot.sent[-1] == TelegramHandler._WEB_LINK_HINT

    @pytest.mark.asyncio
    async def test_the_airdrops_command_hands_the_website_the_callers_own_id(self, bot, monkeypatch):
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="": seen.append((name, tg)) or dict(WEB_CARD))
        await bot._handle_message(_update(OPERATOR, "airdrop radar"), None)
        assert seen == [("airdrops", str(OPERATOR))]


# ── 6. the web: the seam under the gate, recorded as a result ──────────────

class TestTheWeb:
    @pytest.mark.parametrize("text,intent,args", [
        ("which nfts are trending", "nft", ()),
        ("spot vs perp", "spot", ()),
        ("any testnets worth farming?", "airdrops", (CALLER,)),
    ])
    def test_the_seam_answers_and_the_result_is_recorded(self, monkeypatch, text, intent, args):
        ug, h = _web(monkeypatch)
        for name in CARDS:
            setattr(h, f"{name}_card_text", AsyncMock(return_value=f"<b>{name}</b> — the website's card"))
        resp, body = _turn(ug, h, text)
        assert resp.status == 200 and body["intent"] == intent
        m = getattr(h, f"{intent}_card_text")
        assert m.await_count == 1 and m.await_args.args == args and m.await_args.kwargs == {"surface": "web"}
        assert body["reply_html"] == m.return_value
        assert f"[{intent}] result:" in _assistant(h)
        for other in CARDS:
            if other != intent:
                assert getattr(h, f"{other}_card_text").await_count == 0, other

    def test_the_gate_refuses_before_the_seam_and_records_not_run(self, monkeypatch):
        ug, h = _web(monkeypatch, role="pending", denial="role")
        for name in CARDS:
            setattr(h, f"{name}_card_text", AsyncMock(return_value="card"))
        resp, body = _turn(ug, h, "nft radar")
        assert resp.status == 403 and body["error"] == "insufficient_permissions"
        assert h.nft_card_text.await_count == 0
        assert "[nft] NOT RUN" in _assistant(h) or "not run" in _assistant(h).lower()
