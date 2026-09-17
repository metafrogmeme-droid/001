"""A fee rate is per leg, and the leg is maker or taker.

Live on 2026-09-17: an ARBUSDT LONG limit order, $33.84 of margin at 5x, stop
0.1846% below entry and target 0.5785% above. The resting card said, four lines
apart, ``R:R at fill: 3.1`` and ``Est. fees: $0.2030 (entry + exit)`` — and
neither number was right, in opposite directions. The ratio had no fee term at
all; the fee charged the MAKER entry at the taker rate, where the venue takes
$0.1359. Net of what is really charged, that 3.13 is **1.88**.

The rule had four names and seven spellings: ``commission_pct`` (every card),
``taker_fee_pct``/``maker_fee_pct`` (the executor), and
``maker_fee_pct if is_limit_entry else taker_fee_pct`` written out six times in
``live_executor.py`` plus once more in prose at
``pos.order_type = "limit"  # limit fill = maker fee rate``. ``trade_costs`` is
the one rule and this is its guard: the walk is DRIVEN (patch the leaf, read
what each surface says), because a byte-identical copy agrees with every
fixture and diverges on the first edit to either.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from bot.core import trade_costs as tc
from bot.core.trade_copilot import human_readable, review
from bot.skills.trading_commands import pending_order_card, position_fee_estimate

# ── the live ticket, as its own numbers ──────────────────────────────────────
ENTRY = 0.4530
STOP_FRAC = 0.001846
TGT_FRAC = 0.005785
SL = ENTRY * (1 - STOP_FRAC)
TP = ENTRY * (1 + TGT_FRAC)
MARGIN = 33.84
NOTIONAL = 169.20
QTY = NOTIONAL / ENTRY

ROW = dict(pair="ARBUSDT", direction="LONG", entry=ENTRY, current=0.4525,
           sl=SL, tp=TP, size_usd=MARGIN, notional_usd=NOTIONAL, leverage=5,
           trade_id="T-1", hold_hours=0.08, quantity=QTY, order_type="limit",
           sl_order="exchange", tp_order="exchange", strategy_type="scalp")


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


class TestTheLegRule:
    def test_a_resting_limit_entry_is_maker(self):
        assert tc.entry_liquidity("limit") == tc.MAKER
        assert tc.entry_liquidity("LIMIT") == tc.MAKER
        assert tc.entry_liquidity(" limit ") == tc.MAKER

    @pytest.mark.parametrize("ot", ["market", "", None, "post_only", 0, object()])
    def test_anything_else_is_taker(self, ot):
        """The venue's default, and the one direction that cannot flatter.

        It is also what every reader already did — `getattr(pos,
        'order_type', '')` compared against `'limit'` — so this is a stated
        choice rather than a fallback nobody chose.
        """
        assert tc.entry_liquidity(ot) == tc.TAKER

    def test_both_live_exits_are_taker(self):
        """`_place_sl_tp` places the take-profit as `type="market"` with
        trigger params, exactly like the stop."""
        assert tc.EXIT_LIQUIDITY == tc.TAKER
        assert tc.exit_rate_pct() == tc.leg_rate_pct(tc.TAKER)

    def test_a_word_it_cannot_place_raises(self):
        """A quiet fallback here is the defect this module exists to remove:
        a rate nobody chose, printed with the authority of a quoted one."""
        with pytest.raises(ValueError):
            tc.leg_rate_pct("post-only")

    def test_the_maker_leg_really_is_cheaper_or_the_rest_proves_nothing(self):
        assert tc.leg_rate_pct(tc.MAKER) < tc.leg_rate_pct(tc.TAKER)
        assert tc.round_trip_pct("limit") < tc.round_trip_pct("market")

    def test_a_hedge_pays_four_taker_legs(self):
        assert tc.taker_legs_pct(4) == pytest.approx(4 * tc.leg_rate_pct(tc.TAKER))
        with pytest.raises(ValueError):
            tc.taker_legs_pct(-1)

    def test_the_arb_reality_check_is_derived_not_written_down(self):
        """It was a hand-written `0.24` under a comment explaining it as
        "4 taker legs @ ~0.06%" — a derivation stated in prose and computed by
        nobody, so it stopped following TAKER_FEE_PCT the moment anybody set
        one."""
        from bot.core.arb_tracker import ROUND_TRIP_FEE_PCT
        assert ROUND_TRIP_FEE_PCT == pytest.approx(tc.taker_legs_pct(4))

    def test_the_dollar_helper_spells_the_division_once(self):
        assert tc.fee_usd(1000.0, 0.06) == pytest.approx(0.6)


class TestTheRatioNetOfIt:
    def test_the_gross_is_live_rrs_and_is_read_from_it(self, monkeypatch):
        """Not restated. A second copy of the gross ratio beside the net one
        is two answers about the same two distances."""
        monkeypatch.setattr(tc, "live_rr", lambda *a, **k: 42.0)
        got = tc.net_reward_risk(ENTRY, SL, TP, order_type="limit")
        assert got is not None and got.gross == 42.0

    def test_the_live_ticket(self):
        got = tc.net_reward_risk(ENTRY, SL, TP, order_type="limit")
        assert got is not None
        assert got.gross == pytest.approx(3.13, abs=0.01)
        assert got.net == pytest.approx(1.88, abs=0.01)
        assert got.cost_over_stop == pytest.approx(0.43, abs=0.01)
        assert got.entry_rate_pct == tc.leg_rate_pct(tc.MAKER)
        assert got.exit_rate_pct == tc.leg_rate_pct(tc.TAKER)
        assert not got.fee_losing

    def test_the_cost_is_the_RISK_leg_and_not_the_reward_one(self):
        """`cost_pct` is the round trip the STOP has to cover.

        Charging the target's exit instead is invisible on any sub-percent
        geometry -- on the live ARB ticket both print 0.08% at two decimals --
        so the fixture is a target far enough away that the two legs' notionals
        genuinely differ. The mutation round is what said the corpus had no
        such row.
        """
        got = tc.net_reward_risk(100.0, 99.0, 200.0, order_type="limit")
        assert got is not None
        entry_fee = 100.0 * tc.leg_rate_pct(tc.MAKER) / 100.0
        stop_fee = 99.0 * tc.leg_rate_pct(tc.TAKER) / 100.0
        target_fee = 200.0 * tc.leg_rate_pct(tc.TAKER) / 100.0
        assert got.cost_pct == pytest.approx(entry_fee + stop_fee, rel=1e-9)
        assert got.cost_pct != pytest.approx(entry_fee + target_fee, rel=1e-6)

    def test_the_same_ticket_as_a_market_entry_is_worse(self):
        """Same geometry, a taker entry: 1.50. That is the difference the
        card's own `Est. fees` line implied and its ratio did not carry."""
        got = tc.net_reward_risk(ENTRY, SL, TP, order_type="market")
        assert got is not None and got.net == pytest.approx(1.50, abs=0.01)

    @pytest.mark.parametrize("bad", [
        {"stop": 0.0}, {"target": 0.0}, {"entry": 0.0},
        {"stop": None}, {"target": float("nan")}, {"entry": float("inf")},
        {"stop": ENTRY},          # the mark is AT the stop: undefined, not zero
    ])
    def test_an_unreadable_leg_answers_none(self, bad):
        kw = {"entry": ENTRY, "stop": SL, "target": TP}
        kw.update(bad)
        assert tc.net_reward_risk(kw["entry"], kw["stop"], kw["target"]) is None

    def test_a_target_inside_the_round_trip_is_fee_losing_not_a_ratio(self):
        got = tc.net_reward_risk(100.0, 99.0, 100.05, order_type="limit")
        assert got is not None
        assert got.fee_losing and got.net is None
        assert got.gross > 0          # the PRICE ratio is real; the net is not

    def test_a_target_that_exactly_pays_the_fees_is_a_measured_zero(self, monkeypatch):
        """`live_rr`'s own rule: 0.0 is returned only where it is measured.

        Driven on a ZERO-rate venue, because the exact break-even target is a
        fixed point (`t = e(1+entry)/(1-exit)`) that binary floating point
        lands a hair either side of — so solving for it with the real rates
        tests the arithmetic of the solve rather than the branch. With no fees
        the reward IS the price move, and a target at the entry is the
        measured zero `live_rr` already documents.
        """
        monkeypatch.setattr(tc, "entry_rate_pct", lambda ot: 0.0)
        monkeypatch.setattr(tc, "exit_rate_pct", lambda: 0.0)
        got = tc.net_reward_risk(100.0, 99.0, 100.0)
        assert got is not None
        assert got.gross == 0.0 and got.net == 0.0
        assert not got.fee_losing

    def test_and_the_branch_point_is_driven_from_both_sides(self):
        """A hair above the break-even target is a ratio; a hair below is
        `fee_losing`. Exact equality is not reachable in floats, so the claim
        the `>= 0` makes is checked where it can be."""
        e, sl = 100.0, 99.0
        xr = tc.exit_rate_pct() / 100.0
        t0 = (e + e * tc.entry_rate_pct("limit") / 100.0) / (1 - xr)
        above = tc.net_reward_risk(e, sl, t0 * (1 + 1e-6), order_type="limit")
        below = tc.net_reward_risk(e, sl, t0 * (1 - 1e-6), order_type="limit")
        assert above is not None and above.net is not None and above.net > 0
        assert below is not None and below.fee_losing


class TestTheCoPilotBarIsOnTheNetRatio:
    TICKET = {"direction": "LONG", "symbol": "ARB/USDT", "entry": ENTRY,
              "sl": SL, "tp": TP, "margin": MARGIN, "order_type": "limit"}

    def test_the_two_sentences_no_longer_contradict_each_other(self):
        """It said BOTH of these, four lines apart, and the first is the
        reason the second was false::

            * Stop is only 0.18% away -- likely to be wicked out by noise.
            * Strong reward:risk (3.13).
        """
        out = human_readable(review(self.TICKET, equity_usd=500.0,
                                    engine_bias="long", existing_exposure="flat"))
        assert "Stop is only 0.18% away" in out
        assert "Strong reward:risk" not in out
        assert "1.88 after fees" in out and "3.13 on price" in out

    def test_a_market_entry_on_the_same_ticket_reads_differently(self):
        rev = review(dict(self.TICKET, order_type="market"))
        assert rev["rr_net"] == pytest.approx(1.50, abs=0.01)
        assert rev["rr"] == pytest.approx(3.13, abs=0.01)

    def test_a_gross_ratio_over_the_bar_can_still_flag(self):
        """The whole point. 2.0 on price, under 1.5 after fees on a stop this
        tight — the bar is applied to the ratio the caller will live with."""
        e = 100.0
        t = {"direction": "LONG", "symbol": "X/USDT", "entry": e,
             "sl": e * (1 - 0.0010), "tp": e * (1 + 0.0020),
             "order_type": "market"}
        rev = review(t)
        assert rev["rr"] >= 1.5              # clears the bar on price
        assert rev["rr_net"] is not None and rev["rr_net"] < 1.5
        assert rev["checks"]["reward_risk"] == "flag"
        assert any("after fees" in f["msg"] for f in rev["flags"])

    def test_a_fee_losing_target_says_hitting_it_is_a_loss(self):
        rev = review({"direction": "LONG", "symbol": "X/USDT", "entry": 100.0,
                      "sl": 99.0, "tp": 100.05, "order_type": "limit"})
        assert rev["rr_net"] is None
        assert rev["checks"]["reward_risk"] == "flag"
        msg = " ".join(f["msg"] for f in rev["flags"])
        assert "does not clear the round trip" in msg
        assert "Hitting the target is a loss" in msg

    def test_a_stop_of_zero_is_invalid_not_a_hundred_percent_stop(self):
        """`_f(0.0)` is `0.0` and `sl < e < tp` is perfectly true of a long
        with no stop, so this reported `R:R 0.01 · stop 100%` -- two
        measurements about a stop nobody stated. `parse_manual_trade` refuses
        a non-positive price; the Review-button endpoint reads entry/sl/tp
        straight off the request body and does not."""
        rev = review({"direction": "LONG", "symbol": "X/USDT", "entry": ENTRY,
                      "sl": 0.0, "tp": TP})
        assert rev["verdict"] == "invalid"
        assert rev["stop_pct"] is None
        assert "no stop on record" in rev["flags"][0]["msg"]
        assert "100" not in rev["flags"][0]["msg"]

    def test_the_wrong_side_still_says_wrong_side(self):
        rev = review({"direction": "LONG", "symbol": "X/USDT",
                      "entry": 100.0, "sl": 101.0, "tp": 102.0})
        assert rev["verdict"] == "invalid"
        assert "wrong side of entry" in rev["flags"][0]["msg"]

    def test_the_levels_row_has_one_producer(self):
        """Both renderers assembled it from four raw fields -- one in Python,
        one byte for byte in `copilot-review-model.js`. Mutate the stamped row
        and the Python renderer prints the mutation, because it reads rather
        than rebuilds."""
        rev = review(self.TICKET)
        rev["levels_line"] = "PLANTED ROW"
        assert "PLANTED ROW" in human_readable(rev)
        assert "after fees" not in human_readable(rev)


class TestTheRestingCard:
    def test_both_ratios_and_the_fee_that_separates_them(self):
        card = plain(pending_order_card(ROW))
        assert "R:R at fill: 1.88 after fees  (3.13 on price)" in card
        assert "Est. fees: $0.1354 (0.02% in + 0.06% out)" in card
        assert "Fees are 43% of the distance to the stop." in card

    def test_the_taker_both_legs_answer_is_what_it_used_to_print(self):
        """$0.2030 was the card's figure and $0.1359 is the venue's — a 49%
        overstatement on the one number this row exists to quote."""
        card = plain(pending_order_card({k: v for k, v in ROW.items()
                                         if k != "order_type"}))
        assert "$0.2030" in card
        assert "1.50 after fees" in card

    def test_a_mark_through_the_stop_is_not_a_green_tick(self):
        card = plain(pending_order_card(dict(ROW, current=0.4515)))
        assert "✅" not in card
        assert "stopped out on arrival" in card

    def test_a_mark_short_of_the_limit_still_shows_the_arrow(self):
        card = plain(pending_order_card(dict(ROW, current=0.4600)))
        assert "⬇️" in card
        assert "stopped out on arrival" not in card

    def test_a_ready_order_with_its_stop_intact_is_a_green_tick(self):
        card = plain(pending_order_card(ROW))
        assert "✅" in card
        assert "stopped out on arrival" not in card

    def test_a_short_through_its_stop_is_read_the_other_way(self):
        card = plain(pending_order_card(
            dict(ROW, direction="SHORT", sl=TP, tp=SL, current=0.4560)))
        assert "stopped out on arrival" in card

    def test_an_unread_mark_does_not_delete_the_listing(self):
        """The live row builder writes `current: None` whenever the mark could
        not be read, and `None > 0` RAISED — inside a loop with no
        try/except, which takes the whole PENDING ORDERS section with it."""
        card = plain(pending_order_card(dict(ROW, current=None)))
        assert "current price unread" in card
        assert "ARBUSDT LONG" in card
        assert "% to fill" not in card

    @pytest.mark.parametrize("junk", [float("nan"), float("inf"), -0.4525, 0.0])
    def test_a_mark_that_is_not_a_price_is_unread(self, junk):
        """`price_on_record`'s own list, on the one field that decides the
        glyph. `float(current or 0) or None` answers the same thing for
        `None` and for a real price -- it diverges on exactly these, and the
        corpus had none of them until the mutation round asked."""
        card = plain(pending_order_card(dict(ROW, current=junk)))
        assert "current price unread" in card
        assert "% to fill" not in card

    def test_a_fee_losing_order_says_so_rather_than_quoting_a_ratio(self):
        card = plain(pending_order_card(
            dict(ROW, entry=100.0, sl=99.0, tp=100.05, current=99.5,
                 notional_usd=1000.0, quantity=10.0)))
        assert "R:R at fill: — after fees" in card
        assert "hitting the target is a loss" in card


class TestTheFilledRowPaysItsOwnLegs:
    FROW = dict(entry=ENTRY, current=0.4525, quantity=QTY,
                notional_usd=NOTIONAL, hold_hours=2.0)

    def test_a_limit_entry_costs_the_maker_rate(self):
        got = position_fee_estimate(dict(self.FROW, order_type="limit"))
        assert got["entry_fee"] == pytest.approx(
            tc.fee_usd(NOTIONAL, tc.leg_rate_pct(tc.MAKER)), rel=1e-6)

    def test_an_unstated_entry_costs_the_taker_rate(self):
        got = position_fee_estimate(self.FROW)
        assert got["entry_fee"] == pytest.approx(
            tc.fee_usd(NOTIONAL, tc.leg_rate_pct(tc.TAKER)), rel=1e-6)

    def test_and_the_two_really_differ(self):
        limit = position_fee_estimate(dict(self.FROW, order_type="limit"))
        taker = position_fee_estimate(self.FROW)
        assert limit["total_fees"] < taker["total_fees"]

    def test_the_rate_is_no_longer_a_parameter(self):
        """It was `position_fee_estimate(pos, comm_pct)` and every caller
        passed the row's `comm_pct`, which every row writer wrote as
        `CONFIG.risk.commission_pct`. The field then had ZERO readers."""
        import inspect
        sig = inspect.signature(position_fee_estimate)
        assert list(sig.parameters) == ["pos"]


class TestOneRule:
    #: Who may still name a raw rate, and why. Everything else asks
    #: `trade_costs`. A module added here without a reason is the
    #: `/setllm` ten-of-eleven shape waiting to happen.
    ALLOWED = {
        "bot/core/trade_costs.py":
            "the rule itself",
        "bot/backtest/engine.py":
            "the backtest's own modelled rate (BacktestConfig.commission_pct), "
            "and maker_fee_pct behind maker_take_profit_enabled — a SIMULATION "
            "knob whose own config comment says it does not alter live placement",
        "bot/backtest/parity.py":
            "the MODELLED rate, whose whole subject is comparing it to realized",
        "bot/backtest/runner.py":
            "the CLI default for --commission",
        "bot/risk/portfolio.py":
            "the paper book's own fee model, injected by the backtest so the "
            "simulated fee matches the run being compared",
        "bot/core/proactive_monitor.py": "a parity_summary caller: the modelled rate",
        "bot/core/web_reports.py": "a parity_summary caller: the modelled rate",
        "bot/skills/engine_ops_commands.py": "a parity_summary caller: the modelled rate",
    }
    RATES = {"taker_fee_pct", "maker_fee_pct", "commission_pct"}

    def _namers(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in sorted(pathlib.Path("bot").rglob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            n = sum(1 for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute) and node.attr in self.RATES)
            if n:
                out[p.as_posix()] = n
        return out

    def test_nothing_else_names_a_raw_rate(self):
        extra = sorted(set(self._namers()) - set(self.ALLOWED) - {"bot/config.py"})
        assert not extra, (
            "these name a fee rate directly instead of asking trade_costs: "
            + ", ".join(extra))

    def test_every_exemption_is_still_using_one(self):
        """The `known_failures.txt` rule: an entry that stops applying is a
        hard failure, so a stale exemption cannot hide the next copy."""
        namers = self._namers()
        stale = sorted(k for k in self.ALLOWED if k not in namers)
        assert not stale, f"exemptions nothing uses any more: {stale}"

    def test_the_executor_asks_rather_than_spelling_the_rule(self):
        """Six copies of `maker_fee_pct if is_limit_entry else taker_fee_pct`
        lived in one file, plus a seventh in prose."""
        src = pathlib.Path("bot/core/live_executor.py").read_text(encoding="utf-8")
        assert "maker_fee_pct if" not in src
        assert src.count("entry_rate_pct(") >= 6
        assert src.count("exit_rate_pct()") >= 3

    def test_the_fee_aware_entry_gate_reads_it_too(self):
        """Its bar was two TAKER legs, so a limit-entry idea had to clear
        0.04 percentage points more than it really pays — on a gate whose
        whole job is a fee comparison. The SLIPPAGE half is untouched:
        slippage is a model, and `trade_costs` charges only what the venue
        charges."""
        src = pathlib.Path("bot/risk/risk_engine.py").read_text(encoding="utf-8")
        assert "round_trip_pct(" in src
        assert "fee_aware_slippage_pct" in src

    def test_the_comm_pct_wire_field_is_gone(self):
        """A field written by three producers and read, once the two fee
        sites moved onto the leg rule, by nobody."""
        for mod in ("bot/skills/trading_commands.py",
                    "bot/formatters/orphan_position.py"):
            assert '"comm_pct"' not in pathlib.Path(mod).read_text(encoding="utf-8")
