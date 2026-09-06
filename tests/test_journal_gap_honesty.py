"""/journal said no trades in a week. /portfolio said 104. Both were "right".

The operator pasted the pair from one screen. Only one of them was talking
about trades:

    /journal    reads engine.journal._entries      (data/trade_journal.json)
    /portfolio  reads executor.closed_positions    (the executor's own record)

`get_weekly_review` finds its store empty and the command renders

    "No trades in the last 7 days."

which is a claim about TRADING derived from the emptiness of a JOURNAL. The
journal is written by `_on_live_position_closed`, so closes from before that
wiring landed -- or from while the bot was down -- simply have no entry. The
trades happened. Nothing recorded them.

The bot holds both stores, so it can check instead of assert. When the
journal is empty and the executor has closes in the same window, that is a
measurable contradiction and the message says so.

    THE EMPTINESS OF ONE RECORD IS NOT EVIDENCE ABOUT ANOTHER.

And deliberately NOT back-filled. A journal entry carries the regime and
strategy that were live at the close; neither can be recovered afterwards.
Reconstructing them would put invented context under a heading that reads as
recorded fact -- the honesty doctrine's "no back-filled histories", which is
a stronger rule than "make the numbers agree".
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from bot.compat import UTC
from bot.skills.engine_ops_commands import _each_executor, _journal_gap_closes

_NOW = datetime.now(UTC)


def _pos(days_ago: float):
    return NS(closed_at=_NOW - timedelta(days=days_ago))


def _engine(*positions, users=None):
    return NS(live_executor=NS(closed_positions=list(positions)),
              _user_executors=users or {})


class TestItCountsWhatTheExecutorSaw:
    def test_a_close_inside_the_window_counts(self):
        assert _journal_gap_closes(_engine(_pos(1)), days=7) == 1

    def test_a_close_outside_the_window_does_not(self):
        assert _journal_gap_closes(_engine(_pos(30)), days=7) == 0

    def test_the_boundary_is_the_window_not_a_guess(self):
        e = _engine(_pos(6.9), _pos(7.1))
        assert _journal_gap_closes(e, days=7) == 1

    def test_a_position_with_no_close_time_is_skipped(self):
        # Not counted as inside the window, and not a crash either.
        assert _journal_gap_closes(_engine(NS(closed_at=None), _pos(1))) == 1

    def test_a_naive_datetime_is_treated_as_utc(self):
        # The executor's persisted records are not uniformly tz-aware, and a
        # TypeError here would take down /journal to improve a message.
        e = NS(live_executor=NS(closed_positions=[NS(closed_at=datetime.now())]),
               _user_executors={})
        assert _journal_gap_closes(e, days=7) == 1


class TestPerUserExecutorsAreIncludedOnce:
    def test_a_user_executor_contributes(self):
        e = NS(live_executor=NS(closed_positions=[]),
               _user_executors={"u1": NS(closed_positions=[_pos(1)])})
        assert _journal_gap_closes(e, days=7) == 1

    def test_a_shared_executor_is_not_double_counted(self):
        # PER_USER_LIVE_ENABLED off resolves every user to the SAME operator
        # executor. Counting it twice would invent closes that never happened
        # -- fixing an undercount with an overcount.
        shared = NS(closed_positions=[_pos(1), _pos(2)])
        e = NS(live_executor=shared, _user_executors={"a": shared, "b": shared})
        assert _journal_gap_closes(e, days=7) == 2

    def test_dedup_is_by_identity_not_equality(self):
        # Two distinct executors holding equal-looking positions are two
        # executors, and both count.
        a = NS(closed_positions=[_pos(1)])
        b = NS(closed_positions=[_pos(1)])
        e = NS(live_executor=a, _user_executors={"b": b})
        assert _journal_gap_closes(e, days=7) == 2

    def test_each_executor_yields_nothing_for_an_empty_engine(self):
        assert list(_each_executor(NS())) == []


class TestItNeverBreaksTheCommand:
    def test_junk_input_returns_zero(self):
        for bad in (None, NS(), 7, "engine"):
            assert _journal_gap_closes(bad, days=7) == 0   # type: ignore[arg-type]

    def test_a_raising_attribute_is_absorbed(self):
        class Hostile:
            @property
            def closed_positions(self):
                raise RuntimeError("nope")
        assert _journal_gap_closes(NS(live_executor=Hostile(),
                                      _user_executors={})) == 0

    def test_a_nonsense_window_is_clamped_not_crashed(self):
        assert _journal_gap_closes(_engine(_pos(1)), days=0) >= 0


class TestTheMessageDistinguishesTheTwoCases:
    def _src(self):
        # /journal lives in the engine-ops mixin since the handler split;
        # read every file the handler class is made of, so the next move
        # cannot turn these scans into a search of the wrong file.
        from tests.source_scan import code_only, handler_sources
        return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())

    def test_a_genuine_quiet_week_still_says_so(self):
        # The fix must not cost the command its normal answer.
        assert "No trades in the last 7 days." in self._src()

    def test_a_recording_gap_is_named_as_one(self):
        src = self._src()
        assert "recording gap, not a quiet week" in src

    def test_the_gap_message_reports_the_count(self):
        src = self._src()
        assert "_journal_gap_closes(self.engine, days=7)" in src
        assert "position(s) closed in that " in src

    def test_it_points_at_the_surfaces_that_do_have_the_history(self):
        # Telling someone their record is incomplete without saying where the
        # complete one lives is half an answer.
        src = self._src()
        i = src.index("recording gap, not a quiet week")
        window = src[i:i + 1200]
        assert "/portfolio" in window and "/performance" in window

    def test_it_states_that_it_does_not_back_fill(self):
        src = self._src()
        i = src.index("recording gap, not a quiet week")
        window = src[i:i + 1200]
        assert "Not back-filled on purpose" in window, (
            "reconstructing entries would put invented regime and strategy "
            "under a heading that reads as recorded fact"
        )
