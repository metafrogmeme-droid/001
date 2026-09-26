"""The DAILY REPORT reports the day.

Both branches of `/daily_report` counted every close ever recorded under a
heading that says DAILY, and the same figures were forwarded to the public
channels as the day's. Driven with one close today (+$5) beside two older
ones, the card read Total 3, Net +$35.00, Best +$50.00 (a close from thirty
days ago), and the public post read "Trades: 3 | W/L: 2/1 | Win Rate: 67%"
for a day with one winning trade. `closes_on_utc_day` is the day: the
current UTC day by each close's recorded time. A close whose time cannot be
read is not filed as today's; it is counted apart and the card says how many.

Best and Worst were also coloured by position rather than by their figures,
so the day's only trade (+$5) was painted red as its Worst.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest

import bot.skills.portfolio_commands as pc
from bot.skills.portfolio_commands import PortfolioCommands, closes_on_utc_day
from bot.warroom.warroom_bot import render_daily_report
from tests.test_the_record_cards_read_the_callers_book import Stand, _Closed

UTC = timezone.utc
NOW = datetime.now(UTC)


def _close(pnl, ago, tid):
    c = _Closed(pnl, tid, "BTC/USDT")
    c.closed_at = None if ago is None else NOW - ago
    return c


class _Fwd:
    def __init__(self):
        self.posted = []

    async def post_daily_report(self, text):
        self.posted.append(text)


class _Live:
    def __init__(self, live):
        self._live = live

    def is_live(self):
        return self._live

    def __getattr__(self, name):
        return getattr(_REAL_CONFIG, name)


_REAL_CONFIG = pc.CONFIG


@pytest.fixture(autouse=True)
def _public_post_stamp_in_tmp(monkeypatch, tmp_path):
    """The public post's once-a-day stamp is on disk; keep it out of data/."""
    monkeypatch.setattr(pc, "PUBLIC_DAILY_POST_STAMP",
                        str(tmp_path / "public_daily_report.json"))


async def _live_report(monkeypatch, rows, scope="own"):
    monkeypatch.setattr(pc, "CONFIG", _Live(True))
    monkeypatch.setattr(pc, "_caller_dd_status", lambda engine, uid: {})
    book = types.SimpleNamespace(closed_positions=rows, open_positions=[],
                                 closed_trades_read_failed=False)
    me = Stand({"scope": scope, "executor": book, "balance": None,
                "total": None, "age_s": None})
    me.forwarder = _Fwd()
    await PortfolioCommands._cmd_daily_report(me, object(), object())
    return "\n".join(me.sent), me.forwarder.posted


THREE = [(50.0, timedelta(days=30)), (-20.0, timedelta(days=10)),
         (5.0, timedelta(minutes=1))]


@pytest.mark.asyncio
async def test_the_card_counts_only_the_days_closes(monkeypatch):
    rows = [_close(p, ago, f"T-{i}") for i, (p, ago) in enumerate(THREE)]
    said, posted = await _live_report(monkeypatch, rows)
    assert "Total ·················· 1" in said, said
    assert "$+5.00" in said and "$+35.00" not in said and "$+50.00" not in said
    assert "Today, UTC" in said


@pytest.mark.asyncio
async def test_the_public_post_is_the_days(monkeypatch):
    # The OPERATOR's book: only the agent's own day is published as its
    # report (tests/test_the_scheduled_posts_say_whose_book_they_read.py).
    rows = [_close(p, ago, f"T-{i}") for i, (p, ago) in enumerate(THREE)]
    _said, posted = await _live_report(monkeypatch, rows, scope="operator")
    assert len(posted) == 1
    assert "Trades: <code>1</code>" in posted[0] and "W/L: <code>1/0</code>" in posted[0]


@pytest.mark.asyncio
async def test_a_day_with_no_close_posts_nothing(monkeypatch):
    rows = [_close(50.0, timedelta(days=2), "T-old")]
    # On the operator's book, which is the only one that may post at all.
    said, posted = await _live_report(monkeypatch, rows, scope="operator")
    assert posted == []
    assert "Total ·················· 0" in said


@pytest.mark.asyncio
async def test_a_close_with_no_time_is_not_todays_and_is_counted(monkeypatch):
    rows = [_close(5.0, timedelta(minutes=1), "T-now"), _close(9.0, None, "T-untimed")]
    said, _posted = await _live_report(monkeypatch, rows)
    assert "Total ·················· 1" in said
    assert "1 close(s) with no recorded time left out" in said


@pytest.mark.asyncio
async def test_the_paper_branch_is_the_day_too(monkeypatch):
    monkeypatch.setattr(pc, "CONFIG", _Live(False))
    old = types.SimpleNamespace(pnl=50.0, asset="ETH/USDT", closed_at=NOW - timedelta(days=3))
    new = types.SimpleNamespace(pnl=5.0, asset="BTC/USDT", closed_at=NOW - timedelta(minutes=1))
    book = types.SimpleNamespace(
        trade_history=[old, new],
        snapshot=lambda: types.SimpleNamespace(max_drawdown_pct=0.0))
    me = Stand({})
    me.engine.user_portfolios = types.SimpleNamespace(get=lambda uid: book)
    me.forwarder = _Fwd()
    await PortfolioCommands._cmd_daily_report(me, object(), object())
    said = "\n".join(me.sent)
    assert "Total ·················· 1" in said, said
    assert "$+5.00" in said and "ETH" not in said


class TestTheDay:
    NOW = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)

    def _row(self, at):
        return types.SimpleNamespace(closed_at=at)

    def test_midnight_is_today_and_a_second_before_is_not(self):
        rows = [self._row(datetime(2026, 9, 26, 0, 0, tzinfo=UTC)),
                self._row(datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC))]
        today, untimed = closes_on_utc_day(rows, self.NOW)
        assert today == rows[:1] and untimed == 0

    def test_a_naive_time_is_utc_and_an_iso_string_is_read(self):
        rows = [self._row(datetime(2026, 9, 26, 1, 0)),
                self._row("2026-09-26T02:00:00+00:00")]
        today, untimed = closes_on_utc_day(rows, self.NOW)
        assert len(today) == 2 and untimed == 0

    @pytest.mark.parametrize("at", [None, "not a time", 12345])
    def test_an_unreadable_time_is_counted_apart(self, at):
        today, untimed = closes_on_utc_day([self._row(at)], self.NOW)
        assert today == [] and untimed == 1

    def test_the_day_is_utc_whatever_zone_now_is_in(self):
        tokyo = timezone(timedelta(hours=9))
        now = datetime(2026, 9, 26, 3, 0, tzinfo=tokyo)   # 2026-09-25 18:00 UTC
        rows = [self._row(datetime(2026, 9, 25, 12, 0, tzinfo=UTC))]
        today, _ = closes_on_utc_day(rows, now)
        assert today == rows


class TestTheColours:
    def _card(self, best, worst):
        return render_daily_report({
            "trades": 1, "wins": 1, "losses": 0, "net_pnl": 5.0,
            "best_trade": "BTC", "best_pnl": best,
            "worst_trade": "BTC", "worst_pnl": worst, "risk_status": "Healthy"})["text"]

    def test_a_winning_worst_is_not_red(self):
        text = self._card(5.0, 5.0)
        worst_line = next(line for line in text.splitlines() if "Worst" in line)
        assert "\U0001f534" not in worst_line and "\U0001f7e2" in worst_line

    def test_a_losing_best_is_not_green(self):
        text = self._card(-2.0, -9.0)
        best_line = next(line for line in text.splitlines() if "Best" in line)
        assert "\U0001f7e2" not in best_line and "\U0001f534" in best_line

    def test_an_unread_figure_is_muted(self):
        text = self._card(None, None)
        best_line = next(line for line in text.splitlines() if "Best" in line)
        assert "⚪" in best_line

    def test_a_flat_figure_is_neither_colour(self):
        text = self._card(0.0, 0.0)
        worst_line = next(line for line in text.splitlines() if "Worst" in line)
        assert "⚪" in worst_line
