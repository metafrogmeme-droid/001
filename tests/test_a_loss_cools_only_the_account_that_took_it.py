"""A loss cools down the account that took it, and nothing else.

`_tick` stops scanning while `engine._cooldown_until` is set, for every
account at once, and the live monitoring loop set it after ANY book's loss --
the operator's or any per-user account's. Driven before the fix: one per-user
live loss left the engine COOLING_DOWN for 120s, so one person's losing close
paused the agent for everybody.

The per-account wait already existed and nobody had to build it: each account
has its own `RiskEngine` (`risk_for(uid)`), `_on_live_position_closed` records
a priced close into it, and its COOLDOWN check refuses that account's next
confirm for `COOLDOWN_AFTER_LOSS_SEC`. So the fix is three narrowings:

  * the engine-wide pause is armed by the OPERATOR's book alone, decided by
    executor IDENTITY (`_ex is self.live_executor`), never by an id;
  * a close NOBODY COULD PRICE now arms the owning account's own wait. The
    engine-wide pause used to be the only thing that covered it, and cooling
    down on an unpriced close is the cheap side of the asymmetry
    `loss_cooldown_reason` states -- so narrowing the pause without this
    would have left an unpriced per-user close cooling nobody;
  * a PRACTICE close (the sim opt-in book) feeds no risk engine at all.

The third is the larger finding. A per-user paper book is practice -- the bot
places no paper trade of its own and the one writer into those books is the
sim opt-in fill -- and its closes were routed into `risk_for(user_id)`, which
with per-user live off is the OPERATOR's live engine. Driven: ten practice
wins took the operator's live-performance governor from PAUSE (size x0.00) to
OK (x1.00), and the live loss streak from 16 to 6; one practice loss armed the
live cooldown. Practice results were sizing real trades, in the loosening
direction. The practice book keeps a post-loss wait of its own, read off its
own ledger where its entries are made (`practice_cooldown_reason`).
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.core.engine as engine_mod
from bot.compat import UTC
from bot.core.engine import AgentState, RuneClawEngine, practice_cooldown_reason
from bot.utils.models import Direction, RiskCheck, RiskVerdict, TradeIdea
from tests.test_a_close_reaches_whoever_holds_the_position import (  # noqa: F401
    OWNER,  # live_multi_user is a fixture, used by name
    _Ex,
    live_multi_user,
)
from tests.test_core import _DEFAULT_ATR, _DEFAULT_MAX_POS, _make_idea

CLOSE = "BTC/USDT LONG closed\nPnL: -$5.00 (-1.20%)"
CD = engine_mod.CONFIG.risk.cooldown_after_loss_seconds
OTHER = "222"


def _now():
    return engine_mod.datetime.now(UTC)


def _closed(pnl, *, ago=1.0, symbol="BTC/USDT"):
    """A live close, as the executor's ledger holds it."""
    return NS(closed_at=_now() - timedelta(seconds=ago), pnl_usd=pnl,
              symbol=symbol)


def _pass(real):
    real._last_sltp_verify_ts = time.monotonic()
    for name in ("close", "fill", "sync"):
        setattr(real, f"_{name}_notify_callback", AsyncMock())
    real._owner_notify_callback = AsyncMock()
    asyncio.run(real._check_open_positions())


def _paused(real):
    return bool(real._cooldown_until) and time.monotonic() < real._cooldown_until


def _cooldown_line(risk):
    """The COOLDOWN check's own line, and whether it refused."""
    check = risk.evaluate(_make_idea(), atr=_DEFAULT_ATR,
                          max_position_usd=_DEFAULT_MAX_POS)
    failed = [x for x in check.checks_failed if x.startswith("COOLDOWN")]
    passed = [x for x in check.checks_passed if x.startswith("COOLDOWN")]
    assert len(failed) + len(passed) == 1, (failed, passed)
    return (failed or passed)[0], bool(failed)


# ── the engine-wide pause is the operator's book's ───────────────────────

@pytest.mark.usefixtures("live_multi_user")
class TestThePauseIsTheOperatorsBook:
    """Driven through the real `_check_open_positions`."""

    def _engine(self, *, op_closes=(), user_closes=()):
        real = RuneClawEngine()
        real.live_executor = _Ex(None, checked=[CLOSE] if op_closes else [])
        real.live_executor._closed_trades = list(op_closes)
        mine = _Ex(OWNER, checked=[CLOSE] if user_closes else [])
        mine._closed_trades = list(user_closes)
        real._user_executors = {OWNER: mine}
        return real

    @pytest.mark.parametrize("pnl", [-5.0, None], ids=["loss", "unpriced"])
    def test_a_users_close_does_not_pause_the_engine(self, pnl):
        real = self._engine(user_closes=[_closed(pnl)])
        _pass(real)
        assert not _paused(real)
        assert real.state != AgentState.COOLING_DOWN

    @pytest.mark.parametrize("pnl", [-5.0, None], ids=["loss", "unpriced"])
    def test_the_operators_close_still_pauses_it(self, pnl):
        real = self._engine(op_closes=[_closed(pnl)])
        _pass(real)
        assert _paused(real)
        assert real.state == AgentState.COOLING_DOWN

    def test_a_users_loss_beside_the_operators_win_pauses_nothing(
            self):
        # Both books close in one pass: the ledger read is per book, so the
        # user's loss cannot reach the pause through the operator's pass.
        real = self._engine(op_closes=[_closed(+3.0)],
                            user_closes=[_closed(-5.0)])
        _pass(real)
        assert not _paused(real)

    def test_the_operators_loss_beside_a_users_win_still_pauses(
            self):
        real = self._engine(op_closes=[_closed(-5.0)],
                            user_closes=[_closed(+3.0)])
        _pass(real)
        assert _paused(real)

    def test_a_book_with_no_id_is_not_the_operators(self):
        # Identity decides, never `user_id is None`.
        real = self._engine()
        stray = _Ex(None, checked=[CLOSE])
        stray._closed_trades = [_closed(-5.0)]
        real._user_executors = {"x": stray}
        _pass(real)
        assert not _paused(real)


# ── the account that took the loss waits ─────────────────────────────────

@pytest.fixture
def two_accounts(request):
    request.getfixturevalue("live_multi_user")
    real = RuneClawEngine()
    real._is_operator_user = lambda uid: False
    # The close path publishes to the website on background threads; those
    # are not this suite's subject, and a thread the test started reaching a
    # venue is charged to it.
    real._sync_flight_records = lambda: None
    real._sync_live_state_to_website = lambda: None
    return real


def _close_on(real, pnl, uid):
    pos = NS(symbol="BTC/USDT", direction="LONG", trade_id="T-1", pnl_usd=pnl,
             opened_at=_now() - timedelta(minutes=5), closed_at=_now(),
             entry_price=100.0, close_price=99.0, stop_loss=98.0,
             take_profit=106.0, quantity=1.0, cost_usd=10.0, leverage=5,
             fill_source="exchange", close_reason="SL HIT")
    real._on_live_position_closed(pos, uid)


class TestTheAccountThatTookItWaits:
    def test_a_users_loss_refuses_only_that_users_next_entry(self, two_accounts):
        real = two_accounts
        _close_on(real, -5.0, OWNER)
        line, refused = _cooldown_line(real.risk_for(OWNER))
        assert refused and line.endswith("after last loss"), line
        assert _cooldown_line(real.risk)[1] is False
        assert _cooldown_line(real.risk_for(OTHER))[1] is False

    def test_an_unpriced_close_refuses_that_users_next_entry(self, two_accounts):
        real = two_accounts
        _close_on(real, None, OWNER)
        mine = real.risk_for(OWNER)
        line, refused = _cooldown_line(mine)
        assert refused, line
        assert line.endswith("after a close that could not be priced"), line
        # ... and says it was not a loss, because it was not one.
        assert "last loss" not in line
        assert _cooldown_line(real.risk)[1] is False
        assert _cooldown_line(real.risk_for(OTHER))[1] is False

    def test_an_unpriced_close_is_not_counted_as_a_loss(self, two_accounts):
        real = two_accounts
        _close_on(real, None, OWNER)
        mine = real.risk_for(OWNER)
        assert mine._consecutive_losses == 0
        assert list(mine._realized_pnl_window) == []
        assert mine._last_loss_time is None
        assert mine.live_daily_pnl_today() == 0.0

    def test_the_operators_unpriced_close_refuses_the_operators_entry(
            self, two_accounts):
        real = two_accounts
        _close_on(real, None, "")
        line, refused = _cooldown_line(real.risk)
        assert refused and "could not be priced" in line, line

    def test_the_wait_names_the_newer_of_the_two(self, two_accounts):
        mine = two_accounts.risk_for(OWNER)
        mine._last_loss_time = mine._now() - 60
        mine.note_unpriced_close()
        assert "could not be priced" in _cooldown_line(mine)[0]
        mine._last_unpriced_close_time = mine._now() - 90
        assert _cooldown_line(mine)[0].endswith("after last loss")

    def test_the_wait_ends(self, two_accounts):
        mine = two_accounts.risk_for(OWNER)
        mine.note_unpriced_close()
        mine._last_unpriced_close_time -= CD + 1
        line, refused = _cooldown_line(mine)
        assert not refused and "elapsed" in line, line


# ── a practice close never touches a live account ───────────────────────

def _live_state(risk):
    return (risk._consecutive_losses, list(risk._realized_pnl_window),
            risk._last_loss_time, risk._last_unpriced_close_time)


def _practice_close(real, uid, pnl, *, asset="BTC/USDT"):
    """Open and close one practice position through the real paper book."""
    book = real.user_portfolios.get(uid)
    idea = TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=90.0, take_profit=120.0, confidence=0.7,
                     reasoning="practice", source="scan")
    trade = book.open_position(idea, 50.0, leverage=1)
    exit_px = 100.0 * (1 + pnl / 50.0)
    book.close_position(trade.trade_id, exit_px)
    return book.trade_history[-1]


@pytest.mark.usefixtures("live_multi_user")
class TestPracticeNeverTouchesLive:
    @pytest.mark.parametrize("per_user", [False, True], ids=["shared", "per-user"])
    def test_a_practice_loss_reaches_no_risk_engine(self, per_user):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", per_user)
        real = RuneClawEngine()
        real._is_operator_user = lambda uid: False
        before_op = _live_state(real.risk)
        before_mine = _live_state(real.risk_for(OWNER))
        t = _practice_close(real, OWNER, -5.0)
        assert t.pnl < 0                         # it really was a loss
        assert _live_state(real.risk) == before_op
        assert _live_state(real.risk_for(OWNER)) == before_mine
        assert _cooldown_line(real.risk)[1] is False

    def test_practice_wins_do_not_unpause_the_live_governor(self):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", False)
        real = RuneClawEngine()
        for i in range(20):
            real.risk.record_live_trade_result(10.0 if i < 4 else -10.0)
        assert real.risk.live_performance_state()["status"] == "PAUSE"
        for _ in range(10):
            _practice_close(real, OWNER, +5.0)
        assert real.risk.live_performance_state()["status"] == "PAUSE"
        assert real.risk.live_performance_size_multiplier == 0.0

    def test_both_callbacks_are_off_not_only_the_user_aware_one(self):
        # MultiUserPortfolio falls back to the plain callback whenever the
        # user-aware one is unset, so the plain one is the same door.
        real = RuneClawEngine()
        assert real.user_portfolios._on_trade_close is None
        assert real.user_portfolios._on_trade_close_user is None

    def test_a_practice_stop_out_does_not_pause_the_engine(self):
        real = RuneClawEngine()
        book = real.user_portfolios.get(OWNER)
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=95.0, take_profit=120.0,
                         confidence=0.7, reasoning="practice", source="scan")
        book.open_position(idea, 50.0, leverage=1)
        real._fetch_prices_by_category = AsyncMock(return_value={"BTC/USDT": 94.0})
        real._last_known_prices = {}
        asyncio.run(real._check_paper_positions(real.portfolio.open_positions))
        assert book.trade_history and book.trade_history[-1].pnl < 0
        assert not _paused(real)
        assert real.state != AgentState.COOLING_DOWN

    def test_a_shared_book_stop_out_still_pauses_it(self, monkeypatch):
        # The shared book is the engine's own, and a restored position can
        # still close there; its loss keeps the pause it always had. Its close
        # callback mirrors the book to the website on a thread -- not this
        # test's subject.
        monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                            lambda *a, **k: None)
        real = RuneClawEngine()
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=95.0, take_profit=120.0,
                         confidence=0.7, reasoning="shared", source="scan")
        real.portfolio.open_position(idea, 50.0, leverage=1)
        real._fetch_prices_by_category = AsyncMock(return_value={"BTC/USDT": 94.0})
        real._last_known_prices = {}
        asyncio.run(real._check_paper_positions(real.portfolio.open_positions))
        assert _paused(real)


# ── the practice book's own wait ─────────────────────────────────────────

NOW = engine_mod.datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def _history(*rows):
    """Rows stamped against ONE fixed clock the reading is handed too, so a
    boundary case sits exactly on its boundary rather than a few
    microseconds past it -- where `<` and `<=` agree."""
    return [NS(asset=a, pnl=p, closed_at=NOW - timedelta(seconds=ago))
            for a, p, ago in rows]


class TestThePracticeBooksOwnWait:
    def test_a_recent_practice_loss_waits(self):
        why = practice_cooldown_reason(_history(("ETH/USDT", -2.0, 30)), NOW, 120)
        assert why is not None
        assert "practice loss on ETH/USDT 30s ago" in why
        assert "wait 90s more" in why and "Nothing was opened" in why

    @pytest.mark.parametrize("rows", [
        [],
        [("ETH/USDT", +2.0, 10)],               # a win
        [("ETH/USDT", 0.0, 10)],                # a measured break-even
        [("ETH/USDT", -2.0, 121)],              # outside the wait
        [("ETH/USDT", -2.0, 120)],              # exactly at its end
    ], ids=["empty", "win", "flat", "old", "boundary"])
    def test_nothing_to_wait_for(self, rows):
        assert practice_cooldown_reason(_history(*rows), NOW, 120) is None

    def test_the_newest_loss_decides(self):
        why = practice_cooldown_reason(
            _history(("OLD/USDT", -9.0, 100), ("NEW/USDT", -1.0, 20)), NOW, 120)
        assert "NEW/USDT 20s ago" in why

    def test_a_close_stamped_in_the_future_waits_the_whole_time(self):
        why = practice_cooldown_reason(_history(("ETH/USDT", -2.0, -60)), NOW, 120)
        assert "0s ago" in why and "wait 120s more" in why

    def test_a_row_with_no_close_time_or_no_pnl_is_skipped(self):
        rows = [NS(asset="A", pnl=-1.0, closed_at=None),
                NS(asset="B", pnl=None, closed_at=NOW)]
        assert practice_cooldown_reason(rows, NOW, 120) is None


def _fill_engine():
    real = RuneClawEngine()
    real.live_executor = MagicMock()
    real.live_executor.execute = AsyncMock(
        side_effect=AssertionError("a practice fill must never reach the venue"))
    return real


def _recheck():
    return RiskCheck(trade_id="T1", verdict=RiskVerdict.APPROVED,
                     position_size_usd=50.0, position_pct=1.0)


def _pending(real):
    idea = TradeIdea(asset="SOL/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=98.0, take_profit=106.0,
                     confidence=0.8, reasoning="practice", source="scan")
    real._pending_ideas["T1"] = idea
    return idea


class TestThePracticeFillWaits:
    def test_a_practice_entry_after_a_practice_loss_waits(self):
        real = _fill_engine()
        _practice_close(real, OWNER, -5.0)
        before = len(real.user_portfolios.get(OWNER).open_positions)
        msg = asyncio.run(real._simulate_paper_fill(
            _pending(real), _recheck(), OWNER, "T1"))
        assert msg.startswith("⏸ [PAPER]") and "Nothing was opened" in msg
        assert len(real.user_portfolios.get(OWNER).open_positions) == before
        assert "T1" in real._pending_ideas          # still pending, not consumed

    def test_another_practice_book_does_not_wait(self):
        real = _fill_engine()
        _practice_close(real, OWNER, -5.0)
        msg = asyncio.run(real._simulate_paper_fill(
            _pending(real), _recheck(), OTHER, "T1"))
        assert "⏸" not in msg and "[PAPER]" in msg
        assert len(real.user_portfolios.get(OTHER).open_positions) == 1

    def test_a_practice_win_does_not_wait(self):
        real = _fill_engine()
        _practice_close(real, OWNER, +5.0)
        msg = asyncio.run(real._simulate_paper_fill(
            _pending(real), _recheck(), OWNER, "T1"))
        assert "⏸" not in msg
        assert len(real.user_portfolios.get(OWNER).open_positions) == 1
