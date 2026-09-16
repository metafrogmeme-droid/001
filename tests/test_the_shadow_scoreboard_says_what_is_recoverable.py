"""
The shadow scoreboard's total is an ATTRIBUTION, and the card spent it as a
measurement.

From the live nightly card of 2026-09-16:

    Shadow book: MTF_ALIGNMENT is the costliest gate (net +86.2R over 73
    blocked trades · avg +1.181R/trade, 95% lower bound +0.68R/trade)

The interval is real and was the previous slice's fix. The quantity it bounds
is not a measurement of the gate. `RiskEngine.evaluate` fails no check early —
its verdict is ``APPROVED if len(failed) == 0``, so every failed check
accumulates — and `ShadowBook.record_rejection` charges the whole outcome to
``gates[0]``, saying so in its own docstring: *"the FIRST entry is the primary
gate charged with the outcome"*. A documented simplification in the WRITER, and
an undocumented claim on the card, which ends by inviting the operator to
loosen that gate on a live account.

A trade that also failed CONFIDENCE is not placed by loosening MTF_ALIGNMENT —
CONFIDENCE still refuses it. The list that says so has been on every row since
`record_rejection` was written and was read by NOTHING: grepped across the
tree, `tr["gates"]` had no reader at all.

Measured on this box's live ledger (data/shadow_book.json, 403 rows) the shape
is real rather than theoretical: one row carries
``['CONFIDENCE: 0.57 < 0.6 minimum', 'VOLATILITY: ATR 7.66% > 7.0% guard']``,
so VOLATILITY blocked three trades and its row counts two.

AND THE BIAS IS ORDERED. `failed.append` order is source-line order in
`evaluate`: EQUITY_CURVE at :1246, CIRCUIT_BREAKER at :1478 and DAILY_LOSS at
:1553 soak up attribution, while FUNDING_CLOCK (:2199), INTENT_POLICY (:2237),
VALIDATION (:2297) and AUTHORITY (:2373) are starved. MTF_ALIGNMENT is gate 19
of ~30. The ranking the card calls "costliest" is decided by which check the
engine happens to reach first.

THE SOLE SUBSET NEEDS NO RE-RANKING, and that is why the fix is small: a
sole-cause row carries exactly one gate, so that gate IS the charged one.
Evaluation order cannot move a trade into or out of any gate's sole subset, and
a gate that solely blocked nothing has no recoverable R to claim.
"""

from __future__ import annotations

import ast
import io
import types

import pytest

from bot.core.self_audit import costliest_gate_line
from bot.core.shadow_book import (
    CAUSE_CO_BLOCKED,
    CAUSE_SOLE,
    CAUSE_UNKNOWN,
    MIN_GATE_TRADES,
    SCOPE_ALL_CHECKS,
    SCOPE_PRE_RISK,
    ShadowBook,
    cause_state,
    gate_verdict,
)


def _idea(sym="BTC/USDT", direction="LONG"):
    return types.SimpleNamespace(
        id=sym, asset=sym, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, strategy_type="scalp",
        direction=types.SimpleNamespace(value=direction))


def _book(tmp_path, name="sb.json"):
    return ShadowBook(state_file=str(tmp_path / name))


def _rec(book, gates, r, scope=SCOPE_ALL_CHECKS, sym=None, regime="TREND"):
    """Record a rejection through the REAL writer and close it at `r`."""
    tr = book.record_rejection(
        _idea(sym or f"A{len(book._trades)}/USDT"), gates, "; ".join(gates),
        ref_price=100.0, now_ts=1000.0, regime=regime, scope=scope)
    assert tr is not None
    tr.update(status="closed", outcome="tp", r=r, exit_ts=2000.0,
              exit_price=110.0)
    return tr


# ── 1. the per-row reading ────────────────────────────────────────────────

class TestCauseState:
    def test_one_gate_from_a_full_evaluation_is_a_sole_cause(self):
        assert cause_state({"gates": ["MTF_ALIGNMENT: 1/3"],
                            "scope": SCOPE_ALL_CHECKS}) == CAUSE_SOLE

    def test_two_gates_is_co_blocked_whatever_the_scope(self):
        for scope in (SCOPE_ALL_CHECKS, SCOPE_PRE_RISK, "", "nonsense"):
            assert cause_state({"gates": ["A: x", "B: y"],
                                "scope": scope}) == CAUSE_CO_BLOCKED, scope

    def test_the_pre_risk_path_establishes_nothing(self):
        """The liquidity call site returns ABOVE the risk-rejection branch, so
        its one-entry list means *nothing else had been checked yet*. Reading
        it as a sole cause would claim the trade would certainly have been
        placed, on a gate the risk engine never got to vote on."""
        assert cause_state({"gates": ["LIQUIDITY: spread 40bps"],
                            "scope": SCOPE_PRE_RISK}) == CAUSE_UNKNOWN

    def test_a_row_that_does_not_say_is_unknown_not_sole(self):
        """The asymmetry `plan_cleanup` draws, one subject over. Over-claiming
        puts recoverable R on the card that asks an operator to loosen a risk
        gate on a live account; under-claiming costs a missed optimisation."""
        assert cause_state({"gates": ["MTF_ALIGNMENT: 1/3"]}) == CAUSE_UNKNOWN
        assert cause_state({"gates": ["A: x"], "scope": ""}) == CAUSE_UNKNOWN

    def test_the_placeholder_gate_is_not_a_gate(self):
        """`record_rejection` writes `(unspecified)` when its caller named no
        gate. A one-entry list holding it establishes nothing."""
        assert cause_state({"gates": ["(unspecified)"],
                            "scope": SCOPE_ALL_CHECKS}) == CAUSE_UNKNOWN

    @pytest.mark.parametrize("row", [
        {}, {"gates": None}, {"gates": []}, {"gates": "MTF"}, None, "x", 7,
    ])
    def test_nothing_readable_is_unknown(self, row):
        assert cause_state(row) == CAUSE_UNKNOWN

    def test_truncation_can_only_hide_a_co_blocker(self, tmp_path):
        """`record_rejection` keeps five gates. A row with six failed checks
        stores five, which is still `> 1` — the cut can never turn a real
        co-blocked row into a sole one."""
        tr = _rec(_book(tmp_path), [f"G{i}: x" for i in range(6)], 1.0)
        assert len(tr["gates"]) == 5
        assert cause_state(tr) == CAUSE_CO_BLOCKED


# ── 2. the scoreboard ─────────────────────────────────────────────────────

class TestGateReportSplitsByCause:
    def test_the_three_counts_partition_the_charged_count(self, tmp_path):
        b = _book(tmp_path)
        for i in range(4):
            _rec(b, ["MTF_ALIGNMENT: 1/3"], 2.0)
        for i in range(3):
            _rec(b, ["MTF_ALIGNMENT: 1/3", "CONFIDENCE: low"], 2.0)
        for i in range(2):
            _rec(b, ["MTF_ALIGNMENT: 1/3"], 2.0, scope=SCOPE_PRE_RISK)
        g = b.gate_report()["MTF_ALIGNMENT"]
        assert g["n"] == 9
        assert (g["sole_n"], g["co_n"], g["unknown_n"]) == (4, 3, 2)
        assert g["sole_n"] + g["co_n"] + g["unknown_n"] == g["n"]
        assert g["net_r"] == pytest.approx(18.0)
        assert g["sole_net_r"] == pytest.approx(8.0)
        assert g["co_net_r"] == pytest.approx(6.0)
        assert g["unknown_net_r"] == pytest.approx(4.0)

    def test_the_verdict_is_the_recoverable_subset_s(self, tmp_path):
        """The heart of it. Twenty charged trades all netting +2R would be a
        blazing `eating_edge` on the charged total; nine of them recoverable
        is under the sample floor and has no verdict at all."""
        b = _book(tmp_path)
        for i in range(9):
            _rec(b, ["MTF_ALIGNMENT: 1/3"], 2.0)
        for i in range(11):
            _rec(b, ["MTF_ALIGNMENT: 1/3", "CONFIDENCE: low"], 2.0)
        g = b.gate_report()["MTF_ALIGNMENT"]
        assert g["n"] == 20 and g["net_r"] == pytest.approx(40.0)
        assert g["sole_n"] == 9 < MIN_GATE_TRADES
        assert g["verdict"] is None, (
            "a verdict was read off trades loosening this gate cannot place")

    def test_enough_sole_causes_still_establish_it(self, tmp_path):
        b = _book(tmp_path)
        for i in range(MIN_GATE_TRADES + 2):
            _rec(b, ["MTF_ALIGNMENT: 1/3"], 2.0)
        g = b.gate_report()["MTF_ALIGNMENT"]
        assert g["verdict"] == "eating_edge"
        assert g["lower_r"] is not None and g["lower_r"] > 0

    def test_a_gate_with_no_sole_causes_has_no_average(self, tmp_path):
        """`None`, never `0.0`. A mean over no samples is not a break-even —
        and `0.0R` is the one figure a reader takes as a measured one."""
        b = _book(tmp_path)
        for i in range(12):
            _rec(b, ["LIQUIDITY: spread"], 2.0, scope=SCOPE_PRE_RISK)
        g = b.gate_report()["LIQUIDITY"]
        assert g["sole_n"] == 0
        assert g["sole_avg_r"] is None
        assert g["verdict"] is None and g["lower_r"] is None

    def test_the_sort_ranks_by_what_loosening_would_buy_back(self, tmp_path):
        """"Costliest" is a claim about recoverable R. A gate with a huge
        charged total and nothing recoverable does not outrank one with a
        smaller total that is all recoverable."""
        b = _book(tmp_path)
        for i in range(20):
            _rec(b, ["BIG: x", "ALSO: y"], 5.0)          # 100R, none recoverable
        for i in range(12):
            _rec(b, ["SMALL: x"], 1.0)                   # 12R, all recoverable
        rep = b.gate_report()
        assert rep["BIG"]["net_r"] > rep["SMALL"]["net_r"]
        assert list(rep) == ["SMALL", "BIG"]

    def test_a_ledger_with_no_scope_degrades_to_the_old_ranking(self, tmp_path):
        """Every row unclassifiable scores 0 recoverable, so the sort falls
        back to the charged total rather than to an arbitrary order."""
        b = _book(tmp_path)
        b._loaded = True
        b._trades = ([{"status": "closed", "r": 1.0, "gate": "SMALL: x"}] * 3
                     + [{"status": "closed", "r": 5.0, "gate": "BIG: x"}] * 3)
        rep = b.gate_report()
        assert list(rep) == ["BIG", "SMALL"]
        assert all(g["sole_n"] == 0 and g["unknown_n"] == g["n"]
                   for g in rep.values())

    def test_the_internal_accumulator_does_not_reach_a_reader(self, tmp_path):
        """`scan_skill` publishes each row as `{**stats}` to the website. The
        sole sum-of-squares is working state, not a figure."""
        b = _book(tmp_path)
        _rec(b, ["G: x"], 1.0)
        assert "_sole_r2" not in b.gate_report()["G"]


class TestThereIsOneVerdictBranch:
    def test_gate_report_asks_the_leaf(self, tmp_path, monkeypatch):
        """A byte-identical copy of the branch agrees with every fixture and
        diverges on the first edit to either, so equality proves nothing.
        Patch the leaf and read what the scoreboard answers."""
        import bot.core.shadow_book as sb
        seen = []

        def fake(n, sum_r, sum_r2):
            seen.append((n, sum_r))
            return -9.0, 9.0, "planted"

        monkeypatch.setattr(sb, "gate_verdict", fake)
        b = _book(tmp_path)
        for i in range(3):
            _rec(b, ["G: x"], 2.0)
        g = b.gate_report()["G"]
        assert g["verdict"] == "planted", "gate_report kept its own copy"
        assert (g["lower_r"], g["upper_r"]) == (-9.0, 9.0)
        assert seen == [(3, 6.0)], "the leaf was handed the SOLE sample"

    def test_the_sample_floor_lives_in_the_leaf(self):
        lo, hi, verdict = gate_verdict(MIN_GATE_TRADES - 1, 20.0, 40.1)
        assert lo is not None and lo > 0, "the interval itself clears zero"
        assert verdict is None, "a floor remembered at each call site is none"


# ── 3. the writers say which check set they ran ───────────────────────────

class TestBothCallSitesDeclareTheirScope:
    """A scan, and it says so: both calls sit inside a ~200-line branch of
    `_evaluate` that a unit test cannot reach without standing up an engine, a
    signal and an analyzer. What is DRIVEN below is the reading itself — the
    field lands, and `cause_state` answers from it."""

    @staticmethod
    def _calls():
        tree = ast.parse(io.open("bot/core/engine.py", encoding="utf-8").read())
        out = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "record_rejection"):
                out.append(node)
        return out

    def test_every_call_site_names_a_scope(self):
        calls = self._calls()
        assert len(calls) == 2, f"expected 2 writers, found {len(calls)}"
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            assert "scope" in kw, (
                f"engine.py:{call.lineno} records a rejection without saying "
                "which check set ran — every row it writes is unclassifiable")
            assert isinstance(kw["scope"], ast.Name)
            assert kw["scope"].id in ("SCOPE_ALL_CHECKS", "SCOPE_PRE_RISK")

    def test_the_liquidity_writer_is_the_pre_risk_one(self):
        """It returns ABOVE the risk-rejection branch. Recording it as a full
        evaluation would make every liquidity rejection a sole cause."""
        scopes = []
        for call in self._calls():
            kw = {k.arg: k.value for k in call.keywords}
            first = call.args[1] if len(call.args) > 1 else None
            is_liq = (isinstance(first, ast.List) and len(first.elts) == 1
                      and isinstance(first.elts[0], ast.Name)
                      and first.elts[0].id == "liq_reason")
            scopes.append((is_liq, kw["scope"].id))
        assert (True, "SCOPE_PRE_RISK") in scopes
        assert (False, "SCOPE_ALL_CHECKS") in scopes

    def test_the_field_lands_and_is_read_back(self, tmp_path):
        b = _book(tmp_path)
        tr = _rec(b, ["G: x"], 1.0, scope=SCOPE_PRE_RISK)
        assert tr["scope"] == SCOPE_PRE_RISK
        assert cause_state(tr) == CAUSE_UNKNOWN
        reloaded = ShadowBook(state_file=b.state_file)
        reloaded._load()
        row = [t for t in reloaded._trades if t["id"] == tr["id"]][0]
        assert row["scope"] == SCOPE_PRE_RISK, "the scope did not survive disk"


# ── 4. the nightly card ───────────────────────────────────────────────────

def _row(**over):
    row = {"n": 0, "net_r": 0.0, "avg_r": 0.0, "sole_n": 0, "sole_net_r": 0.0,
           "sole_avg_r": None, "co_n": 0, "co_net_r": 0.0, "unknown_n": 0,
           "unknown_net_r": 0.0, "lower_r": None, "upper_r": None,
           "verdict": None}
    row.update(over)
    return row


class TestTheCardQuotesTheSetItJudged:
    def test_an_established_gate_quotes_the_recoverable_figures(self):
        line = costliest_gate_line({"MTF_ALIGNMENT": _row(
            n=23, net_r=40.4, avg_r=1.757, sole_n=14, sole_net_r=22.4,
            sole_avg_r=1.6, co_n=9, lower_r=0.68, upper_r=2.52,
            verdict="eating_edge")})
        assert "costliest gate" in line
        assert "+22.4R" in line
        assert "14 trade(s) blocked by this gate alone" in line
        assert "+40.4R" not in line, (
            "the charged total was quoted beside a verdict earned on 14")
        assert "9 more are charged to it" in line

    def test_the_caveat_is_absent_when_there_is_nothing_to_caveat(self):
        line = costliest_gate_line({"CORRELATION": _row(
            n=14, net_r=22.4, sole_n=14, sole_net_r=22.4, sole_avg_r=1.6,
            lower_r=0.68, upper_r=2.52, verdict="eating_edge")})
        assert "costliest gate" in line
        assert "more are charged" not in line, (
            "a caveat printed when nothing is caveated is one nobody reads")

    def test_the_live_card_withholds_what_it_cannot_support(self):
        """The sentence production sees first: the ledger's rows predate the
        scope field, so the +86.2R is real and none of it is shown to be
        recoverable."""
        line = costliest_gate_line({"MTF_ALIGNMENT": _row(
            n=73, net_r=86.2, avg_r=1.181, sole_n=0, unknown_n=73)})
        assert "costliest gate" not in line
        assert "charged 73 trades, net +86.2R" in line
        assert "no trade on record is marked as blocked by this gate alone" \
            in line
        assert "No gate is established as costing edge." in line

    def test_an_absent_count_is_not_a_count_of_none(self):
        """ABSENT IS NOT ZERO. A row with no `sole_n` does not say none were
        blocked alone; it says nobody counted."""
        line = costliest_gate_line({"OLD": _row(n=50, net_r=12.0, avg_r=0.24,
                                                sole_n=None)})
        assert "no readable count" in line
        assert "no trade on record is marked" not in line
        assert "None" not in line

    def test_a_thin_recoverable_subset_quotes_no_bound(self):
        line = costliest_gate_line({"MTF_ALIGNMENT": _row(
            n=23, net_r=40.4, sole_n=6, sole_net_r=12.0, sole_avg_r=2.0,
            co_n=17)})
        assert "not established" in line
        assert f"fewer than the {MIN_GATE_TRADES}" in line
        assert "6 trade(s) blocked by this gate alone" in line

    def test_an_unreadable_recoverable_total_does_not_crash_the_card(self):
        """`None <= 0` is a TypeError, and this comparison sits outside the
        try — it would take the whole card down rather than one line of it."""
        assert costliest_gate_line({"X": _row(n=40, net_r=5.0, sole_n=12,
                                              sole_net_r=None)}) is None

    def test_a_failed_read_is_still_its_own_sentence(self):
        line = costliest_gate_line(None)
        assert "could not be read" in line


# ── 5. the /shadow scoreboard ─────────────────────────────────────────────

class TestTheScoreboardRowMatchesItsIcon:
    def test_the_figures_beside_a_colour_are_the_set_it_judged(self, tmp_path):
        b = _book(tmp_path)
        for i in range(14):
            _rec(b, ["MTF_ALIGNMENT: 1/3"], 1.6)
        for i in range(9):
            _rec(b, ["MTF_ALIGNMENT: 1/3", "CONFIDENCE: low"], 2.0)
        out = b.render_report()
        assert "blocked alone 14tr" in out
        assert "net +22.4R" in out
        head = [ln for ln in out.splitlines() if "MTF_ALIGNMENT" in ln][0]
        assert "\U0001f7e5" in head, "an established gate lost its colour"
        assert "+40.4R" not in head, "the charged total sat beside the icon"
        assert "└ charged 23tr net +40.4R — 9 also failed another gate" in out

    def test_a_gate_with_nothing_recoverable_prints_no_figure(self, tmp_path):
        """Not `0.0R` and not `avg +0.00R` — the one number a reader takes as
        a measured break-even."""
        b = _book(tmp_path)
        for i in range(12):
            _rec(b, ["LIQUIDITY: spread"], 2.0, scope=SCOPE_PRE_RISK)
        out = b.render_report()
        head = [ln for ln in out.splitlines() if "LIQUIDITY" in ln][0]
        assert "no trade on record was blocked by this gate alone" in head
        assert "+0.0R" not in head and "0.00R" not in head
        assert "⬜" in head
        assert "12 the record cannot classify" in out

    def test_the_header_says_which_trades_the_figures_are(self, tmp_path):
        b = _book(tmp_path)
        _rec(b, ["G: x"], 1.0)
        assert "blocked ALONE" in b.render_report()

    def test_a_gate_whose_rows_are_all_sole_gets_no_second_line(self, tmp_path):
        b = _book(tmp_path)
        for i in range(3):
            _rec(b, ["G: x"], 1.0)
        assert "└ charged" not in b.render_report()
