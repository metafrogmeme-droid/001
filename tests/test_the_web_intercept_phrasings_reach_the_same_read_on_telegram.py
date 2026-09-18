"""Three of the website's chat intercepts, reached by the same words on Telegram.

`app/routes/chat.js` answers fifteen phrasings from its own Node intercepts
before a turn reaches the bot; three of them (`networth`, `rwa`, `research`)
have a Telegram command that renders the same reading and, typed as WORDS on
Telegram, reached nothing: "my net worth" and "rwa radar" were GREETED by the
social gate, "research SOL" reached a chat model with no dossier tool. They
are routed intents on both surfaces now — the Telegram branch dispatches the
guarded command (the guard IS the role gate), the web's Python path answers
the phrasings the Node intercepts miss from the same seam under the same
gate, and every branch records what it showed.

Three decisions worth pinning because a rule does not carry them:
`exposure` stays with `check_risk` (a pinned routing); `deep dive on <sym>`
stays with the chart rules on Telegram, where the web's research intercept
claims it as a dossier; and an education question ("what is rwa") is the
model's on Telegram, where the web hands it the radar.

DRIVEN, not scanned: the router over a table with decoys; the Telegram
handler through the store and through its guard; the web turn through
`_chat_turn`; the net-worth reading through a store that answers, has no
venue, will not decrypt, times out, and RAISES — the fourth word is the one
the command used to fold into "not connected — /connect to link one".
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from bot.nlp.conversation_store import ConversationStore
from bot.nlp.intent_router import IntentRouter, routed_skill_names
from bot.nlp.skill_memory import card_shown_memory
from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION
from bot.skills.telegram_handler import TelegramHandler
from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update
from tests.test_the_status_question_is_answered_by_the_status_card import CALLER, _Engine
from tests.test_web_and_scan_authorization import ROUTED_INTENT_SEAM


@pytest.fixture(name="bot")
def _bot(tmp_path):
    yield from _halt_bot.__wrapped__(tmp_path)


# ── 1. the router ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def route(router, text: str) -> str:
    x = router.classify_rules(text)
    if x.is_social:
        return "SOCIAL"
    if not x.skill:
        return "MODEL"
    return f"ASK:{x.skill}" if x.confidence < 1.0 else x.skill


ROWS = [
    ("my net worth", "networth"), ("net worth", "networth"), ("networth", "networth"),
    ("what's my net worth", "networth"), ("total balance across everything", "networth"),
    ("total equity", "networth"), ("everything i own", "networth"), ("how much am i worth", "networth"),
    ("rwa radar", "rwa"), ("rwa", "rwa"), ("real world assets", "rwa"),
    ("tokenized treasuries", "rwa"), ("hows rwa doing", "rwa"), ("rwa sector", "rwa"),
    ("research SOL", "research"), ("research sol", "research"), ("research $PENDLE", "research"),
    ("research PENDLE", "research"), ("dossier on ondo", "research"),
    ("due diligence on eth please", "research"), ("can you research SOL for me", "research"),
    ("do some research on ondo", "research"), ("research sol/usdt", "research"),
]
#: A rule that matches inside a sentence routes the sentence's question as the
#: command: an education question is the model's, and a document is not a
#: ticker.
DECOYS = [
    ("what is net worth", "MODEL"), ("what is rwa", "MODEL"), ("what are real world assets", "MODEL"),
    ("research the docs", "MODEL"), ("research report", "MODEL"), ("look at the research", "MODEL"),
    ("what's the research on eth", "MODEL"), ("research it", "MODEL"),
    ("research", "ASK:research"), ("research eth and sol", "ASK:research"),
    ("dossier", "ASK:research"), ("due diligence", "ASK:research"),
    # short words the social gate now knows and no rule claims
    ("radar", "MODEL"), ("tokenized", "MODEL"), ("rwa?", "rwa"),
]
#: Neighbours that must not move: each is a pinned routing of its own.
UNCHANGED = [
    ("deep dive on sol", "analyze_asset"), ("whats my exposure", "check_risk"),
    ("am i overexposed", "check_risk"), ("whats my equity", "get_portfolio"),
    ("balance", "get_portfolio"), ("show my balance", "get_portfolio"),
    ("my open orders", "get_orders"), ("hows btc doing", "analyze_asset"),
    ("scan the market", "scan_market"), ("status", "status"), ("help", "help"),
]


class TestTheRouter:
    @pytest.mark.parametrize("text, expected", ROWS + DECOYS + UNCHANGED,
                             ids=[t for t, _ in ROWS + DECOYS + UNCHANGED])
    def test_each_phrase_reaches_what_answers_it(self, router, text, expected):
        assert route(router, text) == expected, text

    def test_nothing_in_the_tables_is_greeted(self, router):
        # RED HERRING: "net worth", "rwa" and "research" are each under four
        # words with no symbol, which is exactly the shape the social gate
        # calls small talk unless a word of its own vocabulary is present.
        for text, _ in ROWS + DECOYS + UNCHANGED:
            assert route(router, text) != "SOCIAL", text

    def test_the_research_rule_carries_the_symbol_and_asks_which_when_it_cannot(self, router):
        assert router.classify_rules("research SOL").kwargs == {"symbol": "SOL/USDT"}
        assert router.classify_rules("research PENDLE").kwargs == {"symbol": "PENDLE/USDT"}
        assert router.classify_rules("dossier on ondo").kwargs == {"symbol": "ONDO/USDT"}
        bare = router.classify_rules("research")
        assert bare.skill == "research" and bare.confidence < 1.0 and "symbol" not in bare.kwargs
        two = router.classify_rules("research eth and sol")
        assert two.skill == "research" and two.confidence < 1.0, "two assets named is not one asset asked about"

    def test_the_three_are_router_names_and_the_tables_agree(self):
        assert {"networth", "rwa", "research"} <= routed_skill_names()
        from bot.web import user_gateway as ug
        # Twelve since the website's own cards became commands; thirteen with
        # the price alert, a WRITE the website's alert engine holds.
        assert set(ug._WEB_SEAM) == {"networth", "rwa", "research", "nft", "spot", "airdrops",
                                     "replay", "letter", "venue_router", "meme_radar", "wallet", "defi",
                                     "price_alert"}
        assert set(WEB_ROUTED_PERMISSION) == set(ROUTED_INTENT_SEAM)
        assert set(ug._WEB_SEAM) == set(WEB_ROUTED_PERMISSION) - {"status"}
        # The permission is the @guard on the command that renders the seam.
        for intent in ug._WEB_SEAM:
            src = inspect.getsource(getattr(TelegramHandler, f"_cmd_{intent}"))
            assert f'@guard("{WEB_ROUTED_PERMISSION[intent]}")' in src, intent
            assert ROUTED_INTENT_SEAM[intent] in src, (intent, "the command renders the seam")


# ── 2. the Telegram branch: the guarded command, and the card recorded ───────

class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text, command, card, kwargs", [
        ("my net worth", "_cmd_networth", "networth", {}),
        ("rwa radar", "_cmd_rwa", "rwa", {}),
        ("research SOL", "_cmd_research", "research", {"symbol": "SOL/USDT"}),
    ])
    async def test_the_branch_dispatches_the_command_and_records_the_card(self, bot, text, command, card, kwargs):
        bot.conversations = ConversationStore()
        for name in ("_cmd_networth", "_cmd_rwa", "_cmd_research"):
            setattr(bot, name, AsyncMock())
        await bot._handle_message(_update(OPERATOR, text), None)
        for name in ("_cmd_networth", "_cmd_rwa", "_cmd_research"):
            assert getattr(bot, name).await_count == (1 if name == command else 0), name
        if kwargs:
            assert getattr(bot, command).await_args.kwargs == kwargs
        turns = [(m.role, m.content) for m in bot.conversations.get_recent(str(OPERATOR), limit=20)]
        assert [r for r, _ in turns] == ["user", "assistant"], turns
        assert turns[0][1] == text
        assert turns[1][1] == card_shown_memory(card)

    @pytest.mark.asyncio
    async def test_the_branch_goes_through_the_guard_never_to_the_seam(self, bot):
        # The guard on `_cmd_networth` IS the role gate for this reading: a
        # branch that called the seam directly would answer a caller the
        # command refuses. Refused, the seam is never awaited and no card is
        # sent; allowed, the card the seam rendered is what the caller gets.
        bot.conversations = ConversationStore()
        bot.networth_card_text = AsyncMock(return_value="<b>Net worth</b> — a card")
        bot._guard = AsyncMock(return_value=False)
        await bot._handle_message(_update(OPERATOR, "my net worth"), None)
        assert bot._guard.await_count == 1
        assert bot._guard.await_args.args[1] == "networth", bot._guard.await_args
        assert bot.networth_card_text.await_count == 0
        assert not [s for s in bot.sent if "Net worth" in s]
        bot._guard = AsyncMock(return_value=True)
        await bot._handle_message(_update(OPERATOR, "my net worth"), None)
        assert bot.networth_card_text.await_count == 1
        assert bot.networth_card_text.await_args.args[0] == str(OPERATOR)
        assert bot.sent[-1] == "<b>Net worth</b> — a card"

    @pytest.mark.asyncio
    async def test_the_research_command_takes_the_routed_symbol_by_keyword(self):
        # A text message has no `ctx.args`; the branch hands the symbol in by
        # keyword and the guard still runs with the real context.
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
               research_card_text=AsyncMock(return_value="<b>Research: SOL</b>"))
        await TelegramHandler._cmd_research(h, NS(), NS(args=None), symbol="SOL/USDT")
        assert h._guard.await_args.args[1] == "research"
        assert h.research_card_text.await_args.args == ("SOL/USDT",)
        assert h._send.await_args.args[1] == "<b>Research: SOL</b>"
        # The slash form still reads its argument, and no symbol is the usage line.
        h.research_card_text.reset_mock()
        h._send.reset_mock()
        await TelegramHandler._cmd_research(h, NS(), NS(args=["PENDLE"]))
        assert h.research_card_text.await_args.args == ("PENDLE",)
        h.research_card_text.reset_mock()
        h._send.reset_mock()
        await TelegramHandler._cmd_research(h, NS(), NS(args=[]))
        assert h.research_card_text.await_count == 0
        assert "Usage: /research" in h._send.await_args.args[1]


# ── 3. the web's Python path: the same seam, the same gate, recorded ────────

def _web(monkeypatch, role="viewer", denial=None):
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
    h = NS(intent_router=IntentRouter(),
           registry=NS(get=lambda n: None),
           conversations=ConversationStore(),
           users=NS(get_tier=lambda uid: "elite", is_authorized=lambda uid: True,
                    get_role=lambda uid: role, get=lambda uid: {"role": role},
                    permission_denial=lambda uid, perm: denial),
           _llm_chat=None,
           networth_card_text=AsyncMock(return_value="<b>Net worth</b> — read-only"),
           rwa_card_text=AsyncMock(return_value="<b>RWA radar</b> — live venue tickers"),
           research_card_text=AsyncMock(return_value="<b>Research: SOL</b> — dossier"))
    return ug, h


def _turn(ug, h, text):
    async def _json():
        return {"telegram_id": CALLER, "text": text}

    req = NS(app={"tg_handler": h, "engine": _Engine(per_user=True)}, json=_json,
             headers={}, remote="1.2.3.4")
    resp = asyncio.run(ug._chat_turn(req))
    return resp, json.loads(resp.text)


def _assistant(h):
    return "\n".join(m.content for m in h.conversations.get_recent(CALLER, limit=10)
                     if m.role == "assistant")


class TestTheWeb:
    @pytest.mark.parametrize("text, intent, seam, args, kwargs", [
        ("how much am i worth", "networth", "networth_card_text", (CALLER,), {"surface": "web"}),
        ("rwa radar", "rwa", "rwa_card_text", (), {"surface": "web"}),
        ("can you research SOL for me", "research", "research_card_text", ("SOL/USDT",), {}),
    ])
    def test_the_seam_answers_and_the_result_is_recorded(self, monkeypatch, text, intent, seam, args, kwargs):
        ug, h = _web(monkeypatch)
        resp, body = _turn(ug, h, text)
        assert resp.status == 200
        assert body["intent"] == intent
        m = getattr(h, seam)
        assert m.await_count == 1 and m.await_args.args == args and m.await_args.kwargs == kwargs
        assert body["reply_html"] == m.return_value
        rec = _assistant(h)
        assert f"[{intent}] result:" in rec and m.return_value.split(" ")[0].strip("<b>") in rec
        for other in ("networth_card_text", "rwa_card_text", "research_card_text"):
            if other != seam:
                assert getattr(h, other).await_count == 0, other

    def test_the_gate_refuses_before_the_seam_and_records_not_run(self, monkeypatch):
        # RED HERRING: a 403 that reads the seam first has already done the
        # read the gate exists to refuse.
        ug, h = _web(monkeypatch, role="pending", denial="role")
        resp, body = _turn(ug, h, "my net worth")
        assert resp.status == 403 and body["error"] == "insufficient_permissions"
        assert h.networth_card_text.await_count == 0
        assert "[networth] NOT RUN" in _assistant(h)

    def test_a_seam_that_raises_is_named_to_the_person_and_the_model(self, monkeypatch):
        ug, h = _web(monkeypatch)

        async def _boom(*a, **kw):
            raise RuntimeError("channel down: https://internal.host/secret?token=abc")

        h.rwa_card_text = _boom
        resp, body = _turn(ug, h, "rwa radar")
        assert resp.status == 200
        assert "nothing was measured" in body["reply_html"].lower()
        assert "internal.host" not in body["reply_html"] and "token=" not in body["reply_html"]
        assert "[rwa] FAILED" in _assistant(h)

    def test_the_seam_never_names_a_telegram_door_to_a_web_caller(self):
        # The web's words for a channel that did not answer, and for an
        # exchange nobody linked, name no slash command.
        assert "/link" not in TelegramHandler._link_hint("web")
        assert "/link" in TelegramHandler._link_hint("telegram")
        assert "Nothing was read" in TelegramHandler._link_hint("web")
        card = TelegramHandler._format_networth(None, {"connected": False}, surface="web")
        assert "/connect" not in card and "not connected" in card
        assert "/connect" in TelegramHandler._format_networth(None, {"connected": False})


# ── 4. the reading: four words, one copy ─────────────────────────────────────

def _read(store_factory, *, paper_raises=False, balance=None, timeout=False):
    from bot.core import networth_reading as nr

    class _Snap:
        equity_usd = 10140.0
        total_pnl = 140.0

    class _Book:
        def snapshot(self):
            if paper_raises:
                raise RuntimeError("no book")
            return _Snap()

    engine = NS(user_portfolios=NS(get=lambda uid: _Book()))

    async def _balance(venue, fields):
        if timeout:
            await asyncio.sleep(1)
        return balance if balance is not None else {"ok": True, "venue": venue, "equity_usd": 2500.5}

    with patch("bot.core.exchange_credentials.get_credential_store", store_factory), \
         patch("bot.core.exchange_credentials.balance_snapshot", _balance), \
         patch.object(nr, "BALANCE_TIMEOUT_S", 0.01):
        return asyncio.run(nr.networth_reading(engine, "7"))


class TestTheReading:
    def test_a_venue_that_answers_is_read(self):
        store = NS(has=lambda uid: True, get_venue=lambda uid: "bitget", get=lambda uid: {"k": "v"})
        r = _read(lambda: store)
        assert r["paper"] == {"equity_usd": 10140.0, "total_pnl": 140.0, "simulated": True}
        assert r["cex"]["connected"] is True and r["cex"]["equity_usd"] == 2500.5
        card = TelegramHandler._format_networth(r["paper"], r["cex"])
        assert "$10,140.00" in card and "Bitget" in card and "$2,500.50" in card

    def test_no_venue_is_not_connected_and_names_the_door_only_on_telegram(self):
        store = NS(has=lambda uid: False)
        r = _read(lambda: store)
        assert r["cex"] == {"connected": False}
        assert "not connected — /connect" in TelegramHandler._format_networth(r["paper"], r["cex"])
        assert "/connect" not in TelegramHandler._format_networth(r["paper"], r["cex"], surface="web")

    def test_credentials_that_will_not_decrypt_and_a_venue_that_times_out_are_unavailable_with_their_reason(self):
        store = NS(has=lambda uid: True, get_venue=lambda uid: "bybit", get=lambda uid: None)
        r = _read(lambda: store)
        assert r["cex"]["connected"] is True and r["cex"]["equity_usd"] is None
        assert "unavailable (credentials unreadable)" in TelegramHandler._format_networth(None, r["cex"])
        store = NS(has=lambda uid: True, get_venue=lambda uid: "bybit", get=lambda uid: {"k": "v"})
        r = _read(lambda: store, timeout=True)
        assert r["cex"]["equity_usd"] is None and r["cex"]["detail"] == "venue timeout"
        assert "unavailable (venue timeout)" in TelegramHandler._format_networth(None, r["cex"])

    def test_a_store_that_raises_is_could_not_be_read_never_not_connected(self):
        # THE fourth word. The command used to fold this into
        # {"connected": False} and print "not connected — /connect to link
        # one": a store nobody could ask, rendered as an account nobody linked,
        # under a door that re-links it.
        def _boom():
            raise RuntimeError("keyring locked")
        r = _read(_boom)
        assert r["cex"] == {"connected": False, "error": "cex_unavailable"}
        for surface in ("telegram", "web"):
            card = TelegramHandler._format_networth(r["paper"], r["cex"], surface=surface)
            assert "could not be read" in card, card
            assert "not connected" not in card and "/connect" not in card, card

    def test_an_unreadable_paper_book_says_so(self):
        store = NS(has=lambda uid: False)
        r = _read(lambda: store, paper_raises=True)
        assert r["paper"] is None
        assert "no snapshot yet" in TelegramHandler._format_networth(r["paper"], r["cex"])

    def test_the_endpoint_and_the_command_read_the_one_reading(self, monkeypatch):
        from bot.web import user_gateway as ug
        reading = {"paper": {"equity_usd": 1.0, "total_pnl": 0.0, "simulated": True},
                   "cex": {"connected": False, "error": "cex_unavailable"}}
        seen = []

        async def _reading(engine, uid):
            seen.append(uid)
            return reading

        monkeypatch.setattr("bot.core.networth_reading.networth_reading", _reading)
        monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
        req = NS(app={"engine": NS(), "tg_handler": NS()}, query={"telegram_id": "7"})
        body = json.loads(asyncio.run(ug.handle_networth(req)).text)
        assert body["paper"] == reading["paper"] and body["cex"] == reading["cex"]
        assert body["read_only"] is True and "updated_at" in body
        h = NS(engine=NS(), _format_networth=TelegramHandler._format_networth)
        card = asyncio.run(TelegramHandler.networth_card_text(h, "7", surface="web"))
        assert "could not be read" in card
        assert seen == ["7", "7"], "both surfaces asked the one reading"
        # And neither carries its own copy of the fetch any more.
        for fn in (ug.handle_networth, TelegramHandler._cmd_networth, TelegramHandler.networth_card_text):
            assert "balance_snapshot(" not in inspect.getsource(fn), fn.__name__


# ── 5. the other two seams ───────────────────────────────────────────────────

class TestTheOtherSeams:
    def test_the_rwa_seam_reads_off_the_loop_and_says_which_channel_did_not_answer(self, monkeypatch):
        """The seam fetches the card RENDERED and formats nothing.

        It used to pull the payload and hand it to `_format_rwa`, a second
        Python copy of the website's card. The claims are otherwise the same:
        off the event loop, and the channel-down sentence keyed by surface.
        """
        import bot.utils.web_data_pull as wdp
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: None)
        h = NS(_link_hint=TelegramHandler._link_hint)
        tg = asyncio.run(TelegramHandler.rwa_card_text(h))
        web = asyncio.run(TelegramHandler.rwa_card_text(h, surface="web"))
        assert tg == TelegramHandler._WEB_LINK_HINT and "/link" in tg
        assert "/link" not in web and "Nothing was read" in web
        # The card arrives rendered: its <br> become newlines and a tag
        # Telegram does not render is dropped with its text kept.
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: {
            "reply_html": "\U0001f3e6 <b>RWA radar</b><br>Sector: <span>—</span>",
            "intent": "rwa"})
        card = asyncio.run(TelegramHandler.rwa_card_text(h))
        assert "<b>RWA radar</b>" in card and "\n" in card
        assert "<span" not in card and "Sector: —" in card
        # A payload with no card string is an unanswered channel, never an
        # empty card — the rule `web_card_text` states.
        for junk in ({"nonsense": 1}, {"reply_html": None}, {"reply_html": "  "}):
            monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, _j=junk, **k: _j)
            assert asyncio.run(TelegramHandler.rwa_card_text(h, surface="web")) == web, junk
        # The name the seam asks for is the card's own.
        asked = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, *a, **k: asked.append(name) or None)
        asyncio.run(TelegramHandler.rwa_card_text(h))
        assert asked == ["rwa"]

    def test_the_research_seam_reads_off_the_loop_and_names_no_command(self, monkeypatch):
        import bot.utils.web_data_pull as wdp
        asked = []
        monkeypatch.setattr(wdp, "fetch_research", lambda sym: asked.append(sym) or None)
        h = NS(_format_research=TelegramHandler._format_research)
        out = asyncio.run(TelegramHandler.research_card_text(h, "SOL/USDT"))
        assert asked == ["SOL/USDT"] and "No dossier" in out and "/" not in out.replace("/USDT", "")
        monkeypatch.setattr(wdp, "fetch_research", lambda sym: {
            "base": "SOL", "sections": [{"title": "Supply", "html": "x<br>y"}]})
        out = asyncio.run(TelegramHandler.research_card_text(h, "SOL"))
        assert "Research: SOL" in out and "Supply" in out
