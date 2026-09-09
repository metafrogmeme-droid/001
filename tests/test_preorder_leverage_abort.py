"""A confirmed pre-order overshoot must not become a fill the guard flattens.

TWO LIVE CARDS, ONE HOUR APART, 2026-09-09:

    ⚠️ EXECUTION ABORTED — TRX/USDT
    The venue filled at 20x against a 5x target (sticky per-symbol setting),
    which is 4.0× the approved leverage. The position was CLOSED ...
    Entry: $0.3403 → Exit: $0.3403
    PnL: -$0.1008 (-0.06% margin / -0.00% notional, 20×) | Fees: $0.10

The price did not move. The whole loss was the fee. CLUSDT cost $0.81 the
same way.

WHAT ACTUALLY HAPPENED, and it is not the "unverifiable leverage" case the
fail-open default was written for:

    fetch_leverage      -> 20   (parses, so `_lev_verified = True`)
    20 != 5             -> retry set_leverage long + short
    re-verify           -> 20   (Bitget's sticky per-symbol leverage)
    LEVERAGE_FAIL_OPEN  -> "1" by default -> proceeding with warning
    order fills at 20x
    post-fill guard     -> 20/5 = 4.0 > 1.5 -> FLATTEN
    two fees

`_lev_verified` was set when the read-back merely PARSED, never when it
MATCHED — so a confirmed mismatch counted as verified, and the block whose
comment promises "only THAT still fails closed" was skipped entirely. The
engine read 20x, tried to fix it, confirmed it was STILL 20x, and placed the
order anyway.

The fix is not to close the fail-open default. That was an operator directive
(2026-07-21, "I can't open trades") about leverage the venue would not
CONFIRM — ETHFI returned a payload the parser could not read while
`set_leverage` had succeeded — and that branch must keep proceeding. An
unreadable value never reaches `preorder_leverage_verdict`.

What changed is narrower: a CONFIRMED reading at or beyond the same ratio the
post-fill guard flattens on aborts before capital moves. One threshold, both
gates, so they cannot disagree about what an overshoot is. Proceeding there
does not open a trade — it opens one that is closed seconds later, and
charges two fees for the privilege.
"""

import pytest

from bot.core.live_executor import (leverage_overshoot_verdict,
                                    preorder_leverage_verdict)

RATIO = 1.5


class TestTheLiveIncident:
    def test_trx_would_not_have_been_placed(self):
        v = preorder_leverage_verdict(5, 20, RATIO)
        assert v["decision"] == "abort"
        assert v["ratio"] == 4.0

    def test_the_reason_says_why_not_placing_is_the_point(self):
        why = preorder_leverage_verdict(5, 20, RATIO)["why"]
        assert "20x" in why and "5x" in why
        assert "overshoot guard would flatten" in why

    def test_clusdt_too(self):
        assert preorder_leverage_verdict(5, 21, RATIO)["decision"] == "abort"


class TestItAgreesWithThePostFillGuard:
    """One threshold, both gates. Disagreement is the whole defect."""

    @pytest.mark.parametrize("target,observed", [
        (5, 20), (5, 21), (10, 20), (3, 20), (5, 8), (2, 10), (5, 100),
        (5, 5), (5, 4), (10, 10), (20, 5), (5, 7), (10, 14), (4, 6),
    ])
    def test_abort_exactly_when_the_guard_would_close(self, target, observed):
        pre = preorder_leverage_verdict(target, observed, RATIO)
        post = leverage_overshoot_verdict(target, observed, RATIO)
        assert (pre["decision"] == "abort") == (post["decision"] == "close"), (
            f"{target}x target, venue {observed}x: pre-order says "
            f"{pre['decision']} and the post-fill guard says {post['decision']} "
            "— the engine would open what it is about to close")

    def test_the_ratios_match_where_both_compute_one(self):
        for target, observed in ((5, 20), (5, 4), (10, 25)):
            pre = preorder_leverage_verdict(target, observed, RATIO)
            post = leverage_overshoot_verdict(target, observed, RATIO)
            assert pre["ratio"] == post["ratio"]


class TestUnderLeverageIsNotAnAbort:
    """The old check was `!= target`, which blocks LESS risk than approved."""

    @pytest.mark.parametrize("observed", [1, 2, 3, 4, 5])
    def test_at_or_under_target_proceeds(self, observed):
        v = preorder_leverage_verdict(5, observed, RATIO)
        assert v["decision"] == "proceed"

    def test_the_old_equality_would_have_blocked_it(self):
        """Stated so the loosening is deliberate and not an accident."""
        assert 4 != 5
        assert preorder_leverage_verdict(5, 4, RATIO)["decision"] == "proceed"


class TestUnreadableIsTheOtherBranchsQuestion:
    """Fail-open governs UNCONFIRMED leverage. This function never sees it."""

    @pytest.mark.parametrize("observed", [None, "", "abc", float("nan"),
                                          float("inf"), object()])
    def test_an_unreadable_reading_proceeds(self, observed):
        v = preorder_leverage_verdict(5, observed, RATIO)
        assert v["decision"] == "proceed"
        assert v["ratio"] is None

    @pytest.mark.parametrize("target", [None, 0, -5, "x"])
    def test_an_unusable_target_proceeds(self, target):
        assert preorder_leverage_verdict(
            target, 20, RATIO)["decision"] == "proceed"

    def test_zero_observed_is_not_an_overshoot(self):
        v = preorder_leverage_verdict(5, 0, RATIO)
        assert v["decision"] == "proceed"

    def test_the_function_is_total(self):
        """It runs on the live order path; raising here kills an execution."""
        for t in (None, 0, -1, 5, "5", 5.0, float("inf"), float("nan")):
            for o in (None, 0, -1, 20, "20", 20.0, float("inf"), object()):
                out = preorder_leverage_verdict(t, o, RATIO)
                assert out["decision"] in ("abort", "proceed")


class TestTheThresholdIsRead:
    def test_a_looser_limit_lets_the_same_reading_through(self):
        assert preorder_leverage_verdict(5, 20, 1.5)["decision"] == "abort"
        assert preorder_leverage_verdict(5, 20, 5.0)["decision"] == "proceed"

    def test_the_boundary_is_not_an_abort(self):
        """`> max_ratio`, same comparison as the post-fill guard."""
        assert preorder_leverage_verdict(
            10, 15, 1.5)["decision"] == "proceed"     # exactly 1.5x
        assert preorder_leverage_verdict(
            10, 16, 1.5)["decision"] == "abort"


# ── the wiring, DRIVEN ────────────────────────────────────────────────────
#
# A source scan of these two sites was written first and thrown away. An hour
# earlier in this same session a line-based scan of a multi-line dispatch
# survived the mutation it existed to catch, with 33 tests green — so the
# claim "the order is not placed" is exercised, not grepped.

def _drive_ensure_leverage(readings, positions=None, target=5,
                           set_raises=False, fail_open=True, monkeypatch=None):
    """Run `_ensure_leverage` against a stub venue and return what happened.

    `readings` is the sequence `fetch_leverage` answers (each is fed through
    the real `_parse_leverage_readback`); `positions` is what `fetch_positions`
    answers. Returns (aborted, error_text).
    """
    import asyncio

    from bot.core import live_executor as LE

    monkeypatch.setenv("LEVERAGE_FAIL_OPEN", "1" if fail_open else "0")

    calls = {"set_leverage": 0}
    seq = list(readings)

    class _Exchange:
        async def set_margin_mode(self, *a, **k):
            return None

        async def set_leverage(self, *a, **k):
            calls["set_leverage"] += 1
            if set_raises:
                raise RuntimeError("venue refused set_leverage")
            return {}

        async def fetch_leverage(self, *a, **k):
            if not seq:
                raise RuntimeError("no more readings")
            nxt = seq.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        async def fetch_positions(self, *a, **k):
            if positions is None:
                raise RuntimeError("fetch_positions unavailable")
            return positions

    class _Venue:
        id = "bitget"

        @staticmethod
        def futures_params():
            return {}

    ex = LE.LiveExecutor.__new__(LE.LiveExecutor)
    ex._venue = _Venue()
    ex._lev_unverified_warned = set()
    ex._hedge_mode = False

    async def _get_exchange():
        return ex_obj

    ex_obj = _Exchange()
    ex._get_exchange = _get_exchange
    ex._compute_target_leverage = lambda symbol: target

    async def _detect_hold_mode():
        return None

    ex._detect_hold_mode = _detect_hold_mode

    try:
        asyncio.run(ex._ensure_leverage("TRX/USDT"))
    except RuntimeError as exc:
        return True, str(exc)
    except Exception:
        # Anything else means the stub is short a seam, not that the guard
        # decided something — surface it rather than reading it as "proceed".
        raise
    return False, ""


def _lev(x):
    """A fetch_leverage payload the real parser reads as `x`."""
    return {"leverage": x, "info": {"leverage": str(x)}}


#: The ETHFI shape: the call SUCCEEDS and the payload parses to None. An
#: unreadable read-back is not an exception, which is what the first draft of
#: these tests modelled — and raising RuntimeError from the stub tripped
#: `_ensure_leverage`'s own `except RuntimeError: raise`, so the test failed
#: for a reason that had nothing to do with the guard.
_UNREADABLE = {"info": {}}


class TestTheOrderPathRefusesAConfirmedOvershoot:
    def test_the_trx_sequence_aborts(self, monkeypatch):
        """20x read, re-set, 20x again — exactly the live card."""
        aborted, why = _drive_ensure_leverage(
            [_lev(20), _lev(20)], target=5, monkeypatch=monkeypatch)
        assert aborted, "the order would have been placed at 20x"
        assert "20" in why and "5" in why

    def test_it_aborts_even_though_fail_open_is_on(self, monkeypatch):
        """The default that let TRX through. A CONFIRMED overshoot ignores it."""
        aborted, _ = _drive_ensure_leverage(
            [_lev(20), _lev(20)], target=5, fail_open=True,
            monkeypatch=monkeypatch)
        assert aborted

    def test_a_venue_that_accepts_the_retry_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(20), _lev(5)], target=5, monkeypatch=monkeypatch)
        assert not aborted, "the retry fixed it; the order should go"

    def test_a_matching_first_read_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(5)], target=5, monkeypatch=monkeypatch)
        assert not aborted

    def test_under_leverage_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(3), _lev(3)], target=5, monkeypatch=monkeypatch)
        assert not aborted, "less risk than approved is not a reason to abort"

    def test_a_small_overshoot_inside_tolerance_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(6), _lev(6)], target=5, monkeypatch=monkeypatch)
        assert not aborted, "1.2x is inside the 1.5x the guard allows"

    def test_an_unreadable_re_read_keeps_the_first_reading(self, monkeypatch):
        """Failing to confirm a fix is not confirming one."""
        aborted, _ = _drive_ensure_leverage(
            [_lev(20), _UNREADABLE], target=5, monkeypatch=monkeypatch)
        assert aborted, (
            "the first read confirmed 20x and the re-read said nothing — "
            "that is not evidence the retry worked")

    def test_the_position_read_also_aborts(self, monkeypatch):
        """The second confirmation source. A fix in one site is half a fix."""
        aborted, why = _drive_ensure_leverage(
            [_UNREADABLE],
            positions=[{"leverage": 20, "info": {"leverage": "20"}}],
            target=5, monkeypatch=monkeypatch)
        assert aborted
        assert "20" in why

    def test_the_ethfi_case_still_opens(self, monkeypatch):
        """2026-07-21: unparseable read-back, set_leverage SUCCEEDED.

        The regression the fail-open default was added for. An unreadable
        value never reaches the verdict, so this must still proceed.
        """
        aborted, _ = _drive_ensure_leverage(
            [_UNREADABLE], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch)
        assert not aborted, "the 2026-07-21 regression is back"
