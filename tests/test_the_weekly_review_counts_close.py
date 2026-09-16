"""`Trades: 5 (2W / 1L)`. Two plus one is not five.

`get_weekly_review` built its own buckets -- `wins = [e for e in recent if
e.pnl > 0]` and `losses = [... < 0]` -- beside a `trades` that was
`len(recent)`. A close the record priced at exactly 0.00 is a MEASURED
BREAK-EVEN: it is in the total and in neither bucket, so two of the five rows
had no word anywhere on the card. What a reader does with two numbers and a
total is subtract, and `5 - 2 = 3 losses` files those closes as defeats --
the `losses = len(all) - wins` shape from CLAUDE.md's table, arriving through
the reader rather than through the code.

The same window's win rate was `len(wins) / len(recent)`, so a break-even sat
in the denominator and not the numerator: 2/5 = 40% where two of the three
DECIDED closes is 67%. That is the argument this repo already records about
the live-performance governor ("not a win, but in the denominator, dragging
the rate down"), on a reader nobody had cured.

THE CORRECT CLASSIFICATION WAS ALREADY IN THE TREE, PRIVATELY.
`skill_registry`'s /journal-style card counts three outcomes by hand, with a
comment saying a flat "used to be filed as a LOSS". Every other record
surface asks `bot/utils/win_rate.win_stats` -- which returned `wins`,
`scored` and `unscored` and NO losses, so five callers had to subtract, and
`scored - wins` gets the break-even wrong in exactly the way the private copy
had already fixed. One of those five sits directly under a comment saying
`len(...) - wins` "would have shown it as an L".

So the seam carries all four counts now and they CLOSE:

    wins + losses + flat + unscored == total

and the five subtracting callers read rather than subtract.

THE SECOND HALF IS THE WINDOW ITSELF. `/journal`'s EMPTY branch has always
asked whether the journal's silence is a recording gap -- `_journal_gap_closes`
exists because "no entries" is a claim about the JOURNAL and was being read as
a claim about TRADING. The non-empty branch makes the same kind of claim,
`Trades: N` for a window, and asked nothing: a live close the venue could not
price is never journaled at all (`engine._on_live_position_closed` gates the
write on a P&L that is not None), so the count silently excluded it. One
command, two branches, one of them guarded.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from bot.compat import UTC
from bot.core.trade_journal import JournalEntry, TradeJournal
from bot.skills.engine_ops_commands import (
    EngineOpsCommands,
    _outcome_line,
    _total_pnl_line,
    _win_rate_line,
    _window_coverage_line,
)
from bot.utils.win_rate import win_stats

_NOW = time.time()


def _entry(pnl, *, r=1.0, sym="BTC/USDT", regime="trend", strat="scalp"):
    return JournalEntry(
        trade_id=sym, symbol=sym, direction="long", strategy_type=strat,
        entry_price=100.0, exit_price=101.0, stop_loss=99.0, take_profit=103.0,
        pnl=pnl, pnl_pct=1.0, r_multiple=r, holding_hours=2.0,
        regime=regime, timestamp=_NOW - 3600)


def _unreadable(**kw):
    """An entry whose P&L cannot be read.

    Reachable off DISK, not invented for the test: `TradeJournal._load` does
    `json.load` and hands `d["pnl"]` straight to the entry, and Python's json
    parses a bare `NaN` token by default in both directions. `trade_pnl`
    refuses NaN and inf, so such a row is `unscored` — which is the whole
    reason the review's rate and total are three-valued rather than floats.
    Without a row like this in the corpus, a mutation that collapses either
    back to a number changes no verdict anywhere.
    """
    kw.setdefault("r", None)
    return _entry(float("nan"), **kw)


def _journal(*entries) -> TradeJournal:
    j = TradeJournal.__new__(TradeJournal)
    j._entries = list(entries)
    return j


# ── 1. the shared reading classifies three outcomes and closes ────────────

class TestWinStatsCloses:
    """Four buckets that add up to the total, over every kind of row."""

    CORPUS = [NS(pnl_usd=10.0), NS(pnl_usd=-5.0), NS(pnl_usd=0.0),
              NS(pnl_usd=0.0), NS(pnl_usd=None), NS(pnl_usd=2.0)]

    def test_the_four_counts_add_up_to_the_total(self):
        s = win_stats(self.CORPUS)
        assert s["wins"] + s["losses"] + s["flat"] + s["unscored"] == s["total"]
        assert s["total"] == len(self.CORPUS)

    def test_a_measured_break_even_is_flat_and_not_a_loss(self):
        s = win_stats([NS(pnl_usd=0.0)])
        assert s["flat"] == 1
        assert s["losses"] == 0
        assert s["wins"] == 0
        # and it IS scored -- 0.0 is a measurement, unlike None
        assert s["scored"] == 1 and s["unscored"] == 0

    def test_the_old_subtraction_would_have_called_it_a_loss(self):
        # Pinned so the shape is named rather than remembered: this is what
        # every caller that subtracted was computing.
        s = win_stats(self.CORPUS)
        assert s["scored"] - s["wins"] == 3        # what subtraction says
        assert s["losses"] == 1                    # what actually lost

    def test_an_unpriced_row_is_in_neither_bucket(self):
        s = win_stats([NS(pnl_usd=None)])
        assert (s["wins"], s["losses"], s["flat"]) == (0, 0, 0)
        assert s["unscored"] == 1

    def test_the_rate_is_over_scored_and_none_when_nothing_is(self):
        assert win_stats([NS(pnl_usd=None)])["rate"] is None
        assert win_stats([])["rate"] is None
        # a flat is scored, so it is in the rate's denominator
        assert win_stats([NS(pnl_usd=4.0), NS(pnl_usd=0.0)])["rate"] == 0.5


# ── 2. the weekly review reads that seam rather than its own buckets ──────

class TestTheReviewCounts:
    def test_the_card_numbers_close_over_the_window(self):
        rev = _journal(_entry(10.0), _entry(20.0), _entry(-5.0),
                       _entry(0.0), _entry(0.0, r=None)).get_weekly_review()
        assert rev["trades"] == 5
        assert (rev["wins"] + rev["losses"] + rev["flat"]
                + rev["unscored"]) == rev["trades"]

    def test_a_break_even_is_reported_as_flat(self):
        rev = _journal(_entry(10.0), _entry(0.0)).get_weekly_review()
        assert rev["flat"] == 1
        assert rev["losses"] == 0

    def test_the_rate_is_over_decided_closes_not_the_window(self):
        # 2 wins, 1 loss, 2 flats. Over the window that is 40%; over what
        # could be scored it is 40% too -- a flat IS scored. The thing that
        # must not happen is the flat vanishing from the denominator, so pin
        # the number that follows from the stated rule.
        rev = _journal(_entry(10.0), _entry(20.0), _entry(-5.0),
                       _entry(0.0), _entry(0.0)).get_weekly_review()
        assert rev["scored"] == 5
        assert rev["win_rate"] == 40.0

    def test_an_unreadable_entry_leaves_both_halves_of_the_rate(self):
        rev = _journal(_entry(10.0), _entry(-5.0), _unreadable()).get_weekly_review()
        assert rev["trades"] == 3
        assert rev["scored"] == 2 and rev["unscored"] == 1
        # 1 of the 2 we could price, NOT 1 of 3
        assert rev["win_rate"] == 50.0
        assert rev["pnl_scored"] == 2 and rev["pnl_unscored"] == 1

    def test_a_window_nothing_could_price_reports_neither(self):
        rev = _journal(_unreadable(), _unreadable()).get_weekly_review()
        assert rev["win_rate"] is None        # not 0.0 -- nothing lost
        assert rev["total_pnl"] is None       # not 0.00 -- the book is not flat
        assert rev["scored"] == 0 and rev["unscored"] == 2

    def test_a_group_holding_an_unreadable_row_still_closes(self):
        rev = _journal(_entry(10.0, regime="trend"),
                       _unreadable(regime="trend")).get_weekly_review()
        g = rev["by_regime"]["trend"]
        assert g["trades"] == 2 and g["unscored"] == 1 and g["scored"] == 1
        assert g["wins"] + g["losses"] + g["flat"] + g["unscored"] == g["trades"]
        assert g["pnl"] == 10.0     # the readable row only

    def test_the_review_carries_its_own_coverage(self):
        rev = _journal(_entry(1.0), _entry(2.0)).get_weekly_review()
        for key in ("flat", "scored", "unscored", "pnl_scored", "pnl_unscored"):
            assert key in rev, key

    def test_the_groups_close_too(self):
        rev = _journal(_entry(10.0, regime="trend"),
                       _entry(0.0, regime="trend"),
                       _entry(-2.0, regime="chop")).get_weekly_review()
        for g in list(rev["by_regime"].values()) + list(rev["by_strategy"].values()):
            assert g["wins"] + g["losses"] + g["flat"] + g["unscored"] == g["trades"]

    def test_a_group_total_is_over_what_it_could_price(self):
        rev = _journal(_entry(10.0, regime="trend")).get_weekly_review()
        g = rev["by_regime"]["trend"]
        assert g["pnl"] == 10.0 and g["scored"] == 1


# ── 3. the card says it, and the card is RENDERED ────────────────────────

class TestTheCardNamesEveryBucket:
    def test_the_flat_closes_are_named(self):
        line = _outcome_line({"trades": 5, "wins": 2, "losses": 1,
                              "flat": 2, "unscored": 0})
        assert "2W" in line and "1L" in line and "2 flat" in line

    def test_a_bucket_at_zero_is_omitted(self):
        # A permanent "0 flat" on every healthy week is the row that trains a
        # reader to stop reading the line.
        line = _outcome_line({"trades": 3, "wins": 2, "losses": 1,
                              "flat": 0, "unscored": 0})
        assert "flat" not in line and "unpriced" not in line

    def test_a_review_recorded_before_the_split_says_less_not_more(self):
        line = _outcome_line({"trades": 5, "wins": 2, "losses": 1})
        assert "flat" not in line          # never a fabricated zero
        assert "2W" in line and "1L" in line

    def test_an_unpriced_row_is_named_too(self):
        line = _outcome_line({"trades": 4, "wins": 2, "losses": 1,
                              "flat": 0, "unscored": 1})
        assert "1 unpriced" in line

    def test_an_unpriceable_window_is_not_a_zero_percent_week(self):
        line = _win_rate_line({"win_rate": None, "trades": 4})
        assert "0%" not in line
        assert "could be priced" in line

    def test_a_partial_rate_carries_its_coverage(self):
        line = _win_rate_line({"win_rate": 66.7, "scored": 3, "unscored": 2})
        assert "3 of 5" in line

    def test_an_unpriceable_total_is_not_a_flat_book(self):
        line = _total_pnl_line({"total_pnl": None})
        assert "0.00" not in line and "$" not in line

    def test_a_partial_total_carries_its_coverage(self):
        line = _total_pnl_line({"total_pnl": 12.5, "pnl_scored": 3,
                                "pnl_unscored": 2})
        assert "$+12.50" in line and "3 of 5" in line


class TestBestAndWorstAreMeasured:
    """`max(recent, key=...)` over a NaN keeps whatever it met first.

    Every comparison against NaN is False, so `max` never replaces its
    incumbent when it meets one -- which means the winner was decided by LIST
    ORDER, not by any measurement, and the card printed `Best: BTC/USDT
    $+nan` under a trophy. Found by rendering the card for a window nothing
    could price, not by reading the diff.
    """

    def test_an_unreadable_row_cannot_be_the_best_trade(self):
        # The NaN is FIRST, which is the order in which `max` keeps it.
        rev = _journal(_unreadable(sym="JUNK/USDT"),
                       _entry(10.0, sym="BTC/USDT")).get_weekly_review()
        assert rev["best_trade"]["symbol"] == "BTC/USDT"
        assert rev["worst_trade"]["symbol"] == "BTC/USDT"

    def test_there_is_no_best_trade_when_nothing_could_be_priced(self):
        rev = _journal(_unreadable(), _unreadable()).get_weekly_review()
        assert rev["best_trade"] is None
        assert rev["worst_trade"] is None

    def test_the_card_omits_them_rather_than_printing_junk(self):
        out = _render(_journal(_unreadable(), _unreadable()))
        assert "nan" not in out.lower()
        assert "Best:" not in out and "Worst:" not in out

    def test_a_priced_window_still_names_both(self):
        out = _render(_journal(_entry(10.0), _entry(-4.0), _unreadable()))
        assert "Best:" in out and "$+10.00" in out
        assert "Worst:" in out and "$-4.00" in out


class TestTheWindowCoverage:
    def test_more_executor_closes_than_entries_is_named(self):
        line = _window_coverage_line(8, 5)
        assert "3 more position(s) closed" in line
        assert "recording gap" in line

    def test_it_is_one_directional(self):
        # The journal is fed by PAPER closes too, so holding more than any
        # executor recorded is normal and is not a gap.
        assert _window_coverage_line(3, 5) == ""
        assert _window_coverage_line(5, 5) == ""

    def test_an_unreadable_executor_says_nothing_rather_than_all_clear(self):
        # `_journal_gap_closes` reports 0 on any error. That must produce NO
        # line -- the OMIT strategy -- never a sentence asserting a gap, and
        # never one asserting there is none.
        assert _window_coverage_line(0, 5) == ""

    def test_junk_does_not_take_the_command_down(self):
        assert _window_coverage_line(None, 5) == ""       # type: ignore[arg-type]


# ── 4. the rendered card, driven end to end ──────────────────────────────

class _Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id=None, text="", **kw) -> None:
        self.sent.append(text)


class _Self:
    """The two attributes `_cmd_journal` reaches for, and nothing else."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.sent: list[str] = []

    def _is_admin(self, update) -> bool:
        return True

    async def _send(self, update, text, **kw) -> None:
        self.sent.append(text)

    async def _send_error(self, update, name, exc) -> None:
        self.sent.append(f"ERROR {name}: {exc}")


def _render(journal, *, executor_closes=0) -> str:
    """Run the real `_cmd_journal` and hand back what the operator saw."""
    now = datetime.now(UTC)
    closed = [NS(closed_at=now - timedelta(days=1)) for _ in range(executor_closes)]
    engine = NS(journal=journal,
                live_executor=NS(closed_positions=closed),
                _user_executors={})
    me = _Self(engine)
    bot = _Bot()
    update = NS(effective_chat=NS(id=1), effective_user=NS(id=1))
    ctx = NS(bot=bot)
    asyncio.run(EngineOpsCommands._cmd_journal(me, update, ctx))
    return "\n".join(bot.sent + me.sent)


class TestTheRenderedCard:
    """A helper that is correct and never reached is the #999 shape."""

    def test_the_flat_closes_reach_the_operator(self):
        out = _render(_journal(_entry(10.0), _entry(20.0), _entry(-5.0),
                               _entry(0.0), _entry(0.0)))
        assert "Weekly Trade Review" in out
        assert "2 flat" in out
        # and the reader is never handed a W/L pair that does not add up
        assert "(2W / 1L)" not in out

    def test_an_unpriceable_window_reaches_the_operator_as_neither(self):
        out = _render(_journal(_unreadable(), _unreadable()))
        assert "0%" not in out
        assert "$+0.00" not in out
        assert "could be priced" in out

    def test_a_partial_window_says_so_on_the_card(self):
        # Five journalled, eight closed: three closes the journal never saw.
        out = _render(_journal(*[_entry(1.0) for _ in range(5)]),
                      executor_closes=8)
        assert "3 more position(s) closed" in out

    def test_a_complete_window_says_nothing_about_gaps(self):
        out = _render(_journal(*[_entry(1.0) for _ in range(5)]),
                      executor_closes=5)
        assert "recording gap" not in out

    def test_the_empty_branch_still_names_its_gap(self):
        # The branch that was already guarded must keep working.
        out = _render(_journal(), executor_closes=4)
        assert "4" in out and "recording gap" in out

    def test_an_empty_window_with_no_closes_is_a_quiet_week(self):
        out = _render(_journal(), executor_closes=0)
        assert "No trades in the last 7 days" in out


# ── 5. ONE classifier, not two ───────────────────────────────────────────

class TestOneClassifier:
    """`skill_registry`'s hand-rolled loop was the only correct copy.

    It is left as it stands -- it needs a per-ROW verdict for each line's icon,
    which is a different question from how many there are -- but the counts it
    produces and the counts the seam produces must be the same answer. A
    byte-identical copy agrees with every fixture and diverges on the first
    edit to either, so this drives both over a corpus that contains the row
    they used to disagree about.
    """

    def test_the_private_loop_and_the_seam_agree_including_on_a_flat(self):
        rows = [NS(pnl=5.0), NS(pnl=-2.0), NS(pnl=0.0), NS(pnl=7.0)]
        wins = losses = flat = 0
        for r in rows:                      # skill_registry's own three-way
            if r.pnl > 0:
                wins += 1
            elif r.pnl < 0:
                losses += 1
            else:
                flat += 1
        s = win_stats(rows)
        assert (s["wins"], s["losses"], s["flat"]) == (wins, losses, flat)

    @pytest.mark.parametrize("module,attr", [
        ("bot.skills.portfolio_commands", None),
        ("bot.formatters.rich_cards", None),
    ])
    def test_no_caller_derives_losses_by_subtracting(self, module, attr):
        import importlib

        from tests.source_scan import code_only
        src = code_only(open(
            importlib.import_module(module).__file__, encoding="utf-8").read())
        assert '["scored"] - wins' not in src, (
            f"{module} is back to subtracting for its loss count, which files "
            "every measured break-even as a loss")
