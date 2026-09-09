"""An R-multiple is a ratio against the stop. Without a stop there is no R.

FOUND BY WIDENING THE VOCABULARY, which is the point of the vocabulary being
shared. `scripts/honesty_gate.py` decides what counts as a measurement from
`tests/honesty_vocabulary.json`, and that list did not contain the word
``net`` — so ``net_r``, the field PR #314's dashboard defect was rendered
from, was invisible to the gate written to find exactly that class of defect.
Adding ``net`` (and the substring ``rmultiple``) surfaced this, two files
away from where the search started.

WHAT IT WAS::

    initial_risk = abs(entry_price - stop_loss)
    r_multiple = pnl / (initial_risk * (1 if direction == "LONG" else -1)) \\
        if initial_risk > 0 else 0

and BOTH live callers hand it::

    stop_loss=float(getattr(pos, "stop_loss", 0) or 0),
    entry_price=float(getattr(pos, "entry_price", 0) or 0),

— the absent-field-is-zero shape, applied to the one field the whole
calculation is a ratio *against*.

TWO WRONG ANSWERS FROM ONE UNREADABLE FIELD:

  * **stop unreadable, entry readable.** `abs(entry - 0)` is `entry`, so
    `r_multiple = pnl / entry_price`. That is not an R-multiple. It is P&L
    over the entry price — a different quantity, in the right units, wearing
    the name of one that means something. An orphan position (the kind the bot
    did not open, and therefore the kind whose stop it cannot read) is exactly
    the case that lands here.
  * **neither readable.** `abs(0 - 0) == 0` takes the `else 0` branch, and 0R
    is a REAL outcome — a trade that ended exactly at its risk distance. An
    unmeasurable close entered the permanent record indistinguishable from a
    measured break-even. The same defect as the ghost close booked at its own
    entry price, one module over.

Both flowed into `get_weekly_review`'s `avg_r_multiple`, which `/journal`
prints as `Avg R-Multiple: +0.02R` above `Best: SOL $+41.00 (0.0R)`.
"""

import json
import types

import pytest

from bot.core.trade_journal import (
    JournalEntry,
    TradeJournal,
    average_r,
    r_multiple_for,
)
from bot.skills.engine_ops_commands import _avg_r_line, _r_tag

# ── 1. the arithmetic ─────────────────────────────────────────────────────

class TestRMultipleFor:
    def test_a_real_long_measures(self):
        # entry 100, stop 95 -> risk 5; +10 pnl is +2R.
        assert r_multiple_for(100.0, 95.0, 10.0, "LONG") == pytest.approx(2.0)

    def test_a_real_short_measures(self):
        assert r_multiple_for(100.0, 105.0, 10.0, "SHORT") == pytest.approx(-2.0)

    def test_a_losing_long_is_negative_r(self):
        assert r_multiple_for(100.0, 95.0, -5.0, "LONG") == pytest.approx(-1.0)

    def test_no_stop_on_record_has_no_r(self):
        """The expensive case: it used to answer pnl / entry_price."""
        assert r_multiple_for(100.0, 0.0, 10.0, "LONG") is None

    def test_it_does_not_answer_the_old_fabricated_number(self):
        # 10 / 100 == 0.1, which is what the old expression produced and what
        # the card printed as "0.1R". Pinned as a number, not as prose.
        assert r_multiple_for(100.0, 0.0, 10.0, "LONG") != pytest.approx(0.1)

    def test_neither_price_readable_has_no_r(self):
        """`abs(0 - 0) == 0` took the `else 0` branch — a measured 0R."""
        out = r_multiple_for(0.0, 0.0, 10.0, "LONG")
        assert out is None
        assert out is not 0.0  # noqa: F632 - identity is the point

    def test_a_stop_at_the_entry_has_no_r(self):
        assert r_multiple_for(100.0, 100.0, 10.0, "LONG") is None

    def test_a_negative_price_is_not_a_price(self):
        assert r_multiple_for(-1.0, 95.0, 10.0, "LONG") is None
        assert r_multiple_for(100.0, -5.0, 10.0, "LONG") is None

    def test_garbage_answers_none_rather_than_raising(self):
        for bad in (None, "x", object()):
            assert r_multiple_for(bad, 95.0, 10.0, "LONG") is None
            assert r_multiple_for(100.0, bad, 10.0, "LONG") is None

    def test_a_genuine_zero_r_still_measures(self):
        """0R is real — the fix must not swallow it along with the unknowns."""
        assert r_multiple_for(100.0, 95.0, 0.0, "LONG") == 0.0


# ── 2. the average, and its coverage ──────────────────────────────────────

def _entry(r):
    return types.SimpleNamespace(r_multiple=r)


class TestAverageR:
    def test_it_averages_only_what_was_measured(self):
        out = average_r([_entry(2.0), _entry(None), _entry(-1.0), _entry(None)])
        assert out["avg"] == pytest.approx(0.5)      # (2 + -1) / 2, not / 4
        assert out["scored"] == 2 and out["total"] == 4

    def test_nothing_measurable_is_none_not_zero(self):
        out = average_r([_entry(None), _entry(None)])
        assert out["avg"] is None
        assert out["scored"] == 0 and out["total"] == 2

    def test_an_all_measured_window_is_unchanged(self):
        out = average_r([_entry(1.0), _entry(3.0)])
        assert out["avg"] == pytest.approx(2.0)
        assert out["scored"] == out["total"] == 2

    def test_a_measured_zero_counts(self):
        out = average_r([_entry(0.0), _entry(2.0)])
        assert out["avg"] == pytest.approx(1.0)
        assert out["scored"] == 2

    def test_an_empty_window_is_none(self):
        assert average_r([])["avg"] is None
        assert average_r(None)["avg"] is None


# ── 3. through the journal, end to end ────────────────────────────────────

class TestRecordTrade:
    @staticmethod
    def _journal(tmp_path):
        return TradeJournal(journal_file=str(tmp_path / "journal.json"))

    def test_a_close_with_no_stop_records_no_r(self, tmp_path):
        j = self._journal(tmp_path)
        e = j.record_trade(
            trade_id="T1", symbol="SOL/USDT", direction="LONG",
            strategy_type="swing", entry_price=100.0, exit_price=141.0,
            stop_loss=0.0, take_profit=0.0, pnl=41.0, holding_hours=3.0)
        assert e.r_multiple is None, (
            "an orphan with no recorded stop got an R off pnl / entry_price")

    def test_a_close_with_a_stop_still_records_one(self, tmp_path):
        j = self._journal(tmp_path)
        e = j.record_trade(
            trade_id="T2", symbol="SOL/USDT", direction="LONG",
            strategy_type="swing", entry_price=100.0, exit_price=110.0,
            stop_loss=95.0, take_profit=115.0, pnl=10.0, holding_hours=3.0)
        assert e.r_multiple == pytest.approx(2.0)

    def test_the_weekly_review_averages_only_the_scoreable(self, tmp_path):
        j = self._journal(tmp_path)
        j.record_trade(trade_id="A", symbol="S", direction="LONG",
                       strategy_type="x", entry_price=100.0, exit_price=110.0,
                       stop_loss=95.0, take_profit=0.0, pnl=10.0)
        j.record_trade(trade_id="B", symbol="S", direction="LONG",
                       strategy_type="x", entry_price=100.0, exit_price=141.0,
                       stop_loss=0.0, take_profit=0.0, pnl=41.0)
        rev = j.get_weekly_review()
        assert rev["trades"] == 2
        assert rev["r_scored"] == 1 and rev["r_unscored"] == 1
        assert rev["avg_r_multiple"] == pytest.approx(2.0), (
            "the unscoreable close was averaged in as 0R")

    def test_a_window_with_no_scoreable_close_reports_none(self, tmp_path):
        j = self._journal(tmp_path)
        j.record_trade(trade_id="A", symbol="S", direction="LONG",
                       strategy_type="x", entry_price=100.0, exit_price=141.0,
                       stop_loss=0.0, take_profit=0.0, pnl=41.0)
        rev = j.get_weekly_review()
        assert rev["avg_r_multiple"] is None
        assert rev["r_scored"] == 0

    def test_the_r_keyed_tags_do_not_fire_without_an_r(self, tmp_path):
        """`runner` and `full_stop` are claims about the size of a move in
        risk units, and there are no risk units here. They used to be absent
        anyway — because an unknown R arrived as 0.0 and failed every
        threshold — which is the right output reached for the wrong reason,
        and reached differently the moment a threshold moves."""
        j = self._journal(tmp_path)
        e = j.record_trade(trade_id="A", symbol="S", direction="LONG",
                           strategy_type="x", entry_price=100.0,
                           exit_price=400.0, stop_loss=0.0, take_profit=0.0,
                           pnl=300.0)
        assert "winner" in e.tags, "the P&L half of the tagging still applies"
        assert "runner" not in e.tags and "solid_win" not in e.tags

    def test_a_real_runner_is_still_tagged(self, tmp_path):
        j = self._journal(tmp_path)
        e = j.record_trade(trade_id="A", symbol="S", direction="LONG",
                           strategy_type="x", entry_price=100.0,
                           exit_price=120.0, stop_loss=95.0, take_profit=0.0,
                           pnl=20.0)                      # +4R
        assert "runner" in e.tags

    def test_it_survives_a_round_trip_through_disk(self, tmp_path):
        """`_save` writes JSON null; `_load` read `.get("r_mult", 0)` and
        turned it straight back into a measured break-even."""
        path = tmp_path / "journal.json"
        j = TradeJournal(journal_file=str(path))
        j.record_trade(trade_id="A", symbol="S", direction="LONG",
                       strategy_type="x", entry_price=100.0, exit_price=141.0,
                       stop_loss=0.0, take_profit=0.0, pnl=41.0)
        assert json.loads(path.read_text())[0]["r_mult"] is None
        again = TradeJournal(journal_file=str(path))
        assert again._entries[0].r_multiple is None, (
            "an unscoreable R came back off disk as 0")

    def test_an_entry_predating_the_field_loads_as_unknown(self, tmp_path):
        """An older file simply has no `r_mult` key."""
        path = tmp_path / "journal.json"
        path.write_text(json.dumps([{
            "trade_id": "OLD", "symbol": "S", "direction": "LONG",
            "entry": 100.0, "exit": 110.0, "sl": 95.0, "tp": 0.0, "pnl": 10.0,
        }]))
        j = TradeJournal(journal_file=str(path))
        assert j._entries[0].r_multiple is None


# ── 4. what the operator is told ──────────────────────────────────────────

class TestTheCard:
    def test_a_measured_r_prints_as_before(self):
        assert _r_tag(2.4) == "2.4R"

    def test_an_unknown_r_says_why(self):
        out = _r_tag(None)
        assert "unknown" in out and "stop" in out
        assert "0.0R" not in out and "R)" != out

    def test_the_average_carries_its_coverage(self):
        out = _avg_r_line({"avg_r_multiple": 0.42, "r_scored": 6,
                           "r_unscored": 14, "trades": 20})
        assert "+0.42R" in out
        assert "6 of 20" in out, (
            "a mean over a sixth of the window printed as the window's")

    def test_full_coverage_is_not_decorated(self):
        out = _avg_r_line({"avg_r_multiple": 0.42, "r_scored": 20,
                           "r_unscored": 0, "trades": 20})
        assert "+0.42R" in out and " of " not in out

    def test_no_scoreable_close_is_a_dash_not_a_zero(self):
        out = _avg_r_line({"avg_r_multiple": None, "trades": 20})
        assert "0.00" not in out and "+0.00R" not in out
        assert "no close in the window had a stop on record" in out

    def test_an_older_review_without_the_counts_still_renders(self):
        """A cached review recorded before the split degrades, never raises."""
        assert "+0.42R" in _avg_r_line({"avg_r_multiple": 0.42})

    def test_neither_renderer_formats_a_none(self):
        for bad in (None, "x", object()):
            _r_tag(bad)                       # must not raise
        _avg_r_line({})


def test_the_dataclass_field_is_optional():
    """Pinned because the whole fix is the type: a `float` field cannot hold
    "no R", and every consumer downstream was written against that."""
    import typing
    hints = typing.get_type_hints(JournalEntry)
    assert type(None) in typing.get_args(hints["r_multiple"])
