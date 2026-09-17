"""The PAPER branch of the chat prompt renders through the live row's rules.

`_live_position_row` and `_closed_trade_line` were written so the model's
evidence about the user's money is three-valued in WORDS — an absent stop
is "SL NONE ON RECORD", an unread mark is "CURRENT PRICE UNAVAILABLE", an
unpriced close is "PnL not recorded" — and the paper arm of the same prompt
kept its own inline rows: `SL ${pos.stop_loss:,.4f}` with no reading (a
stop the record holds as 0.0 printed as a stop at $0.0000), `size` under
the two-meanings name `position_size_basis` retired, a header claiming
"(live data)" over simulated money, `exit ${t.exit_price:,.4f}` with no
reading, no PAPER label anywhere, and a raise on any None that the `except`
around the block turned into "could not be read" for the positions AND the
closed trades at once. The summary line printed `total PnL $+0.00` on an
account that had never closed a trade, and `engine_state` defaulted to ""
— which the context builder OMITS, so a fault before the mode was read
deleted the live/paper/halted line in silence.

One renderer, two vocabularies: `_paper_position_row` feeds the paper
book's fields (`asset`, an enum `direction`, a plain-float `leverage`, a
margin DERIVED as entry x quantity / leverage) into the same
`_position_row_parts` the live row uses, and `_closed_trade_line` reads
both close vocabularies. Every row is labelled PAPER, every absence is a
sentence, and nothing here raises.

Plant the state, read what the model is told, with a red herring in each
block: a measured zero (a break-even mark, a $0.00 paper close) still
prints as a zero; a fully recorded row never says "not on record"; the
live vocabulary still wins when a record carries both.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

import bot.config
from bot.skills import telegram_handler as th
from bot.skills.telegram_handler import TelegramHandler as H
from bot.skills.telegram_handler import (
    _closed_trade_line,
    _live_position_row,
    _live_positions_block,
    _paper_position_row,
    _position_row_parts,
)
from bot.utils.models import Direction, TradeExecution
from tests.source_scan import code_only, handler_sources
from tests.test_chat_prompt_describes_only_the_callers_book import _context_prompt, _handler

CLOSED_AT = datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)


def _paper_pos(**over):
    base = dict(direction=NS(value="LONG"), asset="DOGE/USDT", entry_price=0.1, quantity=10.0,
                leverage=1.0, stop_loss=0.09, take_profit=0.12)
    base.update(over)
    return NS(**base)


def _paper_close(**over):
    base = dict(direction=NS(value="LONG"), asset="DOGE/USDT", entry_price=0.1, exit_price=0.2,
                pnl=1.0, closed_at=CLOSED_AT)
    base.update(over)
    return NS(**base)


def _check(out, must_say, must_not_say):
    for phrase in must_say:
        assert phrase in out, f"omitted {phrase!r}\n---\n{out}"
    for phrase in must_not_say:
        assert phrase not in out, f"wrongly claimed {phrase!r}\n---\n{out}"


# ── the paper position row ──────────────────────────────────────────────────

class TestPaperRow:
    def test_a_fully_recorded_row_prints_every_field_and_never_says_not_on_record(self):
        out = _paper_position_row(_paper_pos(), 0.11)
        _check(out, ["LONG DOGE/USDT", "entry $0.1000", "qty 10", "margin $1.00", "lev 1x",
                     "notional $1.00", "SL $0.0900", "TP $0.1200", "MARK $0.1100",
                     "price move +10.00% from entry", "return on margin +10.00% at 1x",
                     "unrealized $+0.10"],
               ["NOT ON RECORD", "NONE ON RECORD", "UNAVAILABLE", "NOT COMPUTABLE"])

    def test_margin_is_derived_from_the_paper_leverage(self):
        # entry x qty / leverage: the paper book's own definition, so a 5x
        # position of $1.00 notional has $0.20 at risk and a 10% move is 50%
        # on that margin — the two bases print under their own names.
        out = _paper_position_row(_paper_pos(leverage=5.0), 0.11)
        _check(out, ["margin $0.20", "lev 5x", "notional $1.00", "price move +10.00% from entry",
                     "return on margin +50.00% at 5x", "unrealized $+0.10"], [])

    def test_a_short_inverts_the_move_and_the_dollar(self):
        out = _paper_position_row(_paper_pos(direction=NS(value="SHORT")), 0.11)
        _check(out, ["SHORT DOGE/USDT", "price move -10.00% from entry", "unrealized $-0.10",
                     "return on margin -10.00% at 1x"], ["+10.00%"])

    @pytest.mark.parametrize("stop", [0.0, None, "x", float("nan")])
    def test_a_stop_the_record_does_not_hold_is_no_stop_not_a_stop_at_zero(self, stop):
        # The inline row printed `SL $0.0000` for a 0.0 and RAISED for None.
        out = _paper_position_row(_paper_pos(stop_loss=stop), 0.11)
        _check(out, ["SL NONE ON RECORD (no stop known for this position — do not describe one)",
                     # RED HERRING: the rest of the row is still fully recorded
                     "entry $0.1000", "TP $0.1200", "MARK $0.1100"],
               ["SL $0.0000", "$nan"])

    def test_no_target_is_not_a_target_at_zero(self):
        out = _paper_position_row(_paper_pos(take_profit=0.0), 0.11)
        _check(out, ["TP NONE ON RECORD"], ["TP $0.0000"])

    @pytest.mark.parametrize("mark", [None, 0.0, -1.0, "abc", float("inf"), True])
    def test_an_unread_mark_is_stated_and_no_figure_is_derived_from_it(self, mark):
        out = _paper_position_row(_paper_pos(), mark)
        _check(out, ["CURRENT PRICE UNAVAILABLE", "do not estimate it",
                     # RED HERRING: the recorded fields still print
                     "entry $0.1000", "SL $0.0900", "margin $1.00"],
               ["MARK $", "price move", "unrealized", "return on margin", "%"])

    @pytest.mark.parametrize("lev", [None, 0.0, "x"])
    def test_an_unreadable_leverage_withholds_the_margin_and_its_return_only(self, lev):
        out = _paper_position_row(_paper_pos(leverage=lev), 0.11)
        _check(out, ["lev NOT ON RECORD", "margin NOT ON RECORD",
                     "return on margin NOT COMPUTABLE (leverage not on record)",
                     # RED HERRING: the notional and the dollar need only entry and qty
                     "notional $1.00", "price move +10.00% from entry", "unrealized $+0.10"],
               ["margin $", "lev 1x"])

    def test_an_absent_entry_withholds_every_price_derived_figure(self):
        out = _paper_position_row(_paper_pos(entry_price=0.0), 0.11)
        _check(out, ["entry NOT ON RECORD", "margin NOT ON RECORD",
                     "price move and P&L CANNOT BE COMPUTED (no entry on record)", "MARK $0.1100"],
               ["entry $0.0000", "notional $", "unrealized", "price move +"])

    def test_a_measured_break_even_still_prints_zero(self):
        # RED HERRING for the whole file: a mark AT the entry is a measured
        # flat position and prints as one — absent is what must not.
        out = _paper_position_row(_paper_pos(), 0.1)
        _check(out, ["price move +0.00% from entry", "unrealized $+0.00", "MARK $0.1000"],
               ["UNAVAILABLE", "NOT COMPUTABLE"])

    def test_the_real_paper_model_renders_through_the_same_words(self):
        # Direction is an Enum on the real record and the asset is `asset`.
        pos = TradeExecution(trade_id="T-1", asset="SOL/USDT", direction=Direction.SHORT,
                             entry_price=150.0, quantity=2.0, stop_loss=160.0, take_profit=120.0,
                             leverage=3.0)
        out = _paper_position_row(pos, 140.0)
        _check(out, ["SHORT SOL/USDT", "entry $150.0000", "qty 2", "margin $100.00", "lev 3x",
                     "notional $300.00", "SL $160.0000", "TP $120.0000", "MARK $140.0000",
                     "price move +6.67% from entry", "return on margin +20.00% at 3x",
                     "unrealized $+20.00"],
               ["NOT ON RECORD", "?"])

    def test_the_live_row_and_the_paper_row_share_one_renderer(self):
        # The same numbers through both doors print the same sentences: a
        # second copy of the row is a second answer.
        live = NS(status="open", direction="LONG", symbol="DOGE/USDT", entry_price=0.1, quantity=10.0,
                  cost_usd=0.2, leverage=5, stop_loss=0.09, take_profit=0.12)
        paper = _paper_pos(leverage=5.0)
        live_out = _live_position_row(live, 0.11).split(": ", 1)[1]
        paper_out = _paper_position_row(paper, 0.11).split(": ", 1)[1]
        assert live_out == paper_out, (live_out, paper_out)
        src = code_only(inspect.getsource(_live_position_row)) + code_only(inspect.getsource(_paper_position_row))
        assert src.count("_position_row_parts(") == 2
        assert "MARK UNAVAILABLE" not in src, "the wording lives in the shared core only"

    def test_the_core_states_an_absent_mark_beside_the_live_wording(self):
        parts = _position_row_parts(0.1, 10.0, 1.0, 1.0, 1.0, 0.09, 0.12, None, is_short=False, side="LONG")
        joined = ", ".join(parts)
        assert "MARK UNAVAILABLE — CURRENT PRICE UNAVAILABLE" in joined and "do not estimate it" in joined
        # provenance slots where the live row always printed it: after TP, before the mark
        parts = _position_row_parts(0.1, 10.0, 1.0, 1.0, 1.0, 0.09, 0.12, 0.11, is_short=False, side="LONG",
                                    after_tp=["UNPROTECTED: x"])
        assert parts.index("UNPROTECTED: x") == parts.index("TP $0.1200") + 1


# ── the closed-trade line reads both vocabularies ───────────────────────────

class TestClosedRow:
    def test_a_paper_close_prints_its_record_and_its_time(self):
        out = _closed_trade_line(_paper_close())
        _check(out, ["LONG DOGE/USDT", "entry $0.1000", "exit $0.2000", "PnL $+1.00",
                     "closed 2026-09-12 14:30 UTC"],
               ["NOT ON RECORD", "not recorded", "close time not on record", "?"])

    def test_an_unrecorded_paper_exit_is_stated_not_zero_and_does_not_raise(self):
        out = _closed_trade_line(_paper_close(exit_price=None))
        _check(out, ["exit NOT ON RECORD (the close price could not be read)", "PnL $+1.00"],
               ["exit $0.0000"])

    def test_a_measured_zero_paper_pnl_is_still_a_zero(self):
        # RED HERRING: the paper book closes a trade with its P&L set
        # atomically, so 0.0 there is a measured break-even.
        out = _closed_trade_line(_paper_close(pnl=0.0))
        _check(out, ["PnL $+0.00"], ["not recorded"])

    def test_a_record_with_no_close_time_says_so(self):
        out = _closed_trade_line(_paper_close(closed_at=None))
        _check(out, ["close time not on record"], ["closed 20"])
        out = _closed_trade_line(NS(direction=NS(value="LONG"), asset="DOGE/USDT", entry_price=0.1,
                                    exit_price=0.2, pnl=1.0))
        _check(out, ["close time not on record"], [])

    def test_the_live_vocabulary_is_unchanged(self):
        live = NS(trade_id="LIVE-1", symbol="WIF/USDT:USDT", direction="SHORT", entry_price=1.0,
                  close_price=1.2, pnl_usd=87.65, commission=0.01, close_reason="TP", status="closed",
                  closed_at=CLOSED_AT)
        out = _closed_trade_line(live)
        _check(out, ["SHORT WIF/USDT:USDT", "entry $1.0000", "exit $1.2000", "PnL $+87.65", "closed via TP",
                     "closed 2026-09-12 14:30 UTC"], ["NOT ON RECORD", "not recorded"])
        unpriced = _closed_trade_line(NS(symbol="WIF/USDT:USDT", direction="LONG", entry_price=1.0,
                                         close_price=None, pnl_usd=None))
        _check(unpriced, ["exit NOT ON RECORD", "PnL not recorded"], ["$0.00"])

    def test_the_live_reading_wins_when_a_record_carries_both_names(self):
        # A live close that could not be priced (pnl_usd None) must not be
        # rescued by a stray paper-named field.
        out = _closed_trade_line(NS(symbol="WIF/USDT:USDT", asset="OTHER", direction="LONG", entry_price=1.0,
                                    close_price=None, exit_price=1.5, pnl_usd=None, pnl=5.0))
        _check(out, ["WIF/USDT:USDT", "exit NOT ON RECORD", "PnL not recorded"], ["OTHER", "$1.5000", "$+5.00"])


# ── the pending rows never raise and never print a placeholder ─────────────

class TestPendingRows:
    def _exec(self, *rows):
        return NS(open_positions=list(rows))

    def test_a_pending_row_with_no_levels_is_stated_not_zero(self):
        p = NS(status="pending_fill", direction="LONG", symbol="AVAX/USDT:USDT", entry_price=None,
               stop_loss=0.0, take_profit=None)
        out = _live_positions_block(self._exec(p))
        _check(out, ["UNFILLED LIMIT ORDERS", "AVAX/USDT:USDT", "limit price NOT ON RECORD",
                     "SL NONE ON RECORD", "TP NONE ON RECORD", "none right now"],
               ["$0.0000", "could not be read"])

    def test_a_full_pending_row_still_prints_its_levels(self):
        p = NS(status="pending_fill", direction="LONG", symbol="DOGE/USDT", entry_price=0.186440,
               stop_loss=0.188220, take_profit=0.184370)
        out = _live_positions_block(self._exec(p))
        _check(out, ["limit $0.1864", "SL $0.1882", "TP $0.1844"], ["NOT ON RECORD", "NONE ON RECORD"])


# ── the builder: the paper arms, labelled and unbreakable ──────────────────

def _paper_pf(*, open_positions=(), trade_history=(), last_prices=None, total_trades=None):
    trades = list(trade_history)
    return NS(snapshot=lambda: NS(open_positions=len(open_positions), equity_usd=100.0,
                                  total_pnl=sum(float(getattr(t, "pnl", 0.0) or 0.0) for t in trades),
                                  win_rate=0.0, total_trades=len(trades) if total_trades is None else total_trades,
                                  daily_pnl=0.0, max_drawdown_pct=0.0),
              open_positions=list(open_positions), _last_prices=dict(last_prices or {}),
              trade_history=trades)


def _paper_engine(pf):
    return NS(user_portfolios=NS(get=lambda uid, venue="": pf),
              risk=NS(circuit_breaker_active=False, trading_blocked_by=""),
              live_executor=None, pending_ideas=[])


def _prompt(pf, uid="u1"):
    with patch.object(type(bot.config.CONFIG), "is_live", return_value=False):
        return H._build_chat_system_prompt(_handler(_paper_engine(pf)), uid)


class TestThePaperPrompt:
    def test_positions_are_labelled_paper_and_a_zero_stop_is_no_stop(self):
        out = _prompt(_paper_pf(open_positions=[_paper_pos(stop_loss=0.0)], last_prices={"DOGE/USDT": 0.11}))
        _check(out, ["ACTIVE POSITIONS (PAPER — a simulated account, not real money", "LONG DOGE/USDT",
                     "SL NONE ON RECORD", "MARK $0.1100", "price move +10.00% from entry",
                     "equity ~$100.00 (PAPER — a simulated account, not real money)"],
               ["(live data)", "SL $0.0000", "could not be read just now", "size $"])

    def test_a_none_stop_does_not_blank_the_closed_trades(self):
        # The inline row raised on None and the `except` replaced BOTH
        # sections with "could not be read".
        out = _prompt(_paper_pf(open_positions=[_paper_pos(stop_loss=None)], trade_history=[_paper_close()]))
        _check(out, ["ACTIVE POSITIONS (PAPER", "SL NONE ON RECORD", "CURRENT PRICE UNAVAILABLE",
                     "RECENT CLOSED TRADES (PAPER — simulated fills)", "exit $0.2000", "PnL $+1.00",
                     "closed 2026-09-12 14:30 UTC"],
               ["could not be read just now", "SL $0.0000"])

    def test_an_empty_paper_book_reports_no_trades_not_a_zero(self):
        out = _prompt(_paper_pf())
        _check(out, ["ACTIVE POSITIONS (PAPER — a simulated account): none right now",
                     "total PnL: no closed trades yet, so none to report", "win rate not measurable",
                     "total trades 0"],
               # anchored to the section's own header: the base prompt's grounding
               # rule names "RECENT CLOSED TRADES" in prose
               ["total PnL $+0.00", "$+0.00", "(live data)", "RECENT CLOSED TRADES (PAPER"])

    def test_a_closed_paper_trade_prints_its_total_and_its_rows(self):
        # RED HERRING: with a closed trade on record the total IS a figure,
        # and a measured $0.00 close is printed as one.
        out = _prompt(_paper_pf(trade_history=[_paper_close(pnl=0.0)]))
        _check(out, ["total PnL $+0.00", "RECENT CLOSED TRADES (PAPER — simulated fills)", "PnL $+0.00",
                     "total trades 1"],
               ["no closed trades yet"])

    def test_an_unrecorded_paper_exit_reaches_the_model_as_words(self):
        out = _prompt(_paper_pf(trade_history=[_paper_close(exit_price=None)]))
        _check(out, ["exit NOT ON RECORD (the close price could not be read)"], ["exit $0.0000"])

    def test_twelve_paper_closes_render_five_and_the_block_says_so(self):
        """THE PAPER BRANCH IS THE SECOND COPY, and the live one's fix has to
        reach it or this is "fixing two left the third" inside one method.
        `trade_history[-5:]` under a header reading RECENT CLOSED TRADES was
        the model's whole evidence about a record of any length."""
        out = _prompt(_paper_pf(trade_history=[_paper_close() for _ in range(12)]))
        _check(out, ["RECENT CLOSED TRADES (PAPER — simulated fills)",
                     "...and 7 OLDER closed trade(s) not listed",
                     "the most recent 5 of 12",
                     "Do not total or count from this list",
                     "never that it did not happen"], [])

    def test_a_short_paper_record_is_not_told_it_is_partial(self):
        out = _prompt(_paper_pf(trade_history=[_paper_close(), _paper_close()]))
        _check(out, ["RECENT CLOSED TRADES (PAPER — simulated fills)"],
               ["OLDER closed trade(s) not listed", "the most recent 5 of"])

    def test_the_paper_branch_reads_the_same_name(self, monkeypatch):
        """One bound, both branches: it was the literal 5, written twice."""
        monkeypatch.setattr(H, "CHAT_RECENT_CLOSES", 3)
        out = _prompt(_paper_pf(trade_history=[_paper_close() for _ in range(12)]))
        _check(out, ["the most recent 3 of 12", "...and 9 OLDER closed trade(s)"],
               ["the most recent 5 of 12"])

    def test_engine_state_is_stated_when_it_cannot_be_read(self, monkeypatch):
        import bot.core.live_readiness as lr

        def boom(*a, **kw):
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(lr, "mode_label", boom)
        out = _prompt(_paper_pf(open_positions=[_paper_pos()]))
        _check(out, ["Engine state: could not be read — do not tell the user whether trading is live, "
                     "paper or halted; say it could not be confirmed"],
               ["Engine state: PAPER", "CB=OFF"])

    def test_a_healthy_engine_state_is_the_mode_and_the_breaker(self):
        # RED HERRING: the stated default must not replace a reading.
        out = _prompt(_paper_pf())
        _check(out, ["Engine state: PAPER mode, CB=OFF"], ["could not be read —"])
        assert _context_prompt("x", engine_state="") == "", "the context stub mirrors production's omission"


# ── wiring: the paper arms go through the seams ───────────────────────────

def test_the_paper_arms_render_through_the_seams():
    src = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
    i = src.index("def _build_chat_system_prompt")
    body = src[i:src.index("async def _llm_chat", i)]
    for must in ("_paper_position_row(pos, *_paper_mark_of(user_portfolio, pos.asset))",
                 "_refresh_paper_marks(self, user_portfolio)",
                 "trade_lines = [_closed_trade_line(t) for t in recent_trades]",
                 "RECENT CLOSED TRADES (PAPER", "ACTIVE POSITIONS (PAPER"):
        assert must in body, must
    for never in ("SL ${pos.stop_loss", "exit ${t.exit_price", "(live data)", 'engine_state = ""',
                  "size_usd = pos.quantity * pos.entry_price"):
        assert never not in body, never
    assert inspect.isfunction(th._paper_position_row) and inspect.isfunction(th._position_row_parts)
