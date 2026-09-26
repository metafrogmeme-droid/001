"""/resume and /reset clear the live-performance governor's PAUSE, and start fresh.

The governor pauses a live book whose recent closes lose both often and on
balance. A pause opens nothing, so on a flat book it can never lift on its own,
and the 24h probe (`test_a_governor_pause_says_so_and_can_end.py`) was the only
way out. The operator asked for a manual one, and decided what it means
(2026-09-26):

* **Start fresh.** After a clear the governor ignores every close before it and
  trades at full size until `live_perf_min_samples` new closes are on record,
  then scores those as usual (and may pause again). Kelly, the VaR proxy, the
  equity throttle and the adaptive auto-confirm bar keep reading the full
  record, so the window is not wiped: the governor reads the closes after the
  clear (`RiskEngine._governor_closes`).
* **/resume and /reset both clear it**, each on the engines it already resets,
  and the card says it did.

What makes it more than a flag: the clear must survive a restart, and the
window is REBUILT at boot from the closed-trade record. So each close carries
its time, the clear is a time in the persisted risk state, and the seed passes
the record's close times beside the P&Ls.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.config import CONFIG as REAL
from bot.core import live_executor
from bot.core.self_audit import governor_line
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.user_store import UserStore

HOUR = 3600.0


class _Cfg:
    def __init__(self, **kw):
        self.risk = dataclasses.replace(REAL.risk, **kw)

    def __getattr__(self, name):
        return getattr(REAL, name)


_GOV = dict(live_performance_governor_enabled=True, live_perf_window=20,
            live_perf_min_samples=5, live_perf_pause_winrate=0.25,
            live_perf_reduce_winrate=0.40, live_perf_reduce_mult=0.5,
            live_perf_probe_hours=24.0)

#: 4 of 20 won, net negative: PAUSE (win rate <= 25% and net < 0).
_PAUSED = [1.0] * 4 + [-10.0] * 16


@pytest.fixture
def cfg():
    c = _Cfg(**_GOV)
    with patch("bot.risk.risk_engine.CONFIG", c):
        yield c


def _engine(tmp_path, window=_PAUSED, name="r.json"):
    e = RiskEngine(PortfolioTracker(initial_balance=10_000),
                   state_file=os.path.join(str(tmp_path), name))
    e.seed_realized_window(list(window),
                           stamps=[time.time() - 48 * HOUR + i for i in range(len(window))])
    e._last_close_time = time.time() - HOUR
    return e


def _status(e):
    return e.live_performance_state()["status"]


# ── 1. The clear: what it does, and the one thing it acts on ──────────────

def test_a_paused_governor_is_cleared_and_says_what_paused(tmp_path, cfg):
    e = _engine(tmp_path)
    assert _status(e) == "PAUSE"
    assert e.trading_blocked_by.startswith("live_perf_pause:")
    info = e.clear_governor_pause()
    assert info == {"wins": 4, "samples": 20, "min_samples": 5}
    # Start fresh: nothing after the clear yet, so the governor is warming up
    # and applies no reduction at all.
    assert _status(e) == "WARMUP"
    assert e.live_performance_size_multiplier == 1.0
    assert e.trading_blocked_by == ""


def test_every_other_reader_keeps_the_full_record(tmp_path, cfg):
    # The operator's decision: Kelly, the VaR proxy, the equity throttle and
    # the auto-confirm bar keep reading the whole window. Wiping it (the
    # red-team's `reset_performance_window`) would take their evidence too.
    e = _engine(tmp_path)
    e.clear_governor_pause()
    assert list(e._realized_pnl_window) == _PAUSED
    assert e.recent_live_closes(20) == _PAUSED


def test_the_closes_after_the_clear_are_scored_as_usual(tmp_path, cfg):
    e = _engine(tmp_path)
    e.clear_governor_pause()
    for _ in range(4):
        e.record_trade_result(-5.0)
    # Four new closes: still under the floor of five, still full size.
    assert _status(e) == "WARMUP"
    e.record_trade_result(-5.0)
    state = e.live_performance_state()
    # Five losses since the clear: scored, and it pauses again. The window it
    # scores is the five, not the twenty before the clear plus five.
    assert state["status"] == "PAUSE" and state["samples"] == 5


def test_a_recovering_book_after_the_clear_reads_ok(tmp_path, cfg):
    e = _engine(tmp_path)
    e.clear_governor_pause()
    for _ in range(5):
        e.record_trade_result(3.0)
    assert _status(e) == "OK"


@pytest.mark.parametrize("window,expected", [
    ([1.0] * 7 + [-2.0] * 13, "REDUCE"),
    ([5.0] * 12 + [-1.0] * 8, "OK"),
    ([-1.0] * 3, "WARMUP"),
])
def test_only_a_pause_is_cleared(tmp_path, cfg, window, expected):
    # A REDUCE is a size cut nobody asked to lift; clearing it on every
    # /resume would take it away without a word.
    e = _engine(tmp_path, window=window)
    assert _status(e) == expected
    assert e.clear_governor_pause() is None
    assert e._governor_cleared_at is None
    assert _status(e) == expected


def test_a_disabled_governor_has_nothing_to_clear(tmp_path):
    with patch("bot.risk.risk_engine.CONFIG",
               _Cfg(**{**_GOV, "live_performance_governor_enabled": False})):
        e = _engine(tmp_path)
        assert e.clear_governor_pause() is None
        assert e._governor_cleared_at is None


def test_the_breaker_reset_does_not_clear_it(tmp_path, cfg):
    # The backtest and the red team call `reset_circuit_breaker()`; putting the
    # clear inside it would change every benchmark the governor paused in.
    e = _engine(tmp_path)
    e.reset_circuit_breaker()
    assert _status(e) == "PAUSE" and e._governor_cleared_at is None


def test_the_audit_line_carries_counts_and_no_money(tmp_path, cfg):
    e = _engine(tmp_path, window=[5.0] * 4 + [-400.0] * 16)
    seen = []
    with patch("bot.risk.risk_engine.audit",
               lambda _ch, msg, **kw: seen.append((msg, kw))):
        e.clear_governor_pause()
    msg, kw = next(x for x in seen if "cleared by hand" in x[0])
    assert "4 of the last 20" in msg and "$" not in msg
    assert kw["result"] == "CLEARED"
    assert kw["data"] == {"wins": 4, "samples": 20, "min_samples": 5}


# ── 2. Which closes are after the clear ────────────────────────────────────

def test_a_window_built_without_stamps_reads_as_before_the_clear(tmp_path, cfg):
    # An engine a test (or an older boot) filled with no times: the stamps
    # align from the NEWEST end, so the unstamped prefix is before the clear.
    e = RiskEngine(PortfolioTracker(initial_balance=10_000),
                   state_file=os.path.join(str(tmp_path), "r.json"))
    e._realized_pnl_window.extend(_PAUSED)
    assert e.clear_governor_pause() is not None
    for pnl in (2.0, -1.0, 3.0):
        e.record_trade_result(pnl)
    assert e._governor_closes() == [2.0, -1.0, 3.0]


def test_a_close_at_the_clear_instant_is_before_it(tmp_path, cfg):
    e = _engine(tmp_path)
    e.clear_governor_pause()
    at = e._governor_cleared_at
    e._close_stamps().append(at)
    e._realized_pnl_window.append(9.0)
    assert e._governor_closes() == []


def test_mismatched_seed_stamps_are_not_guessed_at(tmp_path, cfg):
    e = RiskEngine(PortfolioTracker(initial_balance=10_000),
                   state_file=os.path.join(str(tmp_path), "r.json"))
    e._governor_cleared_at = time.time() - 10 * HOUR
    # Three stamps for twenty closes: not placed at all, so all twenty read as
    # before the clear, which is where an undated close sorts in the record.
    e.seed_realized_window(_PAUSED, stamps=[time.time()] * 3)
    assert e._governor_closes() == []
    assert list(e._realized_stamps) == [None] * 20


# ── 3. A restart keeps the fresh start ─────────────────────────────────────

def _closed(pnl, hours_ago):
    return SimpleNamespace(pnl_usd=pnl, close_reason="SL HIT", notional_usd=100.0,
                           cost_usd=10.0, leverage=10.0, entry_price=1.0, quantity=100.0,
                           closed_at=datetime.now(UTC) - timedelta(hours=hours_ago))


def test_the_record_gives_each_close_its_time():
    rows = [_closed(-1.0, 3), _closed(2.0, 1),
            SimpleNamespace(pnl_usd=4.0, close_reason="TP HIT", closed_at=None)]
    stamps = live_executor.realized_close_stamps(rows)
    pnls = live_executor.realized_close_pnls(rows)
    # Same order as the P&Ls: the undated close sorts oldest and has no time.
    assert pnls == [4.0, -1.0, 2.0]
    assert stamps[0] is None
    assert stamps[1] < stamps[2] <= time.time()


def test_a_restart_restores_the_clear_and_the_seed_honours_it(tmp_path, cfg):
    e = _engine(tmp_path)
    e.clear_governor_pause()
    cleared = e._governor_cleared_at
    assert json.load(open(e._state_file))["governor_cleared_at"] == cleared

    # The next boot: a new engine over the same state file, its window seeded
    # from a record holding the twenty closes before the clear and two after.
    before = [_closed(p, 48 - i * 0.1) for i, p in enumerate(_PAUSED)]
    after = [_closed(-3.0, 0.01), _closed(4.0, 0.005)]
    # Their times must fall after the clear, which was a moment ago.
    for r in after:
        r.closed_at = datetime.fromtimestamp(cleared + 1, UTC)
    record = before + after
    fresh = RiskEngine(PortfolioTracker(initial_balance=10_000),
                       state_file=e._state_file)
    assert fresh._governor_cleared_at == cleared
    fresh.seed_realized_window(live_executor.realized_close_pnls(record),
                               stamps=live_executor.realized_close_stamps(record))
    assert fresh._governor_closes() == [-3.0, 4.0]
    assert fresh.live_performance_state()["status"] == "WARMUP"
    # And the full record is still there for everyone else.
    assert len(fresh._realized_pnl_window) == 22


def test_the_boot_seed_passes_the_close_times():
    # The one production caller of the seed: without the times, a restart
    # would read every recorded close as before the clear and the governor
    # would warm up again from nothing, or with the clear forgotten, re-pause.
    from bot.core import engine
    src = open(engine.__file__).read()
    seed = src[src.index("self.risk.seed_realized_window("):]
    seed = seed[:seed.index(")\n") + 1]
    assert "stamps=_live_executor_mod.realized_close_stamps(_closed_record)" in seed


def test_the_combined_state_path_restores_it_too(tmp_path, cfg):
    e = _engine(tmp_path)
    e.clear_governor_pause()
    block = e._export_state_dict()
    fresh = RiskEngine(PortfolioTracker(initial_balance=10_000),
                       state_file=os.path.join(str(tmp_path), "other.json"))
    assert fresh._governor_cleared_at is None
    fresh._load_from_state_dict(block)
    assert fresh._governor_cleared_at == e._governor_cleared_at


@pytest.mark.parametrize("junk", [True, "1758900000", float("nan"), float("inf"),
                                  -float("inf"), None, [1.0]])
def test_an_unreadable_clear_is_no_clear(tmp_path, cfg, junk):
    e = RiskEngine(PortfolioTracker(initial_balance=10_000),
                   state_file=os.path.join(str(tmp_path), "r.json"))
    e._restore_governor_clear({"governor_cleared_at": junk})
    # No clear: the governor scores the whole window, which is the tighter
    # reading. The breaker is NOT tripped for it, as it would be for one of
    # the six `_STATE_FIELDS`.
    assert e._governor_cleared_at is None
    assert e._circuit_open is False


def test_a_clear_in_the_future_is_refused(tmp_path, cfg):
    # A future time would read every close before it as cleared and keep the
    # governor warming up, at full size, until then.
    e = RiskEngine(PortfolioTracker(initial_balance=10_000),
                   state_file=os.path.join(str(tmp_path), "r.json"))
    e._restore_governor_clear({"governor_cleared_at": time.time() + HOUR})
    assert e._governor_cleared_at is None
    past = time.time() - HOUR
    e._restore_governor_clear({"governor_cleared_at": past})
    assert e._governor_cleared_at == past


def test_the_six_state_fields_are_untouched():
    # The public breaker reader (`persisted_breaker`) reuses this validator,
    # and an unreadable field there trips the breaker; the clear is kept out.
    assert "governor_cleared_at" not in {k for k, _t, _d in RiskEngine._STATE_FIELDS}


# ── 4. What the readers say about the span ────────────────────────────────

def test_the_state_carries_the_clear(tmp_path, cfg):
    e = _engine(tmp_path)
    assert e.live_performance_state()["cleared_at"] is None
    e.clear_governor_pause()
    assert e.live_performance_state()["cleared_at"] == e._governor_cleared_at


def test_the_audit_card_says_the_window_is_since_the_clear(tmp_path, cfg):
    e = _engine(tmp_path)
    before = governor_line(e.live_performance_state())
    assert "cleared by hand" not in before
    e.clear_governor_pause()
    after = governor_line(e.live_performance_state())
    assert "WARMUP" in after and "since its pause was cleared by hand" in after


# ── 5. The engine's clear walks every engine /reset walks ─────────────────

def test_the_operators_clear_reaches_every_engine_and_survives_a_raise(tmp_path, cfg):
    from bot.core.engine import RuneClawEngine

    class _Broken:
        def clear_governor_pause(self):
            raise RuntimeError("boom")

    shared = _engine(tmp_path, name="shared.json")
    user = _engine(tmp_path, name="u7.json")
    healthy = _engine(tmp_path, window=[5.0] * 12 + [-1.0] * 8, name="u8.json")
    eng = SimpleNamespace(risk=shared,
                          _user_risk={"6": _Broken(), "7": user, "8": healthy})
    out = RuneClawEngine.clear_governor_pauses(eng)
    assert set(out) == {"", "7"}
    assert out[""]["samples"] == 20 and out["7"]["wins"] == 4
    assert _status(shared) == _status(user) == "WARMUP"
    assert _status(healthy) == "OK"


# ── 6. The commands ────────────────────────────────────────────────────────

OPERATOR = "111"
TRADER = "4242"


class _Engine:
    from bot.core.engine import RuneClawEngine as _E
    _is_operator_user = _E._is_operator_user
    risk_for = _E.risk_for
    reset_circuit_breaker_all = _E.reset_circuit_breaker_all
    clear_governor_pauses = _E.clear_governor_pauses
    del _E

    def __init__(self, risk):
        self.risk = risk
        self._halted = False
        self._user_risk: dict = {}
        self._user_store = None

    def _sync_risk_market_context(self, eng):
        pass


def _update(uid):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid), first_name="T", language_code="en"),
        effective_chat=SimpleNamespace(id=int(uid)),
        message=SimpleNamespace(text="/x"), callback_query=None)


@pytest.fixture
def bot(tmp_path, cfg):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = _Engine(_engine(tmp_path, name="shared.json"))
    h.engine._user_store = h.users
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h.sent: list[str] = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _request_operator_admission(*a, **kw):
        return False

    h._send = _send
    h._request_operator_admission = _request_operator_admission
    h.users.authorize(OPERATOR, role="admin", by=OPERATOR)
    h.users.register(TRADER, name="Vouched")
    h.users.authorize(TRADER, role="trader", by=OPERATOR)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = TRADER
        mc.per_user_live_enabled = False
        mc.is_live.return_value = True
    yield h
    patch.stopall()


@pytest.mark.asyncio
async def test_resume_clears_the_pause_and_the_card_says_so(bot):
    risk = bot.engine.risk
    await bot._cmd_resume(_update(OPERATOR), None)
    card = bot.sent[-1]
    assert _status(risk) == "WARMUP"
    assert "governor pause <b>cleared</b>" in card
    assert "4 wins in the last 20" in card and "until 5 are on record" in card
    # The gate is read after the clear, so the card no longer says refused.
    assert "ENABLED" in card and "refused" not in card.lower()


@pytest.mark.asyncio
async def test_resume_on_an_unpaused_governor_says_nothing_about_it(bot, tmp_path):
    bot.engine.risk = _engine(tmp_path, window=[5.0] * 12 + [-1.0] * 8, name="ok.json")
    await bot._cmd_resume(_update(OPERATOR), None)
    assert "governor" not in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_a_failed_clear_is_named_and_the_resume_still_runs(bot):
    risk = bot.engine.risk
    risk._circuit_open = True

    def _boom():
        raise RuntimeError("disk")
    risk.clear_governor_pause = _boom
    await bot._cmd_resume(_update(OPERATOR), None)
    card = bot.sent[-1]
    assert risk._circuit_open is False, "a clear that raised cost the resume"
    assert "REFUSED" in card
    assert "/resume tried to clear it and could not" in card


@pytest.mark.asyncio
async def test_reset_clears_it_and_does_not_say_nothing_to_reset(bot):
    risk = bot.engine.risk
    await bot._cmd_reset(_update(OPERATOR), None)
    card = bot.sent[-1]
    assert _status(risk) == "WARMUP"
    # The breaker was closed and the streak under three, which used to print
    # "Nothing to reset" over a reset that had just cleared a pause.
    assert "Nothing to reset" not in card
    assert "Governor pause cleared" in card and "4 wins in the last 20" in card


@pytest.mark.asyncio
async def test_reset_with_nothing_to_clear_still_says_nothing(bot, tmp_path):
    bot.engine.risk = _engine(tmp_path, window=[5.0] * 12 + [-1.0] * 8, name="ok.json")
    await bot._cmd_reset(_update(OPERATOR), None)
    assert "Nothing to reset" in bot.sent[-1]
    assert "Governor" not in bot.sent[-1]


@pytest.mark.asyncio
async def test_the_operators_reset_clears_every_account_and_counts_the_others(bot, tmp_path):
    bot.engine._user_risk["7"] = _engine(tmp_path, name="u7.json")
    bot.engine._user_risk["8"] = _engine(tmp_path, name="u8.json")
    await bot._cmd_reset(_update(OPERATOR), None)
    card = bot.sent[-1]
    assert all(_status(e) == "WARMUP" for e in bot.engine._user_risk.values())
    assert "also cleared on 2 other account(s)" in card


@pytest.mark.asyncio
async def test_a_trader_on_the_shared_engine_clears_nothing(bot):
    # The refusal `_control_scope` makes for the breaker covers the governor:
    # the shared engine is everybody's.
    await bot._cmd_reset(_update(TRADER), None)
    await bot._cmd_resume(_update(TRADER), None)
    assert _status(bot.engine.risk) == "PAUSE"


def test_the_clear_is_translated_everywhere():
    from bot.utils import i18n
    for key in ("reset_gov_cleared", "reset_gov_cleared_others"):
        for lang in i18n.SUPPORTED_LANGS:
            text = i18n._STRINGS[key].get(lang)
            assert text, f"{key} missing in {lang}"
    for lang in i18n.SUPPORTED_LANGS:
        out = i18n.t("reset_gov_cleared", lang, wins=4, n=20, min=5)
        assert all(str(x) in out for x in (4, 20, 5)), lang
        assert "{" not in out and not math.isnan(len(out))


def test_a_pause_with_probing_off_names_the_manual_way_out_and_no_command():
    from bot.risk.live_perf_gate import probe_clause
    clause = probe_clause(None, 0.0, None)
    # It used to say the pause "lifts only when the settings change", which the
    # clear made false. It reaches the website's scan chip, so no slash command.
    assert "operator clears it" in clause and "settings change" not in clause
    assert "/" not in clause


@pytest.fixture
def per_user_bot(bot, tmp_path):
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        patch(f"{mod}.CONFIG.per_user_live_enabled", True).start()
    bot.engine._user_risk[TRADER] = _engine(tmp_path, name="own.json")
    return bot


@pytest.mark.asyncio
async def test_a_user_clears_their_own_governor_and_nobody_elses(per_user_bot):
    bot = per_user_bot
    await bot._cmd_reset(_update(TRADER), None)
    assert _status(bot.engine._user_risk[TRADER]) == "WARMUP"
    assert _status(bot.engine.risk) == "PAUSE", "a personal reset cleared the operator's"
    card = bot.sent[-1]
    assert "Governor pause cleared" in card and "other account" not in card


@pytest.mark.asyncio
async def test_a_users_resume_clears_their_own_governor(per_user_bot):
    bot = per_user_bot
    await bot._cmd_resume(_update(TRADER), None)
    assert _status(bot.engine._user_risk[TRADER]) == "WARMUP"
    assert _status(bot.engine.risk) == "PAUSE"
    assert "governor pause <b>cleared</b>" in bot.sent[-1]


def test_the_accounts_card_says_the_count_is_since_the_clear():
    import asyncio

    from bot.skills.engine_ops_commands import EngineOpsCommands

    def _row(acct, cleared_at):
        return {"account": acct, "equity_usd": 100.0, "open_positions": 0,
                "exposure_usd": 0.0, "exposure_scored": 0, "circuit_open": False,
                "consecutive_losses": 0, "breaker_read": "read", "error": None,
                "governor": {"status": "REDUCE", "multiplier": 0.5, "win_rate": 0.3,
                             "net_pnl": -4.0, "samples": 6, "cleared_at": cleared_at},
                "throttle": None, "cap_usd": None}

    rows = [_row("shared", None), _row("7", 1_758_000_000.0)]
    sent: list[str] = []

    class Host(EngineOpsCommands):
        def __init__(self):
            async def _overview():
                return rows
            self.engine = SimpleNamespace(account_risk_overview=_overview)

        def _is_admin(self, update):
            return True

        async def _send(self, update, text, **kw):
            sent.append(text)

    asyncio.run(Host()._cmd_accounts(None, None))
    lines = [ln for ln in sent[0].splitlines() if "REDUCE" in ln]
    assert len(lines) == 2
    assert "n=6)" in lines[0] and "since cleared" not in lines[0]
    assert "n=6 since cleared)" in lines[1]
