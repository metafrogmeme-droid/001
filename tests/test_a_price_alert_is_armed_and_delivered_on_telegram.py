"""The last door row is a command: a price alert typed on Telegram is armed on
the website's alert engine for the caller's linked web account, and a trip
reaches Telegram through the bot's poll of the website's trips queue.

The website owned the whole feature — a parser, a once-a-minute evaluator
over public tickers, delivery by web push ONLY (`app/lib/alerts.js`) — and
the bot had no price-alert code at all; nothing anywhere could send a message
from the website to a Telegram user. So a linked user who armed an alert on
the web was never told on Telegram, and on Telegram the words met a door
notice ("ask the web app in these words"). One store and one evaluator, the
website's; three things added around them:

  * the WORDS are the argument. `/price_alert tell me when BTC drops below
    100k` hands the sentence to the intercept's own parser through the card
    route, so its phrasings are the phrasings, its help sentence answers
    words it could not read, and the delivery sentence is in Telegram's
    words ("I'll message you here…"). With no words it lists yours.
  * a trips queue. When an alert trips the website writes one row beside the
    push it sends — same title, same body — and the bot's proactive monitor
    polls it once a minute and messages the linked Telegram account. The ack
    is three-valued (sent / failed with the exception's class name / nothing
    read), because "sent" acked for a blocked bot would retry forever or lie.
  * an unlinked caller is told NOTHING WAS ARMED: there is no web account to
    hold an alert for them, which is a different sentence from the wallet
    cards' "nothing was read", and from a channel that did not answer.

Deletion stays on the website's Live Feed panel, where the intercept keeps
it too (it has no delete phrasing either), and the card says so. Plant the
payload, drive the pull, the seam, the command, both surfaces, the tables,
the router and the delivery stage; read the words and the store.
"""
from __future__ import annotations

import asyncio
import inspect
import textwrap
import urllib.parse
from types import MethodType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import bot.utils.web_data_pull as wdp
from bot.core.proactive_monitor import ProactiveMonitor, alert_trip_text
from bot.nlp.intent_router import IntentRouter, routed_skill_names
from bot.nlp.skill_memory import card_shown_memory
from bot.nlp.web_reads import WEB_READS
from bot.skills.command_catalog import all_entries
from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import ROLE_PERMISSIONS
from bot.utils.web_data_pull import (
    WEB_CARD_PARAMS,
    WEB_CARDS,
    ack_alert_trips,
    fetch_alert_trips,
    fetch_web_card,
    web_card_text,
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

SENTENCE = "tell me when BTC drops below 100k"
CARD = {"reply_html": "⏰ Alert armed: <b>BTC price below $100,000</b> (now $98,000). "
                      "I'll message you here the moment it trips — one-shot, then it disarms.",
        "intent": "alert_create"}
UNLINKED = {"reply_html": None, "intent": "alerts", "unlinked": True}


@pytest.fixture(name="bot")
def _bot(tmp_path):
    yield from _halt_bot.__wrapped__(tmp_path)


# ── 1. the pull: the sentence travels whole, the queue reads three ways ──────

class TestThePull:
    def test_the_card_is_named_and_takes_the_sentence(self):
        assert "alerts" in WEB_CARDS
        assert WEB_CARD_PARAMS["alerts"] == ("text",)

    def test_the_sentence_travels_whole_and_a_wrong_argument_raises(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        seen = []
        monkeypatch.setattr(wdp, "_request", lambda path, body=None: seen.append(path) or {"ok": 1})
        fetch_web_card("alerts", "770001", text=SENTENCE)
        # 33 characters: the 32 that bounds a token cut this one short.
        assert seen[-1] == "/api/bot/sync/card/alerts?telegram_id=770001&text=" + urllib.parse.quote(SENTENCE)
        fetch_web_card("alerts", "770001", text="x" * 300)
        assert seen[-1].endswith("&text=" + "x" * 240)
        fetch_web_card("alerts", "770001", text="   ")
        assert seen[-1] == "/api/bot/sync/card/alerts?telegram_id=770001", "an empty argument is not sent"
        with pytest.raises(TypeError):
            fetch_web_card("alerts", "770001", stake="5")

    def test_the_trips_queue_reads_none_for_unconfigured_and_unanswered(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "")
        assert fetch_alert_trips() is None
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        for answer in (None, "junk", {"error": "x"}, {"trips": "junk"}):
            monkeypatch.setattr(wdp, "_request", lambda path, body=None, a=answer: a)
            assert fetch_alert_trips() is None, answer
        seen = []
        monkeypatch.setattr(wdp, "_request",
                            lambda path, body=None: seen.append(path) or {"trips": [{"id": 1}, "x", {"id": 2}]})
        assert fetch_alert_trips(limit=7) == [{"id": 1}, {"id": 2}]
        assert seen == ["/api/bot/sync/alerts/pending?limit=7"]

    def test_the_ack_is_a_verdict_about_the_ack(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "")
        assert ack_alert_trips([{"id": 1, "ok": True}]) is False
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        seen = []
        monkeypatch.setattr(wdp, "_request", lambda path, body=None: seen.append((path, body)) or {"ok": True})
        assert ack_alert_trips([]) is False and seen == [], "nothing to ack posts nothing"
        assert ack_alert_trips([{"id": 1, "ok": True}]) is True
        assert seen == [("/api/bot/sync/alerts/ack", {"acks": [{"id": 1, "ok": True}]})]
        for answer in (None, {"ok": False}, {"error": "x"}, "junk"):
            monkeypatch.setattr(wdp, "_request", lambda path, body=None, a=answer: a)
            assert ack_alert_trips([{"id": 1, "ok": True}]) is False, answer


# ── 2. the seam: the card, nothing armed for an unlinked caller, the channel ─

def _host():
    host = NS(_link_hint=TelegramHandler._link_hint, _unlinked_hint=TelegramHandler._unlinked_hint,
              _unlinked_alert_hint=TelegramHandler._unlinked_alert_hint)
    host._web_card_text = MethodType(TelegramHandler._web_card_text, host)
    return host


def _seam(host, *args, **kw):
    return asyncio.run(TelegramHandler.price_alert_card_text(host, *args, **kw))


class TestTheSeam:
    def test_the_card_arrives_with_the_words_and_the_caller(self, monkeypatch):
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="", **kw: seen.append((name, tg, kw)) or dict(CARD))
        assert _seam(_host(), "770001", SENTENCE) == web_card_text(CARD)
        assert _seam(_host(), "770001", "my alerts", surface="web") == web_card_text(CARD)
        assert seen == [("alerts", "770001", {"text": SENTENCE}), ("alerts", "770001", {"text": "my alerts"})]

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_an_unlinked_caller_is_told_nothing_was_armed(self, monkeypatch, surface):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="", **kw: dict(UNLINKED))
        out = _seam(_host(), "770001", SENTENCE, surface=surface)
        assert out == TelegramHandler._unlinked_alert_hint(surface)
        assert "Nothing was armed" in out and "hold an alert" in out
        # A WRITE's unlinked sentence is not the wallet cards' "nothing was
        # read", and not "the channel did not answer" either.
        assert out != TelegramHandler._unlinked_hint(surface)
        assert out != TelegramHandler._link_hint(surface)
        assert "Nothing was read" not in out
        if surface == "telegram":
            assert "not linked" in out and "/link" in out
        else:
            assert "could not map" in out and "/link" not in out and "not linked" not in out

    @pytest.mark.parametrize("answer", [None, {"error": "Card unavailable"}, {"reply_html": ""}, "junk"])
    def test_a_channel_that_did_not_answer_is_the_link_hint(self, monkeypatch, answer):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="", **kw: answer)
        assert _seam(_host(), "1", SENTENCE) == TelegramHandler._WEB_LINK_HINT
        assert _seam(_host(), "1", SENTENCE, surface="web") == TelegramHandler._link_hint("web")


# ── 3. the command: the words, the list default, the gate ───────────────────

class TestTheCommand:
    @pytest.mark.asyncio
    async def test_the_kwarg_wins_then_the_typed_words_then_the_list(self):
        h = NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(), _get_tg_id=lambda u: "770001",
               price_alert_card_text=AsyncMock(return_value="<b>card</b>"))
        await TelegramHandler._cmd_price_alert(h, NS(), NS(args=["my", "alerts"]), text=SENTENCE)
        h.price_alert_card_text.assert_awaited_once_with("770001", SENTENCE)
        h.price_alert_card_text.reset_mock()
        await TelegramHandler._cmd_price_alert(h, NS(), NS(args=["alert", "me", "if", "SOL", "hits", "200"]))
        h.price_alert_card_text.assert_awaited_once_with("770001", "alert me if SOL hits 200")
        h.price_alert_card_text.reset_mock()
        await TelegramHandler._cmd_price_alert(h, NS(), NS(args=[]))
        h.price_alert_card_text.assert_awaited_once_with("770001", "my alerts")
        assert h._send.await_args.args[1] == "<b>card</b>"
        assert h._guard.await_args.args[1] == "price_alert"

    @pytest.mark.asyncio
    async def test_a_refused_gate_reads_nothing_and_sends_nothing(self):
        h = NS(_guard=AsyncMock(return_value=False), _send=AsyncMock(), _get_tg_id=lambda u: "1",
               price_alert_card_text=AsyncMock(return_value="card"))
        await TelegramHandler._cmd_price_alert(h, NS(), NS(args=[]), text=SENTENCE)
        assert h.price_alert_card_text.await_count == 0 and h._send.await_count == 0


# ── 4. the tables agree, and the door table holds one row ───────────────────

class TestTheTables:
    def test_registered_guarded_catalogued_and_permissioned(self):
        src = inspect.getsource(TelegramHandler.build_app)
        assert '("price_alert", self._cmd_price_alert)' in src
        assert '@guard("price_alert")' in inspect.getsource(TelegramHandler._cmd_price_alert)
        assert "price_alert" in all_entries()
        assert WEB_ROUTED_PERMISSION["price_alert"] == "price_alert"
        assert ROUTED_INTENT_SEAM["price_alert"] == "price_alert_card_text"
        for role in ("trader", "paper", "viewer"):
            assert "price_alert" in ROLE_PERMISSIONS[role], role
        assert "price_alert" in routed_skill_names()

    def test_the_door_table_is_down_to_the_idle_yield_read(self):
        assert set(WEB_READS) == {"idle_yield"}
        assert "price_alert" not in WEB_READS

    def test_the_command_branch_sits_above_the_door_branch(self):
        src = textwrap.dedent(inspect.getsource(TelegramHandler._handle_message))
        assert src.index('intent.skill == "price_alert"') < src.index("intent.skill in WEB_READS")

    def test_the_web_seam_is_in_the_table(self):
        gw = pytest.importorskip("bot.web.user_gateway", reason="web gateway needs aiohttp")
        assert "price_alert" in gw._WEB_SEAM


# ── 5. the router: the intercept's trigger words, anchored where it anchors ──

@pytest.fixture(scope="module")
def router():
    return IntentRouter()


@pytest.mark.parametrize("text", [
    SENTENCE, "alert me if sol rises above 200", "notify me when eth hits 3000",
    "let me know when eth moves 5%", "ping me if btc breaks 100k", "warn me when sol falls below 100",
    "alert me every time btc moves 5%", "please tell me when doge pumps 10%",
    "my alerts", "show my alerts", "list active alerts", "price alerts", "set an alert for eth at 3000",
    # The intercept claims this too, and answers its help card — parity.
    "tell me when the scan finishes",
])
def test_the_phrasing_routes_to_the_command_intent(router, text):
    i = router.classify_rules(text)
    assert i is not None and i.matched and i.skill == "price_alert", text


@pytest.mark.parametrize("text", [
    "anomaly alerts every 2h",            # /alerts, the anomaly scope
    "can you tell me when btc drops?",    # the intercept anchors at the start; so does this rule
    "what is a price alert",
])
def test_the_neighbours_stay_where_they_were(router, text):
    i = router.classify_rules(text)
    assert not (i is not None and i.matched and i.skill == "price_alert"), text


# ── 6. Telegram: the branch, the card, the unlinked sentence ────────────────

class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", [SENTENCE, "my alerts", "alert me if sol rises above 200"])
    async def test_the_branch_hands_the_words_to_the_command_and_records_the_card(self, bot, text):
        store = _store(bot)
        bot._cmd_price_alert = AsyncMock()
        await bot._handle_message(_update(OPERATOR, text), None)
        bot._cmd_price_alert.assert_awaited_once()
        assert bot._cmd_price_alert.await_args.kwargs == {"text": text}
        turns = [(m.role, m.content) for m in store.get_recent(str(OPERATOR), limit=5)]
        assert turns == [("user", text), ("assistant", card_shown_memory("price_alert"))]
        assert bot.registry.dispatched == []

    @pytest.mark.asyncio
    async def test_end_to_end_the_websites_answer_reaches_the_chat(self, bot, monkeypatch):
        # RED HERRING: before this slice the same words sent the door notice
        # ("is handled by the RUNECLAW web app's chat … ask it there").
        seen = []
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda name, tg="", **kw: seen.append((name, tg, kw)) or dict(CARD))
        await bot._handle_message(_update(OPERATOR, SENTENCE), None)
        assert bot.sent[-1] == web_card_text(CARD)
        assert "web app's chat" not in bot.sent[-1] and "ask it there" not in bot.sent[-1]
        assert seen == [("alerts", str(OPERATOR), {"text": SENTENCE})]

    @pytest.mark.asyncio
    async def test_end_to_end_an_unlinked_caller_is_told_nothing_was_armed(self, bot, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, tg="", **kw: dict(UNLINKED))
        await bot._handle_message(_update(OPERATOR, SENTENCE), None)
        assert bot.sent[-1] == TelegramHandler._unlinked_alert_hint("telegram")
        assert "/link" in bot.sent[-1] and "Nothing was armed" in bot.sent[-1]


# ── 7. the web: the seam under the gate, the words handed over ──────────────

class TestTheWeb:
    @pytest.mark.parametrize("text", ["alert me if sol rises above 200", "my alerts"])
    def test_the_seam_answers_with_the_words_and_the_result_is_recorded(self, monkeypatch, text):
        ug, h = _web(monkeypatch)
        h.price_alert_card_text = AsyncMock(return_value="<b>alerts</b> — the website's answer")
        resp, body = _turn(ug, h, text)
        assert resp.status == 200 and body["intent"] == "price_alert"
        assert h.price_alert_card_text.await_count == 1
        assert h.price_alert_card_text.await_args.args == (CALLER, text)
        assert h.price_alert_card_text.await_args.kwargs == {"surface": "web"}
        assert body["reply_html"] == h.price_alert_card_text.return_value
        assert "[price_alert] result:" in _assistant(h)

    def test_the_gate_refuses_before_the_seam_and_records_not_run(self, monkeypatch):
        ug, h = _web(monkeypatch, role="pending", denial="role")
        h.price_alert_card_text = AsyncMock(return_value="card")
        resp, body = _turn(ug, h, SENTENCE)
        assert resp.status == 403 and body["error"] == "insufficient_permissions"
        assert h.price_alert_card_text.await_count == 0
        assert "not run" in _assistant(h).lower()


# ── 8. the delivery stage: three outcomes, acked in their own words ──────────

TRIP = {"id": 7, "alert_id": 3, "telegram_id": "770001", "title": "⏰ BTC alert tripped",
        "body": "BTC price below $100,000 — BTC is now $99,850.", "tripped_at": "2026-09-14T19:00:00Z"}


def _monitor(rows, dm=None, acked=True):
    mon = ProactiveMonitor(NS())
    calls = {"fetch": 0, "acks": []}

    def _fetch(limit=50):
        calls["fetch"] += 1
        return rows

    def _ack(acks):
        calls["acks"].append(list(acks))
        return acked

    mon._test_fetch, mon._test_ack = _fetch, _ack
    if dm is not None:
        mon.set_dm_fn(dm)
    return mon, calls


def _run(mon, calls, monkeypatch):
    monkeypatch.setattr(wdp, "fetch_alert_trips", mon._test_fetch)
    monkeypatch.setattr(wdp, "ack_alert_trips", mon._test_ack)
    mon._last_trip_poll = 0.0
    asyncio.run(mon._deliver_web_alert_trips())


class TestTheDelivery:
    def test_the_stage_is_in_the_loop_and_the_words_are_the_websites(self):
        src = inspect.getsource(ProactiveMonitor.run)
        assert "self._deliver_web_alert_trips" in src
        text = alert_trip_text(TRIP)
        assert text == "<b>⏰ BTC alert tripped</b>\nBTC price below $100,000 — BTC is now $99,850."
        assert alert_trip_text({"title": "<b>x", "body": "a & b"}) == "<b>&lt;b&gt;x</b>\na &amp; b"
        assert alert_trip_text({}) == "⏰ A price alert of yours tripped, and its words did not travel with it."

    def test_without_a_dm_function_nothing_is_read(self, monkeypatch):
        mon, calls = _monitor([TRIP])
        _run(mon, calls, monkeypatch)
        assert calls["fetch"] == 0 and calls["acks"] == []

    def test_an_unreadable_queue_sends_nothing_and_acks_nothing(self, monkeypatch):
        sent = []
        mon, calls = _monitor(None, dm=AsyncMock(side_effect=lambda c, t: sent.append((c, t))))
        _run(mon, calls, monkeypatch)
        assert calls["fetch"] == 1 and sent == [] and calls["acks"] == []

    def test_a_trip_is_sent_once_and_acked_sent(self, monkeypatch):
        sent = []
        dm = AsyncMock(side_effect=lambda c, t: sent.append((c, t)))
        mon, calls = _monitor([TRIP, dict(TRIP, id=8, telegram_id="770002")], dm=dm)
        _run(mon, calls, monkeypatch)
        assert sent == [("770001", alert_trip_text(TRIP)), ("770002", alert_trip_text(TRIP))]
        assert calls["acks"] == [[{"id": 7, "ok": True}, {"id": 8, "ok": True}]]
        assert list(mon._delivered_trip_ids) == [], "a landed ack forgets the ids"

    def test_a_send_that_raises_is_acked_failed_with_the_class_and_never_the_text(self, monkeypatch):
        dm = AsyncMock(side_effect=RuntimeError("bot was blocked by the user: https://x?token=abc"))
        mon, calls = _monitor([TRIP], dm=dm)
        _run(mon, calls, monkeypatch)
        assert calls["acks"] == [[{"id": 7, "ok": False, "error": "RuntimeError"}]]
        assert "token" not in str(calls["acks"])

    def test_a_row_with_no_telegram_id_is_acked_failed_not_sent(self, monkeypatch):
        sent = []
        mon, calls = _monitor([dict(TRIP, telegram_id="")], dm=AsyncMock(side_effect=lambda c, t: sent.append(t)))
        _run(mon, calls, monkeypatch)
        assert sent == [] and calls["acks"] == [[{"id": 7, "ok": False, "error": "no telegram id"}]]

    def test_an_ack_that_did_not_land_keeps_the_id_so_the_trip_is_not_sent_twice(self, monkeypatch):
        sent = []
        mon, calls = _monitor([TRIP], dm=AsyncMock(side_effect=lambda c, t: sent.append(t)), acked=False)
        _run(mon, calls, monkeypatch)
        assert len(sent) == 1 and list(mon._delivered_trip_ids) == [7]
        # The website still lists the trip; it is acked again, never sent again.
        _run(mon, calls, monkeypatch)
        assert len(sent) == 1
        assert calls["acks"] == [[{"id": 7, "ok": True}], [{"id": 7, "ok": True}]]

    def test_the_poll_is_once_a_minute(self, monkeypatch):
        mon, calls = _monitor([], dm=AsyncMock())
        _run(mon, calls, monkeypatch)
        asyncio.run(mon._deliver_web_alert_trips())
        assert calls["fetch"] == 1, "a second pass inside the interval reads nothing"

    def test_the_installed_dm_function_raises_and_scrubs(self):
        """`alerts_monitor.start_monitor` installs it beside the alert sender,
        which swallows; this one must not, and it goes through the outbound
        scrub because `_send` never sees it. Read off the source: the drive
        needs a whole handler, and the two claims are one call each."""
        from bot.skills import alerts_monitor
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(alerts_monitor))
        i = src.index("async def _dm_fn(")
        body = src[i:src.index("self.monitor.set_dm_fn(_dm_fn)")]
        assert "reply_safe(text)" in body and "except" not in body
