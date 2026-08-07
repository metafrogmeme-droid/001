"""`Realized PnL (all-time): 🟢 $+0.00` when there are no closed trades.

Handoff open item #3. The card asserted a measured break-even where the truth
was "no records", and on 2026-08-05 that reading — beside a real equity and a
live open position — turned a UI artifact into a day-long data-loss
investigation. Persistence was working the whole time.

The claim is made TWICE on one line, which is why fixing the number alone
would have left it half-cured:

    $+0.00   a signed figure, to the cent
    🟢       an accent that says "not down" on its own

Both now come from one tri-state. `pnl_stats` returns None when nothing could
be priced — including the empty set, where `sum(())` is `0.0` and every
caller had been printing it.

Four separate absences reach the same card and only one of them is "flat":

    no closed trades at all           -> "No closed trades recorded."
    closes exist, none carry a P&L    -> em-dash + the coverage note
    closes exist, SOME carry a P&L    -> the partial total, window named
    the store could not be READ       -> "could not be read", never "none"

The fourth is the one that had no representation at all: an unreadable
`closed_trades.json` left the list at `[]`, indistinguishable from an empty
book, and the card reported the reassuring version of a failed read.

Scenarios plant a state and assert what the card SAYS, with a red herring in
each: a healthy equity and an open position, so the surrounding card looks
like a funded account that has simply traded to break-even. That is exactly
what the operator saw.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from bot.formatters.rich_cards import render_live_portfolio_summary
from bot.skills.live_stats import live_win_stats
from bot.utils.win_rate import pnl_stats

# The red herring, held constant across every scenario: a real equity and a
# live position. Nothing about the rest of the card suggests missing data.
FUNDED = dict(equity=545.29, open_count=1, exposure=51.18)


def _card(**over):
    base = dict(**FUNDED, realized_pnl=None, total_closed=0, win_rate=None)
    base.update(over)
    return "\n".join(render_live_portfolio_summary(**base))


def _t(trade_id="TI-1", pnl=1.0, reason="tp"):
    return NS(trade_id=trade_id, pnl_usd=pnl, close_reason=reason)


def _lp(**over):
    """A real closed LivePosition — the round-trip tests need the dataclass,
    not a stand-in, because the defect lived in its serialization."""
    from bot.core.live_executor import LivePosition
    base = dict(trade_id="TI-1", symbol="UNI/USDT", direction="LONG",
                entry_price=7.0, quantity=1.0, cost_usd=7.0,
                stop_loss=6.5, take_profit=7.9, status="closed", pnl_usd=1.0)
    base.update(over)
    return LivePosition(**base)


# ── (id, card kwargs, must_say, must_not_say) ────────────────────────────
SCENARIOS = [
    pytest.param(
        dict(realized_pnl=None, total_closed=0, win_rate=None),
        ["No closed trades recorded", "—"],
        ["$+0.00", "$0.00", "🟢", "Win rate"],
        id="empty-book-is-not-a-flat-book",
    ),
    pytest.param(
        # The legacy shape: a caller that still passes 0.0 for an empty book.
        # The renderer must refuse it anyway — `total_closed == 0` means no
        # measurement was possible, whatever arrived in the argument.
        dict(realized_pnl=0.0, total_closed=0, win_rate=0.0),
        ["No closed trades recorded", "—"],
        ["$+0.00", "🟢"],
        id="a-legacy-caller-still-cannot-print-a-fake-zero",
    ),
    pytest.param(
        dict(realized_pnl=None, total_closed=4, win_rate=None, unscored=4),
        ["—", "0 of 4 closes", "4 carry no recorded"],
        ["$+0.00", "🟢", "Win rate: <code>0%</code>"],
        id="four-closes-none-priced-is-not-a-wipeout",
    ),
    pytest.param(
        dict(realized_pnl=3.0, total_closed=3, win_rate=50.0, unscored=1),
        ["$+3.00", "2 of 3 closes", "1 carry no recorded"],
        ["No closed trades recorded"],
        id="a-partial-total-names-its-window",
    ),
    pytest.param(
        dict(realized_pnl=None, total_closed=0, win_rate=None,
             read_failed=True),
        ["could not be read", "incomplete, not zero", "—"],
        ["No closed trades recorded", "$+0.00", "🟢"],
        id="an-unreadable-store-is-not-an-empty-one",
    ),
    pytest.param(
        # A genuinely measured break-even: real, and it must still render.
        # Suppressing this would be the opposite defect — hiding a fact
        # because it looks like the absence of one.
        dict(realized_pnl=0.0, total_closed=6, win_rate=33.0),
        ["$+0.00", "🟢", "Win rate: <code>33%</code>"],
        ["No closed trades recorded", "could not be read"],
        id="a-measured-breakeven-is-a-fact-and-still-prints",
    ),
]


@pytest.mark.parametrize("kwargs,must_say,must_not_say", SCENARIOS)
def test_the_card_says_which_absence_it_is(kwargs, must_say, must_not_say):
    out = _card(**kwargs)
    for phrase in must_say:
        assert phrase in out, f"MUST_SAY {phrase!r} missing from:\n{out}"
    for phrase in must_not_say:
        assert phrase not in out, f"MUST_NOT_SAY {phrase!r} present in:\n{out}"


class TestColourIsAClaimOfItsOwn:
    """A green accent says "in profit" as loudly as the number does."""

    def test_unknown_gets_the_muted_accent_not_the_green_one(self):
        out = _card(realized_pnl=None, total_closed=3, win_rate=None)
        assert "⚪" in out
        assert "🟢" not in out and "🔴" not in out

    def test_a_real_loss_still_gets_red(self):
        assert "🔴" in _card(realized_pnl=-3.25, total_closed=104,
                             win_rate=59.0)

    def test_a_real_gain_still_gets_green(self):
        assert "🟢" in _card(realized_pnl=12.40, total_closed=104,
                             win_rate=59.0)


class TestTheTriStateAtTheSource:
    """`0.0` and `None` are different answers and must stay different."""

    def test_the_empty_set_totals_to_none_not_zero(self):
        assert pnl_stats([])["total"] is None

    def test_a_set_nothing_can_price_totals_to_none(self):
        s = pnl_stats([_t(pnl=None), _t(pnl=None)])
        assert s["total"] is None and s["unscored"] == 2 and s["scored"] == 0

    def test_a_recorded_zero_is_a_measurement(self):
        # 0.0 is falsy and real. `is None`, not falsiness.
        s = pnl_stats([_t(pnl=0.0)])
        assert s["total"] == 0.0 and s["scored"] == 1 and s["unscored"] == 0

    def test_a_partial_set_totals_only_what_it_read(self):
        s = pnl_stats([_t(pnl=5.0), _t(pnl=None), _t(pnl=-2.0)])
        assert s["total"] == 3.0 and s["scored"] == 2 and s["unscored"] == 1

    def test_nan_and_inf_are_unreadable_not_zero(self):
        s = pnl_stats([_t(pnl=float("nan")), _t(pnl=float("inf"))])
        assert s["total"] is None and s["unscored"] == 2

    def test_the_rate_and_the_total_agree_about_which_rows_were_legible(self):
        # They run over one reader precisely so a card cannot print a total
        # over 12 rows beside a rate over 9.
        trades = [_t(trade_id=f"TI-{i}", pnl=(None if i % 3 else float(i)))
                  for i in range(12)]
        assert pnl_stats(trades)["scored"] == live_win_stats(trades)["scored"]
        assert pnl_stats(trades)["unscored"] == live_win_stats(trades)["unscored"]


class TestTheRoundTripDoesNotInventAMeasurement:
    """Save writes `null`; load read it back as `0.0`.

    `_save_closed_trades` serializes `pos.pnl_usd` verbatim and therefore
    already honoured the tri-state. `_load_closed_trades` collapsed it with
    `float(item.get("pnl_usd") or 0)`, so one restart converted "could not be
    priced" into "broke even" — silently defeating the unscored count that
    `bot/utils/win_rate.py` exists to preserve.
    """

    def _executor(self, tmp_path):
        from bot.core.live_executor import LiveExecutor
        return LiveExecutor(state_dir=str(tmp_path))

    def test_an_unpriced_close_survives_a_restart_as_unpriced(self, tmp_path):
        ex = self._executor(tmp_path)
        ex._closed_trades = [_lp(trade_id="TI-unpriced", pnl_usd=None)]
        ex._save_closed_trades()

        # The store itself must carry the absence, not a zero.
        raw = json.loads(open(ex._closed_trades_file, encoding="utf-8").read())
        assert raw[0]["pnl_usd"] is None

        again = self._executor(tmp_path)
        assert len(again._closed_trades) == 1
        assert again._closed_trades[0].pnl_usd is None, (
            "the restart booked an unpriced close as a measured break-even"
        )
        assert pnl_stats(again._closed_trades)["unscored"] == 1

    def test_a_recorded_zero_survives_a_restart_as_zero(self, tmp_path):
        ex = self._executor(tmp_path)
        ex._closed_trades = [_lp(trade_id="TI-flat", pnl_usd=0.0)]
        ex._save_closed_trades()

        again = self._executor(tmp_path)
        assert again._closed_trades[0].pnl_usd == 0.0
        assert pnl_stats(again._closed_trades)["scored"] == 1


class TestAFailedReadIsNotAnEmptyBook:
    def _executor(self, tmp_path):
        from bot.core.live_executor import LiveExecutor
        return LiveExecutor(state_dir=str(tmp_path))

    def test_an_absent_file_is_a_clean_empty_book(self, tmp_path):
        ex = self._executor(tmp_path)
        assert ex.closed_trades_read_failed is False
        assert ex._closed_trades == []

    def test_a_corrupt_file_is_flagged_rather_than_reported_empty(self, tmp_path):
        ex = self._executor(tmp_path)
        path = ex._closed_trades_file
        import pathlib
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json at all", encoding="utf-8")

        again = self._executor(tmp_path)
        assert again.closed_trades_read_failed is True, (
            "an unreadable store rendered as 'No closed trades recorded'"
        )

    def test_a_partial_parse_is_flagged_too(self, tmp_path):
        """Half the rows load, then a row raises. The list is short, and
        every total over it would otherwise print as a whole book."""
        ex = self._executor(tmp_path)
        import pathlib
        p = pathlib.Path(ex._closed_trades_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        good = {"trade_id": "TI-1", "symbol": "UNI/USDT", "direction": "LONG",
                "entry_price": 7.0, "quantity": 1.0, "cost_usd": 7.0,
                "pnl_usd": 1.5, "opened_at": None, "closed_at": None}
        bad = dict(good, trade_id="TI-2", opened_at="not-a-timestamp")
        p.write_text(json.dumps([good, bad]), encoding="utf-8")

        again = self._executor(tmp_path)
        assert again.closed_trades_read_failed is True
        assert len(again._closed_trades) < 2

    def test_the_flag_reaches_the_card(self, tmp_path):
        out = _card(read_failed=True)
        assert "could not be read" in out
        assert "No closed trades recorded" not in out


class TestTheOtherSurfacesMakingTheSameClaim:
    """Ask which OTHER surface makes the same claim before calling it done.

    The grep tells you where you looked; the test tells you where you did
    not. These run the surfaces rather than matching their source, because
    the same value flowed through several renderings and curing two of them
    is the documented way this defect survives its own fix.
    """

    def test_the_executor_status_line_does_not_price_an_empty_book(self, tmp_path):
        from bot.core.live_executor import LiveExecutor
        ex = LiveExecutor(state_dir=str(tmp_path))
        out = ex.status_summary()
        # `Exposure: $0.00` on the same line IS a measurement — no open
        # positions is a fact — so only the PnL cell is asserted.
        assert out.rsplit("Realized PnL: ", 1)[-1] == "—"

    def test_the_executor_status_line_still_prices_a_real_book(self, tmp_path):
        from bot.core.live_executor import LiveExecutor
        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._closed_trades = [_lp(pnl_usd=2.5)]
        assert "Realized PnL: $2.5000" in ex.status_summary()

    def test_the_start_card_says_na_when_nothing_could_be_priced(self):
        # /start formats `win_rate` with `:.0f`; None there is both a crash
        # and, if defaulted, a claim of total defeat.
        s = live_win_stats([_t(trade_id="TI-a", pnl=None)])
        assert s["total"] == 1 and s["win_rate"] is None

    def test_no_live_surface_still_sums_pnl_with_or_zero(self):
        """The re-run of the search, as an assertion.

        Three separate times a source grep missed sites that used a different
        attribute name. This pins the two modules that own the canonical
        total; a new `sum(... or 0)` in either is the defect coming back.
        """
        from tests.source_scan import code_only
        for path in ("bot/skills/live_stats.py",
                     "bot/formatters/rich_cards.py"):
            src = code_only(open(path, encoding="utf-8").read())
            for shape in ("pnl_usd or 0", "pnl or 0", "net_pnl or 0"):
                assert shape not in src, f"{shape!r} is back in {path}"
