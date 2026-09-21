"""`max_margin_risk_pct` is a claim about the VENUE's leverage.

THE SIZING LEVERAGE CANCELS OUT. Driven, for a position whose margin the venue
locks as ``notional / L_venue``::

    loss_at_stop / venue_locked_margin = L_venue × sl_dist_pct / 100

The leverage an order is SIZED at decides the notional, and so the dollar loss,
and moves that ratio not at all. So the risk gate reducing leverage and writing
the reduction where only `_size_or_block` read it bounded nothing: sized at 2x
with the venue still at 5x and a 10% stop, the audit line read
``30.0% ≤ 30.0%`` while the real figure was 50% of the margin the venue had
locked; at a 20% stop it was 100% — liquidation AT the stop, from the control
written to prevent exactly that.

AND THE SENTENCE ASSERTED ITS OWN ``≤``. `new_margin` was computed and never
compared, so when ``max(min_leverage, …)`` floored the reduction ABOVE the cap
the line read ``SL 16.0% × 2x = 32.0% ≤ 30.0%`` and was filed in `passed`.

Both were latent rather than live: the reduction sits behind
`dynamic_leverage_enabled`, which defaults off and whose else-arm REFUSES the
trade. One env var armed them — the same one that turns on ATR de-leveraging,
which is inert because `update_atr` has no production caller.

What is driven here is the venue half and the leaf's arithmetic. The executor's
clamp is `tests/test_leverage_cap_honored.py`; the sizing path is
`tests/test_entry_gates_are_driven.py`; that all three ASK one reading is
`tests/test_dynamic_leverage_dedup.py`.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import textwrap
import types

import pytest

import bot.core.live_executor as le
from bot.core.leverage import (
    MARGIN_RISK_TOLERANCE_PCT,
    MIN_LEVERAGE_DEFAULT,
    RISK_CAP_ATTR,
    apply_margin_risk_cap,
    leverage_floor,
    margin_risk_verdict,
    set_margin_risk_cap,
)
from bot.core.live_executor import LiveExecutor
from tests.leverage_drive import drive_ensure_leverage, lev


async def _done(value):
    """An already-resolved awaitable, for stubbing an async seam."""
    return value


CAP = 30.0
FLOOR = 2
STD = 5


def _config_default(env_name: str) -> int:
    """The fallback `bot/config.py` itself declares for an env knob.

    `min_leverage: int = int(_env_float_bounded("MIN_LEVERAGE", 2, 1, 125))`
    — the second argument is the number the field falls back to, and it is
    the number this module's own fallback has to be. Read by AST so the
    assertion cannot be satisfied by the constant it is checking.
    """
    tree = ast.parse(pathlib.Path("bot/config.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.startswith("_env")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == env_name):
            return int(node.args[1].value)
    raise AssertionError(f"bot/config.py declares no default for {env_name}")


def _capped(n):
    idea = types.SimpleNamespace()
    set_margin_risk_cap(idea, n)
    return idea


def _verdict(sl_pct, *, leverage=STD, may_reduce=True, floor=FLOOR):
    return margin_risk_verdict(
        leverage=leverage, entry_price=100.0, stop_loss=100.0 - sl_pct,
        max_margin_risk_pct=CAP, min_leverage=floor, may_reduce=may_reduce)


class TestTheVerdictMeasuresWhatItPrints:
    @pytest.mark.parametrize("sl_pct,state,lev_out", [
        (6.0, "ok", 5),         # 6 × 5 = 30, at the cap
        (8.0, "reduced", 3),    # 8 × 5 = 40 over; 8 × 3 = 24 clears it
        (10.0, "reduced", 3),   # 10 × 3 = 30, exactly the cap
        (12.0, "reduced", 2),   # floored at 2; 12 × 2 = 24 still clears
        (16.0, "refused", None),  # 16 × 2 = 32 — the floor cannot clear it
        (20.0, "refused", None),  # 20 × 2 = 40
    ])
    def test_the_four_states(self, sl_pct, state, lev_out):
        v = _verdict(sl_pct)
        assert (v.state, v.leverage) == (state, lev_out), v

    def test_no_accepted_verdict_claims_a_cap_it_does_not_meet(self):
        """The ``≤`` is MEASURED, over every stop distance a trade can have.

        This is the assertion the old block could not make, because it printed
        the operator as a literal: `new_margin` was computed, the comparison
        never was, and the floored reduction was filed as a passed check.
        """
        for i in range(1, 500):
            sl = i / 10.0
            v = _verdict(sl)
            if v.state in ("ok", "reduced"):
                assert v.margin_risk_pct is not None
                assert v.margin_risk_pct <= CAP + MARGIN_RISK_TOLERANCE_PCT, \
                    f"SL {sl}% accepted at {v.margin_risk_pct}%: {v.sentence}"
                assert sl * (v.leverage or 0) == pytest.approx(v.margin_risk_pct)
            else:
                assert "≤" not in v.sentence, v.sentence

    def test_a_named_leverage_is_exactly_the_reduced_state(self):
        """The invariant `set_margin_risk_cap` relies on rather than restating.

        It takes an Optional so the risk gate does not have to narrow one on
        the money path; that is only safe because this leaf pairs the two. A
        `reduced` verdict always names a leverage and no other state ever
        does, over every stop distance a trade can have.
        """
        for i in range(1, 500):
            for may in (True, False):
                v = _verdict(i / 10.0, may_reduce=may)
                named = v.leverage is not None
                assert named == (v.state in ("ok", "reduced")), v
                if v.state == "refused":
                    assert v.leverage is None, v

    def test_recording_no_cap_writes_nothing(self):
        idea = types.SimpleNamespace()
        set_margin_risk_cap(idea, None)
        assert not hasattr(idea, RISK_CAP_ATTR)
        assert apply_margin_risk_cap(5, idea) == 5

    def test_a_refusal_names_the_floor_that_could_not_clear_it(self):
        v = _verdict(20.0)
        assert v.state == "refused"
        assert f"{FLOOR}x floor" in v.sentence, v.sentence
        assert "40.0%" in v.sentence, v.sentence

    def test_the_flag_off_arm_still_refuses_rather_than_resizing(self):
        """Unchanged behaviour: with dynamic leverage off, an over-cap idea
        fails the gate. That is what made both defects LATENT."""
        assert _verdict(6.0, may_reduce=False).state == "ok"
        over = _verdict(10.0, may_reduce=False)
        assert over.state == "refused" and over.leverage is None
        assert "floor" not in over.sentence, \
            "no reduction was attempted, so no floor is the reason"

    @pytest.mark.parametrize("leverage,entry", [(1, 100.0), (5, 0.0)])
    def test_nothing_to_measure_is_its_own_state(self, leverage, entry):
        v = margin_risk_verdict(leverage=leverage, entry_price=entry,
                                stop_loss=entry * 0.9, max_margin_risk_pct=CAP,
                                min_leverage=FLOOR, may_reduce=True)
        assert v.state == "not_applicable"
        assert v.leverage is None and v.margin_risk_pct is None

    def test_one_tolerance_decides_the_trigger_and_the_acceptance(self):
        """A reduction landing inside the slack the TRIGGER allows is accepted.

        At SL 15.1% and 5x the gate triggers (75.5% over a 30% cap), the floor
        bites at 2x, and 30.2% is over the cap but inside the 0.5pp the trigger
        itself allows. A second, tighter tolerance on the acceptance would
        refuse a trade the same slack had just let through — two answers about
        where the cap is, and the second decides whether money moves.
        """
        v = _verdict(15.1)
        assert v.state == "reduced" and v.leverage == FLOOR, v
        assert v.margin_risk_pct == pytest.approx(30.2, abs=0.01)
        assert v.margin_risk_pct > CAP


class TestTheClampIsReduceOnlyAndFailSafe:
    @pytest.mark.parametrize("cap,out", [
        (3, 3), (20, 5), (5, 5), (None, 5), (0, 5), ("x", 5), (-4, 1),
    ])
    def test_every_cap_value(self, cap, out):
        idea = types.SimpleNamespace()
        if cap is not None:
            setattr(idea, RISK_CAP_ATTR, cap)
        assert apply_margin_risk_cap(5, idea) == out

    def test_no_idea_at_all_changes_nothing(self):
        assert apply_margin_risk_cap(5, None) == 5

    def test_an_unreadable_target_fails_to_the_safest_number(self):
        assert apply_margin_risk_cap("x", _capped(3)) == 1


class TestOneFloor:
    def test_it_reads_the_configured_field(self):
        assert leverage_floor(types.SimpleNamespace(min_leverage=7)) == 7

    def test_an_absent_field_gets_ONE_default(self):
        """`live_executor` invented 1 and `risk_engine` invented 2, for a
        dataclass field that is never absent. Unreachable from production, and
        a fallback that cannot fire while disagreeing with its twin is a claim
        that there is a check.

        WHICH number is read from `bot/config.py`'s own declaration rather
        than from the constant under test: comparing to `MIN_LEVERAGE_DEFAULT`
        is a guard deriving its expectation from the thing it guards, so the
        two move together and nothing can see a change. The mutation round is
        what said so — setting it back to the executor's old 1 survived.
        """
        declared = _config_default("MIN_LEVERAGE")
        assert MIN_LEVERAGE_DEFAULT == declared, (MIN_LEVERAGE_DEFAULT, declared)
        assert leverage_floor(types.SimpleNamespace()) == declared

    def test_junk_does_not_raise_into_the_money_path(self):
        assert leverage_floor(types.SimpleNamespace(min_leverage="x")) == \
            MIN_LEVERAGE_DEFAULT
        assert leverage_floor(types.SimpleNamespace(min_leverage=0)) == 1

    def test_both_modules_ask_it(self):
        import bot.risk.risk_engine as re_mod
        assert le.leverage_floor is leverage_floor
        assert re_mod.leverage_floor is leverage_floor


class TestTheVenueIsPushedTheCappedNumber:
    """THE DRIVE. What leverage did the exchange actually receive?

    Nothing could ask this before: the harness stubbed
    `_compute_target_leverage` itself, so the clamp under test was replaced by
    the fixture. It is planted one layer down now.
    """

    def test_an_uncapped_idea_gets_the_standard_leverage(self, monkeypatch):
        out = drive_ensure_leverage([lev(STD)], target=STD, idea=None,
                                    positions=[], margin_mode="isolated", side="long",
                                    monkeypatch=monkeypatch)
        assert out.set_calls, "the venue was never asked to set leverage"
        assert {c[0] for c in out.set_calls} == {STD}
        assert not out.aborted, out.why

    def test_a_capped_idea_pushes_the_REDUCED_leverage(self, monkeypatch):
        out = drive_ensure_leverage([lev(3)], target=STD, idea=_capped(3),
                                    positions=[], margin_mode="isolated", side="long",
                                    monkeypatch=monkeypatch)
        assert {c[0] for c in out.set_calls} == {3}, out.set_calls
        assert not out.aborted, out.why

    def test_the_set_path_is_asked_with_the_idea(self, monkeypatch):
        idea = _capped(3)
        out = drive_ensure_leverage([lev(3)], target=STD, idea=idea,
                                    positions=[], margin_mode="isolated", side="long",
                                    monkeypatch=monkeypatch)
        assert out.target_asks, "the set path never asked for a target"
        assert all(a[1] is idea for a in out.target_asks), out.target_asks

    def test_the_read_back_verifies_the_number_that_will_be_used(self,
                                                                monkeypatch):
        """The venue answering the STANDARD leverage for a capped idea is now
        a mismatch — which is the point. Before, that was the healthy case."""
        out = drive_ensure_leverage([lev(STD), lev(STD)], target=STD,
                                    idea=_capped(3), fail_open=False,
                                    positions=[], margin_mode="isolated",
                                    side="long", monkeypatch=monkeypatch)
        assert out.aborted, "a venue at 5x under a 3x cap must not be confirmed"
        # NAMED, because the drive aborts on a stub short a seam with exactly
        # the same flag — a kill for a reason unrelated to the rule is how a
        # guard reports coverage it does not have.
        assert "leverage" in out.why.lower(), out.why
        assert "fetch_positions" not in out.why, out.why

    def test_the_bitget_entry_hands_the_generic_path_the_idea(self, tmp_path,
                                                             monkeypatch):
        """`_ensure_leverage` is the door; a NON-Bitget venue leaves it at the
        second line for `_ensure_leverage_generic`.

        The mutation round found this: `drive_ensure_leverage` builds a venue
        whose id is `bitget`, and the generic test below calls the generic
        method DIRECTLY — so dropping the idea from the hand-off between them
        changed no verdict anywhere. A seam driven from both ends and never
        across is a seam nothing measures.
        """
        pushed: list = []

        class _Ex:
            def market(self, sym):
                return {}

            async def set_margin_mode(self, mode, sym):
                return None

            async def set_leverage(self, leverage, sym, params=None):
                pushed.append(leverage)

            async def fetch_leverage(self, sym, params=None):
                return {"leverage": 2}

            async def fetch_positions(self, syms, params=None):
                return []

        # The REAL venue object, not a hand-written stand-in: the first draft
        # listed `id`, `margin_mode_call_first`, `swap_symbol`,
        # `futures_params` and `leverage_params`, and the verification block
        # reached for `supports_hedge_mode`. A stand-in that must remember
        # each attribute is one that will forget the next.
        from bot.core.venues import get_venue

        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._venue = get_venue("hyperliquid")
        assert ex._venue.id != "bitget", "this drive needs the generic branch"
        ex._standard_leverage = lambda symbol: STD
        monkeypatch.setattr(ex, "_get_exchange", lambda: _done(_Ex()))
        asyncio.run(ex._ensure_leverage("BTC/USDT", "long", _capped(2)))
        assert pushed and set(pushed) == {2}, pushed

    def test_the_generic_venue_path_pushes_it_too(self, tmp_path):
        """Non-Bitget venues take `_ensure_leverage_generic`, which had its own
        `_compute_target_leverage(symbol)` call. A fix that reached only the
        Bitget topology would be invisible from every Bitget drive."""
        pushed: list = []

        class _Ex:
            def market(self, sym):
                return {}

            async def set_margin_mode(self, mode, sym):
                return None

            async def set_leverage(self, leverage, sym, params=None):
                pushed.append(leverage)

            async def fetch_leverage(self, sym, params=None):
                return {}

            async def fetch_positions(self, syms, params=None):
                return []

        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._standard_leverage = lambda symbol: STD
        asyncio.run(ex._ensure_leverage_generic(_Ex(), "BTC/USDT", _capped(2)))
        assert pushed and set(pushed) == {2}, pushed

        pushed.clear()
        asyncio.run(ex._ensure_leverage_generic(_Ex(), "BTC/USDT", None))
        assert pushed and set(pushed) == {STD}, pushed


class TestTheSetAndTheSizeAreOneNumber:
    def test_one_idea_one_leverage(self, monkeypatch, tmp_path):
        """The whole claim, end to end: for the SAME idea, the number pushed to
        the venue is the number the order is sized with."""
        idea = _capped(3)
        out = drive_ensure_leverage([lev(3)], target=STD, idea=idea,
                                    positions=[], margin_mode="isolated", side="long",
                                    monkeypatch=monkeypatch)
        set_lev = {c[0] for c in out.set_calls}

        sizer = LiveExecutor(state_dir=str(tmp_path))
        sizer._standard_leverage = lambda symbol: STD
        sized = sizer._compute_target_leverage("BTC/USDT", idea)

        assert set_lev == {sized} == {3}, (set_lev, sized)

    def test_the_caller_hands_the_set_path_the_idea_it_already_holds(self):
        """`execute()` has held the idea all along; it simply never passed it.

        Read as a SHAPE: the argument is whatever name `execute` binds the
        idea to, not a spelling written down here.
        """
        src = textwrap.dedent(inspect.getsource(LiveExecutor.execute))
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "execute")
        idea_param = fn.args.args[1].arg

        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_ensure_leverage"]
        assert len(calls) == 1, [ast.unparse(c) for c in calls]
        handed = [ast.unparse(a) for a in calls[0].args]
        assert idea_param in handed, handed

    def test_the_venue_is_set_before_the_order_is_sized(self):
        """Which is why passing the idea costs nothing: the risk gate wrote its
        cap before `execute` was called, and the SET runs first.

        Asked as an ORDER of two calls rather than as two line numbers, which
        is the `path:line` rot this repo keeps recording.
        """
        src = textwrap.dedent(inspect.getsource(LiveExecutor.execute))
        tree = ast.parse(src)
        at = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in ("_ensure_leverage", "_size_or_block"):
                    at.setdefault(n.func.attr, []).append(n.lineno)
        assert sorted(at) == ["_ensure_leverage", "_size_or_block"], at
        assert len(at["_ensure_leverage"]) == 1 and len(at["_size_or_block"]) == 1
        assert at["_ensure_leverage"][0] < at["_size_or_block"][0], at


class TestTheFloorIsOneReadingInTheExecutorToo:
    def test_a_config_with_no_floor_gets_the_shared_default(self, monkeypatch):
        """Unreachable from production — `min_leverage` is a dataclass field.

        Driven anyway, because it is the only input that separates the shared
        reading from the private `getattr(cfg, "min_leverage", 1)` this method
        used to carry. With a real config the two agree, so a round could not
        tell them apart and the disagreement would stay latent.
        """
        monkeypatch.setattr(le, "CONFIG", types.SimpleNamespace(
            exchange=types.SimpleNamespace(default_leverage=3,
                                           dynamic_leverage_enabled=True)))
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._last_atr_pct = {"BTC": 0.05}     # high vol → 3 // 2 = 1
        ex._user_leverage_pref = None
        ex._risk_engine = None
        # Floored at the SHARED default of 2, not at the 1 this method invented.
        assert ex._compute_target_leverage("BTC/USDT") == MIN_LEVERAGE_DEFAULT


class TestTheGateRecordsWhatTheExecutorReads:
    """The producer end, DRIVEN through `RiskEngine.evaluate`.

    Every other claim here is about the leaf or the executor. This is the one
    that says the gate CALLS it — and a byte-identical inline copy of the
    branch agrees with every fixture, so the chain is driven end to end rather
    than asserted about the source.
    """

    @staticmethod
    def _engine(tmp_path):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine as RE
        return RE(PortfolioTracker(initial_balance=10_000.0),
                  state_file=str(tmp_path / "risk_state.json"))

    @staticmethod
    def _idea(stop):
        from bot.utils.models import Direction, TradeIdea
        return TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=stop, take_profit=130.0,
                         confidence=0.9, reasoning="margin-risk drive",
                         source="test")

    @staticmethod
    def _cfg(monkeypatch, *, dynamic):
        """Override ONLY `exchange`, by delegation.

        A hand-written `SimpleNamespace` stand-in for the whole config is the
        drift this repo keeps recording: the first draft of this fixture
        listed `exchange`, `risk` and `is_live`, and `_evaluate_locked` reached
        for `strategy_types` — a fixture that must remember each attribute is
        one that will forget the next. Everything not named here falls through
        to the real object.
        """
        import dataclasses

        import bot.risk.risk_engine as re_mod
        base = re_mod.CONFIG
        exch = dataclasses.replace(base.exchange, default_leverage=STD,
                                   min_leverage=FLOOR,
                                   dynamic_leverage_enabled=dynamic)

        class _Cfg:
            exchange = exch

            def __getattr__(self, name):
                return getattr(base, name)

        monkeypatch.setattr(re_mod, "CONFIG", _Cfg())

    def _margin_lines(self, check):
        return [r for r in list(check.checks_passed) + list(check.checks_failed)
                if r.startswith("MARGIN_RISK")]

    def test_a_reduction_lands_where_the_executor_reads_it(self, tmp_path,
                                                           monkeypatch):
        self._cfg(monkeypatch, dynamic=True)
        idea = self._idea(92.0)          # 8% stop: 8 × 5 = 40 over a 30 cap
        check = self._engine(tmp_path).evaluate(idea, atr=1.0)

        line = self._margin_lines(check)
        assert line and "reduced leverage" in line[0], line
        assert line[0] in check.checks_passed

        ex = LiveExecutor.__new__(LiveExecutor)
        ex._standard_leverage = lambda symbol: STD
        assert ex._compute_target_leverage("BTC/USDT", idea) == 3, \
            "the gate's reduction must be the number the executor uses"

    def test_a_stop_no_permitted_leverage_clears_is_refused(self, tmp_path,
                                                            monkeypatch):
        self._cfg(monkeypatch, dynamic=True)
        idea = self._idea(80.0)          # 20% stop: even 2x is 40% > 30%
        check = self._engine(tmp_path).evaluate(idea, atr=1.0)

        line = self._margin_lines(check)
        assert line and line[0] in check.checks_failed, (line, check.checks_passed)
        assert "floor" in line[0], line[0]
        from bot.utils.models import RiskVerdict
        assert check.verdict == RiskVerdict.REJECTED, check.verdict
        assert getattr(idea, RISK_CAP_ATTR, None) is None, \
            "a refused idea must carry no cap for the executor to honour"

    def test_the_flag_off_arm_is_unchanged(self, tmp_path, monkeypatch):
        self._cfg(monkeypatch, dynamic=False)
        idea = self._idea(92.0)
        check = self._engine(tmp_path).evaluate(idea, atr=1.0)
        line = self._margin_lines(check)
        assert line and line[0] in check.checks_failed, line
        assert "reduced" not in line[0], line[0]
        assert getattr(idea, RISK_CAP_ATTR, None) is None

    def test_a_stop_inside_the_cap_is_left_alone(self, tmp_path, monkeypatch):
        self._cfg(monkeypatch, dynamic=True)
        idea = self._idea(95.0)          # 5% stop: 5 × 5 = 25, under the cap
        check = self._engine(tmp_path).evaluate(idea, atr=1.0)
        line = self._margin_lines(check)
        assert line and line[0] in check.checks_passed, line
        assert "OK" in line[0], line[0]
        assert getattr(idea, RISK_CAP_ATTR, None) is None
