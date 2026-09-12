"""A post-mortem is read from the record, not narrated from two prices.

The web welcome card promises "a post-mortem of any trade" and the dashboard
has an "Ask AI" button for it; every phrasing of that request reached the
model with an entry, an exit and a P&L, so the thesis was inferred from the
price path and told back as the bot's reasoning. `bot/core/trade_postmortem`
reads the caller's closed position and the journal's entry for it, says what
is absent, and is reachable three ways: the `trade_postmortem` chat tool, the
`/postmortem` command, and the router rule for "review my last trade".

Building it found the journal had recorded `exit_price=0.0` for every live
close (the engine read a paper-Trade field a LivePosition does not carry, and
the guard over it drove a fake that carried both names).

The review of the first draft found nine more, each pinned below: a limit
order that never filled was post-mortemed as a break-even trade at +0.00R;
every exchange-reconciled close reason ("TP HIT (exchange)") was told as
"not recorded"; in paper mode every caller was handed the OPERATOR's live
close while their own paper closes read "No closed trades"; a colliding
adopted trade id handed one account another account's journal thesis; open-
position questions ("review my open position") were re-routed off the
positions card; a ticker was read out of English words ("enter near the
top" -> NEAR) and shouted prose (WHAT/USDT); the card's last line was an
instruction to the model, printed to the human; and a resolver that RAISED
was told as "No linked exchange account".

MUST_SAY / MUST_NOT_SAY over planted states, with red herrings: an adopted
position whose dataclass defaults look like a strategy, a stored exit of
`0.0` that looks like a fill, a stop of `0` that would make R look like 0R,
a "canceled" label on a row with a real P&L (a mislabelled trade, not a
non-fill), and a journal entry with the right id and the wrong trade.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from bot.compat import UTC
from bot.core import trade_postmortem as pm
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LivePosition
from bot.core.trade_journal import TradeJournal
from bot.nlp import chat_tools
from bot.nlp.intent_router import IntentRouter, postmortem_symbol, symbol_from_token
from bot.skills.skill_permissions import SKILL_PERMISSION, permission_for
from bot.skills.skill_registry import build_default_registry
from bot.utils.models import Direction, TradeExecution, TradeStatus
from tests.source_scan import code_only
from tests.test_free_text_obeys_the_role_gate import TRADER, _handler, _update

NOW = datetime.now(UTC)


def _live(**over) -> LivePosition:
    base = dict(trade_id="t-eth-1", symbol="ETH/USDT", direction="LONG",
                entry_price=3000.0, quantity=0.1, cost_usd=30.0, stop_loss=2900.0,
                take_profit=3300.0, leverage=10, opened_at=NOW - timedelta(hours=5),
                closed_at=NOW - timedelta(hours=1), close_price=3120.0, pnl_usd=11.4,
                commission=0.6, status="closed", close_reason="TP",
                strategy_type="scalp", signal_type="breakout")
    base.update(over)
    return LivePosition(**base)


def _adopted_unpriced() -> LivePosition:
    return _live(trade_id="t-sol-2", symbol="SOL/USDT", direction="SHORT", entry_price=0.0,
                 quantity=5.0, cost_usd=0.0, stop_loss=0.0, take_profit=0.0, leverage=1,
                 opened_at=None, closed_at=NOW, close_price=None, pnl_usd=None,
                 commission=None, close_reason="ValueError: boom", origin="adopted",
                 fill_source="unread")


def _expired(**over) -> LivePosition:
    """What the executor appends for a limit order that lapsed: the limit
    price as the entry, pnl 0.0, no exit — the newest row on the book."""
    base = dict(trade_id="t-eth-9", close_price=None, pnl_usd=0.0, commission=0.0,
                close_reason="expired", order_type="limit",
                opened_at=NOW - timedelta(minutes=20), closed_at=NOW)
    base.update(over)
    return _live(**base)


def _paper(**over) -> TradeExecution:
    base = dict(trade_id="p-eth-1", asset="ETH/USDT", direction=Direction.LONG,
                entry_price=3000.0, quantity=0.1, stop_loss=2900.0, take_profit=3300.0,
                status=TradeStatus.EXECUTED, pnl=11.4, gross_pnl=12.0, commission=0.6,
                exit_price=3120.0, leverage=10.0, strategy_type="scalp",
                signal_type="breakout", opened_at=NOW - timedelta(hours=5),
                closed_at=NOW - timedelta(hours=1))
    base.update(over)
    return TradeExecution(**base)


def _executor(*positions):
    return NS(closed_positions=list(positions))


def _paper_book(*trades):
    return NS(trade_history=list(trades))


def _journal(tmp_path) -> TradeJournal:
    return TradeJournal(journal_file=str(tmp_path / "journal.json"))


def _record(j, **over):
    base = dict(trade_id="t-eth-1", symbol="ETH/USDT", direction="LONG", strategy_type="scalp",
                entry_price=3000.0, exit_price=3120.0, stop_loss=2900.0, take_profit=3300.0,
                pnl=11.4, regime="TREND_UP", holding_hours=4.0, exit_reason="TP")
    base.update(over)
    return j.record_trade(**base)


# ── finding the trade ──────────────────────────────────────────────────────

class TestFindingTheTrade:
    def test_by_trade_id_then_symbol_then_latest(self):
        a, b = _live(), _adopted_unpriced()
        ex = _executor(a, b)
        assert pm.find_closed_trade(ex, trade_id="t-eth-1")[1] is a
        assert pm.find_closed_trade(ex, symbol="eth")[1] is a
        assert pm.find_closed_trade(ex, symbol="SOL/USDT")[1] is b
        # latest by closed_at, not by list order
        assert pm.find_closed_trade(ex)[1] is b
        assert pm.find_closed_trade(_executor(b, a))[1] is b

    def test_three_ways_of_having_nothing_are_told_apart(self):
        assert pm.find_closed_trade(_executor(_live()), symbol="BTC")[0] == "no_match"
        assert pm.find_closed_trade(_executor(_live()), trade_id="zzz")[0] == "no_match"
        assert pm.find_closed_trade(_executor())[0] == "none"
        assert pm.find_closed_trade(object())[0] == "unreadable"
        assert pm.find_closed_trade(NS(closed_positions=7))[0] == "unreadable"

    def test_a_paper_book_is_read_through_the_same_door(self):
        row = _paper()
        assert pm.find_closed_trade(_paper_book(row))[1] is row
        assert pm.find_closed_trade(_paper_book(row), symbol="eth")[1] is row
        assert pm.find_closed_trade(_paper_book(row), trade_id="p-eth-1")[1] is row
        assert pm.find_closed_trade(_paper_book())[0] == "none"

    def test_a_trade_id_is_validated_before_it_is_looked_up(self):
        assert pm.valid_trade_id("t-eth-1") == "t-eth-1"
        assert pm.valid_trade_id("TI-live-1:x.y") == "TI-live-1:x.y"
        assert pm.valid_trade_id("") is None
        assert pm.valid_trade_id("<script>") is None
        assert pm.valid_trade_id("x" * 80) is None


# ── an order that never filled is not a trade ──────────────────────────────

class TestNonFills:
    def test_the_latest_close_skips_an_order_that_never_filled(self):
        real, lapsed = _live(), _expired()
        assert pm.never_filled(lapsed) and not pm.never_filled(real)
        # the lapsed order is the NEWEST row and is not "my last trade"
        assert pm.find_closed_trade(_executor(real, lapsed))[1] is real
        assert pm.find_closed_trade(_executor(real, lapsed), symbol="ETH")[1] is real
        out = pm.postmortem_for(_executor(real, lapsed), None)
        assert "t-eth-1" in out and "t-eth-9" not in out

    @pytest.mark.parametrize("reason", ["expired", "canceled", "cancelled", "price_drift",
                                        "stale_pending", "rejected", "duplicate_fill_suppressed"])
    def test_every_non_fill_word_of_both_vocabularies_is_read(self, reason):
        assert pm.never_filled(_expired(close_reason=reason))
        assert pm.never_filled(_expired(close_reason=reason.upper()))

    def test_a_book_of_only_non_fills_says_so_with_no_figures(self):
        out = pm.postmortem_for(_executor(_expired(), _expired(trade_id="t-eth-8")), None)
        assert "No filled live trade on this account" in out
        assert "2 order(s) on record never filled" in out
        for never in ("$+0.00", "0.00R", "Held", "Return on margin", "+0.00%"):
            assert never not in out, never
        out = pm.postmortem_for(_executor(_live(symbol="BTC/USDT"), _expired()), None, symbol="ETH")
        assert "No filled live <b>ETH</b> trade on this account" in out
        assert "1 order(s) on record never filled" in out

    def test_named_by_id_it_is_told_as_an_order_that_never_filled(self):
        out = pm.postmortem_for(_executor(_live(), _expired()), None, trade_id="t-eth-9")
        assert "order never filled" in out and "no capital was at risk" in out
        assert "<code>expired</code>" in out
        # the plan it WOULD have had is still on record and still shown
        assert "Entry $3,000.0000" in out and "Planned R: 3.00R" in out
        for never in ("Net P&L", "Realized R", "Return on margin", "Held", "Thesis at entry",
                      "$+0.00", "0.00R"):
            assert never not in out, never

    def test_a_non_fill_label_on_a_priced_close_is_a_mislabelled_trade(self):
        # RED HERRING: "canceled" with a real P&L is a fill somebody labelled
        # wrong, and is never dropped from the book or told as a non-fill.
        row = _live(close_reason="canceled", pnl_usd=-3.2)
        assert not pm.never_filled(row)
        out = pm.postmortem_for(_executor(row), None)
        assert "Net P&L $-3.20" in out and "never filled" not in out


# ── the rendering, three-valued ────────────────────────────────────────────

class TestTheRendering:
    def test_a_journaled_win_reads_its_plan_outcome_and_thesis(self, tmp_path):
        j = _journal(tmp_path)
        _record(j)
        out = pm.postmortem_for(_executor(_live()), j, symbol="ETH")
        for must in ("ETH/USDT LONG</b> (LIVE)", "Trade id: <code>t-eth-1</code>",
                     "<code>scalp</code>", "<code>breakout</code>",
                     "Entry $3,000.0000", "Stop $2,900.0000", "Target $3,300.0000",
                     "Planned R: 3.00R", "Exit $3,120.0000", "Net P&L $+11.40",
                     "fees $0.60", "Realized R: +1.14R", "Return on margin: +38.00% at 10x",
                     "Held 4.0h", "Closed via <code>TP</code>", "Regime: TREND_UP",
                     "nothing above was inferred"):
            assert must in out, must
        # the live close path never scores at entry: said, not defaulted
        assert "Confidence at entry: not recorded" in out
        assert "Signals: not recorded" in out
        # the journal's per-unit, sign-flipped R never reaches the model
        assert "Journal R" not in out

    def test_a_paper_close_renders_from_its_own_vocabulary(self):
        out = pm.postmortem_for(_paper_book(_paper()), None, book_kind="paper")
        for must in ("ETH/USDT LONG</b> (PAPER)", "Trade id: <code>p-eth-1</code>",
                     "Exit $3,120.0000", "Net P&L $+11.40", "fees $0.60", "Realized R: +1.14R",
                     # margin is DEFINED for a paper row: entry * qty / leverage
                     "Return on margin: +38.00% at 10x", "Held 4.0h", "Close reason: not recorded"):
            assert must in out, must
        assert "LIVE" not in out

    def test_realized_r_is_over_dollar_risk_with_the_pnl_sign(self):
        assert pm.realized_r(3000.0, 2900.0, 0.1, 11.4) == pytest.approx(1.14)
        # a losing SHORT is a negative R — the journal's helper says +
        assert pm.realized_r(60000.0, 60600.0, 0.01, -6.4) == pytest.approx(-1.0667, abs=1e-3)
        assert pm.realized_r(3000.0, None, 0.1, 5.0) is None
        assert pm.realized_r(3000.0, 2900.0, 0.0, 5.0) is None
        assert pm.realized_r(3000.0, 3000.0, 0.1, 5.0) is None

    def test_an_adopted_unpriced_close_claims_nothing(self):
        out = pm.postmortem_for(_executor(_adopted_unpriced()), None)
        assert "Adopted from the exchange" in out
        assert "not applicable (adopted position)" in out
        assert "swing" not in out and "momentum_confluence" not in out
        assert "Entry not recorded" in out and "Stop none on record" in out
        assert "Planned R: unknown" in out
        assert "Exit not recorded (the close could not be read from the venue)" in out
        assert "not priced" in out and "do not call it flat" in out
        assert "$0.00" not in out and "0.00R" not in out
        # an exception's text is not a close reason to hand a model, and it
        # IS on record — so the card says so rather than "not recorded"
        assert "boom" not in out and "Close reason: on record but not shown" in out
        assert "No journal entry for this trade" in out and "Do not infer them" in out

    def test_a_stored_zero_exit_is_not_a_fill(self):
        out = pm.postmortem_for(_executor(_live(close_price=0.0)), None)
        assert "Exit not recorded" in out and "$0.0000" not in out

    def test_no_stop_means_r_unknown_not_zero(self):
        out = pm.postmortem_for(_executor(_live(stop_loss=0.0)), None)
        assert "Realized R: unknown — no stop on record" in out
        assert "Stop none on record" in out

    def test_no_quantity_means_r_unknown(self):
        out = pm.postmortem_for(_executor(_live(quantity=0.0)), None)
        assert "Realized R: unknown — quantity not on record" in out

    def test_markup_in_the_record_is_escaped(self):
        out = pm.postmortem_for(_executor(_live(symbol="<b>X</b>/USDT")), None)
        assert "<b>X</b>" not in out and "&lt;b&gt;X&lt;/b&gt;" in out
        out = pm.postmortem_for(_executor(_live(close_reason="TP HIT (<i>x</i>)")), None)
        assert "<i>x</i>" not in out

    def test_the_empty_and_unreadable_books_say_so_and_say_which(self):
        assert "No closed live trades on this account" in pm.postmortem_for(_executor(), None)
        assert "No closed paper trades on this account" in pm.postmortem_for(
            _paper_book(), None, book_kind="paper")
        assert "closed live trades could not be read" in pm.postmortem_for(object(), None)
        out = pm.postmortem_for(_executor(_live()), None, symbol="BTC")
        assert "No closed live <b>BTC</b> trade on this account (1 close(s) on record)" in out

    def test_a_scored_journal_entry_hands_the_thesis_over(self, tmp_path):
        j = _journal(tmp_path)
        _record(j, confidence=0.72, signals_used=["rsi_div", "vol_spike"],
                session="london", volatility="high")
        out = pm.postmortem_for(_executor(_live()), j, trade_id="t-eth-1")
        assert "Confidence at entry: 72%" in out
        assert "Signals: rsi_div, vol_spike" in out
        assert "Session: london" in out and "Volatility: high" in out

    def test_the_footer_is_written_for_the_human_too(self):
        # `/postmortem` and the routed reply hand this text to a PERSON; the
        # model-directed sentence lives in the tool's description instead.
        out = pm.postmortem_for(_executor(_live()), None)
        assert "say so rather than supplying it" not in out
        assert "nothing missing should be guessed" in out
        tool = next(t for t in chat_tools.CHAT_TOOLS if t.name == "trade_postmortem")
        assert "say so rather than supplying it" in tool.description


# ── the close reason is on record, and is printed ──────────────────────────

class TestCloseReason:
    @pytest.mark.parametrize("reason", [
        "TP HIT (exchange)", "SL HIT (inferred)", "TRAILING SL HIT", "TP HIT (estimated)",
        "manually closed", "CLOSED (unknown)", "time_stop", "manual_closeall",
        "smart_exit:rsi_reversal", "reconcile_close",
    ])
    def test_every_reason_the_record_writes_is_printed(self, reason):
        out = pm.postmortem_for(_executor(_live(close_reason=reason)), None)
        assert f"Closed via <code>{reason}</code>" in out, out

    def test_unprintable_text_is_on_record_but_not_shown(self):
        for reason in ("ValueError: boom", "x" * 90, "TP\n<script>"):
            out = pm.postmortem_for(_executor(_live(close_reason=reason)), None)
            assert "Close reason: on record but not shown" in out, reason
            assert "Close reason: not recorded" not in out
        out = pm.postmortem_for(_executor(_live(close_reason="")), None)
        assert "Close reason: not recorded" in out


# ── the journal's lookup, its owner, and the exit price it stored ──────────

class TestTheJournal:
    def test_find_trade_is_by_id_and_owner(self, tmp_path):
        j = _journal(tmp_path)
        _record(j, trade_id="A", user_id="alice")
        _record(j, trade_id="L")  # no owner recorded: a row from before the column
        assert j.find_trade("A", user_id="alice").trade_id == "A"
        assert j.find_trade("A", user_id="bob") is None
        assert j.find_trade("A") is None, "an unknown caller is not handed an owned entry"
        assert j.find_trade("L", user_id="anyone").trade_id == "L"
        assert j.find_trade("B", user_id="alice") is None and j.find_trade("") is None
        assert pm.journal_entry_for(None, _live(trade_id="A")) is None
        assert pm.journal_entry_for(object(), _live(trade_id="A")) is None

    def test_the_owner_survives_a_save_and_a_load(self, tmp_path):
        j = _journal(tmp_path)
        _record(j, trade_id="A", user_id="alice")
        again = _journal(tmp_path)
        assert again.find_trade("A", user_id="alice") is not None
        assert again.find_trade("A", user_id="bob") is None

    def test_a_colliding_id_never_hands_over_another_trade(self, tmp_path):
        # Two accounts adopt SOL in the same second: byte-identical ids. The
        # journal holds the OTHER account's entry under this id — SHORT, its
        # own P&L, its own lessons — and the caller's close is a LONG.
        j = _journal(tmp_path)
        _record(j, trade_id="TI-adopted-SOL-1700000000", symbol="SOL/USDT", direction="SHORT",
                pnl=-40.0, regime="TREND_DOWN")
        mine = _live(trade_id="TI-adopted-SOL-1700000000", symbol="SOL/USDT", direction="LONG",
                     pnl_usd=11.4)
        assert pm.journal_entry_for(j, mine) is None
        out = pm.postmortem_for(_executor(mine), j)
        assert "TREND_DOWN" not in out and "No journal entry for this trade" in out
        # and the SAME id on the SAME trade is attached
        theirs = _live(trade_id="TI-adopted-SOL-1700000000", symbol="SOL/USDT", direction="SHORT",
                       pnl_usd=-40.0)
        assert pm.journal_entry_for(j, theirs) is not None
        # a P&L the journal rounded is still the same trade
        assert pm._entry_describes(NS(symbol="ETH/USDT", direction="LONG", pnl=11.4),
                                   _live(pnl_usd=11.404))
        # an unpriced close cannot be checked on P&L and is checked on the rest
        assert pm._entry_describes(NS(symbol="ETH/USDT", direction="LONG", pnl=11.4),
                                   _live(pnl_usd=None))

    def test_a_live_close_journals_its_close_price_and_its_owner(self, tmp_path):
        """A real LivePosition, not a fake that happens to carry exit_price."""
        from tests.test_journal_records_live_closes import _engine_stub
        j = _journal(tmp_path)
        eng = _engine_stub()
        eng.journal = j
        pos = _live(close_price=3120.0)
        assert not hasattr(pos, "exit_price"), "the premise: LivePosition has no exit_price"
        RuneClawEngine._on_live_position_closed(eng, pos, "u-9")
        e = j.find_trade("t-eth-1", user_id="u-9")
        assert e is not None and e.exit_price == 3120.0, (
            "the journal recorded exit_price 0.0 for every live close: the engine "
            "read `exit_price`, a field LivePosition does not have")
        assert e.user_id == "u-9"
        assert j.find_trade("t-eth-1", user_id="u-other") is None


# ── the skill: whose book, and three outcomes on the way to it ─────────────

def _engine(*, live, mine=None, operator=None, viewer=None, paper=None):
    """An engine whose viewer resolver and paper books are both observable."""
    books = {"me": paper} if paper is not None else {}
    eng = NS(journal=None, live_executor=operator if operator is not None else NS(closed_positions=[]),
             user_portfolios=NS(get=lambda uid, venue="": books.get(uid, _paper_book())),
             portfolio=_paper_book())
    eng.viewer_executor = viewer if viewer is not None else (
        lambda uid="": mine if uid == "me" else eng.live_executor)
    return eng


@pytest.fixture
def live_mode():
    with patch("bot.config.CONFIG", NS(is_live=lambda: True)):
        yield


@pytest.fixture
def paper_mode():
    with patch("bot.config.CONFIG", NS(is_live=lambda: False)):
        yield


class TestTheSkill:
    @pytest.mark.asyncio
    async def test_live_mode_reads_the_callers_own_book(self, live_mode):
        skill = build_default_registry().get("trade_postmortem")
        assert skill is not None
        mine = _executor(_live())
        theirs = _executor(_live(trade_id="t-other", symbol="BTC/USDT"))
        engine = _engine(live=True, mine=mine, operator=theirs)
        out = await skill.execute(engine, user_id="me", symbol="ETH/USDT")
        assert "ETH/USDT LONG" in out and "(LIVE)" in out
        # another account's trade id is not on MY book
        out = await skill.execute(engine, user_id="me", trade_id="t-other")
        assert "No closed live trade <code>t-other</code> on this account" in out

    @pytest.mark.asyncio
    async def test_paper_mode_reads_the_callers_paper_book_never_the_operators_live_one(self, paper_mode):
        # RED HERRING: the operator's live executor is populated (it always
        # is — the executor loads its closed-trades file at construction), and
        # with per-user live OFF the viewer resolver hands it to EVERY caller.
        skill = build_default_registry().get("trade_postmortem")
        operator = _executor(_live(trade_id="op-btc", symbol="BTC/USDT", pnl_usd=999.0))
        viewer = Mock(return_value=operator)
        engine = _engine(live=False, operator=operator, viewer=viewer, paper=_paper_book(_paper()))
        out = await skill.execute(engine, user_id="me")
        assert "(PAPER)" in out and "p-eth-1" in out and "ETH/USDT LONG" in out
        assert "BTC" not in out and "999" not in out
        viewer.assert_not_called()
        # a stranger with an EMPTY paper book: absent from THEIR book, said as such
        out = await skill.execute(engine, user_id="stranger")
        assert "No closed paper trades on this account" in out
        assert "BTC" not in out and "No closed live" not in out

    @pytest.mark.asyncio
    async def test_a_raising_resolver_is_could_not_read_not_no_account(self, live_mode):
        skill = build_default_registry().get("trade_postmortem")
        engine = _engine(live=True, viewer=Mock(side_effect=RuntimeError("store down")))
        out = await skill.execute(engine, user_id="me")
        assert "could not be read" in out and "Try again" in out
        assert "No linked" not in out and "store down" not in out
        # and a resolver that ANSWERS None is the unlinked case, told as such
        engine = _engine(live=True, viewer=lambda uid="": None)
        out = await skill.execute(engine, user_id="me")
        assert "No linked live account" in out and "Try again" not in out

    @pytest.mark.asyncio
    async def test_a_paper_book_that_cannot_be_fetched_is_could_not_read(self, paper_mode):
        skill = build_default_registry().get("trade_postmortem")
        engine = _engine(live=False)
        engine.user_portfolios = NS(get=Mock(side_effect=RuntimeError("disk")))
        out = await skill.execute(engine, user_id="me")
        assert "closed paper trades could not be read" in out and "disk" not in out

    def test_it_is_a_portfolio_skill_on_both_surfaces(self):
        assert SKILL_PERMISSION["trade_postmortem"] == "portfolio"
        assert permission_for("trade_postmortem") == "portfolio"
        names = {t.name for t in chat_tools.CHAT_TOOLS}
        assert "trade_postmortem" in names

    def test_the_skill_branches_on_live_mode_like_its_siblings(self):
        from bot.skills import skill_registry as sr
        src = code_only(inspect.getsource(sr.TradePostmortemSkill.execute))
        assert "CONFIG.is_live()" in src and "_get_portfolio(" in src


class TestTheTool:
    def test_symbol_and_trade_id_pass_through_validated(self):
        kw, problem = chat_tools._kwargs_for("trade_postmortem", {"symbol": "eth", "trade_id": "t-1"})
        assert problem is None and kw == {"symbol": "ETH/USDT", "trade_id": "t-1"}
        kw, problem = chat_tools._kwargs_for("trade_postmortem", {})
        assert problem is None and kw == {}
        kw, problem = chat_tools._kwargs_for("trade_postmortem", {"trade_id": "<x>"})
        assert kw == {} and problem


# ── the router: the asks, the neighbours, and the asset slot ───────────────

class TestTheRouter:
    ASKS = ["post-mortem of my last trade", "post mortem on the ETH trade",
            "why did you enter ETH?", "what went wrong with the SOL trade",
            "review my last trade", "walk me through my last trade",
            "what was the thesis on my BTC long", "postmortem ETH", "debrief my last trade",
            "review my ETH trade", "go over my closed trade", "what went wrong with eth",
            "what went wrong with my last trade", "why did you enter near the top",
            "WHAT WENT WRONG WITH THE TRADE", "POST MORTEM my last trade"]
    NOT = {"why was my trade rejected": "whynot", "show my closed trades": "trade_journal",
           "my positions": "get_portfolio", "what's my pnl today": "get_portfolio",
           "post-mortem of the market today": None,
           # the OPEN book is the positions card, not a closed-trade reader
           "review my open position": "get_portfolio", "review my current position": "get_portfolio",
           "break down my current trade": "get_portfolio", "review my ETH position": "get_portfolio",
           "go over my positions": "get_portfolio", "review my portfolio": "get_portfolio",
           # not a trade at all
           "what went wrong with the deploy": None, "what was the idea behind the bot": None}
    SYMBOL = {"post mortem on the ETH trade": "ETH/USDT", "why did you enter ETH?": "ETH/USDT",
              "review my eth trade": "ETH/USDT", "why did you enter eth?": "ETH/USDT",
              "post mortem on the ETH/USDT trade": "ETH/USDT", "what went wrong with $PEPE": "PEPE/USDT",
              "why did you enter HYPE": "HYPE/USDT", "post-mortem of my bitcoin trade": "BTC/USDT",
              # English words on the ticker list, and shouted prose, are not tickers
              "why did you enter near the top": None, "why did you enter, etc": None,
              "WHAT WENT WRONG WITH THE TRADE": None, "POST MORTEM my last trade": None,
              "review my last trade": None, "why did you enter so near resistance": None}

    @pytest.mark.parametrize("text", ASKS)
    def test_a_post_mortem_ask_reaches_the_record(self, text):
        i = IntentRouter().classify_rules(text)
        assert i.skill == "trade_postmortem" and i.confidence >= 0.8, (text, i.skill)

    @pytest.mark.parametrize("text,symbol", list(SYMBOL.items()))
    def test_the_symbol_is_read_from_the_asset_slot_only(self, text, symbol):
        assert postmortem_symbol(text) == symbol, text
        i = IntentRouter().classify_rules(text)
        assert i.skill == "trade_postmortem", (text, i.skill)
        assert i.kwargs.get("symbol") == symbol, (text, i.kwargs)

    @pytest.mark.parametrize("text,skill", list(NOT.items()))
    def test_neighbours_keep_their_destination(self, text, skill):
        i = IntentRouter().classify_rules(text)
        assert i.skill != "trade_postmortem", (text, i.skill)
        if skill is None:
            assert not i.skill, (text, i.skill)
        else:
            assert i.skill == skill, (text, i.skill)

    def test_a_bare_ticker_token_is_a_ticker_on_the_list_or_not(self):
        assert symbol_from_token("HYPE") == "HYPE/USDT"
        assert symbol_from_token("eth/usdt") == "ETH/USDT"
        assert symbol_from_token("$PEPE") == "PEPE/USDT"
        assert symbol_from_token("bitcoin") == "BTC/USDT"
        assert symbol_from_token("t-eth-1") is None and symbol_from_token("") is None


@pytest.fixture
def bot(tmp_path):
    h = _handler(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = TRADER
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
    yield h
    patch.stopall()


def _sans_user(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k != "user_id"}


class TestTheCommand:
    @pytest.mark.asyncio
    async def test_postmortem_dispatches_the_skill_for_its_words(self, bot):
        await bot._cmd_postmortem(_update(TRADER, "/postmortem ETH"), NS(args=["ETH"]))
        assert bot.registry.dispatched == ["trade_postmortem"]
        kwargs = bot.registry.dispatch_kwargs[-1]
        assert kwargs.get("symbol") == "ETH/USDT" and "trade_id" not in kwargs
        await bot._cmd_postmortem(_update(TRADER, "/postmortem t-eth-1"), NS(args=["t-eth-1"]))
        assert bot.registry.dispatch_kwargs[-1].get("trade_id") == "t-eth-1"
        # a ticker off the known list is a ticker here, not a trade id
        await bot._cmd_postmortem(_update(TRADER, "/postmortem HYPE"), NS(args=["HYPE"]))
        assert _sans_user(bot.registry.dispatch_kwargs[-1]) == {"symbol": "HYPE/USDT"}
        await bot._cmd_postmortem(_update(TRADER, "/postmortem"), NS(args=[]))
        assert _sans_user(bot.registry.dispatch_kwargs[-1]) == {}

    def test_the_command_is_guarded_like_the_portfolio(self):
        from bot.skills import portfolio_commands as pc
        src = code_only(inspect.getsource(pc.PortfolioCommands._cmd_postmortem))
        assert 'dispatch("trade_postmortem"' in src
        decorators = inspect.getsource(pc).split("async def _cmd_postmortem")[0].rstrip().splitlines()[-1]
        assert decorators.strip() == '@guard("portfolio")'


# ── the prompt names the typed door ────────────────────────────────────────

def test_the_cannot_act_rule_names_the_typed_trade_door():
    from bot.skills.chat_runtime import _CHAT_CANNOT_ACT_RULE
    assert "buy SOL 71 sl 70 tp 76" in _CHAT_CANNOT_ACT_RULE
