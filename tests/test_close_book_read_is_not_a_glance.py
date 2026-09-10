"""The read that BOOKS the close hand-rolled the defect its own module names.

`_verify_position_closed` step 2 is the only route to `confirmed=True`, and the
caller states the stakes in as many words:

    Keeping a position that DID close is recoverable: reconcile_positions()
    runs every tick ... Booking a close that did NOT happen is not
    recoverable -- that is the asymmetry this branch is built on.

Three things were wrong with it, and each has a cure already sitting in the
tree that this one caller did not use.

1. IT HAND-ROLLED THE EXPRESSION `position_presence` EXISTS TO REPLACE.

   `float(p.get("contracts", 0) or 0) > 0` is quoted verbatim in
   `order_state.position_presence`'s docstring as the defect:

       reads a row that states no size as a row with NO POSITION, so an
       unparseable payload and a genuinely flat book arrive at the same
       `False` — and callers act on that `False` by deleting local tracking.

   `contracts: null` therefore read as "no position" and booked the close.
   `position_presence` is imported into `live_executor.py` already.

2. IT WAITED 1.0s ON A BOOK DOCUMENTED AS NEEDING 1.5s.

   `_VENUE_SETTLE_SECONDS`'s own docstring names this exact case: "the close's
   25227 branch reads the same book and needs the same patience before it may
   call an empty answer 'closed'." A second, shorter copy of a threshold is a
   second answer — the same finding the position-read retry hit one method
   over, where the two copies happened to be EQUAL and so slipped a test.

3. IT BELIEVED THE FIRST FLAT ANSWER.

   Five hundred lines below, the 25227 branch reads flat, disbelieves it,
   sleeps the constant, RE-READS, and audits `UNSETTLED_NOT_CLOSED` when the
   position reappears — because a guard flattening straight after entry "can
   get 25227 and an empty book for its OWN unsettled position". Two answers to
   one question, in one file, about one venue.

WHAT THE RE-READ IS GATED ON, AND WHY IT IS NOT SIMPLY ALWAYS.

Step 1 already asked the venue whether the close order filled. A CONFIRMED
fill is an independent reading that agrees with a flat book: a reduceOnly close
cannot fill against a position that is not there — it answers 25227/31008
instead — so filled-and-flat is two sources saying the same thing, and one read
settles it. Re-reading there would put 1.5s on every ordinary close for nothing.

An UNCONFIRMED fill is the dangerous shape and it is exactly the 25227 one: the
close may have been rejected, the flat book is then the SOLE evidence, and a
position opened moments ago has not necessarily reached the book yet.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import _VENUE_SETTLE_SECONDS, LiveExecutor
from bot.core.order_state import position_presence, rows_for_side

SYMBOL = "BTC/USDT"


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path):
    with patch.object(live_executor_mod, "_POSITIONS_FILE",
                      str(tmp_path / "live_positions.json")), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE",
                         str(tmp_path / "closed_trades.json")):
        yield


def _executor(tmp_path=None):
    """The harness `test_audit_fixes_batch_5` already uses on this method."""
    ex = LiveExecutor()
    venue = AsyncMock()
    venue.fetch_ticker = AsyncMock(return_value={"last": 100_000.0})
    venue.fetch_positions = AsyncMock(return_value=[])
    venue.close = AsyncMock()
    ex._exchange = venue
    return ex


def _verify(ex, *, fill_confirmed, books, raises=False):
    """Drive step 1's verdict and hand step 2 a SEQUENCE of books.

    A sequence, because every interesting case here is "it said one thing and
    then said another" — a single return value can only ever prove the
    terminal state, which is how a one-shot read went unnoticed.
    """
    ex._verify_order_fill = AsyncMock(return_value={
        "confirmed": fill_confirmed,
        "fill_price": 97_000.0 if fill_confirmed else 0.0,
        "fill_qty": 0.0002 if fill_confirmed else 0.0,
        "fees": 0.0,
        "status": "closed" if fill_confirmed else "open",
        "failure_stage": "" if fill_confirmed else "post_check_unconfirmed",
        "raw": {},
    })
    if raises:
        ex._exchange.fetch_positions = AsyncMock(side_effect=RuntimeError("429"))
    else:
        ex._exchange.fetch_positions = AsyncMock(side_effect=list(books))
    return ex._exchange


async def _run(ex, exchange, direction="LONG"):
    with patch("asyncio.sleep", new=AsyncMock()):
        return await ex._verify_position_closed(
            exchange, SYMBOL, direction, "CLOSE-1")


OPEN_ROW = {"symbol": SYMBOL, "side": "long", "contracts": 0.0002}
FLAT = []


# ── 1. a row that states no size is not an absence of exposure ───────────

class TestAnUnsizedRowIsNotAFlatBook:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("unsized", [
        {"symbol": SYMBOL, "side": "long", "contracts": None},
        {"symbol": SYMBOL, "side": "long", "contracts": ""},
        {"symbol": SYMBOL, "side": "long"},
        {"symbol": SYMBOL, "side": "long", "contracts": "n/a"},
    ])
    async def test_it_is_not_booked_as_a_close(self, tmp_path, unsized):
        """THE DEFECT. `float(None or 0)` is 0.0, 0.0 is not > 0, the loop
        finished, and the function said the close completed — on the read whose
        answer prunes the local record."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=True, books=[[unsized]])
        r = await _run(ex, exchange)
        assert r["confirmed"] is False, (
            "a row the venue would not size was read as a flat book and the "
            "close was booked")
        assert r["failure_stage"] == "book_unread"

    @pytest.mark.asyncio
    async def test_a_genuinely_empty_book_still_confirms(self, tmp_path):
        """The control. An empty list IS a reading — the venue looked and found
        nothing — and must not be swept up with the unreadable case."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=True, books=[FLAT])
        r = await _run(ex, exchange)
        assert r["confirmed"] is True
        assert r["failure_stage"] == ""

    @pytest.mark.asyncio
    async def test_a_measured_zero_is_a_reading_and_confirms(self, tmp_path):
        """`contracts: 0` is the venue stating there is no exposure. Distinct
        from `contracts: null`, and the distinction is the whole subject."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=True, books=[
            [{"symbol": SYMBOL, "side": "long", "contracts": 0}]])
        r = await _run(ex, exchange)
        assert r["confirmed"] is True

    @pytest.mark.asyncio
    async def test_a_sized_row_still_reports_the_residual(self, tmp_path):
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=True, books=[[OPEN_ROW]])
        r = await _run(ex, exchange)
        assert r["confirmed"] is False
        assert r["failure_stage"] == "position_still_open"
        assert r["remaining_qty"] == pytest.approx(0.0002)


# ── 2. the settle interval has one definition ────────────────────────────

class TestItWaitsTheDocumentedInterval:
    @pytest.mark.asyncio
    async def test_the_first_wait_is_the_shared_constant(self, tmp_path):
        """1.0 was a second, SHORTER copy of an interval this file defines and
        whose docstring names this very branch."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=True, books=[FLAT])
        slept = []

        async def _record(seconds):
            slept.append(seconds)

        with patch("asyncio.sleep", new=_record):
            await ex._verify_position_closed(exchange, SYMBOL, "LONG", "CLOSE-1")
        assert slept == [_VENUE_SETTLE_SECONDS], (
            f"expected one wait of the shared constant, got {slept}")

    def test_the_source_names_the_constant_rather_than_a_literal(self):
        """A value check cannot see a second copy that happens to be equal —
        that is precisely how `delay: float = 1.5` slipped past a test named
        for it in the position-read work. Read the source.
        """
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor._verify_position_closed))
        assert "_VENUE_SETTLE_SECONDS" in src, (
            "the close verification went back to its own timing literal")
        assert "asyncio.sleep(1.0)" not in src


# ── 3. the flat answer is re-read when it is the only evidence ───────────

class TestAFlatBookAfterAnUnconfirmedFillIsRead＿Twice:
    @pytest.mark.asyncio
    async def test_a_book_that_had_not_settled_is_not_booked_as_closed(self, tmp_path):
        """The 25227 scenario, on the path that books the trade.

        The close was rejected or unreadable, the book read flat because the
        position had not settled yet, and one read booked it closed — seconds
        after the SL and TP were cancelled, leaving a live leveraged position
        untracked and unprotected.
        """
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=False, books=[FLAT, [OPEN_ROW]])
        r = await _run(ex, exchange)
        assert r["confirmed"] is False, (
            "the position reappeared after the settle window and the close was "
            "still booked")
        assert r["failure_stage"] == "position_still_open"
        assert r["remaining_qty"] == pytest.approx(0.0002)
        assert exchange.fetch_positions.await_count == 2

    @pytest.mark.asyncio
    async def test_a_book_that_stays_flat_confirms(self, tmp_path):
        """The close really did happen; two flat reads say so."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=False, books=[FLAT, FLAT])
        r = await _run(ex, exchange)
        assert r["confirmed"] is True
        assert r["failure_stage"] == ""
        assert exchange.fetch_positions.await_count == 2

    @pytest.mark.asyncio
    async def test_an_unreadable_second_read_is_not_a_confirmation(self, tmp_path):
        """We went looking BECAUSE the first answer was the only evidence.
        Failing to get a second is not the same as getting a flat one."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=False, books=[
            FLAT, [{"symbol": SYMBOL, "side": "long", "contracts": None}]])
        r = await _run(ex, exchange)
        assert r["confirmed"] is False
        assert r["failure_stage"] == "book_unread"

    @pytest.mark.asyncio
    async def test_a_confirmed_fill_costs_exactly_one_read(self, tmp_path):
        """The ordinary close must not pay for the dangerous one.

        A reduceOnly close cannot fill against a position that is not there —
        it answers 25227/31008 — so a confirmed fill and a flat book are two
        independent readings agreeing, and a second look buys nothing but
        1.5 seconds on every close the bot makes.
        """
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=True, books=[FLAT])
        r = await _run(ex, exchange)
        assert r["confirmed"] is True
        assert exchange.fetch_positions.await_count == 1

    @pytest.mark.asyncio
    async def test_a_still_open_position_is_not_re_read_either(self, tmp_path):
        """`present` is a settled answer. Only `flat` was ever in doubt."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=False, books=[[OPEN_ROW]])
        r = await _run(ex, exchange)
        assert r["confirmed"] is False
        assert exchange.fetch_positions.await_count == 1

    @pytest.mark.asyncio
    async def test_the_unsettled_case_leaves_an_audit_trail(self, tmp_path):
        """The 25227 branch audits UNSETTLED_NOT_CLOSED for this. So does this.

        Without it the only trace of "we nearly booked a close that had not
        happened" is a position that quietly stayed open.

        Asserted on the `audit()` CALL rather than on caplog, because `audit`
        passes `result=` as a logging *extra* — so `caplog.text` carries the
        message and not the verdict, and a first draft of this test looked for
        a word that never reaches the captured text. The call is also where the
        structured fields a later reader greps for actually live.
        """
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=False, books=[FLAT, [OPEN_ROW]])
        calls = []
        with patch.object(live_executor_mod, "audit",
                          side_effect=lambda *a, **k: calls.append((a, k))):
            await _run(ex, exchange)
        results = [k.get("result") for _a, k in calls]
        assert "UNSETTLED_NOT_CLOSED" in results, (
            f"no audit of the near-miss; audited: {results}")
        entry = next(k for _a, k in calls if k.get("result") == "UNSETTLED_NOT_CLOSED")
        assert entry["data"]["symbol"] == SYMBOL
        assert entry["data"]["second_state"] == "present"

    @pytest.mark.asyncio
    async def test_an_ordinary_close_audits_no_near_miss(self, tmp_path):
        """The counterpart. An audit line that fires when nothing is wrong is
        how operators learn to skip the next one."""
        ex = _executor(tmp_path)
        exchange = _verify(ex, fill_confirmed=False, books=[FLAT, FLAT])
        calls = []
        with patch.object(live_executor_mod, "audit",
                          side_effect=lambda *a, **k: calls.append((a, k))):
            r = await _run(ex, exchange)
        assert r["confirmed"] is True
        assert "UNSETTLED_NOT_CLOSED" not in [k.get("result") for _a, k in calls]


# ── the reading is scoped to our side, and unreadable stays unreadable ───

class TestRowsForSideDropsOnlyWhatIsDefinitelySomebodyElses:
    def test_another_symbol_is_dropped(self):
        rows = [{"symbol": "ETH/USDT", "side": "long", "contracts": 5}]
        assert rows_for_side(rows, SYMBOL, "long") == []

    def test_the_other_side_is_dropped(self):
        """A hedge-mode short is not our long still being open. Dropping it is
        what lets a genuinely-closed long confirm."""
        rows = [{"symbol": SYMBOL, "side": "short", "contracts": 5}]
        assert rows_for_side(rows, SYMBOL, "long") == []
        assert position_presence(rows_for_side(rows, SYMBOL, "long"))["state"] == "flat"

    @pytest.mark.parametrize("row", [
        {"symbol": SYMBOL, "contracts": 5},
        {"side": "long", "contracts": 5},
        {"contracts": 5},
    ])
    def test_a_row_that_does_not_say_whose_it_is_is_kept(self, row):
        """The whole point. Dropping it would turn "we could not tell" into
        "our side is flat" — the absent-reads-as-a-measurement move, one field
        over from the one this file is about."""
        assert rows_for_side([row], SYMBOL, "long") == [row]

    def test_a_non_dict_row_is_kept_for_position_presence_to_judge(self):
        assert rows_for_side(["nonsense"], SYMBOL, "long") == ["nonsense"]
        assert position_presence(
            rows_for_side(["nonsense"], SYMBOL, "long"))["state"] == "unreadable"

    def test_side_matching_is_case_insensitive(self):
        rows = [{"symbol": SYMBOL, "side": "LONG", "contracts": 5}]
        assert rows_for_side(rows, SYMBOL, "long") == rows

    @pytest.mark.parametrize("rows", [None, [], "not a list"])
    def test_it_never_raises_on_a_shape_it_did_not_expect(self, rows):
        assert rows_for_side(rows, SYMBOL, "long") == []


class TestTheHandRolledExpressionIsGone:
    def test_the_close_verify_no_longer_counts_contracts_itself(self):
        """A source scan, and it says so: what it pins is that ONE reading is
        in force, which is a property of which function is called rather than
        of any output. The behaviour above is driven separately.
        """
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor._verify_position_closed))
        assert 'p.get("contracts", 0) or 0' not in src, (
            "the expression position_presence's docstring quotes as the defect "
            "is back in the caller that books the close")
        assert "position_presence(" in src, (
            "the close verification stopped asking the shared reading")

    def test_the_25227_branch_and_this_one_use_the_same_reading(self):
        """Two answers to one question was the finding. One now."""
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor))
        # Both sites, plus the entry-side sweep that already used it.
        assert src.count("position_presence(") >= 4, (
            "a caller went back to reading the book its own way")
