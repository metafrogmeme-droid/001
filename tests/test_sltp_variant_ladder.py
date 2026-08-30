"""A TAO LONG ran 1.8h with no exchange stop, and the ladder never tried the fix.

2026-08-09. Six consecutive rejections placing the v3 SL/TP strategy order.
The position was protected only by a software stop-loss level — which requires
the bot alive, connected and polling — and the operator's dashboard showed it
as a normal open position. It exited at take-profit, so it cost nothing. That
is luck, not a control.

The audit trail (``logs/trade.jsonl``, action ``sl_tp_v3_retry_cycle``):

    attempt  marginMode  posSide  error
    1        isolated    net      31008
    2        crossed     net      40020
    3        crossed     long     40020
    4        isolated    long     31008
    5        isolated    net      31008      <- attempt 1, again

Two defects, both readable straight off that table:

  * **The missing rung.** bot/core/live_executor.py's own position-mode
    detector says "One-way mode: tradeSide/posSide must NOT be sent." The
    account is one-way — MSFT places first-try with ``hedge_mode: null`` — so
    the combination the venue would have accepted is ``posSide`` OMITTED. The
    old cycle only ever swapped ``net`` <-> the direction. Its own comment
    listed the rung ("attempt 4: toggle posSide + remove") and no branch
    implemented it; the warning line even formatted
    ``payload.get("posSide", "OMITTED")``, a state the ladder could not reach.

  * **The wasted rung.** Toggling two independent fields on attempt parity
    cannot enumerate their product. Attempt 5 re-sent what attempt 1 had
    already failed, spending a retry on a known-bad combination while two
    untried ones remained.

The error codes are the diagnostic and are worth keeping straight, because the
first read of this incident got it backwards:

    31008  "no position"  — the venue looked under THIS marginMode and found
                            nothing. The marginMode is wrong.
    40020  "posSide error" — the venue FOUND the position and objects to
                            posSide. So 40020 confirms the marginMode.

An all-40020 tail therefore means: margin mode is right, every posSide VALUE
was refused, so the field's presence is the problem. Which is the rung that
was missing.
"""
from __future__ import annotations

import pytest

from bot.core.live_executor import sltp_payload_variants

OMIT = None


class TestEveryCombinationExactlyOnce:
    """Six rungs for six attempts (_MAX_31008_RETRIES + 1). A duplicate costs a
    combination; a gap costs the position."""

    @pytest.mark.parametrize("pos_side", ["long", "short"])
    @pytest.mark.parametrize("margin_mode", ["isolated", "crossed"])
    def test_the_full_product_is_covered(self, pos_side, margin_mode):
        rungs = sltp_payload_variants(pos_side, margin_mode)
        expected = {(ps, mm) for ps in (pos_side, OMIT, "net")
                    for mm in ("isolated", "crossed")}
        assert set(rungs) == expected, "a combination the venue accepts is unreachable"
        assert len(rungs) == 6, "six attempts, six rungs — no waste, no gap"

    @pytest.mark.parametrize("pos_side", ["long", "short", "net"])
    @pytest.mark.parametrize("margin_mode", ["isolated", "crossed"])
    def test_no_rung_is_repeated(self, pos_side, margin_mode):
        rungs = sltp_payload_variants(pos_side, margin_mode)
        assert len(rungs) == len(set(rungs)), (
            "the old parity toggle re-sent isolated/net at attempt 5 having "
            "failed it at attempt 1, while two combinations went untried")

    def test_a_net_caller_does_not_silently_lose_two_rungs(self):
        """pos_side="net" collapses onto the trailing net rungs. Dedupe keeps
        the list honest rather than shipping two dead attempts."""
        rungs = sltp_payload_variants("net", "isolated")
        assert len(rungs) == len(set(rungs))
        assert (OMIT, "isolated") in rungs and (OMIT, "crossed") in rungs


class TestTheRungThatWasMissing:
    @pytest.mark.parametrize("pos_side", ["long", "short"])
    @pytest.mark.parametrize("margin_mode", ["isolated", "crossed"])
    def test_posSide_is_omitted_on_some_rung(self, pos_side, margin_mode):
        """THE fix. One-way accounts reject the field's PRESENCE, not its
        value, so no amount of cycling long/short/net can succeed."""
        rungs = sltp_payload_variants(pos_side, margin_mode)
        assert any(ps is OMIT for ps, _ in rungs), (
            "live_executor's own detector says one-way mode must not send "
            "posSide; without an omit rung that account can never be served")

    def test_omit_is_None_not_empty_string(self):
        """`posSide: ""` is a sent field with a bad value — it earns the same
        40020. Only removing the key changes the request."""
        for ps, _ in sltp_payload_variants("long", "crossed"):
            assert ps != "", "an empty posSide is still a posSide"

    def test_omit_is_reached_before_the_budget_runs_out(self):
        """A rung that exists at index 5 of a 5-attempt ladder does not exist.
        Both omit rungs must land inside the attempt budget."""
        rungs = sltp_payload_variants("long", "isolated")
        omit_positions = [i for i, (ps, _) in enumerate(rungs) if ps is OMIT]
        assert omit_positions, "no omit rung at all"
        assert max(omit_positions) <= 5, "unreachable within _MAX_31008_RETRIES + 1"


class TestOrderIsByLikelihoodNotConvenience:
    def test_the_callers_own_detected_values_go_first(self):
        """The detected pair already works for most symbols (MSFT places on the
        first attempt). Reordering it away would trade a live regression for a
        fix to a rarer case."""
        assert sltp_payload_variants("long", "crossed")[0] == ("long", "crossed")
        assert sltp_payload_variants("short", "isolated")[0] == ("short", "isolated")

    def test_the_margin_flip_comes_before_the_posSide_experiments(self):
        """31008 says the marginMode is wrong, and that is the cheaper
        hypothesis to eliminate — it needs one attempt, not four."""
        rungs = sltp_payload_variants("long", "isolated")
        assert rungs[1] == ("long", "crossed")

    def test_net_is_tried_last(self):
        """It is the value the venue rejected loudest in the incident, and on a
        UTA account Bitget reports long/short even in one-way mode."""
        rungs = sltp_payload_variants("long", "isolated")
        assert all(ps == "net" for ps, _ in rungs[-2:]), rungs


class TestTheIncidentItselfReplayed:
    """Plant the venue's real answers and assert the ladder reaches the fix.

    A ladder is only worth what it does against the errors that actually
    happened, so this drives the recorded TAO responses rather than asserting
    on the shape of the list.
    """

    def _venue(self, rung):
        """Bitget's behaviour that morning, as a pure function.

        The account is one-way and the position is CROSSED:
          - isolated  -> 31008, the venue finds no position there
          - crossed + any posSide VALUE -> 40020
          - crossed + posSide omitted   -> accepted
        """
        pos_side, margin_mode = rung
        if margin_mode != "crossed":
            return "31008"
        if pos_side is not None:
            return "40020"
        return "00000"

    def test_the_old_parity_ladder_never_succeeds(self):
        """The control. Without this the fix could be tested against a venue
        model that anything would satisfy."""
        old = [("net", "isolated"), ("net", "crossed"), ("long", "crossed"),
               ("long", "isolated"), ("net", "isolated"), ("net", "crossed")]
        assert all(self._venue(r) != "00000" for r in old), (
            "the recorded sequence must fail, or this proves nothing")

    def test_the_new_ladder_places_the_stop(self):
        rungs = sltp_payload_variants("long", "isolated")   # as detected then
        codes = [self._venue(r) for r in rungs]
        assert "00000" in codes, (
            f"ladder still cannot protect the position: {list(zip(rungs, codes))}")
        landed = rungs[codes.index("00000")]
        assert landed == (OMIT, "crossed"), landed

    def test_it_lands_within_the_attempt_budget(self):
        rungs = sltp_payload_variants("long", "isolated")
        codes = [self._venue(r) for r in rungs]
        assert codes.index("00000") <= 5, "success rung is past the last attempt"

    def test_an_all_40020_tail_is_what_points_at_the_omit_rung(self):
        """The reasoning in the docstring, pinned: under the right marginMode
        every posSide VALUE 40020s, which is the signal to drop the field."""
        crossed_with_values = [self._venue((ps, "crossed")) for ps in ("net", "long", "short")]
        assert set(crossed_with_values) == {"40020"}
        assert self._venue((OMIT, "crossed")) == "00000"


class TestTheLoopActuallyOmitsTheField:
    """The pure ladder above is not the fix; the WIRING is.

    Mutating `payload.pop("posSide", None)` to `payload["posSide"] = ""` left
    every test above passing — the list still contained a None rung, and
    nothing checked what the loop did with it. An empty posSide is a sent field
    with a bad value: same 40020, bug fully restored, suite green.

    So this drives the real `_place_sl_tp_v3` against a fake venue and asserts
    on the REQUEST BODIES it sent.
    """

    def _run(self, monkeypatch, venue):
        import asyncio
        from unittest.mock import MagicMock

        from bot.core import bitget_v3_client as v3mod
        from bot.core.live_executor import LiveExecutor
        from bot.utils.models import Direction

        sent: list[dict] = []

        class _FakeClient:
            @staticmethod
            def from_config():
                return _FakeClient()

            # RC-2026-011: the executor now asks for THIS account's client so a
            # per-user stop is signed with the user's keys, not the operator's.
            # The double ignores whose keys they are — it replaces the
            # transport entirely — but it has to offer the method.
            @staticmethod
            def for_account(_credentials):
                return _FakeClient()

            def request(self, _method, _path, body):
                sent.append(dict(body))
                return {"code": venue(body), "data": {"orderId": "OID-1"},
                        "msg": "fake"}

        # _place_sl_tp_v3 imports the client INSIDE the function, so the patch
        # has to land on the defining module, not on live_executor.
        monkeypatch.setattr(v3mod, "BitgetV3Client", _FakeClient)
        monkeypatch.setattr(LiveExecutor, "_fetch_position_margin_mode_v3",
                            staticmethod(lambda _s, *_: "isolated"))
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        ex = LiveExecutor.__new__(LiveExecutor)
        # __new__ skips __init__, so every attribute the path touches is set
        # by hand. `_credentials` is None here = the operator executor, which
        # is what this ladder test is about.
        ex._credentials = None
        ex._actual_margin_mode = "isolated"
        ex._hedge_mode = False
        ex._last_sltp_error = {}
        ex._client_oid = lambda s: "oid"
        ex._venue = MagicMock()

        out = asyncio.run(ex._place_sl_tp_v3(
            "TAO/USDT", Direction.LONG, 1.0,
            stop_loss=204.82, take_profit=205.65, price_precision=2))
        return out, sent

    def test_one_request_carries_no_posSide_key_at_all(self, monkeypatch):
        """The incident's venue: crossed position, one-way account."""
        def venue(body):
            if body.get("marginMode") != "crossed":
                return "31008"
            return "40020" if "posSide" in body else "00000"

        (sl_id, tp_id), sent = self._run(monkeypatch, venue)
        omitted = [b for b in sent if "posSide" not in b]
        assert omitted, (
            "no request omitted posSide — an empty string is still a posSide "
            "and earns the same 40020")
        assert sl_id == "OID-1" and tp_id == "OID-1", (
            "the stop must actually be placed, not merely attempted")

    def test_it_never_sends_an_empty_posSide(self, monkeypatch):
        def venue(_body):
            return "31008"          # force the full ladder
        _out, sent = self._run(monkeypatch, venue)
        assert sent, "no request was made at all"
        assert all(b.get("posSide", "x") != "" for b in sent), (
            f"empty posSide sent: {[b.get('posSide') for b in sent]}")

    def test_the_full_ladder_is_walked_without_repeats(self, monkeypatch):
        def venue(_body):
            return "31008"
        _out, sent = self._run(monkeypatch, venue)
        combos = [(b.get("posSide", None), b["marginMode"]) for b in sent]
        assert len(combos) == len(set(combos)), f"repeated rung: {combos}"
        assert len(combos) == 6, f"expected 6 attempts, got {len(combos)}: {combos}"


async def _no_sleep(_seconds):
    return None
