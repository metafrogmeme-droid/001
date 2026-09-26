"""An alert about ONE person's position, and who it reaches.

``Alert.audience`` exists because a previous report found the wrong people
being alerted: its own comment records it — ``/watch`` is ``@guard("scan")``,
so every scan-tier user who ran ``/watch on`` was in ``_enabled_chats`` and
"an operator asked why users were being told to 'add or rotate an LLM API
key'". Twelve alert types were narrowed to ``"admin"`` on the strength of it,
and the position alerts were deliberately left at ``"all"`` as "the position
and drawdown ones a trader needs".

That judgement is right and the field could not carry it. **AN AUDIENCE IS A
CLASS AND A POSITION BELONGS TO A PERSON**: "the ones a trader needs" is true
of the trader who HOLDS the position, and no value of ``audience`` can say so.

Driven on the tree before the fix — two user portfolios, three watching chats
— every chat received every alert. A chat holding nothing was told

    ⚠️ STOP LOSS APPROACHING — ETH/USDT
    - Current Price: $2,960.0000 · Stop Loss: $2,955.0000 · Entry: $3,000.0000
    👉 /positions — review open trades

about somebody else's position, under a door that would show them something
different, and a CRITICAL "Place a stop on Bitget manually now" about somebody
else's naked live account with that account's venue rejection text attached.

Three checks walk per-user books and dropped the owner on the next line:
``_check_sl_tp_proximity`` (SL_PROXIMITY, TP_PROXIMITY),
``_check_time_stops`` (TIME_STOP_WARN, TIME_STOP_CLOSE) and
``_check_unprotected_positions`` (POSITION_UNPROTECTED).

WHAT IS DELIBERATELY NOT NARROWED. The SHARED ``engine.portfolio`` book — the
``else`` branch of both walks — keeps the fan-out it has always had. That is
the bot's own book, whose entries the product already broadcasts as
TRADE_SIGNAL, and narrowing it would remove a thing watchers subscribe for on
no measurement at all. ``user_id`` is None there, and None NEVER means "the
operator": one value meaning both "nobody in particular" and "a specific
person" is the two-meanings-under-one-name defect, so the operator's own book
takes the audience that already means operator.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import logging
import pathlib
import types

import pytest

from bot.core.proactive_monitor import Alert, ProactiveMonitor
from bot.utils.logger import system_log
from tests.source_scan import code_only

SRC = pathlib.Path("bot/core/proactive_monitor.py")


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def operators():
    """Plant the configured operator chats. Both config objects are frozen
    dataclasses, so this is the same `object.__setattr__` route
    `test_alert_audience` documents, restored in teardown."""
    from bot.config import CONFIG
    original = CONFIG.telegram

    def _set(chat_id="", admin_ids=""):
        object.__setattr__(CONFIG, "telegram", dataclasses.replace(
            original, chat_id=chat_id, admin_ids=admin_ids))

    yield _set
    object.__setattr__(CONFIG, "telegram", original)


class _Pos:
    def __init__(self, asset, entry, sl, tp, tid, direction="LONG"):
        self.asset, self.entry_price = asset, entry
        self.stop_loss, self.take_profit, self.trade_id = sl, tp, tid
        self.direction = types.SimpleNamespace(value=direction)
        self.opened_at = None


class _Book:
    def __init__(self, positions):
        self.open_positions = positions


class _Books:
    def __init__(self, by_user):
        self._by_user = by_user

    def all_portfolios(self):
        return self._by_user

    def get(self, uid):
        return self._by_user[uid]


class _Feed:
    def __init__(self, prices):
        self._prices = prices

    def is_connected(self):
        return True

    def get_prices(self, max_age_sec=0):
        return self._prices


#: ASYMMETRIC ON PURPOSE. Plant the same symbol in both books and a card that
#: read the wrong one is indistinguishable from a card that read the right one
#: — the symmetric-fixture failure this repo records from the chat-prompt
#: slice, where four of its own tests passed over the defect.
ALICE_SYMBOL = "ETH/USDT"
BOB_SYMBOL = "SOL/USDT"


def _two_user_engine():
    """Alice holds ETH near its stop; Bob holds SOL at its target."""
    return types.SimpleNamespace(
        user_portfolios=_Books({
            "alice": _Book([_Pos(ALICE_SYMBOL, 3000.0, 2955.0, 3300.0, "T-A")]),
            "bob": _Book([_Pos(BOB_SYMBOL, 200.0, 197.0, 240.0, "T-B")]),
        }),
        portfolio=_Book([]),
        ws_feed=_Feed({ALICE_SYMBOL: 2960.0, BOB_SYMBOL: 240.0}),
    )


def _shared_book_engine():
    """No per-user portfolios: the `else` branch, the shared book."""
    return types.SimpleNamespace(
        user_portfolios=_Books({}),
        portfolio=_Book([_Pos(ALICE_SYMBOL, 3000.0, 2955.0, 3300.0, "T-S")]),
        ws_feed=_Feed({ALICE_SYMBOL: 2960.0}),
    )


def _stale_position(asset, entry):
    """Open long enough to trip a time stop, and NOT in profit (the walk skips
    a winner). 30h clears the 24h swing close bar."""
    from datetime import datetime, timedelta, timezone
    pos = _Pos(asset, entry, entry * 0.90, entry * 1.20, f"T-{asset[:3]}")
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=30)
    return pos


def _stale_shared_book_engine():
    return types.SimpleNamespace(
        user_portfolios=_Books({}),
        portfolio=_Book([_stale_position(ALICE_SYMBOL, 3000.0)]),
        ws_feed=_Feed({ALICE_SYMBOL: 2900.0}),      # below entry: not in profit
    )


def _stale_two_user_engine():
    """Only ALICE's position is stale; Bob's is fresh, so a walk that kept the
    owner and one that lost it differ in WHO is told, not just how many."""
    from datetime import datetime, timezone
    fresh = _Pos(BOB_SYMBOL, 200.0, 180.0, 240.0, "T-BOB")
    fresh.opened_at = datetime.now(timezone.utc)
    return types.SimpleNamespace(
        user_portfolios=_Books({
            "alice": _Book([_stale_position(ALICE_SYMBOL, 3000.0)]),
            "bob": _Book([fresh]),
        }),
        portfolio=_Book([]),
        ws_feed=_Feed({ALICE_SYMBOL: 2900.0, BOB_SYMBOL: 190.0}),
    )


def _monitor(engine=None, chats=(), admin_fn=None):
    """A monitor with a planted watch list. `hydrate()` is never called, so
    nothing loads from disk and no operator is auto-enrolled."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = engine
    m._enabled_chats = set(chats)
    m._chart_fn = None
    m._admin_fn = admin_fn
    return m


def _delivered(monitor, alerts) -> dict:
    """``{chat_id: [body, ...]}`` — what each chat was actually sent."""
    got: dict = {}

    async def send_fn(chat_id, msg, *a):
        got.setdefault(str(chat_id), []).append(msg)

    async def _run():
        for alert in alerts:
            await monitor._dispatch(alert, send_fn)

    asyncio.run(_run())
    return got


# ── the leak, driven ─────────────────────────────────────────────────────

class TestOneUsersPositionReachesOnlyThem:

    def test_the_proximity_alerts_carry_their_owner(self):
        alerts = _monitor(_two_user_engine())._check_sl_tp_proximity()
        owners = {a.alert_type: a.user_id for a in alerts}
        assert owners == {"SL_PROXIMITY": "alice", "TP_PROXIMITY": "bob"}, owners

    def test_a_watcher_who_holds_nothing_is_told_nothing(self, operators):
        """Carol runs `/watch on` and holds no position. Before the fix she
        received both alerts — the whole leak in one assertion."""
        operators(chat_id="", admin_ids="")
        m = _monitor(_two_user_engine(), chats=("alice", "bob", "carol"))
        got = _delivered(m, m._check_sl_tp_proximity())
        assert "carol" not in got, got.get("carol")

    def test_neither_user_is_told_about_the_other(self, operators):
        operators(chat_id="", admin_ids="")
        m = _monitor(_two_user_engine(), chats=("alice", "bob", "carol"))
        got = _delivered(m, m._check_sl_tp_proximity())
        assert sorted(got) == ["alice", "bob"], sorted(got)
        alice = "\n".join(got["alice"])
        bob = "\n".join(got["bob"])
        assert ALICE_SYMBOL in alice and BOB_SYMBOL not in alice
        assert BOB_SYMBOL in bob and ALICE_SYMBOL not in bob

    def test_the_time_stop_alerts_carry_their_owner(self):
        """The same walk, the same two sources, one method down. Fixing one and
        leaving the other is this repo's own "fixing two left the third"."""
        src = code_only(SRC.read_text(encoding="utf-8"))
        body = _function_source(src, "_check_time_stops")
        # The walk carries whether the book is the operator's LIVE one, which
        # is an audience rather than a person (`_position_walk`).
        assert "for owner, operator_book, pos in all_positions:" in body
        assert body.count("user_id=owner,") == 2, body.count("user_id=owner,")
        assert body.count("audience='admin'") == 2, body.count("audience='admin'")

    def test_the_unprotected_alert_is_scoped_to_the_account_it_is_about(self):
        """CRITICAL, live-only, and it tells the reader to go and place a stop
        by hand on an exchange account. Its own docstring says it "covers every
        executor (operator + per-user)"; every one of those alerts went to
        everybody."""
        alert = _unprotected_for(user_id="bob")
        assert alert.user_id == "bob"
        # DRIVEN, not read off the field: `user_id` has to WIN over the
        # constant audience, and only the dispatch can say whether it does.
        m = _monitor(chats=("bob", "carol"), admin_fn=lambda c: c == "carol")
        assert sorted(_delivered(m, [alert])) == ["bob"]

    def test_the_operators_unprotected_position_is_an_operator_fact(self):
        """`_all_live_executors` documents `user_id is None` as the operator.
        That is an audience, not a person — the body carries the venue's
        rejection of the operator's own order."""
        alert = _unprotected_for(user_id=None)
        assert alert.user_id is None
        assert alert.audience == "admin"
        m = _monitor(chats=("bob", "carol"), admin_fn=lambda c: c == "carol")
        assert sorted(_delivered(m, [alert])) == ["carol"]


# ── the owner who is not watching ────────────────────────────────────────

class TestAnOwnerWhoIsNotWatchingGetsNothingAndNobodyElseDoes:

    def test_it_is_not_broadcast(self, operators):
        """THE FALLBACK IS THE LEAK. A user who never ran `/watch on`, or whose
        book is keyed by a web id with no Telegram chat behind it, is exactly
        the case a "send it to the watchers instead" fallback would take."""
        operators(chat_id="", admin_ids="")
        m = _monitor(chats=("carol", "dave"))
        got = _delivered(m, [_person_alert("alice")])
        assert got == {}, got

    def test_the_silence_is_recorded(self, operators):
        """A silence that is recorded is a different thing from one that is
        not — the argument `_admin_recipients` already makes for its own.

        `caplog` CANNOT SEE THIS. `bot.utils.logger` sets `propagate = False`
        on its loggers, so the root handler pytest installs is never reached
        and `caplog.records` comes back empty however loudly the code logged —
        an assertion that can only ever fail. The handler goes on the logger
        that actually emits.
        """
        operators(chat_id="", admin_ids="")
        m = _monitor(chats=("carol",))
        said: list = []

        class _Catch(logging.Handler):
            def emit(self, record):
                said.append(record.getMessage())

        h = _Catch(level=logging.WARNING)
        system_log.addHandler(h)
        try:
            _delivered(m, [_person_alert("alice")])
        finally:
            system_log.removeHandler(h)
        assert any("alice" in s and "not sent" in s for s in said), said


# ── the public feed ──────────────────────────────────────────────────────

class TestThePersonScopedTitleDoesNotReachTheLandingPage:

    def test_no_feed_event_for_a_person_scoped_alert(self, operators, monkeypatch):
        """`SL Proximity: ETH/USDT` NAMES A SYMBOL ONE PERSON HOLDS. Scoping
        the Telegram send and leaving this emit takes the message from every
        watching chat and gives it to every visitor — the leak moved to a wider
        audience by the fix for it. It is the argument the admin exclusion
        three lines up already makes."""
        operators(chat_id="", admin_ids="")
        from bot.core import agent_feed
        seen: list = []
        monkeypatch.setattr(agent_feed.FEED, "emit",
                            lambda *a, **k: seen.append((a, k)))
        m = _monitor(chats=("alice",))
        _delivered(m, [_person_alert("alice")])
        assert seen == [], seen

    def test_an_unscoped_alert_still_reaches_the_feed(self, operators, monkeypatch):
        """The gate must not silence the broadcast it was never about."""
        operators(chat_id="", admin_ids="")
        from bot.core import agent_feed
        seen: list = []
        monkeypatch.setattr(agent_feed.FEED, "emit",
                            lambda *a, **k: seen.append((a, k)))
        m = _monitor(chats=("alice",))
        _delivered(m, [_person_alert(None)])
        assert len(seen) == 1, seen


# ── what must NOT change ─────────────────────────────────────────────────

class TestTheSharedBookKeepsItsFanOut:

    def test_the_shared_book_has_no_owner(self):
        alerts = _monitor(_shared_book_engine())._check_sl_tp_proximity()
        assert alerts and all(a.user_id is None for a in alerts)

    def test_every_watching_chat_still_receives_it(self, operators):
        """A single-user deploy must behave exactly as it does today. The
        shared book is the bot's own, and the product broadcasts its entries."""
        operators(chat_id="", admin_ids="")
        m = _monitor(_shared_book_engine(), chats=("dave", "erin", "frank"))
        got = _delivered(m, m._check_sl_tp_proximity())
        assert sorted(got) == ["dave", "erin", "frank"], sorted(got)

    def test_an_ordinary_alert_is_untouched(self, operators):
        operators(chat_id="", admin_ids="")
        m = _monitor(chats=("111", "222"))
        got = _delivered(m, [_person_alert(None)])
        assert sorted(got) == ["111", "222"], sorted(got)

    def test_the_time_stop_walk_over_the_shared_book_too(self, operators):
        """THE MUTATION ROUND ASKED FOR THIS ONE. Giving the shared book an
        owner in `_check_time_stops` changed no verdict in the first round,
        because the compat case was driven for `_check_sl_tp_proximity` alone —
        the round reporting a coverage gap rather than a code one, which is
        what it is for. The two walks are near-identical and a fix that lands
        on one is this repo's own "fixing two left the third"."""
        operators(chat_id="", admin_ids="")
        m = _monitor(_stale_shared_book_engine(), chats=("dave", "erin"))
        alerts = m._check_time_stops()
        assert alerts, "the fixture did not reach a time stop"
        assert all(a.user_id is None for a in alerts), [a.user_id for a in alerts]
        got = _delivered(m, alerts)
        assert sorted(got) == ["dave", "erin"], sorted(got)

    def test_the_time_stop_walk_scopes_a_users_book(self, operators):
        """And the other direction on the same walk."""
        operators(chat_id="", admin_ids="")
        m = _monitor(_stale_two_user_engine(), chats=("alice", "bob", "carol"))
        alerts = m._check_time_stops()
        assert alerts, "the fixture did not reach a time stop"
        assert {a.user_id for a in alerts} == {"alice"}, [a.user_id for a in alerts]
        got = _delivered(m, alerts)
        assert sorted(got) == ["alice"], sorted(got)


# ── the operator-account alerts the fan-out was already warned about ─────

class TestTheOperatorsOwnFiguresAreAnOperatorAudience:

    def test_idle_cash_is_admin(self):
        """`$X of free margin` off `engine._live_balance_cache`. `_dispatch`'s
        own comment names "idle-cash balances" as detail that must not reach a
        wider audience, and guarded the public feed with it while the fan-out
        one line below sent the figure to every `/watch on` chat."""
        assert _audience_of("IDLE_CASH") == "admin"

    def test_slippage_is_admin(self):
        """Dollars lost on the operator's own fills. Its method docstring says
        it exists so "the operator" can switch to limit orders, trim size or
        drop the symbol."""
        assert _audience_of("SLIPPAGE_HIGH") == "admin"


# ── the ratchet: a check added tomorrow cannot reintroduce it ────────────

#: The two engine attributes that hand back somebody's own book, and the one
#: walk over both (`_position_walk`) the position checks call instead of
#: reading them: a check that asks the walk reads a per-user book as surely
#: as one that walks it by hand.
PER_USER_SOURCES = ("user_portfolios", "_all_live_executors", "_position_walk")


class TestAPerUserCheckCannotBuildAnUnscopedAlert:

    def test_every_alert_built_over_a_per_user_book_names_its_scope(self):
        """DERIVED FROM THE SOURCE IT READS, not from a list of the five types
        I happened to fix — a hand-written list is the `/setllm`
        ten-of-eleven shape, where the check added tomorrow is the one missing
        from it.

        A method that reads a per-user book must pass `user_id=` or set
        `audience=` on every Alert it constructs. Passing neither is the
        defect: an alert about one person's money, fanned out by a field that
        can only name a class.
        """
        tree = ast.parse(code_only(SRC.read_text(encoding="utf-8")))
        offenders: list = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.unparse(node)
            if not any(s in body for s in PER_USER_SOURCES):
                continue
            for call in ast.walk(node):
                if not (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "Alert"):
                    continue
                named = {k.arg for k in call.keywords}
                if not ({"user_id", "audience"} & named):
                    at = next((ast.literal_eval(k.value) for k in call.keywords
                               if k.arg == "alert_type"
                               and isinstance(k.value, ast.Constant)), "?")
                    offenders.append(f"{node.name} -> {at}")
        assert not offenders, (
            "These alerts are built from a per-user book and name neither a "
            "person nor an audience, so they fan out to every watching chat: "
            + ", ".join(offenders))

    def test_the_rule_can_fail(self):
        """A rule no input can reach is a claim that there is a check. Driven
        on a planted tree where the rule is the only thing in play."""
        planted = ast.parse(
            "def _check_x(self):\n"
            "    for uid in self.engine.user_portfolios.all_portfolios():\n"
            "        yield Alert(alert_type='X', severity='INFO',\n"
            "                    title='t', body='b')\n")
        found = []
        for node in ast.walk(planted):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(s in ast.unparse(node) for s in PER_USER_SOURCES):
                    for call in ast.walk(node):
                        if (isinstance(call, ast.Call)
                                and isinstance(call.func, ast.Name)
                                and call.func.id == "Alert"
                                and not ({"user_id", "audience"}
                                         & {k.arg for k in call.keywords})):
                            found.append(node.name)
        assert found == ["_check_x"], found


# ── the reading is one reading ───────────────────────────────────────────

class TestDispatchAsksTheOneReading:

    def test_dispatch_does_not_pick_recipients_itself(self):
        """A second copy of a routing rule is a second answer about who may
        read somebody's position."""
        body = _function_source(
            code_only(SRC.read_text(encoding="utf-8")), "_dispatch")
        assert "_recipients_for(alert)" in body
        assert "list(self._enabled_chats)" not in body

    def test_the_reading_is_driven_not_scanned(self, operators):
        """Patch the walk and `_dispatch` answers what it said — the one thing
        a source scan cannot check, and the check a byte-identical copy would
        pass."""
        operators(chat_id="", admin_ids="")
        m = _monitor(chats=("alice", "bob"))
        m._recipients_for = lambda alert: ["zoe"]
        got = _delivered(m, [_person_alert(None)])
        assert sorted(got) == ["zoe"], sorted(got)


# ── helpers ──────────────────────────────────────────────────────────────

def _function_source(src: str, name: str) -> str:
    """One function's body by AST lookup, never a slice to "whatever is next" —
    a boundary that is whatever happens to be next manufactures accusations."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def _person_alert(user_id):
    return Alert(alert_type="SL_PROXIMITY", severity="WARNING",
                 title=f"SL Proximity: {ALICE_SYMBOL}",
                 body="stop approaching", user_id=user_id)


def _audience_of(alert_type: str) -> str:
    """The `audience=` this alert type is constructed with, read off the
    source rather than asserted from memory."""
    tree = ast.parse(code_only(SRC.read_text(encoding="utf-8")))
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "Alert"):
            continue
        kw = {k.arg: k.value for k in call.keywords}
        at = kw.get("alert_type")
        if isinstance(at, ast.Constant) and at.value == alert_type:
            aud = kw.get("audience")
            return aud.value if isinstance(aud, ast.Constant) else "all"
    raise AssertionError(f"no Alert(alert_type={alert_type!r}) in {SRC}")


def _unprotected_for(user_id):
    """Run the real `_check_unprotected_positions` over one executor whose
    `user_id` is as given, and hand back the one alert it builds."""
    from datetime import datetime, timedelta, timezone

    from bot.config import CONFIG

    pos = types.SimpleNamespace(
        status="open", symbol="ETH/USDT", trade_id="T-1",
        opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
        sl_order_id=None, unprotected=True, stop_loss=2955.0,
        direction="LONG")
    ex = types.SimpleNamespace(user_id=user_id, open_positions=[pos])
    engine = types.SimpleNamespace(_all_live_executors=lambda: [ex])
    m = _monitor(engine)

    # `is_live()` needs THREE things, not one: both flags AND a configured
    # chat allow-list (its own F-04 guard). The first draft of this helper set
    # `simulation_mode` alone, the check returned early, and both tests failed
    # over a fixture that never reached the code it was driving.
    was = (CONFIG.simulation_mode, CONFIG.live_trading_enabled, CONFIG.telegram)
    object.__setattr__(CONFIG, "simulation_mode", False)
    object.__setattr__(CONFIG, "live_trading_enabled", True)
    object.__setattr__(CONFIG, "telegram",
                       dataclasses.replace(was[2], chat_id="999"))
    try:
        assert CONFIG.is_live(), "fixture did not reach the live branch"
        alerts = m._check_unprotected_positions()
    finally:
        object.__setattr__(CONFIG, "simulation_mode", was[0])
        object.__setattr__(CONFIG, "live_trading_enabled", was[1])
        object.__setattr__(CONFIG, "telegram", was[2])
    assert len(alerts) == 1, alerts
    return alerts[0]


def test_the_helper_reads_the_real_method():
    """`inspect.getsource` on the method the helper drives — so a rename that
    left this file pointing at nothing fails here rather than silently
    measuring a method nobody calls."""
    assert "POSITION_UNPROTECTED" in inspect.getsource(
        ProactiveMonitor._check_unprotected_positions)
