"""Six more of the website chat's own cards are Telegram commands — the four
public reads (/replay, /letter, /venue_router, /meme_radar) and the two
wallet reads (/wallet, /defi) — through the same card route, and the door
table is down to the one WRITE.

The four public cards have no account in them (the operator agent's record
mirrored at the caller's stake, the agent's letter, the funding-cost router,
DEXScreener's feed), so a door notice was the honest answer only until the
card could be fetched. The two wallet cards ARE the caller's linked wallet:
the website maps their Telegram id to a web account and reads THAT wallet,
and answers `unlinked` for an id it cannot map — a third fact beside "a card"
and "the channel did not answer", with its own sentence on each transport,
never a hedge and never a guessed wallet.

Three of the cards take one argument the intercept reads out of the sentence
— a stake, an asset, a chain — and `bot/nlp/web_card_args.py` reads it the
same way here, so "best venue for BTC" narrows on Telegram exactly as it
does on the website. And the forwarding boundary keeps only the tags
Telegram renders: a `<span>` on a website card arrives without the span
rather than not at all.

Plant the payload, drive the readers, the seams, the commands, both surfaces
and the tables; read the words and the store.
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
from bot.nlp.intent_router import IntentRouter, _is_social_message, routed_skill_names
from bot.nlp.skill_memory import card_shown_memory
from bot.nlp.web_card_args import replay_stake, stake_from_token, venue_base, wallet_chain
from bot.nlp.web_reads import WEB_READS
from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import ROLE_PERMISSIONS
from bot.utils.web_data_pull import (
    WEB_CARD_PARAMS,
    WEB_CARDS,
    fetch_web_card,
    web_card_text,
    web_card_unlinked,
)
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

CARDS = ("replay", "letter", "venue_router", "meme_radar", "wallet", "defi")
PUBLIC = ("replay", "letter", "venue_router", "meme_radar")
PERSONAL = ("wallet", "defi")
CARD = {"reply_html": "📽️ <b>What-if replay</b> — $1,000 on every agent trade<br>• Win rate: <b>50%</b><br>"
                      "<i>Hypothetical mirror.</i>", "intent": "replay"}
UNLINKED = {"reply_html": None, "intent": "wallet", "unlinked": True}


@pytest.fixture(name="bot")
def _bot(tmp_path):
    yield from _halt_bot.__wrapped__(tmp_path)


# ── 1. the argument readers mirror the intercepts' capture groups ──────────

class TestTheArgumentReaders:
    @pytest.mark.parametrize("text,stake", [
        ("replay every signal with $1k", 1000.0), ("what if i'd taken every trade with 500", 500.0),
        ("replay all positions with 2m", 2_000_000.0), ("replay every signal with $2,500", 2500.0),
        ("replay every signal", None), ("replay", None), ("", None),
    ])
    def test_the_stake_a_what_if_ask_names(self, text, stake):
        assert replay_stake(text) == stake

    @pytest.mark.parametrize("token,stake", [("500", 500.0), ("$1k", 1000.0), ("2m", 2_000_000.0),
                                             ("1,000", 1000.0), ("abc", None), ("", None), ("0", None), ("-5", None)])
    def test_a_typed_stake_token(self, token, stake):
        assert stake_from_token(token) == stake

    @pytest.mark.parametrize("text,base", [
        ("best venue for BTC", "BTC"), ("cheapest exchange to short ethusdt", "ETH"),
        ("best venue to be long sol", "SOL"), ("venue router", ""), ("cheapest funding", ""), ("", ""),
    ])
    def test_the_asset_a_routing_ask_names(self, text, base):
        assert venue_base(text) == base

    @pytest.mark.parametrize("text,chain", [
        ("my wallet on base", "base"), ("wallet holdings on Arbitrum", "arbitrum"),
        ("on-chain balance on polygon", "polygon"), ("my wallet", ""), ("wallet balance", ""), ("", ""),
    ])
    def test_the_chain_a_wallet_ask_narrows_to(self, text, chain):
        assert wallet_chain(text) == chain


# ── 2. the pull: nine names, three arguments, one unlinked answer ──────────

class TestThePull:
    def test_the_nine_names_and_the_three_arguments(self):
        assert WEB_CARDS == ("nft", "spot", "airdrops", "replay", "letter", "venue_router",
                             "meme_radar", "wallet", "defi", "alerts")
        assert WEB_CARD_PARAMS == {"replay": ("stake",), "venue_router": ("base",), "wallet": ("chain",),
                                   "alerts": ("text",)}

    def test_the_paths_carry_the_argument_only_when_given(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        calls = []
        monkeypatch.setattr(wdp, "_request", lambda path, body=None: calls.append(path) or {"ok": 1})
        fetch_web_card("replay", stake="500")
        fetch_web_card("replay", stake=None)
        fetch_web_card("replay")
        fetch_web_card("venue_router", base="BTC")
        fetch_web_card("venue_router", base="")
        fetch_web_card("wallet", "770001", chain="base")
        fetch_web_card("wallet", "770001", chain="")
        fetch_web_card("defi", "770001")
        fetch_web_card("letter")
        fetch_web_card("meme_radar")
        assert calls == ["/api/bot/sync/card/replay?stake=500", "/api/bot/sync/card/replay",
                         "/api/bot/sync/card/replay", "/api/bot/sync/card/venue_router?base=BTC",
                         "/api/bot/sync/card/venue_router",
                         "/api/bot/sync/card/wallet?telegram_id=770001&chain=base",
                         "/api/bot/sync/card/wallet?telegram_id=770001",
                         "/api/bot/sync/card/defi?telegram_id=770001",
                         "/api/bot/sync/card/letter", "/api/bot/sync/card/meme_radar"]

    def test_an_argument_a_card_does_not_take_is_a_programming_error_not_a_dropped_value(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        monkeypatch.setattr(wdp, "_request", lambda *a, **k: pytest.fail("must not be called"))
        for name, kw in (("letter", {"stake": "5"}), ("wallet", {"base": "BTC"}), ("nft", {"chain": "base"})):
            with pytest.raises(TypeError):
                fetch_web_card(name, **kw)

    def test_the_argument_is_quoted_and_bounded(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        calls = []
        monkeypatch.setattr(wdp, "_request", lambda path, body=None: calls.append(path) or {})
        fetch_web_card("venue_router", base="a&b=c" + "x" * 40)
        # 32 characters survive: the five of "a&b=c" and twenty-seven x's.
        assert calls == ["/api/bot/sync/card/venue_router?base=a%26b%3Dc" + "x" * 27]

    def test_unlinked_is_read_as_its_own_fact(self):
        assert web_card_unlinked(UNLINKED) is True
        for other in (CARD, {"reply_html": "x", "unlinked": False}, {"unlinked": "yes"}, None, "junk", {}):
            assert web_card_unlinked(other) is False, other
        assert web_card_text(UNLINKED) is None


class TestTheForwardingBoundary:
    def test_telegrams_tags_pass_and_every_other_tag_leaves_its_text(self):
        html = ('👛 <b>Wallet</b><br><span class="muted">Arbitrum unreadable right now (RPC).</span>'
                '<br><i>read-only</i> <code>0xab</code> <a href="https://x">link</a><p>para</p>')
        assert web_card_text({"reply_html": html}) == (
            "👛 <b>Wallet</b>\nArbitrum unreadable right now (RPC).\n<i>read-only</i> <code>0xab</code> linkpara")

    def test_an_escaped_angle_bracket_is_text_and_stays(self):
        assert web_card_text({"reply_html": "• <b>&lt;b</b> (Base)"}) == "• <b>&lt;b</b> (Base)"


# ── 3. the seams: the card, the channel, and the unlinked caller ──────────

def _host():
    host = NS(_link_hint=TelegramHandler._link_hint, _unlinked_hint=TelegramHandler._unlinked_hint)
    host._web_card_text = MethodType(TelegramHandler._web_card_text, host)
    return host


def _seam(name, host, *args, **kw):
    return asyncio.run(getattr(TelegramHandler, f"{name}_card_text")(host, *args, **kw))


class TestTheSeams:
    def test_each_seam_hands_its_argument_over_and_renders_the_card(self, monkeypatch):
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="", **kw: seen.append((name, tg, kw)) or dict(CARD, intent=name))
        h = _host()
        assert _seam("replay", h, 500.0) == web_card_text(CARD)
        assert _seam("replay", h) == web_card_text(CARD)
        assert _seam("letter", h) == web_card_text(CARD)
        assert _seam("venue_router", h, "BTC") == web_card_text(CARD)
        assert _seam("venue_router", h) == web_card_text(CARD)
        assert _seam("meme_radar", h) == web_card_text(CARD)
        assert _seam("wallet", h, "770001", "base") == web_card_text(CARD)
        assert _seam("wallet", h, "770001") == web_card_text(CARD)
        assert _seam("defi", h, "770001") == web_card_text(CARD)
        assert seen == [("replay", "", {"stake": "500"}), ("replay", "", {"stake": None}),
                        ("letter", "", {}), ("venue_router", "", {"base": "BTC"}),
                        ("venue_router", "", {"base": ""}), ("meme_radar", "", {}),
                        ("wallet", "770001", {"chain": "base"}), ("wallet", "770001", {"chain": ""}),
                        ("defi", "770001", {})]

    def test_a_stake_travels_as_the_number_the_caller_typed(self, monkeypatch):
        # The route reads the parameter with parseFloat, so the spelling only
        # has to round-trip — and a six-significant-digit format does not:
        # ``:g`` sent 12345.67 as "12345.7" and 999999.99 as "1e+06", the
        # caller's own figure printed back on the card as a different one.
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="", **kw: seen.append(kw) or dict(CARD))
        h = _host()
        for stake in (1000.0, 2_000_000.0, 2500.5, 12345.67, 999999.99, 1_234_567.0, 1.1 * 1e3):
            _seam("replay", h, stake)
        assert seen == [{"stake": "1000"}, {"stake": "2000000"}, {"stake": "2500.5"}, {"stake": "12345.67"},
                        {"stake": "999999.99"}, {"stake": "1234567"}, {"stake": "1100"}]
        for sent, typed in zip(seen, (1000.0, 2_000_000.0, 2500.5, 12345.67, 999999.99, 1_234_567.0, 1100.0)):
            assert float(sent["stake"]) == typed

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_an_unlinked_caller_is_told_so_in_the_transports_words(self, monkeypatch, surface):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="", **kw: dict(UNLINKED, intent=name))
        h = _host()
        for name, args in (("wallet", ("770001",)), ("defi", ("770001",))):
            out = _seam(name, h, *args, surface=surface)
            assert out == TelegramHandler._unlinked_hint(surface)
            assert "Nothing was read" in out
            # "Not linked" is a Telegram fact. A web caller is mapped by
            # construction (lib/identity.js: their Telegram id, or
            # `web:<uid>`), so there the honest sentence is that the website
            # found no account for the identity it resolved itself.
            if surface == "telegram":
                assert "not linked" in out and "/link" in out
            else:
                assert "could not map" in out and "not linked" not in out and "/link" not in out
            assert out != TelegramHandler._link_hint(surface), "unlinked is not 'the channel did not answer'"
            assert "wallet" in out.lower() and "$" not in out

    @pytest.mark.parametrize("answer", [None, {"error": "Card unavailable"}, {"reply_html": ""}, "junk"])
    def test_a_channel_that_did_not_answer_is_the_link_hint_on_every_card(self, monkeypatch, answer):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="", **kw: answer)
        h = _host()
        for name, args in (("replay", ()), ("letter", ()), ("venue_router", ()), ("meme_radar", ()),
                           ("wallet", ("1",)), ("defi", ("1",))):
            assert _seam(name, h, *args) == TelegramHandler._WEB_LINK_HINT
            assert _seam(name, h, *args, surface="web") == TelegramHandler._link_hint("web")

    @pytest.mark.asyncio
    async def test_each_command_reads_its_argument_from_the_kwarg_or_the_typed_token(self):
        # /replay: the routed kwarg wins, then the typed token, then nothing.
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
               replay_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_replay(h, NS(), NS(args=["$2k"]), stake=500.0)
        h.replay_card_text.assert_awaited_once_with(500.0)
        h.replay_card_text.reset_mock()
        await TelegramHandler._cmd_replay(h, NS(), NS(args=["$2k"]))
        h.replay_card_text.assert_awaited_once_with(2000.0)
        h.replay_card_text.reset_mock()
        await TelegramHandler._cmd_replay(h, NS(), NS(args=["abc"]))
        h.replay_card_text.assert_awaited_once_with(None)
        assert h._guard.await_args.args[1] == "replay"
        # /venue_router BTC and the routed base.
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
               venue_router_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_venue_router(h, NS(), NS(args=["btc"]))
        h.venue_router_card_text.assert_awaited_once_with("btc")
        h.venue_router_card_text.reset_mock()
        await TelegramHandler._cmd_venue_router(h, NS(), NS(args=[]), base="ETH")
        h.venue_router_card_text.assert_awaited_once_with("ETH")
        assert h._guard.await_args.args[1] == "venue_router"
        # /wallet base and the routed chain, always for THIS caller.
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(), _get_tg_id=lambda u: "770001",
               wallet_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_wallet(h, NS(), NS(args=["Base"]))
        h.wallet_card_text.assert_awaited_once_with("770001", "base")
        h.wallet_card_text.reset_mock()
        await TelegramHandler._cmd_wallet(h, NS(), NS(args=[]), chain="arbitrum")
        h.wallet_card_text.assert_awaited_once_with("770001", "arbitrum")
        assert h._guard.await_args.args[1] == "wallet"
        for name in ("letter", "meme_radar"):
            h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
                   **{f"{name}_card_text": AsyncMock(return_value=f"<b>{name}</b>")})
            await getattr(TelegramHandler, f"_cmd_{name}")(h, NS(), NS(args=[]))
            getattr(h, f"{name}_card_text").assert_awaited_once_with()
            assert h._send.await_args.args[1] == f"<b>{name}</b>"
            assert h._guard.await_args.args[1] == name
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(), _get_tg_id=lambda u: "770001",
               defi_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_defi(h, NS(), NS(args=[]))
        h.defi_card_text.assert_awaited_once_with("770001")
        # A refused gate reads nothing and sends nothing.
        h = NS(_guard=AsyncMock(return_value=False), _send=AsyncMock(), _get_tg_id=lambda u: "1",
               wallet_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_wallet(h, NS(), NS(args=[]))
        assert h.wallet_card_text.await_count == 0 and h._send.await_count == 0


# ── 4. the tables agree, and the door table holds the one write ───────────

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
            title, audience, _desc = entries[name]
            assert audience == "user", (name, audience)
        assert entries["venue_router"][0] == entries["meme_radar"][0] == "🌍 Market context"
        assert entries["replay"][0] == entries["letter"][0] == entries["wallet"][0] == entries["defi"][0] \
            == "💼 Portfolio & record"

    def test_the_roles_that_hold_exposure_hold_these(self):
        for role in ("trader", "paper", "viewer"):
            assert set(CARDS) <= ROLE_PERMISSIONS[role], role
        assert not set(CARDS) & ROLE_PERMISSIONS["pending"]

    def test_the_three_tables_agree_and_the_door_table_holds_the_one_write(self):
        from bot.web import user_gateway as ug
        assert set(CARDS) <= set(ug._WEB_SEAM)
        assert set(CARDS) <= set(routed_skill_names())
        for name in CARDS:
            assert WEB_ROUTED_PERMISSION[name] == name
            assert ROUTED_INTENT_SEAM[name] == f"{name}_card_text"
            assert ug._WEB_SKILL_PERMISSION[name] == name
        assert set(WEB_READS) == {"idle_yield"}, "a door notice over a read that exists"
        assert "idleyield" not in WEB_CARDS and "alerts" in WEB_CARDS

    def test_the_command_branches_sit_above_the_door_branch(self):
        src = textwrap.dedent(inspect.getsource(TelegramHandler._handle_message))
        door = src.index("if intent.skill in WEB_READS:")
        for name in CARDS:
            assert src.index(f'if intent.skill == "{name}":') < door, name


# ── 5. the router: the same phrasings, now a command's, and "my wallet" ────

ROWS = [
    ("replay every signal with $1k", "replay"), ("what-if replay", "replay"),
    ("show me this week's letter", "letter"), ("weekly letter", "letter"),
    ("best venue for BTC", "venue_router"), ("venue router", "venue_router"),
    ("meme radar", "meme_radar"), ("dexscreener", "meme_radar"),
    ("my wallet", "wallet"), ("wallet balance", "wallet"), ("my wallet on base", "wallet"),
    ("on-chain holdings on arbitrum", "wallet"),
    ("my defi positions", "defi"), ("health factor", "defi"),
]


@pytest.mark.parametrize("text,intent", ROWS, ids=[t for t, _ in ROWS])
def test_the_phrasing_routes_to_the_command_intent(text, intent):
    i = IntentRouter().classify_rules(text)
    assert i is not None and i.matched and i.skill == intent and i.confidence >= 0.8
    assert not _is_social_message(text), text


@pytest.mark.parametrize("text,stays", [("balance", "get_portfolio"), ("my portfolio", "get_portfolio"),
                                        ("run a backtest", "run_backtest"), ("rwa radar", "rwa")])
def test_the_neighbours_stay_where_they_were(text, stays):
    i = IntentRouter().classify_rules(text)
    assert i is not None and i.matched and i.skill == stays, (text, getattr(i, "skill", None))


def test_a_bare_wallet_and_an_education_question_are_the_models_not_the_greeters():
    for text in ("wallet", "what is a wallet", "what is defi"):
        i = IntentRouter().classify_rules(text)
        assert not (i is not None and i.matched and i.skill in CARDS), text
        assert not _is_social_message(text), text


# ── 6. Telegram: the guarded command with its argument, the card recorded ──

class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text,name,kwargs", [
        ("replay every signal with $1k", "replay", {"stake": 1000.0}),
        ("replay every signal", "replay", {"stake": None}),
        ("show me this week's letter", "letter", {}),
        ("best venue for BTC", "venue_router", {"base": "BTC"}),
        ("venue router", "venue_router", {"base": ""}),
        ("meme radar", "meme_radar", {}),
        ("my wallet on base", "wallet", {"chain": "base"}),
        ("wallet balance", "wallet", {"chain": ""}),
        ("my defi positions", "defi", {}),
    ])
    async def test_the_branch_dispatches_the_command_with_its_argument_and_records(self, bot, text, name, kwargs):
        store = _store(bot)
        for other in CARDS:
            setattr(bot, f"_cmd_{other}", AsyncMock())
        await bot._handle_message(_update(OPERATOR, text), None)
        for other in CARDS:
            assert getattr(bot, f"_cmd_{other}").await_count == (1 if other == name else 0), other
        assert getattr(bot, f"_cmd_{name}").await_args.kwargs == kwargs
        turns = [(m.role, m.content) for m in store.get_recent(str(OPERATOR), limit=5)]
        assert turns == [("user", text), ("assistant", card_shown_memory(name))]
        assert bot.registry.dispatched == []

    @pytest.mark.asyncio
    async def test_end_to_end_the_websites_card_reaches_the_chat(self, bot, monkeypatch):
        # RED HERRING: before this slice the same words sent the door notice
        # ("is handled by the RUNECLAW web app's chat … ask it there").
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="", **kw: seen.append((name, tg, kw)) or dict(CARD, intent=name))
        await bot._handle_message(_update(OPERATOR, "replay every signal with $500"), None)
        assert bot.sent[-1] == web_card_text(CARD)
        assert "web app's chat" not in bot.sent[-1] and "<br" not in bot.sent[-1]
        assert seen == [("replay", "", {"stake": "500"})]

    @pytest.mark.asyncio
    async def test_end_to_end_an_unlinked_caller_is_told_so_and_the_wallet_card_names_them(self, bot, monkeypatch):
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="", **kw: seen.append((name, tg, kw)) or dict(UNLINKED, intent=name))
        await bot._handle_message(_update(OPERATOR, "my wallet on base"), None)
        assert bot.sent[-1] == TelegramHandler._unlinked_hint("telegram")
        assert "/link" in bot.sent[-1]
        assert seen == [("wallet", str(OPERATOR), {"chain": "base"})]


# ── 7. the web: the seam under the gate, the argument from the words ───────

class TestTheWeb:
    @pytest.mark.parametrize("text,intent,args,kwargs", [
        ("what if i'd taken every trade with $500", "replay", (500.0,), {"surface": "web"}),
        ("replay every signal", "replay", (None,), {"surface": "web"}),
        ("weekly letter", "letter", (), {"surface": "web"}),
        ("cheapest exchange to short eth", "venue_router", ("ETH",), {"surface": "web"}),
        ("dexscreener", "meme_radar", (), {"surface": "web"}),
        ("wallet holdings on arbitrum", "wallet", (CALLER, "arbitrum"), {"surface": "web"}),
        ("health factor", "defi", (CALLER,), {"surface": "web"}),
    ])
    def test_the_seam_answers_with_the_argument_and_the_result_is_recorded(
            self, monkeypatch, text, intent, args, kwargs):
        ug, h = _web(monkeypatch)
        for name in CARDS:
            setattr(h, f"{name}_card_text", AsyncMock(return_value=f"<b>{name}</b> — the website's card"))
        resp, body = _turn(ug, h, text)
        assert resp.status == 200 and body["intent"] == intent
        m = getattr(h, f"{intent}_card_text")
        assert m.await_count == 1 and m.await_args.args == args and m.await_args.kwargs == kwargs
        assert body["reply_html"] == m.return_value
        assert f"[{intent}] result:" in _assistant(h)
        for other in CARDS:
            if other != intent:
                assert getattr(h, f"{other}_card_text").await_count == 0, other

    def test_the_gate_refuses_before_the_seam_and_records_not_run(self, monkeypatch):
        ug, h = _web(monkeypatch, role="pending", denial="role")
        for name in CARDS:
            setattr(h, f"{name}_card_text", AsyncMock(return_value="card"))
        resp, body = _turn(ug, h, "my wallet")
        assert resp.status == 403 and body["error"] == "insufficient_permissions"
        assert h.wallet_card_text.await_count == 0
        assert "not run" in _assistant(h).lower()
