"""Two gates that ran before every live entry and could not be tested.

QC-2 safeguards 0a (stale ticker) and 0b (wide spread) were inline in
`LiveExecutor.execute()`, a 1,717-line method. Nothing could plant a ticker and
read back what the gate decided, and both carried the same defect wearing
different clothes:

    0a   `_tk_ts = ticker.get("timestamp")`  then  `if _max_age > 0 and _tk_ts:`
         A ticker with NO timestamp skipped the staleness gate entirely. An
         unparseable one hit `except (TypeError, ValueError): _age = 0.0` —
         and 0.0 is the FRESHEST possible age, invented for exactly the case
         where the guard had the least information. `_age > _max_age` was then
         False and the entry proceeded.

    0b   `except (TypeError, ValueError): _bid = _ask = 0.0` then
         `if _ask > _bid > 0:` — an unparseable quote collapsed into the same
         branch as a venue that legitimately does not quote bid/ask, and both
         walked past the spread check.

Neither is hypothetical. 0a exists because sizing and gating a trade against a
price that may no longer exist is how an order gets placed into a market that
has already moved; the gate went quiet in precisely the conditions that produce
a bad tick.

WHAT THE EXTRACTION BOUGHT
--------------------------
`ticker_age_verdict` and `spread_verdict` are pure — no clock, no I/O, every
threshold an argument — matching `book_wall_verdict` beside them. The executor
keeps the parts only it can do: the one refetch, the audit, the return.

BEHAVIOUR IS UNCHANGED BY DEFAULT, deliberately. An unreadable reading is now
its own state, but `ENTRY_UNREADABLE_MARKET_GATE` defaults to `warn`: it audits
and proceeds, exactly as before. Making a live entry path newly refuse trades
is not something to ship on the same commit that first makes it visible — the
observe-first rule the book-wall gate already follows.
"""
from __future__ import annotations

import pytest

from bot.core.entry_quality import spread_verdict, ticker_age_verdict

NOW = 1_000_000.0


def _tick(ms_ago=None, **extra):
    t = dict(extra)
    if ms_ago is not None:
        t["timestamp"] = (NOW - ms_ago) * 1000.0
    return t


class TestStalenessIsMeasuredOrDeclared:
    def test_a_fresh_tick_is_fresh(self):
        v = ticker_age_verdict(_tick(5), 120, NOW)
        assert v["state"] == "fresh"
        assert v["age_sec"] == pytest.approx(5.0)

    def test_an_old_tick_is_stale(self):
        v = ticker_age_verdict(_tick(300), 120, NOW)
        assert v["state"] == "stale"
        assert v["age_sec"] == pytest.approx(300.0)

    def test_no_timestamp_is_UNREADABLE_not_a_silent_pass(self):
        # The original skipped the whole gate here: `if _max_age > 0 and _tk_ts`.
        v = ticker_age_verdict({"last": 100.0}, 120, NOW)
        assert v["state"] == "unreadable"
        assert v["age_sec"] is None, "an unknown age was given a number"

    @pytest.mark.parametrize("bad", ["abc", None, [], {}, object()])
    def test_an_unparseable_timestamp_is_never_age_zero(self, bad):
        # The sharpest form of the defect: 0.0 is the freshest reading there
        # is, and it was the fallback for the least-informed case.
        v = ticker_age_verdict({"timestamp": bad}, 120, NOW)
        assert v["state"] == "unreadable"
        assert v["age_sec"] is None

    def test_a_future_timestamp_is_unreadable_not_fresh(self):
        # Negative age sails through any ceiling. A clock that disagrees with
        # ours is not evidence of a fresh tick.
        v = ticker_age_verdict(_tick(-500), 120, NOW)
        assert v["state"] == "unreadable"
        assert "future" in v["reason"]

    def test_a_disabled_ceiling_says_disabled_rather_than_fresh(self):
        for ceiling in (0, -1, None, "x"):
            v = ticker_age_verdict(_tick(9999), ceiling, NOW)
            assert v["state"] == "disabled", ceiling
            assert v["age_sec"] is None, (
                "a disabled gate reported an age as though it had checked")

    def test_the_boundary_belongs_to_fresh(self):
        assert ticker_age_verdict(_tick(120), 120, NOW)["state"] == "fresh"
        assert ticker_age_verdict(_tick(121), 120, NOW)["state"] == "stale"


class TestSpreadSeparatesUnquotedFromUnreadable:
    def test_a_tight_book_is_ok(self):
        v = spread_verdict({"bid": 100.0, "ask": 100.05}, 1.0)
        assert v["state"] == "ok"
        assert v["spread_pct"] == pytest.approx(0.05, abs=1e-3)

    def test_a_wide_book_is_too_wide(self):
        v = spread_verdict({"bid": 100.0, "ask": 105.0}, 1.0)
        assert v["state"] == "too_wide"

    def test_a_venue_that_quotes_nothing_is_not_quoted(self):
        # A real and common fact about some venues, and NOT a bad read. The
        # gate's own comment says "when the venue reports bid/ask".
        v = spread_verdict({"last": 100.0}, 1.0)
        assert v["state"] == "not_quoted"

    @pytest.mark.parametrize("bid,ask", [
        ("x", 1.0), (1.0, "y"), (None, 5.0), (5.0, None),
        (0.0, 5.0), (-1.0, 5.0), (5.0, 4.0),
    ])
    def test_a_present_but_broken_quote_is_unreadable(self, bid, ask):
        # All of these collapsed to `_bid = _ask = 0.0` and then failed
        # `_ask > _bid > 0`, taking the same exit as "not quoted".
        v = spread_verdict({"bid": bid, "ask": ask}, 1.0)
        assert v["state"] == "unreadable", (bid, ask)
        assert v["spread_pct"] is None

    def test_a_disabled_ceiling_says_disabled(self):
        v = spread_verdict({"bid": 1.0, "ask": 99.0}, 0)
        assert v["state"] == "disabled"
        assert v["spread_pct"] is None


class TestNeitherEverRaises:
    """These run before every live entry. A verdict that throws is an entry
    path that dies on a malformed ticker."""

    @pytest.mark.parametrize("junk", [None, "ticker", 7, [], object(), {"bid": {}}])
    def test_junk_input_is_a_verdict_not_an_exception(self, junk):
        assert ticker_age_verdict(junk, 120, NOW)["state"] in (
            "unreadable", "disabled")
        assert spread_verdict(junk, 1.0)["state"] in (
            "unreadable", "not_quoted", "disabled")


class TestTheExecutorConsultsThemAndKeepsTodaysBehaviour:
    def _src(self):
        import inspect

        from bot.core.live_executor import LiveExecutor
        from tests.source_scan import code_only
        return code_only(inspect.getsource(LiveExecutor.execute))

    def test_both_verdicts_are_actually_called(self):
        src = self._src()
        assert "ticker_age_verdict(ticker, _max_age" in src
        assert "spread_verdict(ticker, _max_spread)" in src

    def test_the_fabricated_zero_age_fallback_is_gone(self):
        src = self._src()
        assert "_age = 0.0" not in src, (
            "an unparseable timestamp is being called a brand-new tick again")

    def test_an_unreadable_reading_defaults_to_warn_not_block(self):
        src = self._src()
        assert 'os.environ.get(\n' in src or 'ENTRY_UNREADABLE_MARKET_GATE' in src
        assert '"ENTRY_UNREADABLE_MARKET_GATE", "warn"' in src, (
            "a live entry path must not start refusing trades on the same "
            "commit that first makes the blind spot visible")

    def test_a_refetch_that_comes_back_unreadable_does_not_clear_staleness(self):
        # The subtle one. We measured stale, refetched, and got a ticker with
        # no readable timestamp. Absent is not fresher than what we already
        # measured, so the block stands.
        #
        # Anchored on the unique marker, not on a window after "fetch_ticker" —
        # execute() fetches the ticker earlier too, so that window found the
        # FIRST verdict call and the assertion was reading the wrong branch.
        src = self._src()
        i = src.index("still stale after a refetch")
        block = src[max(0, i - 300):i + 200]
        assert '"state": "stale"' in block, (
            "an unreadable refetch was allowed to clear a measured staleness")
        assert '_age_v["state"] == "unreadable"' in block

    def test_the_stale_block_still_returns_and_audits(self):
        src = self._src()
        assert 'result="BLOCKED_STALE_TICKER"' in src
        assert 'result="BLOCKED_WIDE_SPREAD"' in src
