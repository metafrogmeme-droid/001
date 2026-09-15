"""A FIELD NAME IS NOT A QUANTITY (live incident, 2026-09-15).

Thirteen live fills across ten symbols — BTC, TRUMP (x3), OP, NATGAS (x2),
SUI, RAVE, ETC, DFEN, CL, TRX — every one requested at 5x, every one filled
at 20x, every one flattened seconds later by the post-fill overshoot guard at
a round trip of fees. NATGAS closed at -$1.86 on a -0.29% move.

The pre-order guard was already built and already correct. It never fired,
because the READ-BACK confirmed the target. Bitget carries BOTH the crossed
and the isolated per-side leverage on one payload, and the parser scanned
`longLeverage` before `crossMarginLeverage`:

    longLeverage:        "5"   <- the per-side value the bot had just set
    crossMarginLeverage: "20"  <- the value the fill actually uses

So "a leverage parsed out of this dict" answered 5, the guard saw a match,
and the order went out. `leverage_readback` answers which FIELD the value came
from and whether that field DECIDES the fill for the margin mode in play;
only a reading from the governing field is a confirmation.

Three states, deliberately kept apart. `governs=True` is a measurement of the
fill. `governs=False` is a real number we KNOW is the wrong one. `governs=None`
is a value we cannot PLACE because the margin mode is unreadable — which keeps
the confirmation it has always had, because refusing it changes nothing under
the fail-open default and would abort every trade on a modeless payload under
the opt-in strict mode (the "trades can not open" regression of 2026-07-21).

Every fixture here is ASYMMETRIC on purpose: each crossed payload carries a
`longLeverage` EQUAL to the target, so a reading that scans field names
confirms and a reading that places them refuses. A fixture where both answers
agree cannot tell the two readings apart.
"""

from __future__ import annotations

import logging
import pathlib

import pytest

from bot.core.live_executor import _leverage_field_phrase, leverage_readback
from tests.leverage_drive import drive_ensure_leverage, lev
from tests.source_scan import code_only

TRADE_CHANNEL = "runeclaw.trade"

SRC = code_only(
    pathlib.Path("bot/core/live_executor.py").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _propagate_audit_logger():
    """`bot/utils/logger.py` sets `propagate = False` on the audit channels, so
    caplog — which attaches at the root — cannot see their structured records.
    (The first draft of this file asserted on an empty list while the record
    it wanted was printing to stderr one frame away.)"""
    lg = logging.getLogger(TRADE_CHANNEL)
    saved = lg.propagate
    lg.propagate = True
    yield
    lg.propagate = saved


#: The live shape. ccxt's unified `longLeverage` holds the per-side value the
#: bot set moments earlier; `info.crossMarginLeverage` holds the account
#: default that governs the fill. Note `longLeverage == 5 == the target`: a
#: field-name scan CONFIRMS this payload.
CROSSED = {
    "symbol": "NATGAS/USDT:USDT", "marginMode": "crossed",
    "longLeverage": 5, "shortLeverage": 5,
    "info": {"symbol": "NATGASUSDT", "marginCoin": "USDT",
             "marginMode": "crossed", "longLeverage": "5",
             "shortLeverage": "5", "crossMarginLeverage": "20"},
}

#: The ETHFI shape (2026-07-21): isolated, unified fields None, the real value
#: in `info` as a string. A crossed value sits beside it and must be ignored.
ISOLATED = {
    "symbol": "ETHFI/USDT:USDT", "longLeverage": None, "leverage": None,
    "info": {"symbol": "ETHFIUSDT", "marginCoin": "USDT",
             "marginMode": "isolated", "longLeverage": "5",
             "shortLeverage": "5", "crossMarginLeverage": "20"},
}

#: Isolated with the two sides at DIFFERENT leverages — the state Bitget's
#: per-side storage makes reachable, and the one a single-side read misses.
SPLIT = {"info": {"marginMode": "isolated",
                  "isolatedLongLeverage": "5",
                  "isolatedShortLeverage": "20"}}

#: SPLIT with the sides swapped. Without it, "read the SHORT field" and "take
#: the worst of the two" give the same answer for every short order, so the
#: mutation that ignores the side survives a green suite — which is what it did.
MIRROR = {"info": {"marginMode": "isolated",
                   "isolatedLongLeverage": "20",
                   "isolatedShortLeverage": "5"}}

UNREADABLE = {"info": {"marginCoin": "USDT"}}


class TestTheFixturesAreAsymmetric:
    """A fixture both readings agree on proves nothing about either."""

    def test_a_field_name_scan_would_confirm_the_crossed_payload(self):
        assert CROSSED["longLeverage"] == 5
        assert CROSSED["info"]["longLeverage"] == "5"
        assert CROSSED["info"]["crossMarginLeverage"] == "20"

    def test_the_split_payload_disagrees_with_itself(self):
        info = SPLIT["info"]
        assert info["isolatedLongLeverage"] != info["isolatedShortLeverage"]

    def test_the_mirror_puts_the_overshoot_on_the_other_side(self):
        """One fixture cannot tell "read this side" from "read the worst"."""
        assert SPLIT["info"]["isolatedShortLeverage"] == \
            MIRROR["info"]["isolatedLongLeverage"]
        assert SPLIT["info"]["isolatedLongLeverage"] == \
            MIRROR["info"]["isolatedShortLeverage"]


class TestTheReadingSaysWhichFieldGoverns:
    def test_the_crossed_payload_reads_the_crossed_field(self):
        r = leverage_readback(CROSSED)
        assert r["value"] == 20, "the value the fill uses, not the one we set"
        assert r["field"] == "crossMarginLeverage"
        assert r["governs"] is True
        assert r["mode"] == "crossed"

    def test_the_isolated_payload_reads_the_per_side_field(self):
        r = leverage_readback(ISOLATED, side="LONG")
        assert r["value"] == 5
        assert r["field"] == "longLeverage"
        assert r["governs"] is True
        assert r["mode"] == "isolated"

    @pytest.mark.parametrize("side,expected,field", [
        ("LONG", 5, "isolatedLongLeverage"),
        ("long", 5, "isolatedLongLeverage"),
        ("buy", 5, "isolatedLongLeverage"),
        ("SHORT", 20, "isolatedShortLeverage"),
        ("short", 20, "isolatedShortLeverage"),
        ("sell", 20, "isolatedShortLeverage"),
    ])
    def test_the_side_decides_which_isolated_field(self, side, expected, field):
        r = leverage_readback(SPLIT, side=side)
        assert (r["value"], r["field"], r["governs"]) == (expected, field, True)

    @pytest.mark.parametrize("side,expected,field", [
        ("LONG", 20, "isolatedLongLeverage"),
        ("SHORT", 5, "isolatedShortLeverage"),
    ])
    def test_the_side_decides_it_in_the_other_direction_too(
            self, side, expected, field):
        """On MIRROR the LONG side is the overshoot, so a read that quietly
        always takes the long fields — or always the worst — answers 20 for a
        short order that is correctly set at 5."""
        r = leverage_readback(MIRROR, side=side)
        assert (r["value"], r["field"], r["governs"]) == (expected, field, True)

    @pytest.mark.parametrize("payload,field", [
        (SPLIT, "isolatedShortLeverage"), (MIRROR, "isolatedLongLeverage")])
    def test_no_side_takes_the_worst_of_the_two(self, payload, field):
        """A confirmation that holds for one direction only is not one."""
        r = leverage_readback(payload)
        assert r["value"] == 20
        assert r["field"] == field
        assert r["governs"] is True

    def test_the_payloads_own_mode_beats_the_callers(self):
        """The caller's mode is the account as last verified; the payload's is
        the account as THIS read found it, and a reading describes its own
        moment."""
        r = leverage_readback(CROSSED, "isolated", "LONG")
        assert r["mode"] == "crossed"
        assert r["value"] == 20

    def test_the_callers_mode_is_used_when_the_payload_has_none(self):
        r = leverage_readback({"longLeverage": "5"}, "crossed")
        assert r["mode"] == "crossed"

    @pytest.mark.parametrize("spelling", ["crossMarginLeverage",
                                          "crossedMarginLeverage"])
    def test_both_crossed_spellings_are_read(self, spelling):
        r = leverage_readback({"marginMode": "crossed", spelling: "20"})
        assert (r["value"], r["field"], r["governs"]) == (20, spelling, True)

    @pytest.mark.parametrize("mode", ["cross", "crossed", "CROSSED", " Cross "])
    def test_both_mode_spellings_reach_the_crossed_fields(self, mode):
        """Bitget says "crossed"; ccxt and our own config say "cross"."""
        r = leverage_readback({"crossMarginLeverage": "20"}, mode)
        assert r["governs"] is True

    @pytest.mark.parametrize("mode", ["isolated", "fixed", "ISOLATED"])
    def test_both_isolated_spellings_reach_the_per_side_fields(self, mode):
        r = leverage_readback({"isolatedLongLeverage": "5"}, mode, "long")
        assert r["governs"] is True

    def test_margin_type_is_read_as_the_mode(self):
        """Some ccxt shapes carry `marginType` rather than `marginMode`."""
        r = leverage_readback({"marginType": "crossed",
                               "crossMarginLeverage": "20"})
        assert r["mode"] == "crossed"
        assert r["governs"] is True


class TestNotAConfirmation:
    def test_a_field_that_does_not_govern_is_not_a_confirmation(self):
        """A crossed account whose crossed field could not be read. The
        per-side value is REAL and it is the wrong quantity."""
        r = leverage_readback({"longLeverage": "5"}, "crossed")
        assert r["value"] == 5, "still readable — the read did not fail"
        assert r["field"] == "longLeverage"
        assert r["governs"] is False

    def test_an_unplaceable_value_is_kept_apart_from_a_wrong_one(self):
        """False is 'we know this is the wrong field'; None is 'nothing says
        whether it is'. Both refuse to confirm; they are different facts."""
        wrong = leverage_readback({"longLeverage": "5"}, "crossed")
        unplaceable = leverage_readback({"longLeverage": "5"})
        assert wrong["governs"] is False
        assert unplaceable["governs"] is None
        assert wrong["governs"] != unplaceable["governs"]

    def test_an_unreadable_payload_reads_nothing_anywhere(self):
        for mode in (None, "crossed", "isolated"):
            r = leverage_readback(UNREADABLE, mode)
            assert r["value"] is None
            assert r["field"] is None
            assert r["governs"] is None

    @pytest.mark.parametrize("payload", [None, [], "20", 20,
                                         [{"longLeverage": 5}]])
    def test_a_non_dict_reads_nothing(self, payload):
        assert leverage_readback(payload) == {
            "value": None, "field": None, "governs": None, "mode": None}

    @pytest.mark.parametrize("bad", [0, -5, "0", "n/a", "", None,
                                     float("nan"), float("inf")])
    def test_a_non_positive_or_junk_value_is_not_a_leverage(self, bad):
        assert leverage_readback(
            {"marginMode": "crossed", "crossMarginLeverage": bad},
        )["governs"] is not True

    def test_a_junk_governing_field_falls_back_and_says_it_does_not_govern(self):
        """The governing field is present and unreadable; a real value sits in
        a field that does not decide the fill. That is the False case."""
        r = leverage_readback({"marginMode": "crossed",
                               "crossMarginLeverage": "n/a",
                               "longLeverage": "5"})
        assert r["value"] == 5
        assert r["governs"] is False


class TestTheOperatorIsToldWhichField:
    """"exchange stuck at 20x" does not say what to go and change."""

    def test_the_governing_read_names_the_field_and_the_mode(self):
        out = _leverage_field_phrase(leverage_readback(CROSSED))
        assert "crossMarginLeverage" in out
        assert "crossed" in out
        assert "governs" in out

    def test_a_non_governing_read_says_it_is_not_a_confirmation(self):
        out = _leverage_field_phrase(
            leverage_readback({"longLeverage": "5"}, "crossed"))
        assert "longLeverage" in out
        assert "not a confirmation" in out
        assert "does NOT decide" in out

    def test_an_unplaceable_read_says_the_mode_could_not_be_read(self):
        out = _leverage_field_phrase(leverage_readback({"leverage": "7"}))
        assert "margin mode could not be read" in out

    def test_an_unreadable_read_claims_no_field(self):
        out = _leverage_field_phrase(leverage_readback(UNREADABLE))
        assert "no leverage field" in out
        assert "readable" in out

    def test_the_four_outcomes_are_four_sentences(self):
        seen = {
            _leverage_field_phrase(leverage_readback(CROSSED)),
            _leverage_field_phrase(
                leverage_readback({"longLeverage": "5"}, "crossed")),
            _leverage_field_phrase(leverage_readback({"leverage": "7"})),
            _leverage_field_phrase(leverage_readback(UNREADABLE)),
        }
        assert len(seen) == 4

    @pytest.mark.parametrize("junk", [None, {}, [], "x", {"value": None}])
    def test_it_is_total(self, junk):
        """It renders into a CRITICAL log line on the live order path."""
        assert isinstance(_leverage_field_phrase(junk), str)


# ── the order path, DRIVEN ────────────────────────────────────────────────

class TestTheLiveShapeIsRefused:
    def test_the_crossed_overshoot_aborts_the_order(self, monkeypatch):
        d = drive_ensure_leverage([CROSSED, CROSSED], target=5, side="LONG",
                                  margin_mode="crossed", monkeypatch=monkeypatch)
        assert d.aborted, (
            "this is the 2026-09-15 payload: it opened at 20x thirteen times")
        assert "20x" in d.why
        assert "crossMarginLeverage" in d.why, (
            "an operator told 'stuck at 20x' goes and checks the per-side "
            "setting, which is fine — the crossed default is what fills")

    def test_it_aborts_under_the_fail_open_default(self, monkeypatch):
        """Fail-open governs UNCONFIRMED leverage. This one is confirmed, and
        proceeding does not open a trade — it opens one the post-fill guard
        flattens seconds later, and charges two fees for it."""
        d = drive_ensure_leverage([CROSSED, CROSSED], target=5, side="LONG",
                                  margin_mode="crossed", fail_open=True,
                                  monkeypatch=monkeypatch)
        assert d.aborted

    def test_the_same_account_really_isolated_proceeds(self, monkeypatch):
        """The asymmetry. Same per-side 5x, same crossed 20x sitting on the
        payload — the only difference is which mode the account is in."""
        d = drive_ensure_leverage([ISOLATED], target=5, side="LONG",
                                  margin_mode="isolated", monkeypatch=monkeypatch)
        assert not d.aborted, "isolated at 5x is the target; nothing is wrong"

    def test_the_abort_is_audited_with_the_field_and_the_ratio(
            self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([CROSSED, CROSSED], target=5, side="LONG",
                              margin_mode="crossed", monkeypatch=monkeypatch)
        rows = [r for r in caplog.records
                if getattr(r, "action", "") == "leverage_abort"]
        assert rows, "a refused order leaves a record"
        data = rows[-1].data
        assert data["observed"] == 20
        assert data["target"] == 5
        assert data["field"] == "crossMarginLeverage"
        assert data["margin_mode"] == "crossed"
        assert data["governs"] is True
        assert data["ratio"] == pytest.approx(4.0)

    def test_the_position_row_carries_its_own_margin_mode(self, monkeypatch):
        """A closer reading than the executor's last verification: the row says
        the account is crossed even though the mode read said isolated."""
        d = drive_ensure_leverage(
            [UNREADABLE],
            positions=[{"side": "long", "marginMode": "crossed",
                        "longLeverage": 5,
                        "info": {"crossMarginLeverage": "20"}}],
            target=5, side="LONG", margin_mode="isolated",
            monkeypatch=monkeypatch)
        assert d.aborted
        assert "crossMarginLeverage" in d.why

    def test_the_row_is_read_for_the_side_we_are_about_to_place(
            self, monkeypatch):
        """Under hedge mode `fetch_positions` can hand back the OTHER
        direction's row. Its per-side leverage matching the target says
        nothing about ours — and reading it as though it did is a false
        confirmation on the one path this whole slice is about."""
        row = {"side": "short", **MIRROR}
        d = drive_ensure_leverage([UNREADABLE], positions=[row], target=5,
                                  side="LONG", margin_mode="isolated",
                                  monkeypatch=monkeypatch)
        assert d.aborted, "our LONG is at 20x; the short row's 5x is not ours"
        assert "isolatedLongLeverage" in d.why


class TestAKnownWrongFieldDoesNotCarryTheOrder:
    #: crossed account, crossed field unreadable, per-side value present and
    #: equal to the target. `governs` is False: not a confirmation.
    WRONG_FIELD = {"marginMode": "crossed", "longLeverage": 5,
                   "info": {"marginCoin": "USDT"}}

    def test_it_does_not_verify(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        d = drive_ensure_leverage([self.WRONG_FIELD], positions=[], target=5,
                                  side="LONG", margin_mode="crossed",
                                  monkeypatch=monkeypatch)
        assert not d.aborted, "fail-open proceeds — loudly"
        rows = [r for r in caplog.records
                if getattr(r, "action", "") == "leverage_unverified"]
        assert rows, "a read that cannot confirm says so"
        assert rows[-1].data["governs"] is False
        assert rows[-1].data["field"] == "longLeverage"
        assert "not a confirmation" in rows[-1].getMessage()

    def test_a_per_side_success_does_not_stand_in_for_it(self, monkeypatch):
        """A per-side set succeeding says the per-side value applied. On a
        crossed account that is not the value the fill uses, so it is no
        evidence at all about the governing one."""
        d = drive_ensure_leverage([self.WRONG_FIELD], positions=[], target=5,
                                  side="LONG", margin_mode="crossed",
                                  fail_open=False, monkeypatch=monkeypatch)
        assert d.aborted
        assert "Cannot confirm" in d.why

    def test_a_re_read_that_lands_on_the_wrong_field_does_not_confirm(
            self, monkeypatch):
        """The retry path. The first read confirmed a real 20x overshoot; the
        re-read answers the target from a field that does not decide the fill,
        so the mismatch is cleared and NOTHING was confirmed."""
        d = drive_ensure_leverage([CROSSED, self.WRONG_FIELD], positions=[],
                                  target=5, side="LONG", margin_mode="crossed",
                                  fail_open=False, monkeypatch=monkeypatch)
        assert d.aborted
        assert "Cannot confirm" in d.why

    def test_a_position_row_on_the_wrong_field_does_not_confirm(
            self, monkeypatch):
        """The second confirmation source. A fix in one site is half a fix."""
        d = drive_ensure_leverage(
            [UNREADABLE],
            positions=[{"marginMode": "crossed", "longLeverage": 5,
                        "info": {"marginCoin": "USDT"}}],
            target=5, side="LONG", margin_mode="crossed", fail_open=False,
            monkeypatch=monkeypatch)
        assert d.aborted
        assert "Cannot confirm" in d.why

    def test_an_unplaceable_read_still_confirms(self, monkeypatch):
        """The 2026-07-21 regression: a modeless payload must keep the
        confirmation it has always had, or the strict mode blocks every trade
        on venues that do not echo a margin mode."""
        d = drive_ensure_leverage([lev(5)], target=5, fail_open=False,
                                  monkeypatch=monkeypatch)
        assert not d.aborted


class TestThePerSideSetIsAudible:
    """`except Exception: pass` with an empty body meant this fix could fail on
    EVERY call and leave no trace, so "the flag is on" and "the per-side set is
    applying" were unrelated statements with nothing able to tell them apart."""

    def test_both_sides_are_pushed(self, monkeypatch):
        d = drive_ensure_leverage([lev(5)], target=5, monkeypatch=monkeypatch)
        assert "long" in d.hold_sides and "short" in d.hold_sides

    def test_a_refused_side_is_recorded(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([lev(5)], target=5, per_side_raises=True,
                              monkeypatch=monkeypatch)
        rows = [r for r in caplog.records
                if getattr(r, "action", "") == "leverage_per_side"]
        assert rows, "a remedy whose failure is invisible is a remedy nobody "\
                     "can know is broken"
        assert rows[-1].result == "INCOMPLETE"
        assert rows[-1].levelno == logging.WARNING
        assert rows[-1].data["applied"] == []
        assert rows[-1].data["refused"] == ["long:RuntimeError",
                                            "short:RuntimeError"]

    def test_the_record_quotes_the_class_and_not_the_message(
            self, monkeypatch, caplog):
        """A venue rejection can echo request params, and this line reaches the
        operator log."""
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([lev(5)], target=5, per_side_raises=True,
                              monkeypatch=monkeypatch)
        rows = [r for r in caplog.records
                if getattr(r, "action", "") == "leverage_per_side"]
        assert "venue refused holdSide leverage" not in rows[-1].getMessage()
        assert "RuntimeError" in rows[-1].getMessage()

    def test_a_success_is_not_recorded_as_a_problem(self, monkeypatch, caplog):
        """A warning that fires when nothing is wrong is how operators learn
        to skip the next one."""
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([lev(5)], target=5, monkeypatch=monkeypatch)
        assert not [r for r in caplog.records
                    if getattr(r, "action", "") == "leverage_per_side"]

    def test_the_flag_still_turns_it_off(self, monkeypatch):
        d = drive_ensure_leverage([lev(5)], target=5, force_per_side="0",
                                  monkeypatch=monkeypatch)
        assert d.hold_sides == [None], "only the bare call"

    def test_a_bare_set_is_not_a_confirmation(self, monkeypatch):
        """THIRTEEN live fills returned 200 from the bare call and opened at
        the sticky default. Under the strict mode it no longer stands in for a
        read-back that could not confirm."""
        d = drive_ensure_leverage([UNREADABLE], positions=[], target=5,
                                  force_per_side="0", fail_open=False,
                                  monkeypatch=monkeypatch)
        assert d.aborted

    def test_a_per_side_success_does_stand_in(self, monkeypatch):
        d = drive_ensure_leverage([UNREADABLE], positions=[], target=5,
                                  fail_open=False, monkeypatch=monkeypatch)
        assert not d.aborted
        assert "long" in d.hold_sides

    def test_a_refused_per_side_does_not(self, monkeypatch):
        d = drive_ensure_leverage([UNREADABLE], positions=[], target=5,
                                  per_side_raises=True, fail_open=False,
                                  monkeypatch=monkeypatch)
        assert d.aborted


class TestAModeNobodyReadSaysSo:
    """`grep "MARGIN MODE MISMATCH"` coming back EMPTY said nothing at all —
    the three ways the account read fails to answer a mode were `logger.debug`
    or silent, and that is how the first diagnostic round on the 20x incident
    read as an all-clear."""

    def test_an_unread_mode_is_recorded(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([lev(5)], target=5, margin_mode=None,
                              monkeypatch=monkeypatch)
        rows = [r for r in caplog.records
                if getattr(r, "action", "") == "margin_mode_unread"]
        assert rows
        assert rows[-1].levelno == logging.WARNING
        assert "not a mismatch" in rows[-1].getMessage()
        assert "not an all-clear" in rows[-1].getMessage()

    def test_a_read_mode_is_not(self, monkeypatch, caplog):
        """A warning that fires when nothing is wrong is how operators learn
        to skip the next one."""
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([ISOLATED], target=5, side="LONG",
                              margin_mode="isolated", monkeypatch=monkeypatch)
        assert not [r for r in caplog.records
                    if getattr(r, "action", "") == "margin_mode_unread"]

    def test_it_is_said_once_per_symbol(self, monkeypatch, caplog):
        """A warning that repeats every tick is a warning nobody reads. Three
        calls on the SAME executor — a fresh one each time cannot tell "once
        per process" from "every time"."""
        caplog.set_level(logging.INFO, logger=TRADE_CHANNEL)
        drive_ensure_leverage([lev(5), lev(5), lev(5)], target=5,
                              margin_mode=None, runs=3,
                              monkeypatch=monkeypatch)
        assert len([r for r in caplog.records
                    if getattr(r, "action", "") == "margin_mode_unread"]) == 1

    def test_the_constructor_declares_the_set(self):
        """Read directly rather than through a defensive getattr, so a fixture
        short the seam fails loudly instead of silently never warning."""
        assert "self._margin_mode_unread_warned: set = set()" in SRC


class TestTheCallerHandsOverTheDirection:
    def test_the_method_takes_a_side(self):
        assert "async def _ensure_leverage(self, symbol: str, side: str = \"\")" \
            in SRC

    def test_the_order_path_passes_the_ideas_direction(self):
        assert 'await self._ensure_leverage(\n                    swap_sym, ' \
            'getattr(idea.direction, "value", "") or "")' in SRC

    def test_the_mode_is_read_once_outside_the_try(self):
        """An AttributeError inside that try is swallowed by the broad handler
        below, which turns the whole verification into a silent no-op."""
        read = SRC.index(
            '_observed_mode = getattr(self, "_actual_margin_mode", None)')
        opened = SRC.index("lev_info = await exchange.fetch_leverage")
        assert read < opened
        assert "self._actual_margin_mode" not in SRC[read:opened + 4000], \
            "the read-back sites ask the once-read local, not the attribute"
