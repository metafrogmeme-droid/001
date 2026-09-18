"""GET /api/reports is served to ANYONE, and it published three dollar figures.

Driven position-aware over the express dispatch chain (`app/test/helpers/
public_routes.js`), that route carries no auth middleware and no limiter.
Section 4 of this repo's rules: public surfaces carry percent, ratio and
count only. It carried:

    arb.carries[].earned_usd   per-coin paper carry, in dollars
    parity.net_pnl             the OPERATOR's realized net on the live book
    parity.total_fees          the OPERATOR's fees paid

THE FIX REACHED THE VERDICT AND NOT THE ROWS BESIDE IT. `_arb_section` strips
the dollar out of `verdict` under a comment reading "no dollar figure, because
/api/reports is served to anyone" -- three lines above `carries`, which
carried one per coin. `_carry_row` popped the per-entry sample list and left
the total those samples sum to. Ask which OTHER field on the same payload
makes the same claim.

AND THE PARITY JUSTIFICATION WAS THE ONE `public_no_dollars.test.js` ALREADY
RECORDS AS FALSE. `reports.js` called the parity headline "already public on
/track". Driven, /track publishes `equity_curve_idx` -- INDEXED TO 100
precisely so no account size escapes -- plus `win_rate_pct` and
`profit_factor`, and no dollar at all. That is the `get_track_record` defect
that guard's own header describes ("its own `source` string claimed 'same data
as the public /track page' ... so the tool was strictly more revealing than the
page it said it mirrored"), one route over, with the same sentence.

WHY THIS GUARD IS ON THE PRODUCER AND NOT THE ROUTE. `reports.js` emits
`arb: r.arb || null` -- a whole sub-object forwarded from the bot. No key
under `app/routes/` spells `earned_usd` or `net_pnl`, so a key-scan of the
route file cannot see either one, and the JS guard's own stated scope limit
("a dollar field returned from a LIB and spread into a response is invisible
to it") is the same hole one process boundary further out. The producer is
Python and the publisher is Node; nothing checked what crossed. This is that
check, made where the keys exist.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from bot.backtest import parity
from bot.core import web_reports
from bot.core.arb_tracker import PAPER_NOTIONAL_USD, PaperCarry

# Account-money field names, the vocabulary `app/test/public_no_dollars.test.js`
# uses, plus the two this slice found. A name here may not appear as a key
# anywhere in a public section of the payload.
FORBIDDEN = (
    "pnl", "pnl_usd", "net_pnl", "net_pnl_usd", "realized_pnl", "unrealized_pnl",
    "equity", "equity_usd", "balance", "balance_usd", "margin", "margin_usd",
    "notional", "amount_usd", "usd_value", "vusdt", "collateral_usd",
    "earned_usd", "total_fees", "gross_pnl", "entry_carry_usd",
)

# Keys whose name carries a currency and which are NOT account money, each
# with the reason. The unknown case has to be LOUD: a money-shaped key that is
# neither forbidden nor declared here fails, which is the rule
# `tests/command_gates.py` gets right (an unknown gate spelling reads as
# `none`, which demands a reason) and which the JS vocabulary did not have.
DECLARED_SAFE = {
    "notional_usd": (
        "PAPER_NOTIONAL_USD, a published CONSTANT the arb tracker is "
        "denominated in. It discloses nothing about RUNECLAW's capital and it "
        "is the basis that makes every percent beside it readable -- the same "
        "split the MCP run_what_if tool draws for a caller's own stake_usd."
    ),
}

PUBLIC_SECTIONS = ("funding", "arb", "parity")

# The sections the payload builder marks OPERATOR-SENSITIVE. `yield` is served
# to admin-plan users only (app/routes/reports.js re-reads the plan from the
# DB), so it is out of scope here and must NOT be quietly swept in.
SENSITIVE_SECTIONS = ("yield",)


def _money_shaped(key: str) -> bool:
    """A key whose NAME claims a currency."""
    k = key.lower()
    return k.endswith(("_usd", "_usdt")) or k.startswith("usd_") or "_usd_" in k


def _walk(node, path=""):
    """Every (dotted path, key) pair in a JSON-shaped payload."""
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            yield here, k
            yield from _walk(v, here)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def _carry(base="BTC", earned=38.12):
    return PaperCarry(
        base=base, earned_usd=earned, held_hours=72.0, observed_hours=96.0,
        entries=4, last_spread_apr=0.11, venues=("bitget", "bybit"),
        entry_carry_usd=(9.0, 9.1, 10.0, 10.02), open_entry=None,
    )


class TestTheCarryRowIsPercent:
    def test_the_dollar_figure_does_not_reach_the_wire(self):
        row = web_reports._carry_row(_carry(), PAPER_NOTIONAL_USD)
        assert "earned_usd" not in row
        assert "entry_carry_usd" not in row, "the per-entry samples stay off too"
        assert row["earned_pct"] == pytest.approx(3.812)

    def test_it_is_the_verdict_s_own_denominator(self):
        # One unit for the row and the verdict beneath it, or a reader is
        # comparing two quantities that merely look alike.
        row = web_reports._carry_row(_carry(earned=25.0), PAPER_NOTIONAL_USD)
        assert row["earned_pct"] == pytest.approx(100.0 * 25.0 / PAPER_NOTIONAL_USD)

    def test_an_unreadable_carry_is_none_and_not_a_break_even(self):
        row = web_reports._carry_row(_carry(earned=None), PAPER_NOTIONAL_USD)
        assert row["earned_pct"] is None

    def test_a_measured_zero_survives(self):
        # 0 is a real outcome -- a spread that paid nothing -- and must not
        # render as the absence of one.
        row = web_reports._carry_row(_carry(earned=0.0), PAPER_NOTIONAL_USD)
        assert row["earned_pct"] == 0.0

    @pytest.mark.parametrize("junk", ["12.0", float("nan"), float("inf"), None])
    def test_a_carry_that_is_not_a_number_is_unreadable(self, junk):
        row = web_reports._carry_row(_carry(earned=junk), PAPER_NOTIONAL_USD)
        pct = row["earned_pct"]
        assert pct is None or (pct == pct and abs(pct) != float("inf")), pct

    def test_a_notional_of_zero_cannot_divide(self):
        row = web_reports._carry_row(_carry(), 0.0)
        assert row["earned_pct"] is None


class TestTheParityHeadlineIsRatios:
    def test_the_operator_s_dollars_are_not_on_the_keep_list(self):
        src = inspect.getsource(web_reports._parity_section)
        keep = src[src.index("keep = ("):src.index(")", src.index("keep = ("))]
        for banned in ("net_pnl", "total_fees", "gross_pnl"):
            assert f'"{banned}"' not in keep, (
                f"{banned} is an account dollar figure on a route with no auth")

    def test_the_ratio_that_carries_what_the_dollars_said_is_kept(self):
        src = inspect.getsource(web_reports._parity_section)
        keep = src[src.index("keep = ("):src.index(")", src.index("keep = ("))]
        # `pf` is the net as a ratio; fee drag is what `total_fees` was for.
        for kept in ("pf", "fee_drag_of_gross", "win_rate", "fee_vs_model"):
            assert f'"{kept}"' in keep, f"{kept} carries the signal the dollars did"

    def test_every_kept_key_is_one_parity_summary_produces(self):
        # A keep-list naming a key the producer does not emit publishes None
        # under a name, which reads as a measured absence.
        src = inspect.getsource(web_reports._parity_section)
        keep = src[src.index("keep = ("):src.index(")", src.index("keep = ("))]
        produced = inspect.getsource(parity.parity_summary)
        for m in [s.strip().strip('"') for s in keep.split("(", 1)[1].split(",")]:
            if not m or not m.isidentifier():
                continue
            assert f'"{m}"' in produced, f"parity_summary emits no {m}"


class TestNoPublicSectionCarriesAnAccountDollar:
    """The whole payload, walked. This is the check the route cannot make."""

    def _payload(self, monkeypatch):
        monkeypatch.setattr(
            web_reports, "_funding_section",
            lambda: {"rows": [{"base": "BTC", "rates": {"bitget": 0.01},
                               "spread_apr": 1.1}]})
        monkeypatch.setattr(
            web_reports, "_arb_section",
            lambda: {
                "notional_usd": PAPER_NOTIONAL_USD,
                "snapshots": 12,
                "carries": [web_reports._carry_row(_carry(), PAPER_NOTIONAL_USD)],
                "verdict": {"state": "survives", "sentence": "…",
                            "scored": 4, "total": 5, "held_hours": 72.0,
                            "fee_pct": 0.24, "mean_net_pct": 0.85,
                            "interval_pct": [0.1, 1.6]},
            })
        monkeypatch.setattr(
            web_reports, "_parity_section",
            lambda engine: {"trades": 9, "win_rate": 0.55, "pf": 1.4,
                            "fees_read": 9, "realized_fee_rate": 0.0006,
                            "modeled_fee_rate": 0.0006, "fee_vs_model": 1.0,
                            "fee_drag_of_gross": 0.18, "inferred_fills": 0,
                            "excluded_non_fills": 1, "unscored_pnl": 0})
        monkeypatch.setattr(web_reports, "_yield_section", lambda engine: None)
        return web_reports.build_reports_payload(engine=object())

    def test_no_forbidden_key_appears_in_a_public_section(self, monkeypatch):
        payload = self._payload(monkeypatch)
        offenders = []
        for section in PUBLIC_SECTIONS:
            for path, key in _walk(payload.get(section), section):
                if key in FORBIDDEN:
                    offenders.append(path)
        assert offenders == [], (
            "GET /api/reports has no auth and no limiter; these are account "
            "money on it:\n  " + "\n  ".join(offenders))

    def test_a_money_shaped_key_is_forbidden_or_declared_safe(self, monkeypatch):
        payload = self._payload(monkeypatch)
        undeclared = []
        for section in PUBLIC_SECTIONS:
            for path, key in _walk(payload.get(section), section):
                if key in FORBIDDEN or key in DECLARED_SAFE:
                    continue
                if _money_shaped(key):
                    undeclared.append(f"{path} ({key})")
        assert undeclared == [], (
            "a key on the public payload looks like account money and is "
            "neither forbidden nor declared safe with a reason:\n  "
            + "\n  ".join(undeclared))

    def test_the_payload_is_serialisable_as_it_is_served(self, monkeypatch):
        # The route forwards these sections verbatim; a value json cannot
        # encode would 503 the whole public read.
        json.dumps(self._payload(monkeypatch))


class TestTheRuleCanActuallyFail:
    """The property a guard needs before any other property matters."""

    def test_the_walk_finds_a_planted_dollar(self):
        planted = {"arb": {"carries": [{"base": "BTC", "earned_usd": 1.0}]}}
        found = [p for p, k in _walk(planted["arb"], "arb") if k in FORBIDDEN]
        assert found == ["arb.carries[0].earned_usd"]

    def test_the_walk_reaches_inside_lists_and_nested_dicts(self):
        planted = {"a": [{"b": {"net_pnl": 2}}]}
        assert [k for _, k in _walk(planted) if k in FORBIDDEN] == ["net_pnl"]

    @pytest.mark.parametrize("key", ["earned_usd", "carry_usd", "usd_total",
                                     "fees_usd", "x_usd_y"])
    def test_money_shaped_recognises_the_names_that_matter(self, key):
        assert _money_shaped(key)

    @pytest.mark.parametrize("key", ["used", "user_id", "usage", "unused",
                                     "status", "held_hours"])
    def test_money_shaped_does_not_fire_on_ordinary_words(self, key):
        assert not _money_shaped(key)

    def test_every_declared_safe_key_names_a_reason(self):
        for key, why in DECLARED_SAFE.items():
            assert isinstance(why, str) and len(why) > 40, (
                f"{key} is declared safe with no reason -- an unexplained "
                "entry is the hole this file exists to close")

    def test_the_sensitive_section_is_not_swept_into_the_public_set(self):
        # `yield` holds real account balances and is admin-gated at the route.
        # Folding it in here would make this file appear to check something it
        # deliberately does not.
        assert set(PUBLIC_SECTIONS).isdisjoint(SENSITIVE_SECTIONS)
        assert "yield" not in PUBLIC_SECTIONS


class TestTheProducerIsWhereTheKeysAre:
    def test_the_route_spells_none_of_these_keys_itself(self):
        # The reason this guard is in Python. `reports.js` forwards whole
        # sub-objects, so a key-scan of the route file sees nothing.
        src = Path("app/routes/reports.js").read_text(encoding="utf-8")
        for key in ("earned_usd", "net_pnl", "total_fees"):
            assert f"{key}:" not in src, (
                f"{key} is spelled in the route now -- the JS key-scan can see "
                "it, and this test's premise has changed")
        assert "r.arb" in src, "the route still forwards the section wholesale"

    def test_the_carry_row_takes_the_notional_rather_than_importing_one(self):
        """Two copies of the denominator are two answers about what a percent
        is a percent OF.

        ASSERTING THE PARAMETER EXISTS IS NOT ASSERTING IT IS USED. The first
        version of this checked `"notional" in signature(...).parameters`, and
        the mutation that gave the parameter a default SURVIVED — the caller
        still passes the real one, so nothing changed and the assertion held
        either way. That is an equivalent mutant caught by an assertion that
        could not tell. It is DRIVEN now: hand the seam a denominator nothing
        else in the tree uses and read the answer back.
        """
        sig = inspect.signature(web_reports._carry_row)
        assert "notional" in sig.parameters
        odd = 250.0
        assert odd != PAPER_NOTIONAL_USD
        row = web_reports._carry_row(_carry(earned=25.0), odd)
        assert row["earned_pct"] == pytest.approx(10.0), (
            "the row computed its percent against a denominator of its own "
            "rather than the one it was handed")
