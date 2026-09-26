"""Scheduled alerts, digests and public posts, and whose book each one read.

Seven findings, each driven against the real function before it was fixed:

* A hand-typed /trade ticket went to every watching chat as a NEW SIGNAL and to
  the public channels as "RUNECLAW SIGNAL · AI-generated · Confidence 100%":
  `_check_trade_signals` read the whole pending book. Its card's door, `Say
  "confirm"`, routes nowhere, and it printed a DOGE idea's levels at two
  decimals and a PEPE one's as $0.00.
* /daily_report posted the caller's day to the public channels as RUNECLAW's
  report, once per call, whoever called.
* The public close post put a trophy on a losing trade: a regex for any "+N%",
  and the text leads with the gross move.
* The drawdown early warning read a field RiskEngine does not have and could
  never fire.
* The morning brief and evening wrap went to every watcher, were re-sent by a
  restart, counted never-filled orders as closes and read the equity from a
  key nothing writes.
* In LIVE mode the SL/TP proximity, time-stop and news alerts walked the paper
  books, so no real position ever reached them; the one news alert that fired
  was about a practice book, sent to a watcher who held nothing and published
  to the public feed.
* The auto-confirm notice said AUTO-CONFIRMED TRADE over a refusal.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor

NS = types.SimpleNamespace
OPERATOR = "1"
WATCHER = "888"
UTC = timezone.utc


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def operator_chat():
    """TELEGRAM_CHAT_ID planted on the frozen config and restored."""
    from bot.config import CONFIG
    original = CONFIG.telegram

    def _set(chat_id=OPERATOR):
        object.__setattr__(CONFIG, "telegram", dataclasses.replace(
            original, chat_id=chat_id, admin_ids=""))

    _set()
    yield _set
    object.__setattr__(CONFIG, "telegram", original)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


@pytest.fixture(autouse=True)
def _stamps_in_tmp(monkeypatch, tmp_path):
    import bot.skills.portfolio_commands as pc
    monkeypatch.setattr(pm, "DIGEST_STAMP_PATH", str(tmp_path / "digest.json"))
    monkeypatch.setattr(pc, "PUBLIC_DAILY_POST_STAMP",
                        str(tmp_path / "public_daily.json"))


class _Catch(logging.Handler):
    """`system_log` does not propagate, so caplog cannot see it."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.said: list = []

    def emit(self, record):
        self.said.append(record.getMessage())


@pytest.fixture
def warnings_said():
    from bot.utils.logger import system_log
    h = _Catch()
    system_log.addHandler(h)
    yield h.said
    system_log.removeHandler(h)


@pytest.fixture
def feed(monkeypatch):
    from bot.core import agent_feed
    seen: list = []
    monkeypatch.setattr(agent_feed.FEED, "emit",
                        lambda *a, **k: seen.append(a))
    return seen


# ── the real start_monitor, with stand-ins around it ─────────────────────

class _Bot:
    def __init__(self):
        self.out: list = []          # (chat_id, text, reply_markup)

    async def send_message(self, **kw):
        self.out.append((int(kw["chat_id"]), kw.get("text"),
                         kw.get("reply_markup")))

    async def send_photo(self, **kw):
        self.out.append((int(kw["chat_id"]), kw.get("caption"),
                         kw.get("reply_markup")))

    def public(self):
        return [t for cid, t, _ in self.out if cid < 0]

    def to(self, chat):
        return [(t, m) for cid, t, m in self.out if cid == int(chat)]


class _HookEngine(NS):
    """Records every `set_*_callback` the monitor installs."""

    def __getattr__(self, name):
        if name.startswith("set_") and name.endswith("_callback"):
            return lambda cb, n=name: self.__dict__.setdefault(
                "hooks", {}).__setitem__(n, cb)
        raise AttributeError(name)


def _book_engine(**kw):
    """The engine's pending book with the ownership reading the real engine
    keeps, bound off the class."""
    from bot.core.engine import RuneClawEngine
    eng = _HookEngine(_pending_ideas={}, _engine_idea_ids=set(),
                      _pending_atr={}, _pending_pyramid={},
                      live_executor=NS(_last_close_data=None, _positions={}),
                      **kw)
    for name in ("_engine_pending_ids", "_register_engine_idea"):
        setattr(eng, name, types.MethodType(getattr(RuneClawEngine, name), eng))
    return eng


def _wire(engine, watchers=(OPERATOR, WATCHER)):
    """Run the real `AlertsMonitor.start_monitor` over a real ProactiveMonitor
    and a real ChannelForwarder. Public posts land on the same bot at a
    negative chat id (the forwarder is handed the bot by start_monitor)."""
    from bot.marketing.channel_forwarder import ChannelForwarder
    from bot.skills.alerts_monitor import AlertsMonitor

    mon = ProactiveMonitor(engine)
    mon._enabled_chats = set(watchers)
    mon.hydrate = lambda: None
    got: dict = {}

    async def _run(send_fn):
        got["send"] = send_fn

    mon.run = _run
    fwd = ChannelForwarder.__new__(ChannelForwarder)
    fwd._bot, fwd._group_ids = None, {-1001}
    fwd._lock, fwd._enabled = threading.Lock(), True
    recorded: list = []
    host = NS(engine=engine, monitor=mon, forwarder=fwd,
              users=NS(all_tiers=lambda: {}, get=lambda uid: None,
                       anomaly_prefs=lambda cid: None),
              _is_admin_id=lambda c: str(c) == OPERATOR,
              _note_unprompted=lambda cid, kind, text: recorded.append(
                  (str(cid), kind, text)))

    async def _bu(bot=None):
        return "RuneClawBot"

    async def _no_charts(asset, primary):
        return {}

    host._bot_username, host._fetch_chart_timeframes = _bu, _no_charts
    bot = _Bot()

    async def go():
        await AlertsMonitor.start_monitor(host, bot)
        await asyncio.sleep(0)

    asyncio.run(go())
    return NS(monitor=mon, bot=bot, send=got["send"], hooks=engine.hooks,
              recorded=recorded)


def _deliver(w, alerts):
    async def go():
        for a in alerts:
            await w.monitor._dispatch(a, w.send)
    asyncio.run(go())


# ── E1: the signal card is the engine's own ideas, and its door is real ──

def _engine_idea(asset="DOGE/USDT:USDT", entry=0.1234, sl=0.1201, tp=0.1299):
    from bot.utils.models import Direction, TradeIdea
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=entry,
                     stop_loss=sl, take_profit=tp, confidence=0.78,
                     reasoning="scan", signals_used=["scan"])


class TestOnlyTheEnginesOwnIdeaIsASignal:

    def test_a_hand_typed_ticket_reaches_no_watcher_and_no_public_channel(
            self, operator_chat, feed):
        from bot.skills.manual_trade import build_manual_idea, register_manual_idea
        eng = _book_engine()
        ticket = build_manual_idea("LONG", "PEPE", 0.0000102, 0.0000098, 0.0000115)
        register_manual_idea(eng, ticket)
        eng._register_engine_idea(_engine_idea())
        w = _wire(eng)
        alerts = w.monitor._check_trade_signals()
        assert [a.idea.asset for a in alerts] == ["DOGE/USDT:USDT"]
        _deliver(w, alerts)
        everything = "\n".join(t or "" for _, t, _ in w.bot.out)
        assert "PEPE" not in everything, "a person's ticket was broadcast"
        public = w.bot.public()
        assert len(public) == 1 and "RUNECLAW SIGNAL" in public[0]
        assert "DOGE" in public[0]
        # Both watchers still get the engine's own signal: its fan-out is
        # the product's, and it is unchanged.
        assert w.bot.to(OPERATOR) and w.bot.to(WATCHER)

    def test_an_engine_that_cannot_say_which_ideas_are_its_own_signals_none(self):
        eng = NS(_pending_ideas={"x": _engine_idea()})
        assert ProactiveMonitor(eng)._check_trade_signals() == []

    def test_the_door_is_the_take_it_button_on_this_card(self, operator_chat):
        eng = _book_engine()
        idea = _engine_idea()
        eng._register_engine_idea(idea)
        w = _wire(eng)
        alerts = w.monitor._check_trade_signals()
        body = alerts[0].body
        assert 'Say "confirm"' not in body, "a door that routes nowhere"
        assert "tap ✅ Take it" in body
        assert alerts[0].buttons == [
            ("✅ Take it", f"confirm:{idea.id}:{OPERATOR}"),
            ("Skip", f"reject:{idea.id}:{OPERATOR}")]
        _deliver(w, alerts)
        # The TEXT card carries the button it names, whether or not the
        # signal image rendered.
        cards = [m for t, m in w.bot.to(OPERATOR) if t and "NEW SIGNAL" in t]
        assert cards and cards[0] is not None
        data = [b.callback_data for row in cards[0].inline_keyboard for b in row]
        assert f"confirm:{idea.id}:{OPERATOR}" in data

    def test_the_tag_is_the_operators_and_nobody_elses(self):
        """The image card's Take-it has always been tagged to the configured
        operator chat, and for an engine idea that is right: it is the
        operator's trade. A watcher who is shown the button cannot take it."""
        from bot.skills.telegram_handler import TelegramHandler as H
        assert H._callback_owner_ok(OPERATOR, OPERATOR)
        assert not H._callback_owner_ok(WATCHER, OPERATOR)

    def test_no_operator_chat_means_no_button_and_no_door_sentence(self, operator_chat):
        operator_chat("")
        eng = _book_engine()
        eng._register_engine_idea(_engine_idea())
        alert = ProactiveMonitor(eng)._check_trade_signals()[0]
        assert not alert.buttons
        # "Awaiting operator confirmation" contains "confirm": the sentence
        # that must be gone is the door, so the assertion names the door.
        assert "Take it" not in alert.body and 'Say "confirm"' not in alert.body

    @pytest.mark.parametrize("asset,levels,shown", [
        ("DOGE/USDT:USDT", (0.1234, 0.1201, 0.1299),
         ("$0.12340", "$0.12010", "$0.12990")),
        ("PEPE/USDT:USDT", (0.0000102, 0.0000098, 0.0000115),
         ("$0.00001020", "$0.00000980", "$0.00001150")),
    ])
    def test_the_levels_are_printed_as_the_price_is(self, operator_chat, asset,
                                                    levels, shown):
        eng = _book_engine()
        eng._register_engine_idea(_engine_idea(asset, *levels))
        w = _wire(eng)
        alerts = w.monitor._check_trade_signals()
        body = alerts[0].body
        for want in shown:
            assert want in body, (want, body)
        _deliver(w, alerts)
        public = w.bot.public()[0]
        for want in shown:
            assert want in public, (want, public)


    def test_the_public_trade_opened_post_prints_the_price_too(self):
        """`post_trade_opened` carried the same `:,.4f` one method down: a
        PEPE entry published as `$0.0000`."""
        from bot.marketing.channel_forwarder import ChannelForwarder
        fwd = ChannelForwarder.__new__(ChannelForwarder)
        fwd._bot, fwd._group_ids = _Bot(), {-1001}
        fwd._lock, fwd._enabled = threading.Lock(), True
        idea = _engine_idea("PEPE/USDT:USDT", 0.0000102, 0.0000098, 0.0000115)
        asyncio.run(fwd.post_trade_opened(idea, mode="LIVE"))
        post = fwd._bot.public()[0]
        for want in ("$0.00001020", "$0.00000980", "$0.00001150"):
            assert want in post, (want, post)


# ── E2: the public daily report is the agent's, once a day ───────────────

def _daily(monkeypatch, scope, rows=None, times=1):
    from tests.test_the_daily_report_is_the_days import _close, _live_report
    rows = rows or [_close(5.0, timedelta(minutes=1), "T-1"),
                    _close(-2.0, timedelta(minutes=2), "T-2")]
    said, posted = [], []
    for _ in range(times):
        s, p = asyncio.run(_live_report(monkeypatch, rows, scope=scope))
        said.append(s)
        posted.extend(p)
    return said, posted


class TestThePublicDailyReportIsTheAgentsAndOnceADay:

    def test_a_persons_own_day_is_never_posted(self, monkeypatch):
        said, posted = _daily(monkeypatch, "own", times=3)
        assert posted == [], posted
        assert len(said) == 3 and all("Total" in s for s in said)

    def test_the_agents_day_is_posted_once_whoever_asks(self, monkeypatch):
        import bot.skills.portfolio_commands as pc
        said, posted = _daily(monkeypatch, "operator", times=3)
        assert len(posted) == 1, posted
        assert len(said) == 3, "the private card must still answer every call"
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        with open(pc.PUBLIC_DAILY_POST_STAMP) as fh:
            assert json.load(fh) == {"daily_report": day}

    def test_yesterdays_stamp_does_not_hold_today(self, monkeypatch):
        import bot.skills.portfolio_commands as pc
        with open(pc.PUBLIC_DAILY_POST_STAMP, "w") as fh:
            json.dump({"daily_report": "2001-01-01"}, fh)
        _said, posted = _daily(monkeypatch, "operator")
        assert len(posted) == 1

    def test_an_unreadable_stamp_is_not_not_yet_posted(self, monkeypatch,
                                                         warnings_said):
        import bot.skills.portfolio_commands as pc
        with open(pc.PUBLIC_DAILY_POST_STAMP, "w") as fh:
            fh.write("{not json")
        said, posted = _daily(monkeypatch, "operator", times=2)
        assert posted == []
        assert all("Total" in s for s in said), "the private card was blocked"
        with open(pc.PUBLIC_DAILY_POST_STAMP) as fh:
            assert fh.read() == "{not json", "the stamp file was written over"
        assert any("NOT posted" in s and "unreadable" in s for s in warnings_said)

    def test_a_claim_that_could_not_be_saved_posts_nothing(self, monkeypatch,
                                                            warnings_said):
        import bot.utils.json_store as js

        def _full_disk(*a, **k):
            raise OSError("no space")

        monkeypatch.setattr(js, "atomic_write_json", _full_disk)
        said, posted = _daily(monkeypatch, "operator", times=2)
        assert posted == [] and len(said) == 2
        assert any("unwritten" in s for s in warnings_said)

    def test_the_paper_branch_posts_nothing(self, monkeypatch):
        """In paper mode the command reads the caller's PRACTICE book, which
        is never the agent's."""
        import bot.skills.portfolio_commands as pc
        from tests.test_the_daily_report_is_the_days import _Fwd, _Live
        from tests.test_the_record_cards_read_the_callers_book import Stand
        monkeypatch.setattr(pc, "CONFIG", _Live(False))
        trade = NS(asset="BTC/USDT", pnl=5.0,
                   closed_at=datetime.now(UTC) - timedelta(minutes=1))
        book = NS(trade_history=[trade], snapshot=lambda: NS(max_drawdown_pct=0.0))
        me = Stand({"scope": "operator", "executor": None, "balance": None,
                    "total": None, "age_s": None})
        me.engine.user_portfolios = NS(get=lambda uid: book)
        me.forwarder = _Fwd()
        asyncio.run(pc.PortfolioCommands._cmd_daily_report(me, object(), object()))
        assert me.sent and me.forwarder.posted == []


# ── E3: the close post's icon is the close's net ──────────────────────────

class TestTheClosePostsIconIsTheNet:

    @pytest.mark.parametrize("data,want", [
        ({"pnl_usd": 1.0}, "win"), ({"pnl_usd": -0.1}, "loss"),
        ({"pnl_usd": 0.0}, "flat"),
        ({"pnl_usd": None, "pnl_pct_margin_net": -0.4}, "loss"),
        ({"pnl_pct_margin_net": 2.0}, "win"),
        ({"pnl_pct": 5.0}, None),                  # the gross move alone
        ({"pnl_usd": "junk", "pnl_pct": 5.0}, None),
        ({"pnl_usd": float("nan")}, None),
        (None, None), ({}, None),
    ])
    def test_close_outcome_reads_the_net_or_nothing(self, data, want):
        from bot.marketing.public_text import close_outcome
        assert close_outcome(data) == want

    def _close_post(self, operator_chat, slot, msg):
        eng = _HookEngine(live_executor=NS(_last_close_data=slot, _positions={}))
        w = _wire(eng, watchers=())
        asyncio.run(w.hooks["set_close_notify_callback"](msg))
        return w

    SLOT = {"trade_id": "T-9", "symbol": "SOL/USDT:USDT", "direction": "LONG",
            "reason": "TP1 breakeven stop", "pnl_pct": 0.10,
            "pnl_pct_margin": 2.0, "pnl_pct_margin_net": -0.40,
            "pnl_usd": -0.10, "leverage": 20, "hold_time": "3h 2m",
            "margin_usd": 25.0}
    MSG = ("CLOSED LONG SOL/USDT:USDT (TP1 breakeven stop)\n"
           "Entry: $150.0000 → Exit: $150.1500\n"
           "PnL: -$0.1000 (-0.40% on margin after fees / +0.10% move, 20×)"
           " | Fees: $0.60 | Hold: 3h 2m")

    def test_a_loss_that_moved_up_posts_no_trophy(self, operator_chat):
        w = self._close_post(operator_chat, self.SLOT, self.MSG)
        public = w.bot.public()
        assert len(public) == 1
        assert "\U0001f3c6" not in public[0], "a losing close under a trophy"
        assert public[0].startswith("\U0001f4c9")

    def test_a_win_still_gets_its_trophy(self, operator_chat):
        slot = dict(self.SLOT, pnl_pct=0.5, pnl_pct_margin_net=7.6, pnl_usd=0.76)
        msg = self.MSG.replace("-$0.1000", "+$0.7600")
        w = self._close_post(operator_chat, slot, msg)
        assert w.bot.public()[0].startswith("\U0001f3c6")

    def test_an_unmeasured_net_is_neither(self, operator_chat):
        slot = dict(self.SLOT, pnl_usd=None, pnl_pct_margin_net=None)
        w = self._close_post(operator_chat, slot, self.MSG)
        public = w.bot.public()[0]
        assert public.startswith("⚪"), public
        assert "\U0001f7e2" not in public and "\U0001f534" not in public

    def test_the_private_text_fallback_is_neither(self, operator_chat):
        """No record matches this close, so the public post is the private
        text (scrubbed), and nothing in the record says its sign."""
        w = self._close_post(operator_chat, None,
                             self.MSG.replace("-$0.1000", "+$0.1000"))
        public = w.bot.public()[0]
        assert public.startswith("⚪"), public
        assert "\U0001f3c6" not in public

    def test_the_private_card_reads_an_unread_pnl_as_no_sign(self, operator_chat):
        w = self._close_post(operator_chat, None,
                             "CLOSED LONG SOL/USDT (manual)\nPnL: unread")
        private = w.bot.to(OPERATOR)[0][0]
        assert private.startswith("⚪"), private
        assert "❌" not in private

    def test_the_private_card_still_reads_a_stated_loss(self, operator_chat):
        w = self._close_post(operator_chat, None, self.MSG)
        assert w.bot.to(OPERATOR)[0][0].startswith("❌")


# ── E4: the drawdown early warning reads what the breaker gates on ────────

def _risk(tmp_path, dd):
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    pt = PortfolioTracker(initial_balance=1000.0,
                          state_file=str(tmp_path / "pf.json"))
    risk = RiskEngine(pt, state_file=str(tmp_path / "risk.json"))
    risk._live_equity_peak = 1000.0
    risk._last_live_equity = 1000.0 * (1 - dd / 100.0)
    return risk


class TestTheDrawdownWarningFires:

    def test_it_fires_against_the_live_limit_on_a_real_engine(self, tmp_path,
                                                             live, operator_chat,
                                                             feed):
        risk = _risk(tmp_path, 3.5)
        m = ProactiveMonitor(NS(risk=risk))
        a = m._check_drawdown_tiers()
        assert len(a) == 1 and "50%" in a[0].title
        assert "3.50%" in a[0].body and "7.00%" in a[0].body
        assert "live equity high-water mark" in a[0].body
        assert a[0].audience == "admin"
        risk._last_live_equity = 940.0                       # 6.0 of 7.0 = 86%
        b = m._check_drawdown_tiers()
        assert len(b) == 1 and "85%" in b[0].title and b[0].severity == "CRITICAL"
        m._enabled_chats = {OPERATOR, WATCHER}
        m._admin_fn = lambda c: c == OPERATOR
        got: list = []

        async def send(cid, text, *a):
            got.append(cid)

        asyncio.run(m._dispatch(b[0], send))
        assert got == [OPERATOR], "the operator's drawdown reached a watcher"
        assert feed == []

    def test_an_unread_status_fires_nothing(self):
        m = ProactiveMonitor(NS(risk=NS(drawdown_status=lambda: {})))
        assert m._check_drawdown_tiers() == []

    def _stub(self, source):
        return NS(risk=NS(drawdown_status=lambda: {
            "drawdown_pct": 6.0, "drawdown_source": source,
            "effective_limit_pct": 7.0}))

    def test_a_paper_figure_is_not_the_live_breakers(self, live):
        assert ProactiveMonitor(self._stub("paper"))._check_drawdown_tiers() == []
        assert ProactiveMonitor(self._stub("live"))._check_drawdown_tiers()

    def test_a_paper_figure_is_the_paper_breakers(self):
        assert ProactiveMonitor(self._stub("paper"))._check_drawdown_tiers()


# ── E5: the digests are the operator's, once a period, and count fills ────

def _digest_engine(closed=(), balance=None, balance_age=0.0):
    from bot.core.engine import RuneClawEngine
    ex = NS(open_positions=[NS(symbol="BTC/USDT", direction="LONG")],
            closed_positions=list(closed), closed_trades_read_failed=False)
    eng = NS(live_executor=ex, portfolio=None, state="EngineState.IDLE",
             _live_balance_cache=balance or {},
             _live_balance_cache_ts=(time.monotonic() - balance_age
                                     if balance else 0.0))
    eng.live_balance_cached = types.MethodType(
        RuneClawEngine.live_balance_cached, eng)
    return eng


@pytest.fixture
def digest_hours(monkeypatch):
    monkeypatch.setenv("DAILY_BRIEF_HOUR_UTC", "0")
    monkeypatch.setenv("DAILY_WRAP_HOUR_UTC", "0")


class TestTheDigestsAreTheOperatorsAndSentOnce:

    def test_a_viewer_is_not_sent_the_operators_book(self, digest_hours, feed):
        m = ProactiveMonitor(_digest_engine())
        alerts = m._check_daily_digest()
        assert {a.alert_type for a in alerts} == {"DAILY_BRIEF", "DAILY_WRAP"}
        assert all(a.audience == "admin" for a in alerts)
        m._enabled_chats = {OPERATOR, WATCHER}
        m._admin_fn = lambda c: c == OPERATOR
        got: list = []

        async def send(cid, text, *a):
            got.append(cid)

        async def go():
            for a in alerts:
                await m._dispatch(a, send)

        asyncio.run(go())
        assert got == [OPERATOR, OPERATOR], got
        assert feed == []

    def test_a_restart_does_not_send_them_again(self, digest_hours):
        eng = _digest_engine()
        assert len(ProactiveMonitor(eng)._check_daily_digest()) == 2
        assert ProactiveMonitor(eng)._check_daily_digest() == [], (
            "a fresh process re-sent today's digests")

    def test_an_unreadable_stamp_sends_nothing_and_says_so(self, digest_hours,
                                                           warnings_said):
        with open(pm.DIGEST_STAMP_PATH, "w") as fh:
            fh.write("[1, 2")
        m = ProactiveMonitor(_digest_engine())
        assert m._check_daily_digest() == []
        assert m._check_daily_digest() == []
        with open(pm.DIGEST_STAMP_PATH) as fh:
            assert fh.read() == "[1, 2"
        assert sum("NOT sent" in s for s in warnings_said) == 2, warnings_said

    def test_a_claim_that_could_not_be_saved_is_still_sent_once(
            self, digest_hours, monkeypatch, warnings_said):
        import bot.utils.json_store as js

        def _full_disk(*a, **k):
            raise OSError("no space")

        monkeypatch.setattr(js, "atomic_write_json", _full_disk)
        m = ProactiveMonitor(_digest_engine())
        assert len(m._check_daily_digest()) == 2
        assert m._check_daily_digest() == [], "memory holds it to once"
        assert any("may send it" in s for s in warnings_said)

    def test_the_weekly_parity_digest_survives_a_restart(self, monkeypatch,
                                                          tmp_path):
        now = datetime.now(UTC)
        monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
        monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
        f = tmp_path / "closed.json"
        f.write_text(json.dumps([
            {"symbol": "BTC/USDT", "pnl_usd": 10.0, "fees_usd": 0.5,
             "size_usd": 100.0, "close_reason": "take_profit"},
            {"symbol": "ETH/USDT", "pnl_usd": -4.0, "fees_usd": 0.5,
             "size_usd": 100.0, "close_reason": "stop_loss"}]))
        eng = NS(live_executor=NS(_closed_trades_file=str(f)))
        assert len(ProactiveMonitor(eng)._check_parity_digest()) == 1
        assert ProactiveMonitor(eng)._check_parity_digest() == []

    def test_a_digest_with_nothing_to_send_claims_nothing(self, monkeypatch,
                                                          tmp_path):
        now = datetime.now(UTC)
        monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
        monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
        eng = NS(live_executor=NS(_closed_trades_file=str(tmp_path / "none.json")))
        assert ProactiveMonitor(eng)._check_parity_digest() == []
        with pytest.raises(FileNotFoundError):
            open(pm.DIGEST_STAMP_PATH)

    def test_a_never_filled_order_is_not_a_close(self, live):
        P = NS
        closed = ([P(symbol="BTC/USDT", pnl_usd=41.0, close_reason="TP HIT"),
                   P(symbol="ETH/USDT", pnl_usd=12.5, close_reason="TP HIT"),
                   P(symbol="SOL/USDT", pnl_usd=-20.0, close_reason="SL HIT")]
                  + [P(symbol=f"X{i}/USDT", pnl_usd=0.0, close_reason=r)
                     for i, r in enumerate(["stale_pending", "expired",
                                            "price_drift", "cancelled",
                                            "stale_pending"])])
        body = ProactiveMonitor(_digest_engine(closed))._digest_body("wrap")
        assert "Recent closes: <b>3</b> (<b>2</b> wins of 3 priced)" in body, body

    def test_the_equity_is_the_balance_the_engine_reads(self, live):
        eng = _digest_engine(balance={"total": 12500.0, "free": 8000.0,
                                      "used": 4500.0})
        body = ProactiveMonitor(eng)._digest_body("brief")
        assert ("Equity <code>$12,500.00</code> · free margin "
                "<code>$8,000.00</code>") in body, body

    def test_an_unread_free_margin_is_not_zero(self, live):
        eng = _digest_engine(balance={"total": 12500.0})
        body = ProactiveMonitor(eng)._digest_body("brief")
        assert "Equity <code>$12,500.00</code>" in body
        assert "free margin" not in body

    @pytest.mark.parametrize("balance,age", [(None, 0.0),
                                             ({"total": 12500.0}, 5000.0),
                                             ({"free": 3.0}, 0.0)])
    def test_an_unread_equity_says_unread_in_live(self, live, balance, age):
        body = ProactiveMonitor(_digest_engine(balance=balance,
                                               balance_age=age))._digest_body("wrap")
        assert "Equity <code>unread</code>" in body, body
        assert "$0.00" not in body

    def test_paper_has_no_live_equity_line(self):
        body = ProactiveMonitor(_digest_engine())._digest_body("brief")
        assert "Equity" not in body


# ── E6: in live mode the position alerts walk the live books ──────────────

def _live_pos(symbol="BTC/USDT", entry=64000.0, sl=62700.0, tp=70000.0,
              tid="T-LIVE-1", hours=60.0, status="open", direction="LONG"):
    from bot.core.live_executor import LivePosition
    return LivePosition(trade_id=tid, symbol=symbol, direction=direction,
                        entry_price=entry, quantity=0.01, cost_usd=100.0,
                        stop_loss=sl, take_profit=tp, status=status,
                        opened_at=datetime.now(UTC) - timedelta(hours=hours))


class _Feed:
    def __init__(self, prices):
        self._p = prices

    def is_connected(self):
        return True

    def get_prices(self, max_age_sec=0):
        return self._p


def _live_engine(op_positions=(), users=None, practice=None, shared=(),
                 prices=None):
    op = NS(user_id=None, open_positions=list(op_positions))
    execs = [op] + [NS(user_id=uid, open_positions=list(p))
                    for uid, p in (users or {}).items()]
    books = {uid: NS(open_positions=list(p))
             for uid, p in (practice or {}).items()}
    return NS(live_executor=op, _all_live_executors=lambda: execs,
              user_portfolios=NS(all_portfolios=lambda: books,
                                 get=lambda uid: books[uid]),
              portfolio=NS(open_positions=list(shared)),
              ws_feed=_Feed(prices or {"BTC/USDT": 63000.0,
                                       "ETH/USDT": 2960.0}))


def _sent(m, alerts, watchers=(OPERATOR, WATCHER, "777")):
    m._enabled_chats = set(watchers)
    m._admin_fn = lambda c: c == OPERATOR
    got: dict = {}

    async def send(cid, text, *a):
        got.setdefault(cid, []).append(text)

    async def go():
        for a in alerts:
            await m._dispatch(a, send)

    asyncio.run(go())
    return got


class TestTheLiveBooksReachThePositionAlerts:

    def test_the_operators_live_position_is_alerted_to_the_operator(
            self, live, operator_chat, feed):
        m = ProactiveMonitor(_live_engine([_live_pos()]))
        prox = m._check_sl_tp_proximity()
        stops = m._check_time_stops()
        assert [a.alert_type for a in prox] == ["SL_PROXIMITY"]
        assert [a.alert_type for a in stops] == ["TIME_STOP_CLOSE"]
        for a in prox + stops:
            assert a.audience == "admin" and a.user_id is None
        got = _sent(m, prox + stops)
        assert sorted(got) == [OPERATOR], got
        assert feed == [], "an operator position reached the public feed"

    def test_a_per_user_live_position_reaches_only_its_owner(self, live, feed):
        eth = _live_pos("ETH/USDT", 3000.0, 2955.0, 3300.0, "T-U-1")
        m = ProactiveMonitor(_live_engine(users={"777": [eth]}))
        prox = m._check_sl_tp_proximity()
        assert [(a.alert_type, a.user_id) for a in prox] == [("SL_PROXIMITY", "777")]
        assert sorted(_sent(m, prox)) == ["777"]
        assert feed == []

    def test_a_resting_order_is_not_a_position(self, live):
        m = ProactiveMonitor(_live_engine([_live_pos(status="pending_fill")]))
        assert m._check_sl_tp_proximity() == [] and m._check_time_stops() == []

    def test_a_per_user_book_with_no_id_is_skipped_and_said(self, live,
                                                            warnings_said):
        eng = _live_engine()
        eng._all_live_executors = lambda: [eng.live_executor,
                                           NS(user_id="", open_positions=[_live_pos()])]
        assert ProactiveMonitor(eng)._check_sl_tp_proximity() == []
        assert any("no account id" in s for s in warnings_said)

    def test_the_shared_paper_book_is_not_the_live_book(self, live):
        """A position left in the operator's paper book is not a live one,
        and in live mode it is not walked."""
        paper = NS(asset="BTC/USDT", direction=NS(value="LONG"),
                   entry_price=64000.0, stop_loss=62700.0, take_profit=70000.0,
                   trade_id="T-PAPER", opened_at=None)
        m = ProactiveMonitor(_live_engine(shared=[paper]))
        assert m._check_sl_tp_proximity() == []

    def test_a_practice_book_is_still_scoped_to_its_owner(self, live):
        paper = NS(asset="ETH/USDT", direction=NS(value="LONG"),
                   entry_price=3000.0, stop_loss=2955.0, take_profit=3300.0,
                   trade_id="T-PR", opened_at=None)
        m = ProactiveMonitor(_live_engine(practice={"777": [paper]}))
        assert [a.user_id for a in m._check_sl_tp_proximity()] == ["777"]


def _radar(*pairs):
    from bot.core.news import Impact, NewsItem, NewsRadar
    radar = NewsRadar()
    radar.ingest([NewsItem(title=title, url=f"https://news.example/{base}",
                           source="feed", published_ts=time.time() - 120,
                           impact=Impact.HIGH, impact_reasons=("hack",),
                           symbols=(base,))
                  for base, title in pairs])
    return radar


@pytest.fixture
def news_on(monkeypatch):
    monkeypatch.setenv("NEWS_RADAR_ENABLED", "1")
    monkeypatch.delenv("NEWS_STANDDOWN_ALERTS", raising=False)


class TestTheNewsStandDownIsAboutAHolder:

    def test_each_holder_is_told_about_their_own_and_the_feed_is_not(
            self, live, operator_chat, news_on, feed):
        eng = _live_engine([_live_pos()],
                           practice={"777": [NS(asset="PEPE/USDT")]})
        eng._news_radar = _radar(("BTC", "Bitcoin exchange hacked"),
                                 ("PEPE", "PEPE exploit drains pool"))
        m = ProactiveMonitor(eng)
        alerts = m._check_news_standdown()
        scoped = {(a.title, a.audience, a.user_id) for a in alerts}
        assert scoped == {("High-impact news · BTC", "admin", None),
                          ("High-impact news · PEPE", "all", "777")}, scoped
        got = _sent(m, alerts)
        assert "BTC" in got[OPERATOR][0] and len(got[OPERATOR]) == 1
        assert WATCHER not in got, "a watcher holding nothing was told"
        assert "PEPE" in got["777"][0]
        assert feed == [], "a symbol one person holds reached the public feed"

    def test_two_holders_of_one_symbol_are_each_told(self, live, news_on):
        eng = _live_engine([_live_pos()],
                           users={"777": [_live_pos(tid="T-U-2")]})
        eng._news_radar = _radar(("BTC", "Bitcoin exchange hacked"))
        alerts = ProactiveMonitor(eng)._check_news_standdown()
        assert sorted((a.audience, a.user_id or "") for a in alerts) == [
            ("admin", ""), ("all", "777")]

    def test_the_shared_paper_book_keeps_its_fan_out(self, news_on, feed):
        eng = NS(user_portfolios=None,
                 portfolio=NS(open_positions=[NS(asset="BTC/USDT")]),
                 _news_radar=_radar(("BTC", "Bitcoin exchange hacked")))
        m = ProactiveMonitor(eng)
        alerts = m._check_news_standdown()
        assert [(a.audience, a.user_id) for a in alerts] == [("all", None)]
        got = _sent(m, alerts, watchers=(OPERATOR, WATCHER))
        assert sorted(got) == [OPERATOR, WATCHER] and len(feed) == 1


# ── E7: an auto-confirm that placed nothing says so ───────────────────────

def _auto(monkeypatch, operator_chat, result):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", OPERATOR)
    monkeypatch.delenv("ADMIN_CHAT_ID", raising=False)
    w = _wire(_HookEngine(live_executor=NS(_last_close_data=None, _positions={})),
              watchers=())
    idea = _engine_idea("PEPE/USDT:USDT", 0.0000102, 0.0000098, 0.0000115)
    asyncio.run(w.hooks["set_auto_confirm_notify_callback"](idea, result))
    return w


class TestAnAutoConfirmThatPlacedNothingSaysSo:

    @pytest.mark.parametrize("result", [
        "Trade REJECTED by risk: DAILY_LOSS 5.10% >= 5.00% limit",
        "⏭️ Skipped: already have an open/pending order for PEPE/USDT",
    ])
    def test_a_refusal_is_not_an_auto_confirmed_trade(self, monkeypatch,
                                                       operator_chat, result):
        w = _auto(monkeypatch, operator_chat, result)
        card = w.bot.to(OPERATOR)[0][0]
        assert "AUTO-CONFIRMED TRADE" not in card
        assert "NOTHING PLACED" in card
        assert "nothing was placed" in card
        assert "Confidence exceeded auto-confirm threshold" not in card
        assert [k for _, k, _ in w.recorded] == ["AUTO_CONFIRM_REFUSED"]

    def test_an_unreadable_answer_placed_nothing_that_anyone_can_say(
            self, monkeypatch, operator_chat):
        w = _auto(monkeypatch, operator_chat, None)
        card = w.bot.to(OPERATOR)[0][0]
        assert "NOTHING PLACED" in card and "could not be read" in card

    def test_a_placement_is_still_announced(self, monkeypatch, operator_chat):
        w = _auto(monkeypatch, operator_chat, "<b>Filled</b> 0.5 PEPE")
        card = w.bot.to(OPERATOR)[0][0]
        assert "AUTO-CONFIRMED TRADE" in card and "NOTHING PLACED" not in card
        assert "→ Filled 0.5 PEPE" in card
        assert [k for _, k, _ in w.recorded] == ["AUTO_CONFIRMED"]
        assert "$0.00001020" in card, "the entry printed as a price"
