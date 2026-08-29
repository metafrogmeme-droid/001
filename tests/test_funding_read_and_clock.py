"""An unreadable funding rate impersonated "the market is closed".

`live_executor.execute()` read the venue's funding payload like this:

    funding_rate = float(funding_info.get("fundingRate", 0) or 0)
    if funding_rate != 0:
        ...check whether funding is against us...

An absent field, a JSON null, an empty string and a genuine 0.0 all collapsed
to 0.0, and the check then skipped. That is CLAUDE.md's `.get("pnl", 0)` row,
except the impersonation is sharper here: the comment three lines above says

    # 0% funding on metals/stocks = market likely closed

so zero is not a neutral filler in this domain — it is a specific, meaningful
reading, and an unreadable rate was borrowing it. The surrounding
`except Exception: pass` finished the job: a failed fetch and a calm market
produced the same record, which is why nobody could tell how often the check
ran at all.

AND THE CLOCK WAS A SECOND COPY OF A SHARED CALENDAR
----------------------------------------------------
The settlement guard hand-rolled `settlement_times = [0, 480, 960]` while
`bot/risk/funding_clock.seconds_to_settlement()` computes exactly that — and
the risk engine, `scan_skill` and the analyzer all already call it. Four users,
one of them with its own arithmetic, is how an 8h assumption drifts in exactly
one place. The executor's audit event is even named `funding_clock`, after the
module it was not using.

Nothing here blocks a trade. Funding has always been WARN-only on this path,
and it still is — the change is what the record says, not what it permits.
"""
from __future__ import annotations

import pytest

from bot.risk.funding_clock import (
    SETTLEMENT_INTERVAL_SEC,
    read_funding_rate,
    seconds_to_settlement,
)


class TestAnUnreadableRateIsNotZero:
    def test_a_real_rate_reads_through(self):
        assert read_funding_rate({"fundingRate": 0.0003}) == pytest.approx(0.0003)
        assert read_funding_rate({"fundingRate": "-0.0012"}) == pytest.approx(-0.0012)

    def test_a_measured_zero_is_still_zero(self):
        # The mirror defect: None must not swallow a REAL 0.0, which is the
        # reading that means "market likely closed".
        assert read_funding_rate({"fundingRate": 0.0}) == 0.0
        assert read_funding_rate({"fundingRate": "0"}) == 0.0

    @pytest.mark.parametrize("payload", [
        {},                          # absent field  -> was 0.0 via .get(_, 0)
        {"fundingRate": None},       # JSON null     -> was 0.0 via `or 0`
        {"fundingRate": ""},         # empty string  -> was 0.0 via `or 0`
        {"fundingRate": "n/a"},      # unparseable
        {"fundingRate": []},
        None,                        # no payload at all
        "not a dict",
    ])
    def test_every_unreadable_shape_is_None(self, payload):
        assert read_funding_rate(payload) is None

    def test_nan_is_not_a_reading(self):
        assert read_funding_rate({"fundingRate": float("nan")}) is None

    def test_it_never_raises(self):
        for junk in (object(), 7, [], {"fundingRate": object()}):
            assert read_funding_rate(junk) is None


class TestTheClockIsTheSharedOne:
    def test_the_executor_calls_the_module_not_its_own_calendar(self):
        import inspect

        from bot.core.live_executor import LiveExecutor
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor.execute))
        assert "seconds_to_settlement(" in src, (
            "the executor stopped using the shared funding clock")
        assert "[0, 480, 960]" not in src, (
            "the hand-rolled settlement calendar is back — a second copy of "
            "the 8h assumption the risk engine, scan_skill and the analyzer "
            "all read from bot/risk/funding_clock")

    def test_the_executor_reads_the_rate_through_the_shared_reader(self):
        import inspect

        from bot.core.live_executor import LiveExecutor
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor.execute))
        assert "read_funding_rate(funding_info)" in src
        assert 'get("fundingRate", 0)' not in src, (
            "the collapsing read is back: absent, null and a real 0.0 are "
            "one value again")

    def test_a_fetch_failure_is_recorded_rather_than_swallowed(self):
        import inspect

        from bot.core.live_executor import LiveExecutor
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor.execute))
        assert 'result="FETCH_FAILED"' in src
        assert 'result="UNREADABLE"' in src, (
            "a venue that answered without a usable rate leaves no trace again")

    def test_funding_still_never_blocks_an_entry(self):
        # The whole section is advisory. If a `return` ever appears in it, a
        # venue's funding endpoint gained the power to stop trading.
        import inspect

        from bot.core.live_executor import LiveExecutor
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor.execute))
        # Anchored on CODE, not on the banner comment — code_only() strips
        # comments, so the first draft's `src.index("Funding rate awareness")`
        # raised on a string that was no longer there.
        i = src.index("read_funding_rate(funding_info)")
        block = src[i:src.index("_preflight_check(", i)]
        assert "return" not in block, (
            "funding became able to refuse an entry — it has always been "
            "WARN-only on this path")


class TestTheSharedClockItself:
    def test_it_counts_down_to_the_next_settlement(self):
        # 07:55 UTC -> 5 minutes to the 08:00 settle.
        ts = 7 * 3600 + 55 * 60
        assert seconds_to_settlement(ts) == pytest.approx(300.0)

    def test_exactly_at_settlement_reports_a_full_interval_not_zero(self):
        # The old inline version used `(st - mins) % 1440`, which is 0 at the
        # settle, and excluded it with `0 < mins_until`. The shared clock
        # returns a full interval there, so the same moment is still outside
        # the 5-minute window — the behaviour survives the swap.
        assert seconds_to_settlement(8 * 3600) == pytest.approx(SETTLEMENT_INTERVAL_SEC)

    def test_the_window_the_executor_warns_in(self):
        # 0 < mins <= 5 is the executor's condition; check both edges.
        assert seconds_to_settlement(8 * 3600 - 1) / 60 == pytest.approx(1 / 60)
        assert seconds_to_settlement(8 * 3600 - 300) / 60 == pytest.approx(5.0)
        assert seconds_to_settlement(8 * 3600 - 301) / 60 > 5.0
