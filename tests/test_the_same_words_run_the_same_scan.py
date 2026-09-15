"""The same words must run the same scan, whichever client you type them into.

`SCAN_DISPATCH` had TWO columns. Typing "deep scan" ran the full universe
sweep on Telegram and the shallow movers scan on the web; "scalp scan" ran
`pro_scan` in scalp mode on one and `scan_market` on the other. The paywall
differed with it, and the capability card is derived from that table — so a
row could be printed as reachable on the strength of a mapping no dispatcher
used. Recording the disagreement is what made it visible; this file is what
keeps it closed.

No count is spelled anywhere in this file. The section this slice added to
CLAUDE.md is about a universe size that was stale in five places, and a
number in a test docstring is the same shape one file over — every size here
is read from `deepscan_universe_size()`.

FOUR PROPERTIES, each driven rather than read:

  1. ONE ANSWER — the table has one column, so the two dispatchers cannot
     disagree, and the ARGUMENTS travel with the skill name. All three scan
     skills are `execute(self, engine, **kwargs)` and `intent.kwargs` is `{}`
     for every scan rule, so a dispatcher that reads the name and not the mode
     renders the INTRADAY card for a scalp request and raises nothing.

  2. ONE PAYWALL — `check_user` is keyed by FEATURE, the web passed it a
     SKILL, and `pro_scan` is sold as `premium_scan`. Eight of the nine paid
     skills were gated by coincidence (their names match) and the ninth was
     free. `tier_gate.feature_for` is the one reading.

  3. THE HEADER IS THE CALLER'S BOOK — `ProScanSkill` read
     `engine.live_executor` for every caller, printing the OPERATOR's open
     positions and realized P&L in dollars one line under the CALLER's own
     equity. Five siblings had been cured of exactly this.

  4. A PARTIAL SAYS IT IS ONE — the web's 45s chat deadline is shorter than
     the sweep's worst case (twelve `Semaphore(10)` batches at 15s each), and
     a routed skill emits no SSE frames, so nothing resets an inactivity timer
     while it runs. The scan takes a budget and returns what it measured,
     labelled.

A fifth section holds the five properties the first mutation round SURVIVED,
which is the honest place for them: each names a gap the four above could not
see, the sharpest being that every parity test derives its expectation from
the table and so cannot notice the table drifting.

The red herring throughout is a card that looks complete: a `Scanned` row with
nothing beside it reads as a finished sweep of a quiet market.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.token.tier_gate as tier_gate
from bot.nlp.skill_doors import SCAN_DISPATCH, dispatch_kwargs, dispatches_to

# ── 1. one answer, arguments included ───────────────────────────────────────

def test_the_table_answers_a_skill_and_its_arguments_for_every_intent():
    for intent, row in SCAN_DISPATCH.items():
        skill = dispatches_to(intent)
        assert skill and skill != intent, intent
        assert dispatch_kwargs(intent), (
            f"{intent} names a skill and no arguments — half a table")
        assert set(row) == {"skill", "kwargs"}, (intent, sorted(row))


def test_the_arguments_are_a_copy_the_caller_may_mutate():
    """A dispatcher does `{**intent.kwargs, **dispatch_kwargs(i)}` and the web
    then `setdefault`s a budget onto it. Handing back the table's own dict
    would let one turn's budget become every later turn's."""
    first = dispatch_kwargs("scan_scalp")
    first["mode"] = "tampered"
    first["budget_sec"] = 1.0
    assert dispatch_kwargs("scan_scalp") == {"mode": "scalp"}


def test_an_intent_the_table_does_not_name_dispatches_to_itself():
    assert dispatches_to("get_portfolio") == "get_portfolio"
    assert dispatch_kwargs("get_portfolio") == {}


#: A real phrase per intent, so the drive covers the ROUTER too: this test is
#: about what happens when a person types words, and a planted `IntentResult`
#: would prove only that two dispatchers agree about a value nothing produced.
TYPED = {
    "scan_deep": "deep scan",
    "scan_full": "full scan",
    "scan_swing": "swing scan",
    "scan_scalp": "scalp scan",
    "scan_intraday": "intraday scan",
    "scan_deep_1h": "1h scan",
    "scan_deep_1d": "1d scan",
}


def test_every_intent_in_the_table_has_a_phrase_that_reaches_it():
    """A phrase list that drifts from the table silently stops covering a
    mode — the `known_failures.txt` rule, one table over."""
    from bot.nlp.intent_router import IntentRouter

    assert set(TYPED) == set(SCAN_DISPATCH), sorted(set(TYPED) ^ set(SCAN_DISPATCH))
    router = IntentRouter()
    for intent, phrase in TYPED.items():
        got = router.classify_rules(phrase)
        assert got.skill == intent and got.confidence >= 0.8, (phrase, got.skill)


def test_the_number_on_the_card_is_a_phrase_the_router_answers():
    """The waiting message and the tool description both name the universe
    size, and a caller who reads one types it back. The rule's alternative was
    the literal `67 symbols?` — the count that card carried years ago — so the
    number the product prints TODAY reached nothing.

    Both spellings are the same ask and both must route; the count is read off
    the code, not written here, or this test is one more copy of it.
    """
    from bot.nlp.intent_router import IntentRouter
    from bot.skills.skill_registry import deepscan_universe_size

    router = IntentRouter()
    for phrase in (f"{deepscan_universe_size()} symbols", "67 symbols"):
        got = router.classify_rules(phrase)
        assert got.skill == "scan_deep", (phrase, got.skill)


@pytest.mark.parametrize("intent", sorted(SCAN_DISPATCH))
def test_both_dispatchers_run_the_same_skill_with_the_same_arguments(
        monkeypatch, tmp_path, intent):
    """TYPED, ROUTED AND DISPATCHED on both surfaces, and the two recordings
    compared.

    A copy of the table inside either dispatcher would agree here — that is
    what `test_both_dispatchers_read_the_one_scan_table` is for — but a copy
    that has DRIFTED is what this catches, and drifted is the state the tree
    was in: `scan_deep` ran the full universe sweep on Telegram and the shallow
    movers scan on the web, from the same two words.
    """
    from bot.nlp.conversation_store import ConversationStore
    from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
    from tests.test_a_routed_answer_is_in_the_transcript import _web, _web_turn
    from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update

    phrase = TYPED[intent]
    seen: dict[str, tuple[str, dict]] = {}

    def _record(surface, name, kw):
        kw = dict(kw)
        # The caller id and the web's time budget are properties of the
        # SURFACE, not of the scan the words asked for; everything else must
        # match, and a budget is asserted separately below.
        kw.pop("user_id", None)
        kw.pop("budget_sec", None)
        seen[surface] = (name, kw)

    # ── the web ──
    ug, handler = _web(monkeypatch, ConversationStore())

    def _web_get(name):
        async def _exec(engine, **kw):
            _record("web", name, kw)
            return "<b>card</b>"
        return SimpleNamespace(execute=_exec)

    handler.registry = SimpleNamespace(get=_web_get)
    handler.users.permission_denial = lambda uid, perm: None
    _web_turn(ug, handler, phrase)

    # ── telegram ──
    gen = _halt_bot.__wrapped__(tmp_path)
    tg_bot = next(gen)
    try:
        async def _tg_dispatch(name, engine, **kw):
            _record("telegram", name, kw)
            return "<b>card</b>"

        tg_bot.registry = SimpleNamespace(dispatch=_tg_dispatch)
        tg_bot._token_gate_blocks = AsyncMock(return_value=False)
        asyncio.run(tg_bot._handle_message(_update(OPERATOR, phrase), None))
    finally:
        for _ in gen:
            pass

    assert set(seen) == {"web", "telegram"}, (phrase, seen)
    assert seen["web"] == seen["telegram"], (phrase, seen)
    assert seen["telegram"] == (dispatches_to(intent), dispatch_kwargs(intent))


# ── 2. one paywall ──────────────────────────────────────────────────────────

def test_the_feature_reading_knows_the_one_skill_whose_names_differ():
    assert tier_gate.feature_for("pro_scan") == "premium_scan"
    assert tier_gate.feature_for("deepscan") == "deepscan"
    # Answering the name unchanged is right for the eight that share one, and
    # is exactly why the ninth went unnoticed.
    assert tier_gate.feature_for("anything_at_all") == "anything_at_all"


@pytest.mark.parametrize("intent", sorted(SCAN_DISPATCH))
def test_every_scan_the_table_dispatches_is_sold_by_the_ladder(intent):
    """A skill whose feature is absent from `FEATURE_MIN_TIER` is FREE, and
    `check_user` says `(True, 'ok')` for it — a gate call that reads like a
    paywall and charges nobody. That is the state `pro_scan` was in on the
    web."""
    feature = tier_gate.feature_for(dispatches_to(intent))
    assert feature in tier_gate.FEATURE_MIN_TIER, (intent, feature)


@pytest.mark.parametrize("intent", sorted(SCAN_DISPATCH))
def test_the_web_gate_refuses_a_scan_the_caller_has_not_paid_for(
        monkeypatch, intent):
    """The gate ON and no linked wallet. This returned 200 and ran the scan
    for `pro_scan`, because the SKILL name reached a check keyed by FEATURE.

    Driven through `_web_skill_denied` — the web's own door — rather than
    through `check_user`, which is the half that was always right.
    """
    from bot.web import user_gateway as ug

    monkeypatch.setenv("TOKEN_TIER_GATE_ENABLED", "true")
    monkeypatch.setenv("RCLAW_MINT", "RCLAWmint1111111111111111111111111111111111")
    users = SimpleNamespace(
        permission_denial=lambda uid, perm: None,
        get=lambda uid: {"role": "trader"},
        get_sol_wallet=lambda uid: None,       # nothing linked
    )
    handler = SimpleNamespace(users=users)
    resp = ug._web_skill_denied(handler, "web:1", dispatches_to(intent))
    assert resp is not None, f"{intent} is free on the web"
    assert resp.status == 402, resp.status
    body = json.loads(resp.body.decode())
    assert body["error"].startswith("tier_"), body


@pytest.mark.parametrize("intent", sorted(SCAN_DISPATCH))
def test_the_two_surfaces_refuse_it_in_the_same_words(monkeypatch, tmp_path,
                                                      intent):
    """THREE NOUNS: the skill that runs, the feature that is checked, and the
    word the refusal shows. `_token_gate_blocks`'s docstring separates the
    last two in as many words — "`mode` is only ever shown to the user;
    `feature` is what is actually checked" — and the web had ONE name for all
    three, so it told the caller "Pro_scan scan is a staked-tier feature": an
    internal skill name, capitalised, in a sentence asking them to buy
    something.

    Driven through the whole web turn, because the display word is computed
    at the dispatch site and `_web_skill_denied` only receives it.
    """
    from bot.nlp.conversation_store import ConversationStore
    from bot.skills.skill_registry import ProScanSkill
    from tests.test_a_routed_answer_is_in_the_transcript import _web, _web_turn

    monkeypatch.setenv("TOKEN_TIER_GATE_ENABLED", "true")
    monkeypatch.setenv("RCLAW_MINT", "RCLAWmint1111111111111111111111111111111111")

    gw, handler = _web(monkeypatch, ConversationStore())
    handler.registry = SimpleNamespace(get=lambda n: SimpleNamespace(
        execute=AsyncMock(side_effect=AssertionError("must not run"))))
    handler.users.permission_denial = lambda uid, perm: None
    handler.users.get_sol_wallet = lambda uid: None
    said = _web_turn(gw, handler, TYPED[intent])["reply_html"]

    # The same sentence Telegram builds for the same words: its `mode`, which
    # is `dispatch_kwargs`' mode or "deep" for the universe sweep.
    mode = dispatch_kwargs(intent).get("mode") or "deep"
    assert said == tier_gate.upgrade_message(mode), (intent, said)
    # ...and never the internal names.
    for internal in (dispatches_to(intent), tier_gate.feature_for(
            dispatches_to(intent))):
        if internal != mode:
            assert internal.lower() not in said.lower(), (internal, said)
    assert ProScanSkill.name not in said


def test_a_paid_scan_is_not_refused_when_the_gate_is_off(monkeypatch):
    from bot.web import user_gateway as ug

    monkeypatch.setenv("TOKEN_TIER_GATE_ENABLED", "false")
    handler = SimpleNamespace(users=SimpleNamespace(
        permission_denial=lambda uid, perm: None,
        get=lambda uid: {"role": "trader"},
        get_sol_wallet=lambda uid: None))
    for intent in SCAN_DISPATCH:
        assert ug._web_skill_denied(handler, "web:1", dispatches_to(intent)) is None


# ── 3. the header is the CALLER's book ──────────────────────────────────────

class _Book:
    """An executor with positions and closes, standing in for one account."""

    def __init__(self, open_n, net):
        self.open_positions = list(range(open_n))
        self.closed_positions = [SimpleNamespace(pnl_usd=net, symbol="BTC/USDT")]


def _proscan_engine(viewer, *, equity=1234.0):
    """An engine whose scan finds nothing, so the card is its header."""
    async def _scan():
        return []

    async def _equity(uid):
        return equity

    return SimpleNamespace(
        risk=SimpleNamespace(circuit_breaker_active=False),
        get_effective_equity_async=_equity,
        viewer_executor=viewer,
        user_portfolios=SimpleNamespace(get=lambda uid: SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                equity_usd=0.0, open_positions=0, daily_pnl=0.0))),
        portfolio=SimpleNamespace(snapshot=lambda: SimpleNamespace(
            equity_usd=0.0, open_positions=0, daily_pnl=0.0)),
        scanner=SimpleNamespace(scan=_scan),
        _last_scan_signals=None,
    )


@pytest.fixture(name="live_mode")
def _live_mode():
    """`CONFIG` is a frozen dataclass, so `object.__setattr__` is the only
    door — which puts the write outside monkeypatch's bookkeeping, the shape
    that leaked a gateway secret into 40 later tests. It restores in a
    `finally`.

    `is_live()` is shadowed rather than `simulation_mode` flipped, because it
    reads THREE things (`live_trading_enabled`, `simulation_mode` and a
    configured Telegram chat id) and a fixture that sets one of them is a
    fixture that silently exercises the paper branch — which is what the first
    draft of this file did, and the header assertions passed against it.
    """
    from bot.config import CONFIG

    object.__setattr__(CONFIG, "is_live", lambda: True)
    try:
        assert CONFIG.is_live()
        yield
    finally:
        object.__delattr__(CONFIG, "is_live")


@pytest.mark.asyncio
async def test_the_scan_header_describes_the_book_this_caller_may_view(live_mode):
    """It read `engine.live_executor` — the OPERATOR's open positions and
    realized P&L, in dollars, one line under the CALLER's own equity, for
    every caller on a live deployment. Five siblings had been cured of exactly
    this (`check_risk`, `playbook`, `get_portfolio`, `/positions`, the chat
    prompt) and the scan header was missed; it is reachable from Telegram
    today, and aligning the web's scan dispatch would have added a second door
    to it.

    The red herring is a symmetric fixture: plant the SAME numbers on both
    books and a card reading the wrong one is indistinguishable. These differ.
    """
    from bot.skills.skill_registry import ProScanSkill

    operator, caller = _Book(7, 4242.0), _Book(1, -5.0)
    engine = _proscan_engine(lambda uid: caller if uid == "web:1" else operator)
    out = await ProScanSkill().execute(engine, mode="swing", user_id="web:1")

    assert "1/" in out, out[:400]
    assert "7/" not in out.split("Timeframe")[0], "the operator's open count"
    assert "4,242" not in out and "4242" not in out, "the operator's P&L"


@pytest.mark.asyncio
async def test_an_unreadable_book_reads_not_linked_rather_than_zero(live_mode):
    """`None` is a book nobody could describe. `Open: 0/5 | PnL: $+0.00` tells
    an unlinked caller two measurements about an account nobody looked at —
    and the scan itself needs no account, so the card still runs."""
    from bot.skills.skill_registry import ProScanSkill

    engine = _proscan_engine(lambda uid: None)
    out = await ProScanSkill().execute(engine, mode="swing", user_id="web:1")

    head = out.split("Timeframe")[0]
    assert head.count("not linked") == 2, head
    assert "0/" not in head and "$+0.00" not in head, head
    # ...and the equity line, which IS read, is still printed.
    assert "1,234" in head or "1234" in head, head


@pytest.mark.asyncio
async def test_an_unpriced_close_is_not_counted_as_break_even(live_mode):
    """`sum(t.pnl_usd or 0)` books a close nobody could price as a measured
    zero and prints the partial as a whole. The shared `realized_totals` is
    the reading."""
    from bot.skills.skill_registry import ProScanSkill

    book = _Book(1, 0.0)
    book.closed_positions = [SimpleNamespace(pnl_usd=None, symbol="BTC/USDT")]
    engine = _proscan_engine(lambda uid: book)
    out = await ProScanSkill().execute(engine, mode="swing", user_id="web:1")
    head = out.split("Timeframe")[0]
    # Whatever it says, it must not be a confident break-even read off a
    # close with no price on record.
    assert "$+0.00" not in head, head


# ── 4. a partial says it is one ─────────────────────────────────────────────

#: Enough candles for every detector to run. `[]` was the first draft, and it
#: falls into the too-little-history bucket, so `scanned` stayed 0 and the
#: budget assertions could not tell a bounded sweep from a complete one.
_CANDLES = [[i * 60_000, 1.0, 1.1, 0.9, 1.0 + i * 0.001, 10.0] for i in range(60)]


def _deepscan_engine(latency):
    """An engine whose every OHLCV fetch takes `latency` seconds. The sweep's
    WIDTH is what is under test here, not what it finds."""
    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            await asyncio.sleep(latency)
            return [list(c) for c in _CANDLES]

    async def _spot():
        return _Ex()

    async def _fut():
        return _Ex()

    return SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_spot, _get_futures_exchange=_fut),
        risk=SimpleNamespace(circuit_breaker_active=False),
        _last_deepscan_hits=None,
    )


def _row(card, key):
    """The value on the stats row `key`, or None when the row is absent.

    The rows are `  <key> ·········· <value>` and a value may contain spaces
    (`75 (time budget)`), so the dot run is the separator — not the last
    space, which the first draft used and which silently cut every multi-word
    value down to its last word.
    """
    import re

    m = re.search(rf"^\s*{re.escape(key)}\s+\u00b7+\s*(.+?)\s*$",
                  card, re.M)
    return m.group(1) if m else None


@pytest.mark.asyncio
async def test_an_unbudgeted_scan_sweeps_the_whole_universe_and_claims_nothing():
    """`None` is the default and stays it: Telegram's `/deepscan` wraps the
    dispatch in its own timeout and a caller who can afford the wait says so
    by not passing a budget. The partial rows must not appear when nothing was
    left unreached — a row that shows up when nothing is wrong is how a reader
    learns to skip the next one."""
    from bot.skills.skill_registry import DeepScanSkill, deepscan_universe_size

    card = await DeepScanSkill().execute(_deepscan_engine(0.0), timeframe="4h")
    n = deepscan_universe_size()
    assert _row(card, "Scanned") == f"{n}/{n}", _row(card, "Scanned")
    assert _row(card, "Not reached") is None
    assert "time budget" not in card
    assert "not reached are unknown" not in card


@pytest.mark.asyncio
async def test_a_budgeted_scan_returns_what_it_read_and_names_what_it_did_not():
    """The universe through a `Semaphore(10)` is twelve sequential batches, so
    the worst case is 12 x 15s — longer than the web's 45s chat deadline, and
    a routed skill emits no SSE frames, so nothing resets an inactivity timer
    while it runs. The caller was then shown a DEPLOYMENT-PAIRING sentence
    manufactured from a timeout.

    The red herring is a `Scanned` row on its own: a partial count with
    nothing beside it reads as a finished sweep of a quiet market.
    """
    from bot.skills.skill_registry import DeepScanSkill, deepscan_universe_size

    n = deepscan_universe_size()
    card = await DeepScanSkill().execute(
        _deepscan_engine(0.2), timeframe="4h", budget_sec=0.5)

    scanned = int(_row(card, "Scanned").split("/")[0])
    unreached = int(_row(card, "Not reached").split(" ")[0])
    assert 0 < scanned < n, scanned
    assert scanned + unreached == n, (scanned, unreached)
    # Kept apart from `errors`: a symbol the budget ran out before is not a
    # symbol that failed, and folding the two reports a healthy exchange as
    # 75 errors.
    assert int(_row(card, "Errors")) == 0, _row(card, "Errors")
    assert "time budget" in card
    assert f"{unreached} not reached are unknown" in card
    assert "not quiet" in card


@pytest.mark.asyncio
async def test_an_empty_partial_does_not_say_the_market_is_quiet():
    """"No actionable patterns detected" is a claim about the UNIVERSE. Over a
    partial sweep it is a claim about symbols nobody looked at, and the sweep
    that read NOTHING is where it is most wrong: every symbol is unknown and
    the sentence says the market is quiet.

    Driven at the zero-read end rather than a middling one, because that is
    the only budget that reliably leaves `top` empty — the detectors score
    almost any real candle series, so a partial sweep that found nothing is
    not something a fixture can arrange on demand.
    """
    from bot.skills.skill_registry import DeepScanSkill

    card = await DeepScanSkill().execute(
        _deepscan_engine(5.0), timeframe="4h", budget_sec=0.01)
    assert _row(card, "Scanned").startswith("0/"), _row(card, "Scanned")
    assert "No actionable patterns detected." not in card
    assert "No actionable patterns in the 0 read" in card
    assert "not reached are unknown" in card


@pytest.mark.asyncio
async def test_a_budget_that_expires_before_the_first_fetch_reads_nothing():
    """The edge the `max(0.0, ...)` exists for: a deadline already past must
    give `asyncio.wait` a non-negative timeout, and the card must say it read
    nothing rather than printing a complete sweep of zero hits."""
    from bot.skills.skill_registry import DeepScanSkill, deepscan_universe_size

    n = deepscan_universe_size()
    card = await DeepScanSkill().execute(
        _deepscan_engine(5.0), timeframe="4h", budget_sec=0.01)
    assert _row(card, "Scanned") == f"0/{n}", _row(card, "Scanned")
    assert _row(card, "Not reached") == f"{n} (time budget)"


def test_the_web_bounds_the_scan_and_telegram_does_not(monkeypatch, tmp_path):
    """A DECISION, written down and DRIVEN. The web's budget is its own HTTP
    deadline; Telegram's `/deepscan` already wraps the dispatch in
    `CONFIG.deepscan_timeout_sec`, and giving the routed path a second,
    shorter bound would silently shrink a scan the operator is waiting for.

    The first draft asserted `"budget_sec" not in
    inspect.getsource(_handle_message)` — an ABSENCE in raw source, which is
    the assertion this repo records as its most reliable misfire: the comment
    explaining the flag names it, so the scan cannot tell the code from the
    prose about the code. Both halves dispatch for real and read what arrived.
    """
    from bot.nlp.conversation_store import ConversationStore
    from bot.skills.skill_registry import DeepScanSkill
    from bot.web import user_gateway as ug
    from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
    from tests.test_a_routed_answer_is_in_the_transcript import _web, _web_turn
    from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update

    assert ug._WEB_SCAN_BUDGET_SEC > 0
    seen: dict[str, dict] = {}

    gw, handler = _web(monkeypatch, ConversationStore())

    def _get(name):
        async def _exec(engine, **kw):
            seen["web"] = kw
            return "<b>card</b>"
        return SimpleNamespace(execute=_exec)

    handler.registry = SimpleNamespace(get=_get)
    handler.users.permission_denial = lambda uid, perm: None
    _web_turn(gw, handler, TYPED["scan_deep"])

    gen = _halt_bot.__wrapped__(tmp_path)
    tg_bot = next(gen)
    try:
        async def _dispatch(name, engine, **kw):
            seen["telegram"] = kw
            return "<b>card</b>"

        tg_bot.registry = SimpleNamespace(dispatch=_dispatch)
        tg_bot._token_gate_blocks = AsyncMock(return_value=False)
        asyncio.run(tg_bot._handle_message(
            _update(OPERATOR, TYPED["scan_deep"]), None))
    finally:
        for _ in gen:
            pass

    assert seen["web"].get("budget_sec") == ug._WEB_SCAN_BUDGET_SEC, seen
    assert "budget_sec" not in seen["telegram"], seen
    # ...and the skill really reads the name the web sends.
    assert "budget_sec" in DeepScanSkill.execute.__code__.co_consts, (
        "the skill no longer reads the key the web bounds it with")


# ── 5. the five the first mutation round survived ───────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["scalp", "intraday", "swing"])
async def test_a_scan_named_for_a_mode_runs_that_mode(live_mode, mode):
    """THE TABLE AGREES WITH ITSELF, WHICH IS NOT A MEASUREMENT.

    Every other test here derives the expected arguments FROM `SCAN_DISPATCH`,
    so swapping `scan_swing`'s mode to `intraday` leaves them all green — the
    words would quietly start meaning something else and nothing would say so.
    This one anchors the table to the intent's own name and to the card the
    mode produces: `scan_<mode>` runs `mode=<mode>`, and `pro_scan` renders
    that mode's label and timeframe.

    `MODE_CFG.get(mode, MODE_CFG["intraday"])` is why it has to be the CARD
    rather than the kwargs alone: an unknown mode falls back silently, so a
    scalp request rendered "INTRADAY SCAN / 15M" with no marker of any kind.
    """
    from bot.skills.skill_registry import ProScanSkill

    intent = f"scan_{mode}"
    assert dispatches_to(intent) == "pro_scan"
    assert dispatch_kwargs(intent) == {"mode": mode}, dispatch_kwargs(intent)

    cfg = ProScanSkill.MODE_CFG[mode]
    engine = _proscan_engine(lambda uid: _Book(1, 1.0))
    card = await ProScanSkill().execute(
        engine, user_id="web:1", **dispatch_kwargs(intent))
    assert cfg["label"] in card, (mode, card[:200])
    assert cfg["timeframe"].upper() in card, (mode, card[:400])


def test_the_web_turn_really_hands_the_scan_a_budget(monkeypatch):
    """DRIVEN, because the source scan could not see it.

    `test_the_web_bounds_the_scan_and_telegram_does_not` asserts `budget_sec`
    appears in `_chat_turn`'s source — and the comment above the `setdefault`
    explains the flag BY NAME, so deleting the line left the assertion
    matching the prose. That is the false PASS this repo records about raw
    source scans, reproduced inside a guard written after the lesson.
    """
    from bot.nlp.conversation_store import ConversationStore
    from bot.web import user_gateway as ug
    from tests.test_a_routed_answer_is_in_the_transcript import _web, _web_turn

    seen: dict = {}

    def _get(name):
        async def _exec(engine, **kw):
            seen.update(kw)
            seen["_name"] = name
            return "<b>card</b>"
        return SimpleNamespace(execute=_exec)

    gw, handler = _web(monkeypatch, ConversationStore())
    handler.registry = SimpleNamespace(get=_get)
    handler.users.permission_denial = lambda uid, perm: None
    _web_turn(gw, handler, "deep scan")

    assert seen["_name"] == "deepscan", seen
    assert seen.get("budget_sec") == ug._WEB_SCAN_BUDGET_SEC, seen


@pytest.mark.asyncio
async def test_a_venue_answering_with_too_little_history_is_its_own_row():
    """A THIRD BUCKET. The venue answered and the answer was too short to
    score — neither an error nor a symbol that was read. It was counted as
    neither, so `Scanned 0/N · Errors 0` claimed a complete sweep of a
    universe in which nothing had been measured."""
    from bot.skills.skill_registry import DeepScanSkill, deepscan_universe_size

    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            return [[i * 60_000, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(5)]

    async def _ex():
        return _Ex()

    engine = SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_ex, _get_futures_exchange=_ex),
        risk=SimpleNamespace(circuit_breaker_active=False),
        _last_deepscan_hits=None)
    n = deepscan_universe_size()
    card = await DeepScanSkill().execute(engine, timeframe="4h")

    assert _row(card, "Scanned") == f"0/{n}"
    assert _row(card, "Errors") == "0", "a short answer is not a failed fetch"
    assert _row(card, "Too little history") == str(n), card


@pytest.mark.asyncio
async def test_a_paywalled_scan_is_recorded_under_the_skill_it_refused(tmp_path):
    """The refusal record named `"deepscan" if _deep else "pro_scan"` — a
    second spelling of the dispatch, fifteen lines above the comment arguing
    against exactly that. The model is told WHICH tool a gate refused, and it
    must be the one the table would have run."""
    from bot.nlp.conversation_store import ConversationStore
    from bot.nlp.skill_memory import not_run_memory
    from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
    from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update

    gen = _halt_bot.__wrapped__(tmp_path)
    tg_bot = next(gen)
    try:
        tg_bot._token_gate_blocks = AsyncMock(return_value=True)
        tg_bot.registry = SimpleNamespace(
            dispatch=AsyncMock(side_effect=AssertionError("must not run")))
        store = tg_bot.conversations = ConversationStore()
        await tg_bot._handle_message(_update(OPERATOR, "scalp scan"), None)
    finally:
        for _ in gen:
            pass

    said = "\n".join(m.content for m in store.get_recent(str(OPERATOR), limit=5)
                     if m.role == "assistant")
    want = dispatches_to("scan_scalp")
    assert f"[{want}]" in said, said
    assert "[scan_scalp]" not in said, said
    assert "NOT RUN" in not_run_memory(want, "x")


@pytest.mark.asyncio
async def test_the_sweep_is_ordered_by_the_universe_not_by_who_answered_first():
    """`asyncio.wait` hands back a SET. `hits.sort` is stable, so with every
    score tied the set's iteration order decides the whole top list — the same
    universe would produce a different card run to run, and a "top 15" would
    be fifteen arbitrary symbols presented as the best fifteen."""
    from bot.skills.skill_registry import DEEPSCAN_UNIVERSE, TRADFI_PERPETUALS, DeepScanSkill

    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            # Identical for every symbol, so every score ties and ORDER is the
            # only thing that can decide the ranking.
            return [list(c) for c in _CANDLES]

    async def _ex():
        return _Ex()

    engine = SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_ex, _get_futures_exchange=_ex),
        risk=SimpleNamespace(circuit_breaker_active=False),
        _last_deepscan_hits=None)
    await DeepScanSkill().execute(engine, timeframe="4h", max_results=15)

    universe = list(DEEPSCAN_UNIVERSE) + list(TRADFI_PERPETUALS)
    got = [h["symbol"] for h in engine._last_deepscan_hits]
    assert got == universe[:len(got)], (got[:5], universe[:5])
