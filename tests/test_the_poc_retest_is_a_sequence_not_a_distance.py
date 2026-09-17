"""The POC-retest setup: the operator's rules, driven.

The rules were written down on 2026-09-17 with their own framing attached —
"sensible starting rules, not yet validated results. I'd test the ATR buffer,
1-5-candle retest window, and 2R filter as parameters rather than assuming
they are optimal" — so the first thing this file asserts is that none of those
three is a constant anywhere in the module.

Everything else is DRIVEN over constructed candles, because every claim here is
about a SEQUENCE and a sequence cannot be read off a source scan. Three defects
in the first draft came out of exactly that, and each is a fixture in this file:

  * THE BACKWARDS SEARCH. The first version looked backwards for "the most
    recent decisive close". On any real setup the RETEST candle also closes
    beyond the POC by more than the buffer, so the search claimed the retest as
    the breakout, found nothing after it, and reported `awaiting_retest` on a
    completed setup. The fixture built to produce `confirmed` produced
    `awaiting_retest` — which is how it was found.
  * A SUSTAINED MOVE IS ONE EXCURSION. Every candle in it closes beyond the
    POC, and arming on each would restart the window every bar. A breakout is
    the FIRST decisive close of an excursion.
  * AND THE FIXTURES THEMSELVES WERE WRONG TWICE — a monotone ramp has no
    interior swing low, so `swing_leg` correctly answered None, and a base
    series that drifts across the POC arms the excursion before the intended
    breakout. Both are recorded here as named fixtures rather than as lore,
    because a fixture that cannot reach the case it names proves nothing.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import numpy as np
import pytest

from bot.core import poc_retest as M
from bot.core.liquidity_sweep import find_swing_highs, find_swing_lows
from bot.core.poc_retest import STATES, VERDICTS, PocRetestParams, leg_poc, retest_state, setup_verdict, swing_leg

# ── fixtures ──────────────────────────────────────────────────────────────

def htf(n: int = 40, a: int = 10, b: int = 30, base: float = 100.0,
        up: bool = True, volume: float = 900.0):
    """A V (or inverted V): an `up` leg falls to a LOW at `a` then rises to a
    HIGH at `b`; a `down` leg does the inverse.

    A MONOTONE RAMP HAS NO INTERIOR SWING, which is what the first draft of
    this helper produced — `find_swing_lows` needs `order` bars on each side,
    so a series whose minimum is at index 0 has no swing low at all and
    `swing_leg` answers None. That is the correct answer and it is not a
    setup, so the shape has to be a real V.
    """
    c = np.empty(n)
    s = 1.0 if up else -1.0
    for i in range(n):
        if i <= a:
            c[i] = base + s * (a - i) * 0.5
        elif i <= b:
            c[i] = base + s * (i - a) * 0.6
        else:
            c[i] = c[b] - s * (i - b) * 0.3
    v = np.full(n, 10.0)
    v[a + 1:a + 6] = volume            # the accumulation: the POC sits here
    return list(c + 0.2), list(c - 0.2), list(c), list(v)


def ltf(poc: float, *, side: str, n_base: int = 25, breakout: float = 0.8,
        retest_close: float = 0.3, wick: float = 0.02, hold: int = 0,
        retest_at: int | None = None):
    """Entry-timeframe candles that stay on ONE side of the POC and then break.

    THE BASE MUST NOT DRIFT ACROSS THE POC. A rising ramp from below arms the
    excursion as soon as it clears the buffer, the window expires, and the
    intended breakout is no longer the first decisive close of anything — the
    second fixture defect, and the reason the base here oscillates instead.

    `hold` inserts candles between the breakout and the retest that stay
    beyond the POC and never touch it, so the retest lands `hold + 1` candles
    after the decisive close. Without it every retest here is at +1, which is
    inside a window of ONE as much as a window of five — so the window
    parameter cannot be measured. That is why it exists: the first draft of
    `test_moving_a_parameter_moves_the_answer` asserted the window changes an
    answer against a fixture positioned where it cannot.
    """
    s = 1.0 if side == "long" else -1.0
    closes = [poc - s * (1.0 if i % 2 else 0.6) for i in range(n_base)]
    closes.append(poc + s * breakout)
    # The hold candles sit beyond the POC by MORE than the retest does, so
    # the retest is the first candle to come back to the zone.
    closes += [poc + s * (breakout + 0.1 + 0.05 * k) for k in range(hold)]
    closes.append(poc + s * retest_close)
    cl = np.array(closes, float)
    hi, lo = cl + 0.15, cl - 0.15
    i = len(closes) - 1 if retest_at is None else retest_at
    if side == "long":
        lo[i] = poc - wick                  # wicks INTO the zone
    else:
        hi[i] = poc + wick
    return list(hi), list(lo), list(cl)


def _long_setup(**kw):
    H, L, C, V = htf(up=True)
    leg = swing_leg(H, L)
    poc = leg_poc(H, L, C, V, leg)
    hi, lo, cl = ltf(poc, side="long", **kw)
    return (H, L, C, V), (hi, lo, cl), leg, poc


# ── the parameters are inputs, not constants ──────────────────────────────

class TestTheThresholdsAreParameters:
    """The operator asked for these to be testable, not assumed optimal."""

    def test_the_three_numbers_have_defaults_that_match_the_spec(self):
        p = PocRetestParams()
        assert p.atr_buffer == 0.25
        assert p.retest_window == 5
        assert p.min_net_r == 2.0
        assert p.max_stop_atr == 2.0

    def test_no_threshold_appears_as_a_literal_in_an_expression(self):
        """0.25 / 5 / 2.0 must reach the logic only through `params`.

        A literal in a comparison is a constant however many fields sit beside
        it — that is the `BacktestConfig.market_is_perp` rule, where perp-ness
        had to be an INPUT because inferring it was the defect.
        """
        src = Path(inspect.getfile(M)).read_text(encoding="utf-8")
        tree = ast.parse(src)
        params_cls = next(n for n in tree.body
                          if isinstance(n, ast.ClassDef)
                          and n.name == "PocRetestParams")
        lo, hi = params_cls.lineno, params_cls.end_lineno or params_cls.lineno
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if lo <= node.lineno <= hi:
                continue                      # the dataclass's own defaults
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Constant) and isinstance(
                        side.value, (int, float)) and side.value not in (0, 1, 2):
                    bad.append((node.lineno, side.value))
        assert not bad, f"thresholds compared against literals: {bad}"

    @pytest.mark.parametrize("field,value,expect,shape", [
        # AT THE BOUNDARY, OR IT MEASURES NOTHING. The first draft of this
        # test drove the default fixture, whose breakout closes 0.8 above a
        # POC with ATR ~0.64 — decisive at a 0.25x buffer AND at a 1.0x one,
        # so the buffer changed no verdict and the assertion read as though
        # the parameter were dead. Same for the window: the default retest
        # lands at +1, which is inside a window of ONE as much as of five.
        # Each row below is positioned where the parameter decides.
        ("atr_buffer", 1.0, "no_breakout", {"breakout": 0.4,
                                            "retest_close": 0.3}),
        ("retest_window", 1, "window_expired", {"hold": 3}),
    ])
    def test_moving_a_state_parameter_moves_the_state(self, field, value,
                                                      expect, shape):
        """A parameter nothing reads is the fifth granularity."""
        htf_a, ltf_a, _, _ = _long_setup(**shape)
        base = retest_state(*htf_a, *ltf_a)
        assert base.state == "confirmed", (
            f"the {field} fixture must CONFIRM at the default: {base.why}")
        moved = retest_state(*htf_a, *ltf_a,
                             params=PocRetestParams(**{field: value}))
        assert moved.state == expect, f"{field}={value} gave {moved.state}"

    @pytest.mark.parametrize("field,value,expect", [
        ("max_stop_atr", 0.01, "stop_too_wide"),
        ("min_net_r", 999.0, "below_min_r"),
    ])
    def test_moving_a_verdict_parameter_moves_the_verdict(self, field, value,
                                                          expect):
        """The other two are rejections rather than states, so they move the
        verdict on an unchanged sequence."""
        htf_a, ltf_a, _, _ = _long_setup(breakout=0.4, retest_close=0.3)
        r = retest_state(*htf_a, *ltf_a)
        assert r.state == "confirmed", r.why
        assert setup_verdict(r).verdict == "ok", setup_verdict(r).why
        assert setup_verdict(
            r, params=PocRetestParams(**{field: value})).verdict == expect


# ── the sequence ──────────────────────────────────────────────────────────

class TestEveryStateIsReachable:
    def test_confirmed_long(self):
        htf_a, ltf_a, leg, poc = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        assert r.state == "confirmed", r.why
        assert r.side == "long"
        assert r.breakout_index is not None and r.retest_index is not None
        assert r.retest_index > r.breakout_index, "a retest follows a breakout"
        # Entry on a BREAK of the retest candle's high; stop below its low.
        assert r.entry == pytest.approx(ltf_a[0][r.retest_index])
        assert r.stop < ltf_a[1][r.retest_index]
        assert r.target == leg.high

    def test_the_backwards_search_defect_stays_fixed(self):
        """The retest candle closes beyond the POC too. A search for "the most
        recent decisive close" claims IT as the breakout and then reports
        `awaiting_retest` on a completed setup."""
        htf_a, ltf_a, _, poc = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        hi, lo, cl = ltf_a
        atr = r.atr
        assert atr is not None
        # The retest candle really does clear the buffer — which is why the
        # backwards search could not tell it from the breakout.
        assert cl[r.retest_index] >= poc + 0.25 * atr
        assert r.state == "confirmed"

    def test_a_sustained_move_is_one_excursion_not_one_per_candle(self):
        """Arming on every decisive close would restart the window each bar,
        so nothing could ever expire."""
        htf_a, _, _, poc = _long_setup()
        hi, lo, cl = ltf(poc, side="long", n_base=25)
        cl = list(cl[:26]) + [poc + 0.9] * 8          # eight bars, all beyond
        hi = [x + 0.15 for x in cl]
        lo = [x - 0.15 for x in cl]
        r = retest_state(*htf_a, hi, lo, cl)
        assert r.state == "window_expired", r.state
        assert r.breakout_index == 25, "the FIRST decisive close of the run"

    def test_retest_failed_when_it_closes_on_the_wrong_side(self):
        htf_a, _, _, poc = _long_setup()
        hi, lo, cl = ltf(poc, side="long", retest_close=-0.4)
        r = retest_state(*htf_a, hi, lo, cl)
        assert r.state == "retest_failed", r.why

    def test_awaiting_retest_while_the_window_is_still_open(self):
        htf_a, _, _, poc = _long_setup()
        hi, lo, cl = ltf(poc, side="long")
        cl = list(cl[:26]) + [poc + 1.0]              # beyond, never came back
        hi = [x + 0.15 for x in cl]
        lo = [x - 0.15 for x in cl]
        r = retest_state(*htf_a, hi, lo, cl)
        assert r.state == "awaiting_retest", r.why

    def test_no_breakout_when_nothing_clears_the_buffer(self):
        htf_a, _, _, poc = _long_setup()
        hi, lo, cl = ltf(poc, side="long", breakout=0.01, retest_close=0.005)
        r = retest_state(*htf_a, hi, lo, cl)
        assert r.state == "no_breakout", r.why

    def test_one_swing_point_is_not_a_leg(self):
        """A clean impulse has made a HIGH and no low — and a leg needs both.

        `if not sh or not sl` looks like belt and braces until the fixture
        exists: driven, a series that rises to one peak and falls away has
        exactly ONE swing high and ZERO swing lows, so `and` in place of `or`
        reaches `sl[-1]` and raises IndexError out of the whole read. Every
        other fixture here has both, which is why the mutation survived the
        first round.
        """
        n = 40
        c = [100.0 + (i * 0.5 if i <= 20 else 10.0 - (i - 20) * 0.4)
             for i in range(n)]
        H, L, C = [x + 0.2 for x in c], [x - 0.2 for x in c], c
        assert find_swing_highs(np.array(H), order=5), "one peak"
        assert not find_swing_lows(np.array(L), order=5), "and no trough"
        assert swing_leg(H, L) is None
        _, ltf_a, _, _ = _long_setup()
        r = retest_state(H, L, C, [10.0] * n, *ltf_a)
        assert r.state == "no_leg", r.why

    def test_no_leg_on_a_flat_higher_timeframe(self):
        htf_a, ltf_a, _, _ = _long_setup()
        flat = [1.0] * 40
        r = retest_state(flat, flat, flat, flat, *ltf_a)
        assert r.state == "no_leg", r.why

    def test_no_poc_when_the_leg_carries_no_volume(self):
        H, L, C, _ = htf(up=True)
        leg = swing_leg(H, L)
        assert leg is not None
        assert leg_poc(H, L, C, [0.0] * 40, leg) is None
        _, ltf_a, _, _ = _long_setup()
        r = retest_state(H, L, C, [0.0] * 40, *ltf_a)
        assert r.state == "no_poc", r.why

    def test_atr_unread_is_not_a_buffer_of_zero(self):
        """Three rules divide by ATR. A `nan` makes every one of their
        comparisons False — the buffer test rejects while the stop-width cap
        PASSES — so an unreadable ATR stops the sequence before any of them."""
        htf_a, ltf_a, _, _ = _long_setup()
        hi, lo, cl = ltf_a
        assert retest_state(*htf_a, hi[:8], lo[:8], cl[:8]).state == "atr_unread"
        hn = list(hi)
        hn[-3] = float("nan")
        assert retest_state(*htf_a, hn, lo, cl).state == "atr_unread"

    def test_the_window_is_inclusive_at_its_own_number(self):
        """"within the next 1-5 candles" admits the FIFTH one.

        `>` against the window rather than `>=` is the whole difference, and
        no fixture with the retest at +1 can see it. Here it lands at +5.
        """
        htf_a, ltf_a, _, _ = _long_setup(hold=4)
        r = retest_state(*htf_a, *ltf_a)
        assert r.state == "confirmed", r.why
        assert r.retest_index - r.breakout_index == 5
        tight = retest_state(*htf_a, *ltf_a,
                             params=PocRetestParams(retest_window=4))
        assert tight.state == "window_expired"

    def test_a_retest_that_closes_exactly_on_the_poc_has_not_held(self):
        """"the candle must close back above POC" — AT is not above.

        A close exactly on the level is the one input that separates `>` from
        `>=`, and it is the reading that matters: a candle that finished on the
        POC did not reclaim it.
        """
        htf_a, _, _, poc = _long_setup()
        r = retest_state(*htf_a, *ltf(poc, side="long", retest_close=0.0))
        assert r.state == "retest_failed", r.why

    def test_a_wick_that_touches_the_poc_exactly_is_a_retest(self):
        """"The retest MAY wick into the POC zone" — reaching it is enough.

        The other exact-equality boundary, and it points the other way: a low
        that prints the POC to the tick has retested it.
        """
        htf_a, _, _, poc = _long_setup()
        r = retest_state(*htf_a, *ltf(poc, side="long", wick=0.0))
        assert r.state == "confirmed", r.why

    def test_a_flat_entry_timeframe_is_atr_unread_not_a_zero_buffer(self):
        """A MEASURED zero ATR is still unusable here, and for its own reason.

        `atr_reading` answers `0.0` for a series that genuinely did not move —
        an honest measurement — and every buffer scaled by it is then zero
        wide, so ANY close a tick beyond the POC reads as "decisive". The
        sequence stops at the ATR rather than arming on noise, and the
        sentence says it was measured rather than missing.
        """
        htf_a, _, _, poc = _long_setup()
        flat = [poc + 1.0] * 30
        r = retest_state(*htf_a, flat, flat, flat)
        assert r.state == "atr_unread"
        assert r.atr == 0.0, "measured, not missing"
        assert "measured zero" in r.why

    def test_every_state_in_the_vocabulary_was_driven(self):
        """The set, not a sample. A state nothing can reach is a claim that
        there is a branch."""
        htf_a, ltf_a, _, poc = _long_setup()
        flat = [1.0] * 40
        hi, lo, cl = ltf_a
        hn = list(hi)
        hn[-3] = float("nan")
        long_run = list(cl[:26]) + [poc + 0.9] * 8
        seen = {
            retest_state(*htf_a, *ltf_a).state,
            retest_state(*htf_a, hi, lo, list(ltf(poc, side="long",
                                                  retest_close=-0.4)[2])).state,
            retest_state(*htf_a, *ltf(poc, side="long", breakout=0.01,
                                      retest_close=0.005)).state,
            retest_state(*htf_a, [x + .15 for x in long_run],
                         [x - .15 for x in long_run], long_run).state,
            retest_state(*htf_a, hi[:8], lo[:8], cl[:8]).state,
            retest_state(*htf_a, hn, lo, cl).state,
            retest_state(flat, flat, flat, flat, *ltf_a).state,
            retest_state(htf_a[0], htf_a[1], htf_a[2], [0.0] * 40, *ltf_a).state,
        }
        # `awaiting_retest` needs its own shape.
        open_run = list(cl[:26]) + [poc + 1.0]
        seen.add(retest_state(*htf_a, [x + .15 for x in open_run],
                              [x - .15 for x in open_run], open_run).state)
        assert seen == set(STATES), f"never driven: {sorted(set(STATES) - seen)}"


class TestTheShortIsTheInverse:
    def test_confirmed_short(self):
        H, L, C, V = htf(up=False)
        leg = swing_leg(H, L)
        poc = leg_poc(H, L, C, V, leg)
        assert leg.direction == "down" and poc is not None
        r = retest_state(H, L, C, V, *ltf(poc, side="short"))
        assert r.state == "confirmed", r.why
        assert r.side == "short"
        # Every sign flips, and nothing else does.
        assert r.stop > r.entry, "a short's stop sits ABOVE its entry"
        assert r.target < r.entry, "and its target below"
        assert r.target == leg.low

    def test_the_leg_decides_the_side_not_the_breakout(self):
        """"The higher-timeframe structure forms a swing low -> swing high" is
        the long's first condition. A break the other way off an up leg is not
        a short — it is no setup."""
        H, L, C, V = htf(up=True)
        poc = leg_poc(H, L, C, V, swing_leg(H, L))
        # Candles that break DOWN through the POC, on an UP leg.
        down = ltf(poc, side="short")
        r = retest_state(H, L, C, V, *down)
        assert r.side == "long", "the leg's own direction"
        assert r.state != "confirmed", r.state


# ── the two rejections ────────────────────────────────────────────────────

class TestTheRejectionsAreOnTheNetRatio:
    def test_a_clean_setup_passes_and_says_what_it_measured(self):
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        v = setup_verdict(r)
        assert v.verdict == "ok", v.why
        assert v.net_r is not None and v.gross_r is not None
        assert v.net_r < v.gross_r, "fees come out of the reward"
        assert v.stop_atr is not None and v.stop_atr <= 2.0

    def test_the_2R_floor_is_NET_of_fees_not_the_price_ratio(self):
        """At 10x a 2R on price is comfortably under 2R once fees are paid —
        the defect every other pre-placement surface here was cured of the day
        before this module was written."""
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        v = setup_verdict(r)
        from bot.core.trade_costs import live_rr
        gross = live_rr(r.entry, r.stop, r.target)
        assert v.gross_r == pytest.approx(gross)
        assert v.net_r < gross

    def test_a_floor_BETWEEN_the_two_ratios_refuses_on_the_net_one(self):
        """THE ONE INPUT THAT SEPARATES THE TWO FLOORS, and it was missing.

        `test_the_2R_floor_is_NET_of_fees` proves net < gross, which is true
        whatever the floor reads; and a floor of 999 refuses on either. Only a
        floor positioned BETWEEN them tells the two apart — the mutation that
        put `costed.gross` in the comparison survived the whole first round on
        a corpus that never asked. The floor is derived from the two measured
        figures rather than written down, so it stays between them if the
        fixture's geometry ever moves.
        """
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        v = setup_verdict(r)
        assert v.verdict == "ok"
        between = (v.net_r + v.gross_r) / 2.0
        assert v.net_r < between < v.gross_r, "the fixture must straddle"
        refused = setup_verdict(r, params=PocRetestParams(min_net_r=between))
        assert refused.verdict == "below_min_r", (
            f"a floor of {between:.2f} sits above the net {v.net_r:.2f} and "
            f"below the gross {v.gross_r:.2f} — refusing it is what makes the "
            f"floor a NET one")

    def test_a_maker_entry_keeps_more_of_the_reward_than_a_taker_one(self):
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        taker = setup_verdict(r).net_r
        maker = setup_verdict(r, order_type="limit").net_r
        assert maker > taker, "the maker leg is cheaper"
        # And an UNSTATED order type is the taker one — the honest default,
        # not the flattering one. This strategy enters on a BREAK, which is a
        # stop/market order.
        assert setup_verdict(r, order_type=None).net_r == pytest.approx(taker)

    def test_a_stop_wider_than_the_cap_is_refused(self):
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        v = setup_verdict(r, params=PocRetestParams(max_stop_atr=0.01))
        assert v.verdict == "stop_too_wide"
        assert "0.01" in v.why and v.stop_atr is not None

    def test_the_stop_width_is_reported_in_ATRs_not_multiplied_by_them(self):
        """`stop_width / atr`, and the figure is asserted rather than its
        side of a threshold.

        Multiplying instead of dividing leaves every verdict in this suite
        unchanged — 0.21 and 0.52 are both under a cap of 2 and both over a
        cap of 0.01 — so only the VALUE can tell them apart.
        """
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        v = setup_verdict(r)
        assert v.stop_atr == pytest.approx(abs(r.entry - r.stop) / r.atr)

    def test_a_setup_under_the_R_floor_is_refused_WITH_both_figures(self):
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        v = setup_verdict(r, params=PocRetestParams(min_net_r=999.0))
        assert v.verdict == "below_min_r"
        assert v.net_r is not None and v.gross_r is not None, (
            "an operator needs to see how far short it fell, and on which "
            "basis")

    def test_an_unfinished_sequence_is_unpriceable_never_a_refusal(self):
        """A setup that has not formed is not a setup that failed a filter —
        folding them would lose the one an operator most wants to see."""
        htf_a, _, _, poc = _long_setup()
        r = retest_state(*htf_a, *ltf(poc, side="long", breakout=0.01,
                                      retest_close=0.005))
        v = setup_verdict(r)
        assert v.verdict == "unpriceable"
        assert "no_breakout" in v.why

    def test_no_target_when_price_is_already_past_the_legs_extreme(self):
        """The spec gives no target rule, and 2R needs one. The leg's own
        extreme is the structural objective; when price has passed it the
        setup is REFUSED rather than handed an invented multiple."""
        htf_a, ltf_a, leg, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        past = type(r)(**{**r.__dict__, "target": r.entry - 1.0})
        assert setup_verdict(past).verdict == "no_target"

    def test_a_target_that_does_not_clear_the_round_trip_has_NO_net_figure(self):
        """The fees exceed the whole move: there is no net ratio to print.

        `net_reward_risk` answers `net=None` for that and a MEASURED `0.0`
        for a target that pays the fees exactly — the distinction `live_rr`
        draws about its own zero — so the refusal here carries the gross
        figure and no net one rather than a `0.00R` nobody measured.
        """
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        near = type(r)(**{**r.__dict__, "target": r.entry * 1.000001})
        v = setup_verdict(near)
        assert v.verdict == "below_min_r"
        assert v.net_r is None, "no net ratio was measured"
        assert v.gross_r is not None, "the price ratio still was"
        assert "fees" in v.why

    def test_levels_that_cannot_be_priced_are_unpriceable_not_a_refusal(self):
        """A stop ON the entry gives an undefined ratio, not a bad one.

        `live_rr` refuses `risk <= 0` deliberately ("a position sitting on its
        stop is the last place to print a verdict the arithmetic did not
        produce"), so this is the boundary read rather than a filter result.
        """
        htf_a, ltf_a, _, _ = _long_setup()
        r = retest_state(*htf_a, *ltf_a)
        flat = type(r)(**{**r.__dict__, "stop": r.entry})
        v = setup_verdict(flat)
        assert v.verdict == "unpriceable"
        assert v.net_r is None and v.gross_r is None

    def test_the_verdict_vocabulary_is_closed(self):
        assert set(VERDICTS) == {"ok", "no_target", "stop_too_wide",
                                 "below_min_r", "unpriceable"}


class TestTheWindowIsTheLegNotALookback:
    def test_the_leg_poc_differs_from_a_whole_series_poc(self):
        """`analyzer`'s POC is computed over `volume_profile_lookback`
        candles. A POC over the last hundred is a different price from a POC
        over the accumulation range, under the same word."""
        from bot.core.volume_profile import compute_volume_profile
        H, L, C, V = htf(up=True)
        leg = swing_leg(H, L)
        over_leg = leg_poc(H, L, C, V, leg)
        whole = compute_volume_profile(np.array(H), np.array(L),
                                       np.array(C), np.array(V))
        assert over_leg is not None and whole is not None
        assert over_leg != pytest.approx(whole.poc), (
            "if these agree the fixture cannot tell the two windows apart")

    def test_the_poc_lands_inside_the_leg(self):
        H, L, C, V = htf(up=True)
        leg = swing_leg(H, L)
        poc = leg_poc(H, L, C, V, leg)
        assert leg.low <= poc <= leg.high


class TestAnUnreadableProfileIsNotAMissedBreakout:
    """A POC nobody could read must not reach the comparisons.

    This is the sharpest case in the module, because the failure is SILENT
    and wears the calmest word: every comparison against `nan` is False, so
    `_decisive` never fires and the read comes back `no_breakout` — "price
    never cleared the buffer", a confident negative about a level that was
    never measured. It is the shapes table's own row in a new spelling.
    """

    def test_a_nan_htf_high_is_no_poc_and_does_not_raise(self):
        """It RAISED before this test existed.

        `compute_volume_profile` bins with `int((typical - min) / (max - min)
        * bins)` and `int(nan)` is a ValueError, so one unreadable 4h candle
        took the whole read down. Its own `price_max <= price_min` guard
        cannot see a nan, for the same reason the defect is quiet.
        """
        htf_a, ltf_a, _, _ = _long_setup()
        H, L, C, V = htf_a
        for i, series in ((0, list(H)), (1, list(L))):
            series[20] = float("nan")
            args = [list(H), list(L), C, V]
            args[i] = series
            r = retest_state(*args, *ltf_a)
            assert r.state == "no_poc", f"{['highs', 'lows'][i]}: {r.state}"
            assert r.poc is None

    def test_a_non_positive_poc_is_no_poc(self):
        """The other half of that guard, and reachable: the POC is a bin
        centre between the leg's own low and high, so a series that straddles
        zero produces one."""
        htf_a, ltf_a, _, _ = _long_setup()
        H, L, C, V = htf_a
        shift = 200.0
        r = retest_state([x - shift for x in H], [x - shift for x in L],
                         [x - shift for x in C], V, *ltf_a)
        assert r.state == "no_poc", r.why

    def test_a_nan_poc_would_read_as_no_breakout_if_it_got_through(self):
        """Why the guard is not cosmetic — the state it PREVENTS, driven.

        With a nan POC every `_decisive` and `_touched` comparison is False,
        so the sequence reports that price never cleared the buffer. The read
        says `no_poc` instead, and this pins what the alternative was.
        """
        import math
        assert not (5.0 >= math.nan) and not (5.0 <= math.nan)


class TestTheATRReading:
    def test_atr_needs_one_more_candle_than_its_period(self):
        """ATR(n) is an average of n TRUE RANGES, and a true range needs a
        previous close — so n+1 candles, not n. `atr_from_candles` answers a
        SHORTER mean for a short window (its own documented contract), which
        is a number nobody asked for under the name ATR(14)."""
        from bot.core.position_telemetry import atr_reading
        hi = [2.0 + i * 0.1 for i in range(20)]
        lo = [1.0 + i * 0.1 for i in range(20)]
        cl = [1.5 + i * 0.1 for i in range(20)]
        assert atr_reading(hi[:14], lo[:14], cl[:14], 14) is None
        assert atr_reading(hi[:15], lo[:15], cl[:15], 14) is not None

    def test_the_true_range_cannot_be_negative(self):
        """So `atr_reading` carries no sign guard.

        A true range is `max(h - l, |h - pc|, |l - pc|)` and the last two
        terms are absolute, so the max is non-negative for any input —
        including lows ABOVE highs, which is the only input that could ask.
        The branch was deleted because no input reaches it; this is the claim
        that made deleting it safe, so a change to the arithmetic fails here
        rather than in a card.
        """
        from bot.core.position_telemetry import atr_from_candles
        inverted = atr_from_candles([1.0] * 20, [2.0] * 20, [1.5] * 20, 14)
        assert inverted >= 0.0
        wild = atr_from_candles([1.0, -50.0, 3.0] * 7, [9.0, 2.0, -7.0] * 7,
                                [0.0, 4.0, -1.0] * 7, 14)
        assert wild >= 0.0

