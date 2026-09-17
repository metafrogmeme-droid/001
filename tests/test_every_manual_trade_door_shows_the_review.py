"""EVERY DOOR THAT PROPOSES A MANUAL TRADE CARRIES THE SECOND OPINION.

The co-pilot's only door was ``POST /gateway/trade/copilot``, and that
endpoint's only caller is the dashboard ticket form's Review BUTTON. So the
review was a property of ONE CLIENT's preview rather than of the proposal, and
driven, every other way to reach a Confirm button offered the order with no
second opinion of any kind:

  * ``/trade`` on Telegram — the door the act-intent notice NAMES, and the door
    the free-text grammar path rewrites to and delegates to — under a line
    reading *"Reduced risk checks for manual orders"*: a claim ABOUT checking
    with no statement of what was checked;
  * the web CHAT grammar branch and the api bridge, both through
    ``_propose_from_text``;
  * the dashboard's own confirm MODAL, the last screen before a real order.

The fix is at the BOUNDARY, the ``_fmt_price(None)`` rule: the reading rides on
the PROPOSAL, so a door added tomorrow inherits it instead of having to
remember. ``copilot_context.review_ticket`` is the ONE assembly and this file
proves that by PATCHING it and reading what each door says.

What is driven, in order of what a wrong answer would cost:
  * the Telegram card that places the order carries the review, beside the
    Confirm button;
  * the proposal payload carries it, so both web confirm surfaces can;
  * all three doors answer what the one assembly said;
  * a review that could not be produced is SAID, never silence — the Confirm
    button is live either way;
  * the CONTEXT failing is not the review failing;
  * the advisory footer is one sentence in two runtimes.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.core import copilot_context as cc
from bot.core import trade_copilot as tcp
from bot.skills.trading_commands import TradingCommands

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
SECRET = "g" * 48
HDRS = {"X-Gateway-Secret": SECRET}

#: A review with one of everything a card has to render.
PLANTED = {
    "verdict": tcp.VERDICT_CAUTION, "score": 70,
    "score_basis": {"applied": 3, "total": 4},
    "score_line": "score 70/100 over 3 of the 4 checks",
    "rr": 1.2, "stop_pct": 0.2, "target_pct": 0.24,
    "book": cc.BOOK_PAPER,
    "checks": {"reward_risk": "flag", "stop_distance": "flag",
               "size_vs_equity": "ok", "engine_bias": "unchecked",
               "existing_exposure": "ok"},
    "flags": [{"level": "warn", "msg": "Stop is only 0.2% away — wicked out."}],
    "notes": ["Margin is 2% of equity."],
    "unchecked": [{"name": "engine_bias", "label": "the engine's bias",
                   "reason": "the engine has no open idea on SOL right now"}],
}


# ───────────────────────── the Telegram door ─────────────────────────

class _Bot(TradingCommands):
    """A stand-in `self` carrying only what `_cmd_trade` reaches for.

    The suite's usual `bot` fixture replaces `_send` with a list stub AND
    mocks `CONFIG`, so every boolean under it reads truthy — which is how a
    driven assertion ends up measuring the fixture. This drives the real
    method against the real renderer.
    """

    def __init__(self, engine=None, allowed=True):
        self.engine = engine or SimpleNamespace(_pending_ideas={})
        self._allowed = allowed
        self.sent: list[tuple[str, object]] = []

    async def _guard(self, update, permission):        # noqa: D401
        return self._allowed

    def _get_tg_id(self, update):
        return "u1"

    def _lang(self, update):
        return "en"

    async def _send(self, update, text, **kw):
        self.sent.append((text, kw.get("reply_markup")))


def _update(text):
    return SimpleNamespace(
        message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=4242, first_name="T"))


_LINE = "/trade buy SOL 71.42 sl 70.05 tp 76.42 margin 250"


async def _trade_card(monkeypatch, review, *, engine=None, allowed=True):
    monkeypatch.setattr(cc, "review_ticket",
                        _answering(review), raising=True)
    bot = _Bot(engine=engine, allowed=allowed)
    await bot._cmd_trade(_update(_LINE), SimpleNamespace(args=[]))
    return bot


def _answering(review):
    async def _fake(engine, user_id, trade):
        _fake.calls.append((user_id, dict(trade)))
        return review
    _fake.calls = []
    return _fake


class TestTheTelegramDoorCarriesIt:
    async def test_the_card_that_places_the_order_shows_what_was_reviewed(
            self, monkeypatch):
        bot = await _trade_card(monkeypatch, PLANTED)
        assert len(bot.sent) == 1
        card, markup = bot.sent[0]
        # The verdict, the span, the finding, the coverage and the caveat.
        assert "CO-PILOT" in card
        assert "score 70/100 over 3 of the 4 checks" in card
        assert "wicked out" in card
        assert "Margin is 2% of equity." in card
        assert "Not checked — the engine's bias:" in card
        assert "no open idea on SOL" in card
        assert tcp.COPILOT_FOOTER in card
        # BESIDE THE BUTTON. A review on a card with no Confirm on it would be
        # advice about an order nobody is about to place.
        assert markup is not None
        assert "confirm:" in json.dumps(markup.to_dict())

    async def test_the_block_sits_between_the_levels_and_the_buttons(
            self, monkeypatch):
        # Evidence read AFTER the instruction is evidence nobody used. The
        # levels are above it (the reader needs the ticket first) and the
        # Confirm keyboard is below.
        bot = await _trade_card(monkeypatch, PLANTED)
        card, _ = bot.sent[0]
        assert card.index("$71.4200") < card.index("CO-PILOT")
        assert card.index("CO-PILOT") < card.index("Reduced risk checks")

    async def test_the_reduced_checks_line_STAYS(self, monkeypatch):
        # It is TRUE — `_confirm_trade_inner`'s `is_manual` branch really does
        # skip the price-drift and stale-R:R checks — so the review is an
        # ADDITION beside it, not a replacement for it. Removing a true
        # sentence to make room for a new one is not a fix.
        bot = await _trade_card(monkeypatch, PLANTED)
        card, _ = bot.sent[0]
        assert "Reduced risk checks for manual orders" in card

    async def test_the_ticket_it_reviews_is_the_one_it_is_about_to_place(
            self, monkeypatch):
        fake = _answering(PLANTED)
        monkeypatch.setattr(cc, "review_ticket", fake)
        bot = _Bot()
        await bot._cmd_trade(_update(_LINE), SimpleNamespace(args=[]))
        assert len(fake.calls) == 1
        uid, trade = fake.calls[0]
        assert uid == "u1"        # the CALLER's book, not the operator's
        assert trade == {"direction": "LONG", "symbol": "SOL", "entry": 71.42,
                         "sl": 70.05, "tp": 76.42, "margin": 250.0}

    async def test_a_ticket_the_bot_could_not_review_says_so_on_the_card(
            self, monkeypatch):
        # Never silence. The Confirm button is live either way, so a block that
        # simply vanished would leave the card in exactly the state the
        # co-pilot exists to remove.
        bot = await _trade_card(monkeypatch, None)
        card, markup = bot.sent[0]
        assert "could not be reviewed" in card
        assert "Nothing has been checked." in card
        assert "score" not in card
        assert markup is not None

    async def test_a_refused_caller_gets_no_card_and_no_review(self, monkeypatch):
        fake = _answering(PLANTED)
        monkeypatch.setattr(cc, "review_ticket", fake)
        bot = _Bot(allowed=False)
        await bot._cmd_trade(_update(_LINE), SimpleNamespace(args=[]))
        assert bot.sent == []
        assert fake.calls == []   # a refusal must not spend the read either


class TestTheFreeTextGrammarInheritsIt:
    def test_the_grammar_branch_delegates_rather_than_building_its_own_card(self):
        """A SCAN, and it says so.

        Standing up `_handle_message` means the firewall scan, the router, the
        conversation store and the limit-price flow to read one delegation —
        and what is being asked is structural: does the branch hand the turn to
        `_cmd_trade` (and so inherit its review) or build a card of its own?
        The branch is anchored on `looks_like_manual_trade`, not on a short
        literal.
        """
        from bot.skills import telegram_handler as th
        src = Path(th.__file__).read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_handle_message")
        branches = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.If)
            and any(isinstance(c, ast.Attribute) and c.attr == "_cmd_trade"
                    for c in ast.walk(n))]
        assert len(branches) == 1, (
            "the grammar branch is no longer the one place `_handle_message` "
            "reaches `_cmd_trade`")
        body = ast.unparse(branches[0])
        assert "looks_like_manual_trade" in ast.unparse(fn)
        assert "self._cmd_trade(update, ctx)" in body
        assert "InlineKeyboardMarkup" not in body, (
            "the grammar branch builds its own card; it would not inherit the "
            "review that `_cmd_trade` renders")


# ───────────────────────── the web doors ─────────────────────────

class _Users:
    def register(self, tg, name="", auto_role=""):
        return {"authorized": True, "role": "trader"}

    def get(self, tg):
        return {"authorized": True, "role": "trader"}

    def permission_denial(self, tg, cmd):
        return None

    def is_admitted(self, tg):
        return True


class _Handler:
    def __init__(self):
        self.users = _Users()
        self._limiter = SimpleNamespace(allow=lambda key: True)

    def _allowlist_ids(self):
        return set()

    def _can_trade_live(self, tg_id):
        # Paper on purpose: this suite is about what the CARD carries, and a
        # live mode would put the live-gate decision between the drive and the
        # payload.
        return False


class _Engine:
    def __init__(self):
        self.user_portfolios = SimpleNamespace(
            get=lambda uid: SimpleNamespace(
                snapshot=lambda: SimpleNamespace(equity_usd=10_000.0),
                open_positions=[]))
        self._pending_ideas: dict = {}
        self._pending_atr: dict = {}
        self._pending_margin: dict = {}

    def live_view(self, user_id="", max_age_s=900.0):
        return {"scope": "none", "executor": None, "balance": None,
                "total": None, "age_s": None}

    async def get_user_live_equity(self, user_id=""):
        return None


@contextlib.asynccontextmanager
async def _gateway(engine):
    from bot.web import user_gateway as ug
    app = ug.build_gateway(engine, _Handler())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def secret(monkeypatch):
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)


_BODY = {"telegram_id": "u1", "direction": "LONG", "symbol": "SOL",
         "entry": 71.42, "sl": 70.05, "tp": 76.42, "margin": 250.0}


class TestTheProposalCarriesIt:
    async def test_the_pending_trade_payload_carries_the_review(
            self, secret, monkeypatch):
        monkeypatch.setattr(cc, "review_ticket", _answering(PLANTED))
        async with _gateway(_Engine()) as c:
            r = await c.post("/trade/propose", json=_BODY, headers=HDRS)
            d = await r.json()
        assert r.status == 200
        assert d["pending_trade"]["copilot"] == PLANTED

    async def test_a_review_the_bot_could_not_produce_is_null_not_absent(
            self, secret, monkeypatch):
        # The KEY is always there on this build. Absent means an older bot;
        # null means this build could not review. A renderer that could not
        # tell them apart would have one sentence for two facts — and both
        # get the same one here only because neither is "nothing was found".
        monkeypatch.setattr(cc, "review_ticket", _answering(None))
        async with _gateway(_Engine()) as c:
            r = await c.post("/trade/propose", json=_BODY, headers=HDRS)
            d = await r.json()
        assert "copilot" in d["pending_trade"]
        assert d["pending_trade"]["copilot"] is None

    async def test_the_chat_grammar_branch_proposes_through_the_same_door(
            self, secret, monkeypatch):
        # "buy SOL 71.42 sl 70.05 tp 76.42" typed into the web chat is the
        # same proposal, and used to reach a Confirm card with no review.
        fake = _answering(PLANTED)
        monkeypatch.setattr(cc, "review_ticket", fake)
        async with _gateway(_Engine()) as c:
            r = await c.post("/chat", headers=HDRS, json={
                "telegram_id": "u1",
                "text": "buy SOL 71.42 sl 70.05 tp 76.42 margin 250"})
            d = await r.json()
        assert r.status == 200, d
        assert d["pending_trade"]["copilot"] == PLANTED
        assert len(fake.calls) == 1


class TestOneAssemblyEveryDoor:
    async def test_all_three_doors_answer_what_the_one_assembly_said(
            self, secret, monkeypatch):
        # A byte-identical copy of the assembly per door agrees with every
        # fixture and diverges on the first edit to either, which is what a
        # second answer looks like from outside. Patch the walk; read what each
        # door says.
        marked = {**PLANTED, "verdict": tcp.VERDICT_PARTIAL,
                  "score_line": "score 99/100 over 1 of the 4 checks"}
        monkeypatch.setattr(cc, "review_ticket", _answering(marked))

        bot = _Bot()
        await bot._cmd_trade(_update(_LINE), SimpleNamespace(args=[]))
        assert "score 99/100 over 1 of the 4 checks" in bot.sent[0][0]

        async with _gateway(_Engine()) as c:
            prop = await (await c.post("/trade/propose", json=_BODY,
                                       headers=HDRS)).json()
            cop = await (await c.post("/trade/copilot", json=_BODY,
                                      headers=HDRS)).json()
        assert prop["pending_trade"]["copilot"]["score_line"] == marked["score_line"]
        assert cop["score_line"] == marked["score_line"]

    async def test_the_copilot_endpoint_reports_a_review_it_could_not_produce(
            self, secret, monkeypatch):
        monkeypatch.setattr(cc, "review_ticket", _answering(None))
        async with _gateway(_Engine()) as c:
            r = await c.post("/trade/copilot", json=_BODY, headers=HDRS)
            d = await r.json()
        assert r.status == 500
        assert d["error"] == "copilot_unavailable"


# ───────────────── the assembly's own two failures ─────────────────

class TestTheContextFailingIsNotTheReviewFailing:
    async def test_a_raised_reading_still_yields_a_review(self, monkeypatch):
        # Geometry, reward:risk and stop distance need nothing from any book.
        async def _boom(engine, user_id, symbol):
            raise RuntimeError("the store is down")
        monkeypatch.setattr(cc, "ticket_context", _boom)
        rev = await cc.review_ticket(SimpleNamespace(), "u1", {
            "direction": "LONG", "symbol": "SOL", "entry": 100.0,
            "sl": 98.0, "tp": 106.0, "margin": 50.0})
        assert rev is not None
        assert rev["book"] == cc.BOOK_UNREADABLE
        assert rev["checks"]["reward_risk"] == "ok"
        names = {u["name"]: u["reason"] for u in rev["unchecked"]}
        assert set(names) == {"size_vs_equity", "engine_bias",
                              "existing_exposure"}
        # The ACCOUNT is named, never "not supplied" — which is a thing the
        # reader fixes in the form rather than at the venue.
        for reason in names.values():
            assert reason == cc._CONTEXT_UNREADABLE
        assert rev["verdict"] == tcp.VERDICT_PARTIAL

    async def test_a_raised_review_is_None_and_not_a_half_review(
            self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("arithmetic exploded")
        monkeypatch.setattr(tcp, "review", _boom)
        rev = await cc.review_ticket(SimpleNamespace(), "u1", {
            "direction": "LONG", "symbol": "SOL", "entry": 100.0,
            "sl": 98.0, "tp": 106.0})
        assert rev is None

    async def test_the_score_sentence_is_stamped_once_by_the_assembly(self):
        rev = await cc.review_ticket(SimpleNamespace(), "u1", {
            "direction": "LONG", "symbol": "SOL", "entry": 100.0,
            "sl": 98.0, "tp": 106.0, "margin": 50.0})
        assert rev["score_line"] == tcp.score_line(rev)
        assert "over" in rev["score_line"]


# ───────────────────── the Telegram block itself ─────────────────────

class TestTheBlockIsTheProducersWords:
    def test_a_producer_sentence_is_text_not_markup(self):
        html = tcp.review_card_html({
            **PLANTED,
            "unchecked": [{"name": "engine_bias", "label": "<b>label</b>",
                           "reason": "<img src=x onerror=1>"}]})
        assert "<img" not in html
        assert "&lt;img" in html
        assert "&lt;b&gt;label" in html

    def test_it_omits_the_levels_the_card_above_already_carries(self):
        # Two renderings of one ratio on a card about money is how the five
        # copies in the `R:R 0.0x` slice started.
        html = tcp.review_card_html(PLANTED)
        assert "R:R" not in html
        assert "stop 0.2%" not in html
        # And `human_readable`, whose caller has no card, still prints them.
        assert "R:R" in tcp.human_readable(PLANTED)

    def test_an_invalid_ticket_names_the_geometry_and_scores_nothing(self):
        rev = tcp.review({"direction": "LONG", "symbol": "SOL",
                          "entry": 71.0, "sl": 72.0, "tp": 76.0})
        html = tcp.review_card_html(rev)
        assert "wrong side of entry" in html
        assert "score" not in html
        assert "None" not in html

    def test_the_footer_is_one_sentence_in_two_runtimes(self):
        # `app/public/js/copilot-review-model.js` renders the same review onto
        # three web surfaces. Two surfaces wording the same caveat differently
        # is two answers about what a green badge means.
        js = (ROOT / "app/public/js/copilot-review-model.js").read_text()
        m = re.search(r"var FOOTER = '(.*?)';", js)
        assert m, "the model no longer declares FOOTER"
        assert m.group(1).encode().decode("unicode_escape") == tcp.COPILOT_FOOTER

    def test_there_is_no_branch_for_a_verdict_this_module_cannot_place(self):
        # `review` is the only producer and it is called one line above the
        # render, in this process, so such a word is not an input this code can
        # receive — and a line no input can reach is a claim that there is a
        # check. The BROWSER's renderer does carry that branch and needs to.
        src = (ROOT / "bot/core/trade_copilot.py").read_text()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "review_card_html")
        # STRIP THE DOCSTRING FIRST. It explains the very branch this asserts
        # is absent, so unparsing the function whole makes the assertion match
        # the prose — the "a comment that quotes the string it forbids" trap,
        # one node type over, and the FALSE-PASS direction of it.
        stmts = list(fn.body)
        if (stmts and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)):
            stmts = stmts[1:]
        body = "\n".join(ast.unparse(st) for st in stmts)
        assert "cannot place" not in body
        assert body.count("VERDICT_") == 1     # the invalid branch, and no other


def test_the_event_loop_is_not_needed_for_the_renderer():
    # `review_card_html` is pure, so a card can be rendered in any context.
    assert asyncio.iscoroutinefunction(cc.review_ticket)
    assert not asyncio.iscoroutinefunction(tcp.review_card_html)
