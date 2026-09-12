"""The chat system prompt describes the CALLER's live account, and only that.

`_build_chat_system_prompt` did `executor = self.engine.live_executor if
is_live else None`, so under PER_USER_LIVE_ENABLED every caller's prompt —
Telegram and web, which share the builder — listed the OPERATOR's positions,
stops, closes, P&L and equity; GetPortfolioSkill and /positions had already
been cured through `viewer_executor`, and the model's evidence had not. The
equity read beside it, `resolve_display_equity_sync`, ignored `user_id` on
its live branch, read the operator cache with no age gate, and defaulted an
absent `total` to `0.0`.

`engine.live_view(user_id)` is the one reading now — the executor this caller
may VIEW and the cached balance OF THAT BOOK, age-gated — and the builder
reads it once. A caller the engine maps to no account gets words that say
WHICH absence (never linked / keys will not decrypt / could not be resolved),
never "none right now", which is what a READ flat book says.

Plant the state, read what the model is told. Every scenario carries a red
herring: the operator's book is fully populated and its cache is fresh in
every case, so an absence is isolation, not a broken renderer.
"""
from __future__ import annotations

import inspect
import pathlib
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

import bot.config
import bot.core.engine as eng_mod
from bot.core.engine import RuneClawEngine, _read_balance_total
from bot.skills import telegram_handler as th
from bot.skills.telegram_handler import TelegramHandler as H
from tests.source_scan import code_only, handler_sources

REPO = pathlib.Path(__file__).resolve().parent.parent

# A clock far from zero. `time.monotonic()` starts near zero on a freshly
# booted host, so "an hour ago" stamped as `monotonic() - 3600` is NEGATIVE
# there and reads as never-stamped — the stale branch under test was never
# reached on this box, and a mutation that made stale fall back to the
# operator cache survived. The engine's own docstring on live_balance_cached
# describes exactly this trap; the tests must not stand in it.
_NOW = time.monotonic() + 1_000_000.0


class _Clock:
    @staticmethod
    def monotonic() -> float:
        return _NOW


@pytest.fixture(autouse=True)
def _far_from_boot(monkeypatch):
    monkeypatch.setattr(eng_mod, "time", _Clock)


# Symbols chosen because the base _CHAT_SYSTEM_PROMPT contains "SOL", "BTC",
# "open positions" and "UNFILLED LIMIT ORDERS", and none of these.
OP_POS = NS(status="open", direction="SHORT", symbol="HYPE/USDT:USDT", entry_price=25.0,
            quantity=2.0, cost_usd=50.0, leverage=5, stop_loss=27.0, take_profit=20.0)
OP_PENDING = NS(status="pending_fill", direction="LONG", symbol="AVAX/USDT:USDT",
                entry_price=20.0, stop_loss=18.0, take_profit=24.0)
OP_PENDING_2 = NS(status="pending_fill", direction="LONG", symbol="DOGE/USDT:USDT",
                  entry_price=0.1, stop_loss=0.09, take_profit=0.12)
OP_CLOSED = NS(trade_id="LIVE-1", symbol="WIF/USDT:USDT", direction="LONG", entry_price=1.0,
               close_price=1.2, pnl_usd=87.65, commission=0.01, close_reason="TP", status="closed")
USER_POS = NS(status="open", direction="LONG", symbol="PEPE/USDT:USDT", entry_price=25.0,
              quantity=2.0, cost_usd=50.0, leverage=5, stop_loss=23.0, take_profit=30.0,
              opened_at=datetime.now(timezone.utc), sl_order_id="sl-1", tp_order_id="tp-1")


OP_POS_2 = NS(status="open", direction="LONG", symbol="TIA/USDT:USDT", entry_price=5.0,
              quantity=10.0, cost_usd=50.0, leverage=5, stop_loss=4.5, take_profit=6.0)


def _op_ex(**over):
    base = dict(open_positions=[OP_POS], closed_positions=[OP_CLOSED], closed_trades_read_failed=False)
    base.update(over)
    return NS(**base)


def _user_ex(**over):
    base = dict(open_positions=[USER_POS], closed_positions=[], closed_trades_read_failed=False)
    base.update(over)
    return NS(**base)


def _paper_pf(open_positions=()):
    return NS(snapshot=lambda: NS(open_positions=0, equity_usd=100.0, total_pnl=0.0,
                                  win_rate=0.0, total_trades=0, daily_pnl=0.0,
                                  max_drawdown_pct=0.0),
              open_positions=list(open_positions), _last_prices={},
              trade_history=[NS(direction=NS(value="LONG"), asset="DOGE/USDT", entry_price=0.1,
                                exit_price=0.2, pnl=1.0)])


def _registry(paper):
    """The real MultiUserPortfolio._sanitize raises on an empty id; a planted
    registry that accepts "" pins a builder path production never takes."""
    def get(uid, venue=""):
        if not str(uid or "").strip():
            raise ValueError("Invalid user_id: empty after sanitization")
        return paper if paper is not None else _paper_pf()
    return NS(get=get)


def _engine(*, per_user, linked=None, operators=("777",), op_cache=None, op_age=5.0,
            user_cache=None, user_age=5.0, op_ex=None, paper=None):
    """An engine that borrows the REAL view/resolver methods over planted caches."""
    op_ex = op_ex if op_ex is not None else _op_ex()
    e = NS(live_executor=op_ex,
           _live_balance_cache=op_cache if op_cache is not None else {"total": 4242.42},
           _live_balance_cache_ts=_NOW - op_age,
           _user_live_balance_cache=dict(user_cache or {}),
           _user_live_balance_cache_ts={k: _NOW - user_age for k in (user_cache or {})},
           user_portfolios=_registry(paper),
           risk=NS(circuit_breaker_active=False))
    e._executor_for = lambda uid="", venue="": (linked or {}).get(str(uid), op_ex)
    e._is_operator_user = lambda uid: str(uid) in operators
    for name in ("viewer_executor", "live_balance_cached", "live_view", "resolve_display_equity_sync"):
        setattr(e, name, getattr(RuneClawEngine, name).__get__(e))
    e._per_user = per_user
    return e


def _context_prompt(uid, **kw):
    """Mirrors bot/nlp/conversation_store.build_context_prompt: an EMPTY
    section is OMITTED, so a blank summary or engine state is a missing
    sentence here as it is in production, not a label with nothing after it."""
    parts = []
    if kw.get("portfolio_summary"):
        parts.append(f"- Current portfolio: {kw['portfolio_summary']}")
    if kw.get("engine_state"):
        parts.append(f"- Engine state: {kw['engine_state']}")
    return "\n\n" + "\n".join(parts) if parts else ""


def _handler(engine):
    ns = NS(engine=engine,
            conversations=NS(build_context_prompt=_context_prompt),
            _CHAT_SYSTEM_PROMPT=H._CHAT_SYSTEM_PROMPT,
            CHAT_TICKER_MAX_AGE_SEC=H.CHAT_TICKER_MAX_AGE_SEC,
            CHAT_TICKER_LEAD=H.CHAT_TICKER_LEAD, CHAT_TICKER_MAX=H.CHAT_TICKER_MAX)
    ns._live_ticker_block = H._live_ticker_block.__get__(ns)
    return ns


def _raising_store():
    raise RuntimeError("store down")


@pytest.fixture
def live(monkeypatch):
    """LIVE on both CONFIG readers, and the credential store ALWAYS patched —
    the real one can generate a master key under data/.

    The planted store answers the planted absence for THIS caller's id only
    and "readable" for any other, so a builder that asks about the wrong id
    lands in "unresolved" and every wording assertion fails: the words must
    depend on who was asked."""
    def run(engine, uid, *, absence="absent", store_raises=False, surface="telegram"):
        monkeypatch.setattr(eng_mod, "CONFIG", NS(per_user_live_enabled=engine._per_user,
                                                  is_live=lambda: True))
        if store_raises:
            monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", _raising_store)
        else:
            monkeypatch.setattr(
                "bot.core.exchange_credentials.get_credential_store",
                lambda: NS(credential_state=lambda u: absence if str(u) == str(uid) else "readable"))
        with patch.object(type(bot.config.CONFIG), "is_live", return_value=True):
            return H._build_chat_system_prompt(_handler(engine), uid, surface=surface)
    return run


def _check(out, must_say, must_not_say):
    for phrase in must_say:
        assert phrase in out, f"prompt omitted {phrase!r}\n---\n{out}"
    for phrase in must_not_say:
        assert phrase not in out, f"prompt wrongly claimed {phrase!r}\n---\n{out}"


OPERATOR_BOOK = ["HYPE/USDT:USDT", "WIF/USDT:USDT", "4,242", "$+87.65"]


# ── whose book ──────────────────────────────────────────────────────────────

class TestWhoseBook:
    @pytest.mark.parametrize("paper_open", [(), (NS(asset="DOGE/USDT", direction=NS(value="LONG"),
                                                    entry_price=0.1, quantity=10.0, stop_loss=0.09,
                                                    take_profit=0.12),)],
                             ids=["paper-book-empty", "paper-book-has-a-position"])
    def test_a_stranger_under_per_user_live_is_told_nothing_of_the_operators_book(self, live, paper_open):
        # RED HERRING: the operator's executor is populated and its cache is
        # fresh — every number is available and plausible — and the
        # stranger's own PAPER history is non-empty.
        e = _engine(per_user=True, linked={}, paper=_paper_pf(paper_open))
        out = live(e, "555", absence="absent")
        _check(out,
               ["ACTIVE POSITIONS: none can be reported", "no exchange account is linked", "/connect",
                "Current portfolio: no linked live account",
                "RECENT CLOSED TRADES: none on record for this user",
                "Engine state: LIVE mode, CB=OFF"],
               OPERATOR_BOOK + ["DOGE/USDT", "none right now", "open positions, equity",
                                "ACTIVE POSITIONS (live data)", "SL $", "TIA/USDT:USDT"])

    def test_the_operator_still_reads_the_operator_book(self, live):
        # The operator has no keys of their own (`_executor_for` falls back):
        # legitimate for an operator, and the store is never consulted.
        e = _engine(per_user=True, linked={})
        out = live(e, "777", store_raises=True)
        _check(out,
               ["HYPE/USDT:USDT", "WIF/USDT:USDT", "$+87.65",
                "equity ~$4,242.42 (exchange balance as read", "1 open positions", "total trades 1"],
               ["none can be reported", "ACTIVE POSITIONS: could not be read",
                "Current portfolio: could not be", "no linked live account"])

    def test_per_user_off_is_single_account_and_shows_everyone_the_operator_book(self, live):
        # Looks exactly like the leak; it is the documented byte-identical
        # single-account behaviour (viewer_executor's docstring).
        e = _engine(per_user=False, linked={})
        out = live(e, "555", store_raises=True)
        _check(out, ["HYPE/USDT:USDT", "equity ~$4,242.42"],
               ["none can be reported", "no linked live account"])

    def test_a_linked_user_sees_their_own_book_and_not_the_operators(self, live):
        # Own balance cache EMPTY (the ordinary state); operator cache fresh
        # and the operator has a closed trade. The books are ASYMMETRIC in
        # every field the summary line carries — TWO operator positions and
        # an operator closed store that failed to load against the user's one
        # position and readable store — so a count or a flag read off the
        # operator's executor cannot pass as the user's.
        e = _engine(per_user=True, linked={"555": _user_ex()},
                    op_ex=_op_ex(open_positions=[OP_POS, OP_POS_2], closed_trades_read_failed=True))
        out = live(e, "555", store_raises=True)
        _check(out,
               ["PEPE/USDT:USDT", "1 open positions", "total trades 0",
                "equity unavailable (no live balance has been read recently enough to state"],
               ["HYPE/USDT:USDT", "TIA/USDT:USDT", "WIF/USDT:USDT", "4,242", "2 open positions",
                "total trades 1", "equity ~$", "no linked live account",
                "closed-trade records could not be read"])

    def test_a_linked_users_unreadable_store_is_theirs_not_the_operators(self, live):
        # Roles reversed: the USER's closed store failed and the operator's is
        # fine. The flag must be read off the user's executor.
        e = _engine(per_user=True, linked={"555": _user_ex(closed_trades_read_failed=True)})
        out = live(e, "555", store_raises=True)
        _check(out, ["PEPE/USDT:USDT", "closed-trade records could not be read", "UNKNOWN, not zero",
                     "RECENT CLOSED TRADES (live): could not be read"],
               ["total trades 0", "total trades 1", "WIF/USDT:USDT", "HYPE/USDT:USDT", "$+87.65"])

    def test_an_unidentified_caller_gets_no_book_under_per_user_live(self, live):
        # The portfolio registry REFUSES an empty id (MultiUserPortfolio
        # ._sanitize raises), one line before the view is asked, so an
        # unidentified caller lands in the "could not be read" defaults —
        # never in the operator's book, and never in a confident absence
        # manufactured for an id nobody has. The planted registry raises
        # exactly as the real one does; the first draft of this test planted
        # one that accepted "" and pinned a path production never takes.
        e = _engine(per_user=True, linked={})
        out = live(e, "", absence="absent")
        _check(out, ["ACTIVE POSITIONS: could not be read just now",
                     "Current portfolio: could not be read just now"],
               ["HYPE/USDT:USDT", "4,242", "none right now", "none can be reported",
                "no linked live account"])
        # RED HERRING: the same engine answers the SYSTEM context with the
        # operator figure (website sync, dashboard pusher) — the builder must
        # not be the caller that benefits from that rule.
        with patch.object(eng_mod, "CONFIG", NS(per_user_live_enabled=True, is_live=lambda: True)):
            assert e.resolve_display_equity_sync("") == (4242.42, "live")


# ── the balance is the described book's, with its age ──────────────────────

class TestTheBalance:
    def test_a_linked_users_fresh_own_balance_is_quoted_with_its_age(self, live):
        e = _engine(per_user=True, linked={"555": _user_ex()}, user_cache={"555": {"total": 96.5}},
                    user_age=12.0)
        out = live(e, "555", store_raises=True)
        _check(out, ["equity ~$96.50 (exchange balance as read 12s ago)", "PEPE/USDT:USDT"],
               ["4,242", "equity unavailable"])

    def test_a_linked_users_stale_own_balance_is_not_quoted_and_does_not_fall_back(self, live):
        # The dict still holds 96.5 (a plausible figure an hour old) and the
        # operator's fresh number is one fallback away; a stale balance must
        # not blank the positions block either (omit, not guard).
        e = _engine(per_user=True, linked={"555": _user_ex()}, user_cache={"555": {"total": 96.5}},
                    user_age=3600.0)
        out = live(e, "555", store_raises=True)
        _check(out, ["equity unavailable (no live balance has been read recently enough to state",
                     "PEPE/USDT:USDT"],
               ["96.50", "4,242", "equity ~$", "temporarily unreadable"])

    def test_a_stale_operator_cache_is_not_quoted_as_live(self, live):
        # Positions still render (in-memory records) while the balance does not.
        e = _engine(per_user=True, linked={}, op_age=3600.0)
        out = live(e, "777", store_raises=True)
        _check(out, ["equity unavailable (no live balance has been read recently enough to state",
                     "HYPE/USDT:USDT"],
               ["4,242", "equity ~$"])

    @pytest.mark.parametrize("planted", [{"free": 12.0}, {"total": None}], ids=["no-total", "none-total"])
    def test_an_operator_cache_without_a_total_is_not_zero_dollars(self, live, planted):
        # Non-empty and fresh, so every naive `if self._live_balance_cache:`
        # guard passes, and `float(None or 0.0)` would print $0.00.
        e = _engine(per_user=True, linked={}, op_cache=planted)
        out = live(e, "777", store_raises=True)
        _check(out, ["equity unavailable"], ["equity ~$0.00", "equity ~$12.00"])

    def test_an_empty_account_is_a_real_zero(self, live):
        # 0.0 is falsy; a truthiness test would report a measured empty
        # account as unread.
        e = _engine(per_user=True, linked={}, op_cache={"total": 0.0})
        out = live(e, "777", store_raises=True)
        _check(out, ["equity ~$0.00 (exchange balance as read"], ["equity unavailable"])

    def test_a_linked_users_empty_account_is_a_real_zero_too(self, live):
        # The own-scope arm was driven only with 96.5; a scope-local
        # `bal.get("total") or None` would have told a linked user with an
        # empty account "equity unavailable" and passed every other test.
        e = _engine(per_user=True, linked={"555": _user_ex()}, user_cache={"555": {"total": 0.0}})
        out = live(e, "555", store_raises=True)
        _check(out, ["equity ~$0.00 (exchange balance as read", "PEPE/USDT:USDT"],
               ["equity unavailable", "4,242"])

    @pytest.mark.parametrize("planted", [{"free": 12.0}, {"total": None}], ids=["no-total", "none-total"])
    def test_a_linked_users_cache_without_a_total_is_not_zero_dollars(self, live, planted):
        e = _engine(per_user=True, linked={"555": _user_ex()}, user_cache={"555": planted})
        out = live(e, "555", store_raises=True)
        _check(out, ["equity unavailable"], ["equity ~$0.00", "equity ~$12.00", "4,242"])


# ── which absence ───────────────────────────────────────────────────────────

class TestWhichAbsence:
    def test_unreadable_keys_are_not_a_flat_book(self, live):
        # viewer_executor answers None for this user exactly as for an
        # unlinked one; the two must not be indistinguishable to the model.
        e = _engine(per_user=True, linked={})
        out = live(e, "555", absence="unreadable")
        _check(out,
               ["could not be decrypted", "do not say they hold nothing", "/exchange shows the key state",
                "re-linking with /connect", "Current portfolio: linked account could not be read"],
               ["none right now", "no exchange account is linked", "no linked live account",
                "HYPE/USDT:USDT", "4,242"])

    def test_a_credential_store_that_cannot_be_asked_is_unresolved_not_absent(self, live):
        # A store fault looks, from the builder's side, exactly like a user
        # who never connected; "/connect links their keys" would be a
        # confident diagnosis manufactured from an exception.
        e = _engine(per_user=True, linked={})
        out = live(e, "555", store_raises=True)
        _check(out,
               ["could not be resolved just now", "say the account could not be confirmed",
                "Current portfolio: could not be resolved"],
               ["no exchange account is linked", "no linked live account", "could not be decrypted",
                "HYPE/USDT:USDT", "4,242"])

    def test_the_absence_reader_never_raises_and_never_dresses_a_fault_as_absent(self, monkeypatch):
        monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", _raising_store)
        assert th._live_account_absence("555") == "unresolved"
        monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                            lambda: NS(credential_state=lambda u: "readable"))
        # readable, and yet the engine bound no executor: unresolved, not absent
        assert th._live_account_absence("555") == "unresolved"
        for state in ("absent", "unreadable"):
            monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                                lambda s=state: NS(credential_state=lambda u: s))
            assert th._live_account_absence("555") == state

    def test_every_absence_names_both_sections_and_none_says_none_right_now(self):
        for absence in ("absent", "unreadable", "unresolved", "anything-else"):
            summary, detail = th._no_live_account_block(absence)
            assert summary and "ACTIVE POSITIONS" in detail and "RECENT CLOSED TRADES" in detail
            assert "none right now" not in detail and "none right now" not in summary


# ── counts and stores ───────────────────────────────────────────────────────

class TestCountsAndStores:
    def test_pending_orders_are_not_counted_as_open_positions(self, live):
        # The pending rows DO render, as unfilled orders; only the count line
        # was claiming them as held.
        e = _engine(per_user=True, linked={},
                    op_ex=_op_ex(open_positions=[OP_POS, OP_PENDING, OP_PENDING_2]))
        out = live(e, "777", store_raises=True)
        _check(out, ["1 open positions", "UNFILLED LIMIT ORDERS (the bot's own record", "AVAX/USDT:USDT"],
               ["3 open positions"])

    @pytest.mark.parametrize("rows", [[], [OP_CLOSED]], ids=["empty", "partial"])
    def test_an_unreadable_closed_store_is_not_a_zero_record(self, live, rows):
        # live_executor sets the flag inside an `except` wrapped around a
        # per-row append, so the list may hold a PARTIAL parse: a builder
        # honouring the flag only for an empty list prints the partial record
        # as the whole — `net PnL $+87.65, win rate 100%, total trades 1`.
        e = _engine(per_user=True, linked={},
                    op_ex=_op_ex(closed_positions=rows, closed_trades_read_failed=True))
        out = live(e, "777", store_raises=True)
        _check(out, ["closed-trade records could not be read", "UNKNOWN, not zero",
                     "RECENT CLOSED TRADES (live): could not be read", "Do not say there were none"],
               ["total trades 0", "total trades 1", "net PnL $+0.00", "$+87.65", "win rate 100%",
                "WIF/USDT:USDT"])
        # SIBLING: an empty list with the flag False is a fresh account and
        # legitimately prints total trades 0 — the flag, not the emptiness,
        # drives the wording.
        e = _engine(per_user=True, linked={},
                    op_ex=_op_ex(closed_positions=[], closed_trades_read_failed=False))
        out = live(e, "777", store_raises=True)
        _check(out, ["total trades 0"], ["RECENT CLOSED TRADES (live): could not be read"])

    def test_an_engine_without_the_seam_fails_closed_not_into_the_operator_book(self, monkeypatch):
        # `live_executor` is present and populated and the old resolver stub
        # answers a number — a `getattr(self.engine, "live_view", None) or
        # …live_executor` convenience would pass every other test and reopen
        # the leak here.
        e = NS(live_executor=_op_ex(), user_portfolios=NS(get=lambda uid, venue="": _paper_pf()),
               risk=NS(circuit_breaker_active=False),
               resolve_display_equity_sync=lambda uid: (4242.42, "live"))
        monkeypatch.setattr(eng_mod, "CONFIG", NS(per_user_live_enabled=True, is_live=lambda: True))
        with patch.object(type(bot.config.CONFIG), "is_live", return_value=True):
            out = H._build_chat_system_prompt(_handler(e), "555")
        _check(out, ["ACTIVE POSITIONS: could not be read just now",
                     "Current portfolio: could not be read just now"],
               ["HYPE/USDT:USDT", "4,242", "none right now"])

    def test_a_faulted_portfolio_read_still_states_the_portfolio(self, live):
        # The cache and executor are fine; the fault is one line earlier, and
        # the old `portfolio_summary = ""` default turned it into a silently
        # missing sentence (conversation_store omits an empty summary).
        e = _engine(per_user=True, linked={})
        e.user_portfolios = NS(get=lambda uid, venue="": (_ for _ in ()).throw(RuntimeError("disk")))
        out = live(e, "777", store_raises=True)
        _check(out, ["Current portfolio: could not be read just now — do not quote an equity",
                     "ACTIVE POSITIONS: could not be read just now"],
               ["4,242"])
        # SIBLING: with the old `portfolio_summary = ""` default the sentence
        # is OMITTED by the context builder (the stub mirrors that), so the
        # assertion above is the one that fails, not a "Current portfolio: "
        # scan that could match a label with nothing after it.
        assert _context_prompt("x", portfolio_summary="", engine_state="") == ""


# ── the engine layer: routing with the real methods ────────────────────────

class TestLiveView:
    def _cfg(self, monkeypatch, per_user):
        monkeypatch.setattr(eng_mod, "CONFIG", NS(per_user_live_enabled=per_user, is_live=lambda: True))

    def test_routing(self, monkeypatch):
        self._cfg(monkeypatch, True)
        op_ex, user_ex = _op_ex(), _user_ex()
        stranger = _engine(per_user=True, linked={}, op_ex=op_ex).live_view("555")
        assert stranger["scope"] == "none" and stranger["executor"] is None
        assert stranger["total"] is None and stranger["balance"] is None
        operator = _engine(per_user=True, linked={}, op_ex=op_ex).live_view("777")
        assert operator["scope"] == "operator" and operator["executor"] is op_ex
        assert operator["total"] == 4242.42 and 4.0 < operator["age_s"] < 8.0
        linked = _engine(per_user=True, linked={"555": user_ex}, op_ex=op_ex,
                         user_cache={"555": {"total": 96.5}}).live_view("555")
        assert linked["scope"] == "own" and linked["executor"] is user_ex and linked["total"] == 96.5
        stale = _engine(per_user=True, linked={"555": user_ex}, op_ex=op_ex,
                        user_cache={"555": {"total": 96.5}}, user_age=3600.0).live_view("555")
        assert stale["scope"] == "own" and stale["total"] is None
        assert stale["balance"] is None and stale["age_s"] is None
        op_stale = _engine(per_user=True, linked={}, op_ex=op_ex, op_age=3600.0).live_view("777")
        assert op_stale["scope"] == "operator" and op_stale["executor"] is op_ex
        assert op_stale["balance"] is None and op_stale["total"] is None and op_stale["age_s"] is None
        # never stamped: ts 0.0, dict present, and an infinite max_age would
        # otherwise read it as fresh
        never = _engine(per_user=True, linked={"555": user_ex}, op_ex=op_ex,
                        user_cache={"555": {"total": 96.5}})
        never._user_live_balance_cache_ts["555"] = 0.0
        assert never.live_view("555", max_age_s=float("inf"))["balance"] is None
        # an operator with own keys views their own executor, and its balance
        # is NOT the operator cache (get_user_live_equity mixes those; this
        # deliberately does not)
        own = _engine(per_user=True, linked={"777": user_ex}, op_ex=op_ex).live_view("777")
        assert own["scope"] == "own" and own["executor"] is user_ex and own["total"] is None

    def test_per_user_off_is_the_operator_for_anyone(self, monkeypatch):
        self._cfg(monkeypatch, False)
        op_ex = _op_ex()
        v = _engine(per_user=False, linked={}, op_ex=op_ex).live_view("555")
        assert v["scope"] == "operator" and v["executor"] is op_ex and v["total"] == 4242.42

    def test_resolver_sources(self, monkeypatch):
        self._cfg(monkeypatch, True)
        user_ex = _user_ex()
        fresh = _engine(per_user=True, linked={})
        assert fresh.resolve_display_equity_sync("") == (4242.42, "live")
        assert fresh.resolve_display_equity_sync("auto") == (4242.42, "live")
        assert fresh.resolve_display_equity_sync("555") == (None, "no_account")
        assert _engine(per_user=True, linked={}, op_age=3600.0).resolve_display_equity_sync("") == (None, "unavailable")
        linked = _engine(per_user=True, linked={"555": user_ex}, user_cache={"555": {"total": 96.5}})
        assert linked.resolve_display_equity_sync("555") == (96.5, "live")
        empty = _engine(per_user=True, linked={"555": user_ex}, user_cache={"555": {"total": 0.0}})
        assert empty.resolve_display_equity_sync("555") == (0.0, "live")
        stale = _engine(per_user=True, linked={"555": user_ex}, user_cache={"555": {"total": 96.5}},
                        user_age=3600.0)
        assert stale.resolve_display_equity_sync("555") == (None, "unavailable")
        for planted in ({"free": 1.0}, {"total": None}):
            e = _engine(per_user=True, linked={}, op_cache=planted)
            assert e.resolve_display_equity_sync("777") == (None, "unavailable")
        e = _engine(per_user=True, linked={}, op_cache={"total": 0.0})
        assert e.resolve_display_equity_sync("777") == (0.0, "live")

    def test_the_total_is_a_reading(self):
        assert _read_balance_total(None) is None and _read_balance_total({}) is None
        assert _read_balance_total({"free": 1.0}) is None
        assert _read_balance_total({"total": None}) is None
        assert _read_balance_total({"total": True}) is None
        assert _read_balance_total({"total": "x"}) is None
        assert _read_balance_total({"total": 0.0}) == 0.0
        assert _read_balance_total({"total": "12.5"}) == 12.5
        # Not a number, however it is spelled: a NaN quoted as "~$nan (as
        # read 5s ago)" is the shape the reading exists to remove.
        for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
            assert _read_balance_total({"total": bad}) is None, bad


# ── the door is the surface's own ──────────────────────────────────────────

class TestTheDoorPerSurface:
    @pytest.mark.parametrize("absence", ["absent", "unreadable"])
    def test_a_web_caller_is_never_told_a_slash_command(self, live, absence):
        # The web chat cannot run /connect or /exchange; a command named to it
        # is a door painted on a wall. The dashboard's own step is the door.
        e = _engine(per_user=True, linked={})
        out = live(e, "555", absence=absence, surface="web")
        _check(out, ["dashboard's Account > "],
               ["/connect", "/exchange", "HYPE/USDT:USDT", "4,242", "none right now"])
        if absence == "absent":
            _check(out, ["Connect an exchange step links exchange keys", "none can be reported"], [])
        else:
            _check(out, ["API keys page shows the key state", "could not be decrypted"], [])

    @pytest.mark.parametrize("surface", ["telegram", "api"])
    def test_telegram_and_the_bridge_are_told_the_commands(self, live, surface):
        e = _engine(per_user=True, linked={})
        out = live(e, "555", absence="absent", surface=surface)
        _check(out, ["/connect links exchange keys"], ["dashboard", "HYPE/USDT:USDT"])
        out = live(e, "555", absence="unreadable", surface=surface)
        _check(out, ["/exchange shows the key state", "re-linking with /connect"], ["dashboard"])

    def test_the_surface_travels_from_the_chat_call_to_the_block(self):
        # `_llm_chat(surface=...)` -> builder(surface=...) -> block(absence, surface):
        # the wiring, so the web wording cannot be reachable from tests only.
        src = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
        i = src.index("def _build_chat_system_prompt")
        body = src[i:src.index("async def _llm_chat", i)]
        assert "_live_account_absence(user_id), surface)" in body
        j = src.index("async def _llm_chat")
        chat = src[j:src.index("system_prompt = self._build_chat_system_prompt(", j) + 400]
        assert "surface=surface)" in chat
        sig = inspect.signature(th._no_live_account_block)
        assert list(sig.parameters) == ["absence", "surface"]
        assert set(th._LINK_DOOR) == {"web", "telegram"}
        for door in th._LINK_DOOR.values():
            assert door["link"] and door["state"]


# ── the caches drop what the executor drop invalidates ─────────────────────

class TestCaches:
    def test_dropping_a_users_executor_drops_their_cached_balance(self):
        # /connect with new keys or /disconnect: the cached total belongs to
        # the PREVIOUS account, and live_view would quote it with a fresh
        # age for up to 900s. RED HERRING: another user's caches must stay.
        e = NS(_user_executors={"555": object(), "bybit/555": object(), "777": object()},
               _balance_view_executors={"555": object()},
               _user_live_balance_cache={"555": {"total": 96.5}, "777": {"total": 1.0}},
               _user_live_balance_cache_ts={"555": _NOW, "777": _NOW})
        RuneClawEngine.invalidate_user_executor.__get__(e)("555")
        assert "555" not in e._user_executors and "bybit/555" not in e._user_executors
        assert "555" not in e._balance_view_executors
        assert "555" not in e._user_live_balance_cache and "555" not in e._user_live_balance_cache_ts
        assert e._user_live_balance_cache == {"777": {"total": 1.0}} and "777" in e._user_executors
        # a user with nothing cached is a no-op, not a KeyError
        RuneClawEngine.invalidate_user_executor.__get__(e)("999")

    def test_a_venue_switch_invalidates_the_operator_balance_cache(self, monkeypatch):
        # The cached total is the OLD venue's; /setexchange invalidates and
        # /venue did not, so every reader of the operator cache quoted the
        # previous venue's balance, age-stamped as fresh, until the next fetch.
        import asyncio

        import bot.core.venues as venues
        monkeypatch.setattr(venues, "get_venue", lambda vid: NS(id="bybit", display_name="Bybit"))
        overrides: list = []
        monkeypatch.setattr(venues, "set_venue_override", lambda v: overrides.append(v))
        monkeypatch.setattr(eng_mod, "LiveExecutor",
                            lambda: NS(_venue=NS(id="bybit", display_name="Bybit"), open_positions=[]))
        monkeypatch.setattr(eng_mod, "CONFIG", NS(exchange=NS(venue="bitget")))

        async def _close():
            return None

        old = NS(_venue=NS(id="bitget", display_name="Bitget"), open_positions=[], close=_close)
        e = NS(live_executor=old, risk=object(), ws_feed=None, slippage=None,
               _live_balance_cache={"total": 4242.42}, _live_balance_cache_ts=_NOW,
               _user_live_balance_cache={"555": {"total": 96.5}},
               _user_live_balance_cache_ts={"555": _NOW})
        for name in ("switch_venue", "_invalidate_live_balance_cache"):
            setattr(e, name, getattr(RuneClawEngine, name).__get__(e))
        out = asyncio.run(e.switch_venue("bybit"))
        assert out.startswith("switched"), out
        assert e.live_executor is not old and overrides == ["bybit"]
        assert e._live_balance_cache == {} and e._live_balance_cache_ts == 0.0
        assert e._user_live_balance_cache == {} and e._user_live_balance_cache_ts == {}


# ── an operator who linked their own keys ──────────────────────────────────

class TestOperatorWithOwnKeys:
    """`_executor_for` places an operator's order on their OWN executor when
    they linked keys, and the old `or self._is_operator_user(user_id)` clause
    in get_user_live_equity fetched the OPERATOR balance for them while
    nothing ever wrote their own cache — so live_view, which reads the cache
    OF THE BOOK it describes, answered "equity unavailable" for that operator
    forever. Executor identity decides now, on the fetch and on the recheck."""

    def _engine(self, monkeypatch, *, user_ex):
        import asyncio
        from unittest.mock import AsyncMock
        monkeypatch.setattr(eng_mod, "CONFIG", NS(per_user_live_enabled=True, is_live=lambda: True))
        e = _engine(per_user=True, linked={"777": user_ex})
        e._LIVE_BALANCE_TTL = 30.0
        e.get_live_equity = AsyncMock(return_value={"total": 4242.42})
        for name in ("get_user_live_equity", "_live_recheck_context"):
            setattr(e, name, getattr(RuneClawEngine, name).__get__(e))
        return e, asyncio

    def test_the_balance_is_fetched_through_their_own_executor_and_cached(self, monkeypatch):
        from unittest.mock import AsyncMock
        user_ex = _user_ex()
        user_ex.fetch_balance = AsyncMock(return_value={"total": 96.5, "free": 50.0})
        e, asyncio = self._engine(monkeypatch, user_ex=user_ex)
        bal = asyncio.run(e.get_user_live_equity("777"))
        assert bal == {"total": 96.5, "free": 50.0}
        e.get_live_equity.assert_not_awaited()
        assert e._user_live_balance_cache["777"] == bal and e._user_live_balance_cache_ts["777"] == _NOW
        # ...and the view now quotes THAT balance beside THAT book
        with patch.object(eng_mod, "CONFIG", NS(per_user_live_enabled=True, is_live=lambda: True)):
            v = e.live_view("777")
        assert v["scope"] == "own" and v["executor"] is user_ex and v["total"] == 96.5
        # the pre-execution recheck sizes and counts against the same account
        eq, n_open = asyncio.run(e._live_recheck_context("777"))
        assert eq == 96.5 and n_open == 1

    def test_an_operator_without_own_keys_still_reads_the_operator_balance(self, monkeypatch):
        # RED HERRING: `_is_operator_user` is True for both operators; only
        # the executor identity differs.
        e, asyncio = self._engine(monkeypatch, user_ex=_user_ex())
        e._executor_for = lambda uid="", venue="": e.live_executor
        bal = asyncio.run(e.get_user_live_equity("777"))
        assert bal == {"total": 4242.42}
        e.get_live_equity.assert_awaited_once()
        assert "777" not in e._user_live_balance_cache

    def test_a_failed_own_fetch_is_unread_not_the_operator_figure(self, monkeypatch):
        from unittest.mock import AsyncMock
        user_ex = _user_ex()
        user_ex.fetch_balance = AsyncMock(side_effect=RuntimeError("venue down"))
        e, asyncio = self._engine(monkeypatch, user_ex=user_ex)
        assert asyncio.run(e.get_user_live_equity("777")) is None
        e.get_live_equity.assert_not_awaited()
        assert "777" not in e._user_live_balance_cache


# ── the arg-less system callers are age-gated too ──────────────────────────

class TestArglessCallers:
    """get_effective_equity() with no id reaches resolve_display_equity_sync("")
    -> live_balance_cached(): the website sync, the dashboard pusher, the
    Digital Twin (/twin) and guardian_status (the website's flight records)
    all read it. A cache older than 900s answers None there now, so the twin
    reports equity unknown rather than stress-testing an hours-old figure."""

    def _cfg(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", NS(per_user_live_enabled=True, is_live=lambda: True,
                                                  risk=NS(guardian_digital_twin_enabled=False)))

    def test_a_stale_operator_cache_is_none_for_the_system_context(self, monkeypatch):
        self._cfg(monkeypatch)
        fresh = _engine(per_user=True, linked={})
        stale = _engine(per_user=True, linked={}, op_age=3600.0)
        for e in (fresh, stale):
            e.get_effective_equity = RuneClawEngine.get_effective_equity.__get__(e)
        assert fresh.get_effective_equity("") == 4242.42
        assert stale.get_effective_equity("") is None

    @pytest.mark.parametrize("op_age,known", [(5.0, True), (3600.0, False)], ids=["fresh", "stale"])
    def test_the_digital_twin_reports_equity_unknown_off_a_stale_cache(self, monkeypatch, op_age, known):
        self._cfg(monkeypatch)
        e = _engine(per_user=True, linked={}, op_age=op_age)
        e.get_effective_equity = RuneClawEngine.get_effective_equity.__get__(e)
        e.run_digital_twin = RuneClawEngine.run_digital_twin.__get__(e)
        e._twin_positions = lambda uid="": [{"symbol": "HYPE/USDT:USDT", "direction": "SHORT",
                                             "entry": 25.0, "qty": 2.0, "cost_usd": 50.0,
                                             "leverage": 5, "group": "*"}]
        report = e.run_digital_twin()
        assert report is not None and report["equity_known"] is known, report


# ── the chat tools that read a book: the caller's ──────────────────────────

class TestTheTools:
    """check_risk and playbook are chat TOOLS the model calls for "what's my
    risk" / "how does the bot work": each did `executor = engine.live_executor`
    and `get_effective_equity_async(user_id)`, so under per-user live a
    stranger's tool call answered with the operator's equity, exposure,
    positions and realized P&L, in dollars. `viewer_executor` is the guard
    both cards read now, as GetPortfolioSkill and the prompt already did."""

    def _cfg(self, monkeypatch):
        # The REAL config object, live on both readers: the cards format a
        # dozen of its numbers, and a stub that carries some of them pins the
        # stub's shape rather than the card's.
        import dataclasses

        import bot.skills.skill_registry as reg
        cfg = dataclasses.replace(bot.config.CONFIG, simulation_mode=False)
        monkeypatch.setattr(type(cfg), "is_live", lambda self: True)
        monkeypatch.setattr(reg, "CONFIG", cfg)
        return reg

    def _tool_engine(self, *, viewer, equity=4242.42, balance=None):
        from unittest.mock import AsyncMock
        risk = NS(circuit_breaker_active=False, consecutive_losses=0, trading_blocked_by="",
                  drawdown_status=lambda: {}, _correlation_group=lambda s: "*")
        return NS(user_portfolios=NS(get=lambda uid, venue="": _paper_pf()),
                  portfolio=_paper_pf(), risk=risk, risk_for=lambda uid: risk,
                  cost=NS(snapshot=lambda: NS(total_cost_usd=0.0, calls=0, tokens=0,
                                              daily_cost_usd=0.0, monthly_cost_usd=0.0,
                                              by_provider={}, by_tier={})),
                  macro_calendar=NS(evaluate=lambda: "NORMAL"),
                  viewer_executor=lambda uid="": viewer(uid),
                  get_effective_equity_async=AsyncMock(return_value=equity),
                  live_view=lambda uid="": {"scope": "own", "balance": balance},
                  scanner=NS(scan=AsyncMock(return_value=[])), _pending_ideas={},
                  live_executor=_op_ex())

    @pytest.mark.parametrize("skill_name", ["check_risk", "playbook"])
    def test_a_stranger_gets_no_book_and_no_operator_dollars(self, monkeypatch, skill_name):
        import asyncio
        reg = self._cfg(monkeypatch)
        # RED HERRING: engine.live_executor is populated and the equity read
        # answers a number — a card reading either would print $4,242.42.
        engine = self._tool_engine(viewer=lambda uid: None)
        skill = reg.build_default_registry().get(skill_name)
        out = asyncio.run(skill.execute(engine, user_id="555"))
        _check(out, ["No linked live account" if skill_name == "check_risk" else "no linked live account",
                     "/connect"],
               ["4,242", "HYPE/USDT:USDT", "$50.00", "87.65", "Utilization"])
        engine.get_effective_equity_async.assert_not_awaited()

    @pytest.mark.parametrize("skill_name", ["check_risk", "playbook"])
    def test_a_linked_user_gets_their_own_book(self, monkeypatch, skill_name):
        import asyncio
        reg = self._cfg(monkeypatch)
        user_ex = NS(open_positions=[USER_POS], closed_positions=[], closed_trades_read_failed=False,
                     total_exposure_usd=50.0)
        engine = self._tool_engine(viewer=lambda uid: user_ex if uid == "555" else _op_ex(),
                                   equity=96.5, balance={"total": 96.5, "free": 40.0})
        skill = reg.build_default_registry().get(skill_name)
        out = asyncio.run(skill.execute(engine, user_id="555"))
        _check(out, ["96.50"], ["4,242", "HYPE/USDT:USDT", "WIF/USDT:USDT", "87.65"])
        if skill_name == "playbook":
            _check(out, ["PEPE/USDT:USDT", "$40.00"], [])
        engine.get_effective_equity_async.assert_awaited_once_with("555")

    def test_the_playbook_survives_an_unread_equity(self, monkeypatch):
        # `{utilization_pct:.1f}` raised on the None the previous line
        # deliberately produced — the card crashed on the case it worded.
        import asyncio
        reg = self._cfg(monkeypatch)
        user_ex = NS(open_positions=[], closed_positions=[], closed_trades_read_failed=False,
                     total_exposure_usd=0.0)
        engine = self._tool_engine(viewer=lambda uid: user_ex, equity=None, balance=None)
        out = asyncio.run(reg.build_default_registry().get("playbook").execute(engine, user_id="555"))
        # Anchored to each field's own line: "Total Exposure: $0.00" on this
        # card is a MEASURED zero (no positions) and must stay.
        _check(out, ["Utilization: <code>unavailable (equity unread)", "Equity: <code>--</code>",
                     "Available: <code>unavailable", "Total Exposure: <code>$0.00"],
               ["Utilization: <code>0.0%", "Equity: <code>$0.00", "Available: <code>$0.00", "4,242"])

    def test_per_user_off_is_the_operator_book_for_anyone(self, monkeypatch):
        import asyncio
        reg = self._cfg(monkeypatch)
        op = _op_ex(total_exposure_usd=50.0)
        engine = self._tool_engine(viewer=lambda uid: op)
        out = asyncio.run(reg.build_default_registry().get("check_risk").execute(engine, user_id="555"))
        _check(out, ["4,242"], ["No linked live account"])

    def test_the_risk_card_reads_the_callers_own_breaker_and_streak(self, monkeypatch):
        # `engine.risk` is the SHARED engine; a linked user under per-user
        # live has their own (`risk_for`), and the card scored the streak
        # gauge off the wrong one. RED HERRING: the shared engine is clean.
        import asyncio
        import re
        reg = self._cfg(monkeypatch)
        user_ex = NS(open_positions=[USER_POS], closed_positions=[], closed_trades_read_failed=False)
        engine = self._tool_engine(viewer=lambda uid: user_ex, equity=96.5)
        own = NS(circuit_breaker_active=True, consecutive_losses=3, trading_blocked_by="",
                 drawdown_status=lambda: {}, _correlation_group=lambda s: "*")
        shared = engine.risk
        engine.risk_for = lambda uid: own if uid == "555" else shared
        skill = reg.build_default_registry().get("check_risk")
        mine = asyncio.run(skill.execute(engine, user_id="555"))
        theirs = asyncio.run(skill.execute(engine, user_id="777"))
        streak = lambda out: next(ln for ln in out.splitlines() if "Streak" in ln)  # noqa: E731
        assert re.search(r"\b3\b", streak(mine)), streak(mine)
        assert not re.search(r"\b3\b", streak(theirs)), streak(theirs)


# ── the bridge's identity is one the view policy recognises ────────────────

class TestTheBridgeIdentity:
    def _op(self, chat_id, admin_ids, uid):
        e = NS(_user_store=None)
        with patch.object(eng_mod, "CONFIG", NS(telegram=NS(chat_id=chat_id, admin_ids=admin_ids))):
            return RuneClawEngine._is_operator_user.__get__(e)(uid)

    def test_the_operator_sentinel_is_recognised_only_when_nobody_is_configured(self):
        assert self._op("", "", "operator") is True
        # RED HERRING: a real operator id is configured — the sentinel is then
        # nobody, and the configured id is the operator
        assert self._op("111", "", "operator") is False and self._op("111", "", "111") is True
        assert self._op("", "222,333", "operator") is False and self._op("", "222,333", "333") is True
        assert self._op("", "", "555") is False and self._op("", "", "") is False

    def test_the_bridge_mints_an_identity_the_policy_accepts(self, monkeypatch):
        import os
        import secrets
        os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
        import api_bridge
        for chat_id, admins, want in (("111", "222", "111"), ("", "222, 333", "222"), ("", "", "operator")):
            monkeypatch.setattr(api_bridge, "CONFIG", NS(telegram=NS(chat_id=chat_id, admin_ids=admins)))
            assert api_bridge._bridge_identity() == want, (chat_id, admins)
        src = code_only((REPO / "api_bridge.py").read_text(encoding="utf-8"))
        assert "user_id = _bridge_identity()" in src


# ── the pending-ideas block describes the bot's own queue ──────────────────

class TestPendingIdeas:
    def _block(self, ideas):
        return H._pending_ideas_block.__get__(NS(engine=NS(pending_ideas=ideas)))()

    def test_a_manual_idea_is_counted_never_described(self):
        # The queue is global and a manual idea is somebody's proposal —
        # another user's symbol, entry and stop would be handed to this
        # user's model. RED HERRING: the manual idea carries every field the
        # renderer prints.
        scan = NS(direction=NS(value="LONG"), asset="ARB/USDT", entry_price=1.5, confidence=0.7,
                  source="scan_skill")
        manual = NS(direction=NS(value="SHORT"), asset="ZZZ/USDT", entry_price=9.0, confidence=0.9,
                    source="manual")
        out = self._block([scan, manual])
        _check(out, ["ARB/USDT", "entry $1.5000", "the bot's own queue",
                     "plus 1 manually proposed idea(s)", "may be another user's"],
               ["ZZZ/USDT", "9.0000", "90%"])
        only_manual = self._block([manual])
        _check(only_manual, ["none queued by the bot", "plus 1 manually proposed idea(s)"],
               ["ZZZ/USDT", "9.0000"])
        _check(self._block([]), ["none queued by the bot"], ["plus"])
        # an idea with no source at all is the bot's (TradeIdea defaults to "unknown")
        _check(self._block([NS(direction=NS(value="LONG"), asset="OP/USDT", entry_price=2.0,
                               confidence=0.5)]), ["OP/USDT"], ["plus"])


# ── wiring: the seams are reached, and the leak line is gone ───────────────

def test_the_builder_reads_one_view_and_never_the_operator_executor():
    src = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
    i = src.index("def _build_chat_system_prompt")
    body = src[i:src.index("async def _llm_chat", i)]
    for must in ("self.engine.live_view(", "_no_live_account_block(", "_live_account_absence(",
                 "_live_positions_block(", "_closed_trade_line(", "self._live_ticker_block()"):
        assert must in body, must
    for never in ("self.engine.live_executor", 'portfolio_summary = ""', "close_price or",
                  "resolve_display_equity_sync("):
        assert never not in body, never
    assert inspect.isfunction(th._no_live_account_block) and inspect.isfunction(th._live_account_absence)
    assert not hasattr(H, "_no_live_account_block") and not hasattr(H, "_live_account_absence")
