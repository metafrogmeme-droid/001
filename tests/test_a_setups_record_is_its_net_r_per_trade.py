"""A setup's record on the analyze card is its net R per trade, with a verdict.

The expectancy nudge scores a setup by its win count, and a win count
flatters a setup that loses money: many small wins and a few full stops read
as a good record. The card prints what this kind of setup RETURNED per trade
-- net R, the 95% interval on the mean, and a verdict only when the whole
interval is on one side of zero (bot/core/setup_record.py).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from types import SimpleNamespace as NS

import pytest

from bot.core import setup_record as sr
from bot.core.setup_record import MIN_TRADES, setup_record, setup_record_line
from bot.core.trade_journal import KEEPS, TradeJournal
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _e(r, *, sym="SOL/USDT:USDT", regime="TREND_UP", d="LONG", pnl=None,
       reason="TP HIT"):
    return NS(symbol=sym, regime=regime, direction=d, r_multiple=r,
              pnl=(r * 10.0 if pnl is None and r is not None else pnl),
              exit_reason=reason)


def _rec(entries, sym="SOL/USDT", regime="TREND_UP", d="LONG"):
    return setup_record(entries, sym, regime, d)


# ── the win count is not the verdict ────────────────────────────────────────

def test_a_setup_that_wins_most_trades_and_loses_money_reads_as_losing():
    # 14 wins of +0.3R, 6 full stops: a 70% win rate and a net loss.
    rows = [_e(0.3) for _ in range(14)] + [_e(-1.0) for _ in range(6)]
    rec = _rec(rows)
    assert (rec.wins, rec.losses) == (14, 6)
    assert rec.mean_r == pytest.approx(-0.09)
    # And the card shows both, so the win count is visibly not the answer.
    assert "20 trades (14W/6L), -0.09R per trade" in setup_record_line(rec)


@pytest.mark.parametrize("rs,verdict", [
    ([1.0] * 5 + [0.8] * 5, "edge"),
    ([-1.0] * 6 + [-0.5] * 4, "losing"),
    # A losing mean whose interval still reaches above zero is not a verdict.
    ([3.0] * 2 + [-1.0] * 9, "no_edge"),
    # ...and neither is a WINNING mean whose interval reaches below zero.
    ([3.0] * 3 + [-1.0] * 8, "no_edge"),
    ([1.0] * (MIN_TRADES - 1), "thin"),
])
def test_the_interval_decides_and_the_floor_stands_beside_it(rs, verdict):
    rec = _rec([_e(r) for r in rs])
    assert rec.verdict == verdict, rec
    if verdict == "no_edge":
        assert rec.interval[0] < 0 < rec.interval[1]


def test_the_floor_is_scored_closes_at_its_boundary():
    at = _rec([_e(1.0)] * MIN_TRADES)
    under = _rec([_e(1.0)] * (MIN_TRADES - 1) + [_e(None, pnl=5.0)])
    assert at.verdict == "edge"
    # Ten closes, nine with an R: below the floor, and the tenth is counted.
    assert under.verdict == "thin" and (under.closes, under.scored) == (10, 9)


@pytest.mark.parametrize("rows,verdict", [
    ([], "none"),
    ([_e(None, pnl=4.0), _e(None, pnl=-2.0)], "unscored"),
])
def test_no_close_and_no_readable_r_are_two_facts(rows, verdict):
    assert _rec(rows).verdict == verdict


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "0.5"])
def test_an_r_that_is_not_a_number_is_unscored(bad):
    rec = _rec([_e(1.0)] * 3 + [_e(bad, pnl=1.0)])
    assert (rec.closes, rec.scored) == (4, 3)


# ── which closes ────────────────────────────────────────────────────────────

def test_a_never_filled_order_and_an_execution_abort_are_not_the_setups():
    rows = ([_e(1.0)] * 3
            + [_e(0.0, pnl=0.0, reason="expired")]
            + [_e(-0.2, reason="leverage_overshoot")])
    rec = _rec(rows)
    assert rec.closes == 3
    # A non-fill label on a close that made or lost money is still a trade.
    assert _rec([_e(0.4, reason="canceled")]).closes == 1


def test_symbol_spellings_are_one_market_and_an_empty_regime_is_unknown():
    rows = [_e(1.0, sym="SOLUSDT"), _e(1.0, sym="sol/usdt"), _e(1.0, sym="SOL/USDT:USDT")]
    assert _rec(rows).own_closes == 3
    rec = _rec([_e(1.0, regime="")], regime="")
    assert rec.closes == 1 and rec.regime == "UNKNOWN"
    assert "an unknown regime" in setup_record_line(rec)


def test_the_direction_reads_an_enum_too():
    from bot.utils.models import Direction
    assert _rec([_e(1.0)], d=Direction.LONG).closes == 1
    assert _rec([_e(1.0)], d=Direction.SHORT).closes == 0


# ── backing off ─────────────────────────────────────────────────────────────

def test_a_thin_setup_backs_off_to_its_regime_then_its_direction():
    own = [_e(0.5)] * 3
    peers = [_e(0.5, sym="BTC/USDT")] * 12
    rec = _rec(own + peers)
    assert (rec.tier, rec.closes, rec.own_closes) == ("regime", 15, 3)
    line = setup_record_line(rec)
    assert "longs closed in TREND_UP, any symbol (SOL itself: 3)" in line

    other_regime = [_e(0.5, sym="ETH/USDT", regime="RANGE")] * 12
    rec = _rec(own + other_regime)
    assert rec.tier == "direction" and rec.closes == 15
    assert "longs, any symbol or regime (SOL itself: 3)" in setup_record_line(rec)


def test_a_setup_with_its_own_record_answers_whatever_its_peers_did():
    # The first round's survivor: every fixture had a setup below the floor,
    # so trying the regime tier first changed no verdict. Here the setup's own
    # ten closes made money and its regime peers lost it.
    rec = _rec([_e(1.0)] * MIN_TRADES + [_e(-1.0, sym="BTC/USDT")] * 30)
    assert (rec.tier, rec.verdict, rec.closes) == ("setup", "edge", MIN_TRADES)


def test_when_no_tier_qualifies_the_setup_answers_for_itself():
    # "Too few" is said about the setup, never about a population it is part of.
    rec = _rec([_e(0.5)] * 3 + [_e(0.5, sym="BTC/USDT")] * 2)
    assert rec.tier == "setup" and rec.closes == 3 and rec.verdict == "thin"


# ── the line ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rows,words", [
    ([_e(1.0)] * 5 + [_e(0.8)] * 5, ": made money per trade."),
    ([_e(-1.0)] * 6 + [_e(-0.5)] * 4, ": lost money per trade."),
    ([_e(3.0)] * 2 + [_e(-1.0)] * 9, ": no edge measurable either way."),
    ([_e(1.0)] * 3, "3 trades (3W/0L), too few to judge."),
    ([_e(None, pnl=2.0)], "1 trade (1W/0L, 1 with no readable R), none with a readable R."),
    ([], "no closed trade on record."),
])
def test_each_verdict_has_its_sentence(rows, words):
    assert setup_record_line(_rec(rows)).endswith(words)


def test_a_flat_close_is_counted_as_one():
    rec = _rec([_e(0.0, pnl=0.0)] + [_e(1.0)] * 2)
    assert "(2W/0L/1F)" in setup_record_line(rec)


def test_the_interval_is_printed_with_the_mean():
    # The interval is the one instrument's, not a second copy of it.
    from bot.core.arb_tracker import mean_interval
    lo, hi = mean_interval([1.0] * 5 + [0.8] * 5)
    line = setup_record_line(_rec([_e(1.0)] * 5 + [_e(0.8)] * 5))
    assert f"+0.90R per trade (95% {lo:+.2f} to {hi:+.2f})" in line


def test_an_unplaceable_verdict_is_refused():
    rec = sr.SetupRecord("setup", "SOL", "X", "LONG", 1, 1, 1, 0, 0, 1.0, None,
                         "maybe", 1)
    with pytest.raises(ValueError):
        setup_record_line(rec)


# ── the journal says when it could not be read ──────────────────────────────

def test_a_journal_that_will_not_load_says_so(tmp_path):
    f = tmp_path / "journal.json"
    f.write_text("{not json")
    assert TradeJournal(str(f)).read_failed is True
    assert TradeJournal(str(tmp_path / "absent.json")).read_failed is False


def test_the_journal_keeps_its_newest_keeps_entries(tmp_path):
    j = TradeJournal(str(tmp_path / "j.json"))
    for i in range(KEEPS + 3):
        j.record_trade(trade_id=f"T{i}", symbol="SOL/USDT", direction="LONG",
                       strategy_type="swing", entry_price=10.0, exit_price=11.0,
                       stop_loss=9.0, take_profit=12.0, pnl=1.0, quantity=1.0)
    assert len(json.loads((tmp_path / "j.json").read_text())) == KEEPS


# ── the card ────────────────────────────────────────────────────────────────

def _engine(entries, *, read_failed=False, regime="TREND_UP"):
    asked = []

    def _outcome_regime(sym):
        asked.append(sym)
        return regime
    journal = NS(read_failed=read_failed, closed_entries=lambda: list(entries))
    return NS(journal=journal, _outcome_regime=_outcome_regime), asked


def test_the_card_line_reads_the_record_under_the_journals_own_regime_word():
    from bot.skills.skill_registry import _setup_record_line
    eng, asked = _engine([_e(1.0)] * 3)
    idea = NS(asset="SOL/USDT", direction=NS(value="LONG"))
    line = _setup_record_line(eng, idea)
    assert asked == ["SOL/USDT"]
    assert line == "📒 Record, SOL longs closed in TREND_UP: 3 trades (3W/0L), too few to judge."


def test_an_unreadable_journal_is_not_a_setup_with_no_trades():
    from bot.skills.skill_registry import _setup_record_line
    eng, _ = _engine([], read_failed=True)
    line = _setup_record_line(eng, NS(asset="SOL/USDT", direction="LONG"))
    assert "could not be read" in line and "no closed trade" not in line


def test_a_full_journal_says_its_record_is_its_newest_closes():
    from bot.skills.skill_registry import _setup_record_line
    eng, _ = _engine([_e(1.0, sym="BTC/USDT")] * KEEPS)
    line = _setup_record_line(eng, NS(asset="SOL/USDT", direction="LONG"))
    assert line.endswith(f"(the journal keeps its newest {KEEPS} closes)")


def test_no_journal_or_a_fault_omits_the_line_and_leaves_the_card():
    from bot.skills.skill_registry import _setup_record_line
    assert _setup_record_line(NS(), NS(asset="SOL/USDT", direction="LONG")) == ""

    def boom(_):
        raise RuntimeError("regime read failed")
    eng = NS(journal=NS(read_failed=False, closed_entries=lambda: []),
             _outcome_regime=boom)
    assert _setup_record_line(eng, NS(asset="SOL/USDT", direction="LONG")) == ""


def test_the_analyze_card_prints_it_between_the_ratio_and_the_thesis():
    # The card is the tail of a 180-line async skill behind an exchange and the
    # analyzer; where the line sits is a shape, stated as one.
    src = code_only((ROOT / "bot/skills/skill_registry.py").read_text())
    i = src.index('f"  \\u2606 Risk:Reward')
    assert src.index('f"{record_line}"', i) < src.index('f"{thesis_bq}"', i)
    assert "_record = _setup_record_line(engine, idea)" in src


def test_journal_reports_an_unreadable_journal_rather_than_an_empty_week():
    from bot.skills.engine_ops_commands import EngineOpsCommands
    sent = []

    async def send_message(**kw):
        sent.append(kw["text"])
    self = NS(_is_admin=lambda u: True,
              engine=NS(journal=NS(read_failed=True,
                                   get_weekly_review=lambda: {"trades": 0})))
    update = NS(effective_chat=NS(id=1))
    ctx = NS(bot=NS(send_message=send_message))
    asyncio.run(EngineOpsCommands._cmd_journal(self, update, ctx))
    assert len(sent) == 1 and "could not be read" in sent[0]
    assert "No trades" not in sent[0]


def test_a_card_too_long_for_a_caption_is_sent_whole_not_cut():
    # The /analyze send path sits behind a registry dispatch, the pending-idea
    # book and the image renderer; the rule is stated as a shape: no raw cut,
    # and an over-long card follows its image as a message with the buttons.
    src = code_only((ROOT / "bot/skills/scan_commands.py").read_text())
    assert "result[:1020]" not in src and "result[:1024]" not in src
    i = src.index("len(result) <= _CAPTION_LIMIT")
    tail = src[i:src.index("if not card_sent:", i)]
    assert "await self._send(update, result, reply_markup=kb)" in tail
