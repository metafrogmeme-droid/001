"""
Live-equity truthfulness: /start, /status, /portfolio and the AI chat context
must never show the paper $10,000 baseline while the bot is in LIVE mode.

Operator report: Telegram /start showed "Equity $10,000.00" with mode LIVE and
a real open position. Root cause: the display resolvers fell back to
portfolio.snapshot().equity_usd (paper $10k) whenever the live balance fetch
returned falsy — so a transient auth/network failure masqueraded as a $10k
paper account on a live, funded account.

Fix: engine.resolve_display_equity (async) and resolve_display_equity_sync
return (None, "unavailable") in LIVE mode when the balance can't be read — the
callers render "unavailable", never the paper baseline. A genuinely empty live
account still shows a truthful $0.00 (the balance dict is present, total is 0).
"""

import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.engine import RuneClawEngine
from tests.source_scan import code_only


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._live_balance_cache = {}
    # The sync resolver is age-gated and routes through the viewer resolver
    # now, so the harness carries what those read: a stamp, an operator
    # executor identity, and the (empty) per-user caches.
    eng._live_balance_cache_ts = time.monotonic()
    eng.live_executor = object()
    eng._user_live_balance_cache = {}
    eng._user_live_balance_cache_ts = {}
    _paper_pf = MagicMock()
    _paper_pf.snapshot.return_value = MagicMock(equity_usd=10_000.0)
    eng.user_portfolios = {"op": _paper_pf}
    eng.portfolio = _paper_pf
    return eng


def _live():
    cfg = MagicMock()
    cfg.is_live.return_value = True
    # A MagicMock attribute is TRUTHY: left unset, per_user_live_enabled would
    # send _executor_for("op") through the real credential store.
    cfg.per_user_live_enabled = False
    return cfg


def _paper():
    cfg = MagicMock()
    cfg.is_live.return_value = False
    cfg.per_user_live_enabled = False
    return cfg


class TestResolveDisplayEquityAsync:
    async def test_live_available_returns_real_equity(self):
        eng = _engine()
        eng.get_user_live_equity = AsyncMock(return_value={"total": 17.30})
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "live"
        assert val == 17.30

    async def test_live_unavailable_is_none_not_paper(self):
        eng = _engine()
        eng.get_user_live_equity = AsyncMock(return_value=None)
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "unavailable"
        assert val is None, "must never substitute the paper $10k baseline"

    async def test_empty_live_account_shows_truthful_zero(self):
        eng = _engine()
        eng.get_user_live_equity = AsyncMock(return_value={"total": 0.0})
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "live"
        assert val == 0.0, "a real empty account is $0.00, not $10k, not unavailable"

    async def test_paper_mode_returns_paper_equity(self):
        eng = _engine()
        with patch("bot.core.engine.CONFIG", _paper()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "paper"
        assert val == 10_000.0


class TestResolveDisplayEquitySync:
    def test_live_cache_hit_returns_live(self):
        eng = _engine()
        eng._live_balance_cache = {"total": 42.5}
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = eng.resolve_display_equity_sync("op")
        assert src == "live"
        assert val == 42.5

    def test_live_empty_cache_is_unavailable_not_paper(self):
        eng = _engine()
        eng._live_balance_cache = {}
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = eng.resolve_display_equity_sync("op")
        assert src == "unavailable"
        assert val is None

    def test_paper_mode_returns_paper_equity(self):
        eng = _engine()
        with patch("bot.core.engine.CONFIG", _paper()):
            val, src = eng.resolve_display_equity_sync("op")
        assert src == "paper"
        assert val == 10_000.0

    # ── the reading is age-gated, per caller, and never absent-is-zero ──

    def test_a_stale_or_never_stamped_cache_is_unavailable(self):
        eng = _engine()
        eng._live_balance_cache = {"total": 42.5}
        # A clock far from zero: on a freshly booted host monotonic() - 3600
        # is negative and reads as never-stamped, so the STALE branch would
        # go unmeasured (live_balance_cached's docstring names the trap).
        now = time.monotonic() + 1_000_000.0
        eng._live_balance_cache_ts = now - 3600
        with patch("bot.core.engine.CONFIG", _live()), \
                patch("bot.core.engine.time", MagicMock(monotonic=lambda: now)):
            assert eng.resolve_display_equity_sync("op") == (None, "unavailable")
            eng._live_balance_cache_ts = now - 5
            assert eng.resolve_display_equity_sync("op") == (42.5, "live")
        eng._live_balance_cache_ts = 0.0
        with patch("bot.core.engine.CONFIG", _live()):
            assert eng.resolve_display_equity_sync("op") == (None, "unavailable")

    def test_a_cache_without_a_total_is_not_zero_dollars(self):
        # Fresh, non-empty, and every naive `if self._live_balance_cache:`
        # guard passes; only the is-None reading of `total` refuses.
        for planted in ({"free": 1.0}, {"total": None}):
            eng = _engine()
            eng._live_balance_cache = planted
            with patch("bot.core.engine.CONFIG", _live()):
                assert eng.resolve_display_equity_sync("op") == (None, "unavailable"), planted

    def test_an_empty_account_is_a_real_zero(self):
        eng = _engine()
        eng._live_balance_cache = {"total": 0.0}
        with patch("bot.core.engine.CONFIG", _live()):
            assert eng.resolve_display_equity_sync("op") == (0.0, "live")

    def test_the_system_context_reads_the_operator_cache(self):
        eng = _engine()
        eng._live_balance_cache = {"total": 42.5}
        with patch("bot.core.engine.CONFIG", _live()):
            assert eng.resolve_display_equity_sync("") == (42.5, "live")
            assert eng.resolve_display_equity_sync("auto") == (42.5, "live")

    def test_under_per_user_live_a_caller_with_no_account_is_no_account_not_the_operator(self):
        eng = _engine()
        eng._live_balance_cache = {"total": 42.5}
        eng._executor_for = lambda uid="", venue="": eng.live_executor
        eng._is_operator_user = lambda uid: False
        cfg = _live()
        cfg.per_user_live_enabled = True
        with patch("bot.core.engine.CONFIG", cfg):
            assert eng.resolve_display_equity_sync("op") == (None, "no_account")
            # RED HERRING: the same engine answers the system context with the
            # operator figure — right for the website sync and the dashboard
            # pusher, and the prompt builder must not be the caller that
            # benefits (test_chat_prompt_describes_only_the_callers_book).
            assert eng.resolve_display_equity_sync("") == (42.5, "live")

    def test_the_resolver_reads_through_the_view_and_the_age_gate(self):
        src = code_only(inspect.getsource(RuneClawEngine.resolve_display_equity_sync))
        assert "live_view(" in src and "live_balance_cached()" in src
        assert '_live_balance_cache.get("total"' not in src
