"""Compliance Lock 4's cap is named for the quantity it checks.

`ComplianceEngine` calls its per-trade ceiling `max_notional_usd` and refuses
with *"Notional $X exceeds cap $Y"*. Driven, the figure it is handed is the
MARGIN: `engine._confirm_trade_inner` passes `recheck.position_size_usd`, and
`live_executor`'s own audit F-3 note settles what that is --

    size_usd is MARGIN; the real exchange exposure is notional = quantity *
    price = size_usd * leverage. The risk engine's %-caps are margin-based, so
    they validate size_usd, not this notional.

-- so an authorization lock printed a comparison nobody made. That is the
`size_usd` two-meanings-under-one-name defect, one envelope over, on the last
gate before a live order.

WHAT IT DOES NOT BOUND IS STATED RATHER THAN IMPLIED. At the MAX_LEVERAGE
ceiling (10x shipped), a $10,000 margin ceiling permits $100,000 of notional.
Naming the exposure HERE would need the leverage the order will run at, and
that is resolved ONCE per order inside `LiveExecutor.execute` -- two reads of
the leverage in one order are two answers, which is the defect #201 fixed. So
the cap stays a margin cap and the words follow it.

AND THE FIRST DRAFT OF THIS FILE NAMED THE WRONG BACKSTOP. It said exposure was
bounded by the executor's F-3 hard block, a sentence copied off
`risk_engine.py`'s module docstring, and the drive written for it failed: that
block's ceiling was `max(size, $100) * max(MAX_LEVERAGE, lev) * 1.05`, so a
consistent order at ANY leverage passed it -- an ARITHMETIC check on the
quantity, nothing about the leverage -- and `CONFIG.exchange.max_leverage` had
no other reader in `bot/`, so it bound nothing. That was recorded here rather
than "fixed", as a sizing decision filed with its measurement. The decision was
made on 2026-09-28: `MAX_LEVERAGE` IS the ceiling now. Every placement starts
from `bot.core.leverage.operator_standard` (the standard, capped), every reader
past it only lowers, and the F-3 block reads the ceiling alone as the backstop.
`tests/test_max_leverage_is_the_ceiling.py` drives it; this file keeps the
reads pin, so a third reader of the field arrives here by name.

AND IT CANNOT FIRE AT SHIPPED CAPS, either way, which is why the rename was a
name and not a behaviour: `MICRO_MAX_POSITION_USD` is $100, so the margin never
approaches $10,000 and the notional at the ceiling does not either. Both
arithmetic facts are read from the live constants here, so the day an operator
raises them this test says so rather than the claim going stale.
"""

import ast
import inspect
import textwrap
from types import SimpleNamespace

from bot.compliance.compliance_engine import (
    ComplianceEngine,
    Permission,
    SubjectProfile,
    default_demo_profile,
)
from tests.source_scan import code_only


def _profile(cap: float = 10_000.0) -> SubjectProfile:
    return SubjectProfile(
        subject_id="s1",
        permissions={Permission.LIVE_TRADE},
        jurisdiction="US",
        max_margin_usd=cap,
    )


def _authorize(margin: float, cap: float = 10_000.0):
    eng = ComplianceEngine()
    prof = _profile(cap)
    tok = eng.issue_approval_token("T1", prof.subject_id)
    return eng.authorize(
        action=Permission.LIVE_TRADE, profile=prof, live_mode=True,
        risk_passed=True, macro_ok=True, margin_usd=margin,
        trade_id="T1", approval_token=tok,
    )


class TestTheLockChecksTheMarginAndSaysSo:

    def test_a_margin_inside_the_cap_passes_the_named_lock(self):
        d = _authorize(100.0)
        assert d.granted, d.reasons
        assert "margin_cap" in d.locks_passed
        assert "notional_cap" not in d.locks_passed

    def test_a_margin_over_the_cap_is_refused_by_name(self):
        d = _authorize(10_000.01)
        assert not d.granted
        assert "margin_cap" in d.locks_failed
        assert "notional_cap" not in d.locks_failed

    def test_the_refusal_names_margin_and_never_notional(self):
        """The old sentence said "Notional $X exceeds cap" about a margin."""
        d = _authorize(25_000.0)
        sent = [r for r in d.reasons if "25,000" in r]
        assert sent, d.reasons
        assert "Margin" in sent[0], sent[0]
        assert "otional" not in sent[0], sent[0]
        assert "per-trade margin cap" in sent[0], sent[0]

    def test_the_cap_is_inclusive_so_a_margin_exactly_on_it_passes(self):
        """A cap is a CEILING: `<=`, so a figure on it is inside."""
        assert _authorize(10_000.0).granted
        assert not _authorize(10_000.000001).granted


class TestTheEngineHandsItTheMarginItSized:

    def test_the_call_names_the_margin_and_invents_no_second_figure(self):
        """By AST: the keyword is `margin_usd` and its value is the risk
        re-check's own `position_size_usd`. A notional computed here would be a
        SECOND leverage resolution in one order (#201)."""
        from bot.core.engine import RuneClawEngine

        src = code_only(inspect.getsource(RuneClawEngine._confirm_trade_inner))
        tree = ast.parse(textwrap.dedent(src))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and ast.unparse(n.func).endswith("compliance.authorize")
        ]
        assert len(calls) == 1, f"expected one authorize call, saw {len(calls)}"
        kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        assert "notional_usd" not in kw, kw
        assert kw.get("margin_usd") == "recheck.position_size_usd", kw

    def test_no_subject_profile_in_the_tree_still_spells_the_old_field(self):
        """Read as CODE, because this file's own prose names the old spelling."""
        import pathlib

        offenders = []
        for p in sorted(pathlib.Path("bot").rglob("*.py")):
            if "max_notional_usd" in code_only(p.read_text()):
                offenders.append(str(p))
        assert offenders == ["bot/risk/risk_engine.py"], offenders

    def test_the_risk_engines_own_local_is_a_recorded_misnomer(self):
        """The one remaining `max_notional_usd` is a LOCAL in the risk engine
        that bounds `position_usd` -- the margin -- and its behaviour is
        deliberate and documented (`live_executor`'s F-3 note, and the cap's own
        comment: *"the engine effectively runs flat margin"*). Renaming it would
        move no number and touch every sizing test; the compliance one was worth
        moving because its cap is a SUBJECT's envelope with its own value and its
        own refusal sentence. Recorded, not changed."""
        from bot.risk.risk_engine import RiskEngine

        src = code_only(inspect.getsource(RiskEngine._evaluate_locked))
        assert "position_usd = max_notional_usd" in src, (
            "the risk engine's local no longer clamps the margin: if it has "
            "become a real notional cap, this row is stale")


def _reads_of_max_leverage() -> dict:
    """Every CODE read of the name `max_leverage` in `bot/`, keyed by the
    innermost function holding it (or `<module>`): an attribute load, a
    `getattr(..., "max_leverage")`, a subscript, or a `.get("max_leverage")`.
    A dict KEY is a definition and not a read, so `order_rules.ASSET_RULES`'
    per-class rows are not counted -- and nothing reads those either."""
    import pathlib

    def _is_read(n) -> bool:
        if isinstance(n, ast.Attribute) and n.attr == "max_leverage":
            return True
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            return n.slice.value == "max_leverage"
        if isinstance(n, ast.Call):
            f = ast.unparse(n.func)
            if f == "getattr" and len(n.args) >= 2:
                a = n.args[1]
                return isinstance(a, ast.Constant) and a.value == "max_leverage"
            if f.endswith(".get") and n.args:
                a = n.args[0]
                return isinstance(a, ast.Constant) and a.value == "max_leverage"
        return False

    def _own(node):
        """A scope's own nodes: never descends into a nested def or lambda."""
        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            yield n
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.extend(ast.iter_child_nodes(n))

    found: dict = {}
    for p in sorted(pathlib.Path("bot").rglob("*.py")):
        tree = ast.parse(p.read_text())
        scopes = [("<module>", tree)] + [
            (fn.name, fn) for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for name, scope in scopes:
            for n in _own(scope):
                if _is_read(n):
                    found.setdefault(f"{p}:{name}", []).append(ast.unparse(n))
    return found


class TestWhatTheCapDoesNotBound:
    """The cap bounds MARGIN. What it does not bound is exposure, and the first
    draft of this class named the wrong backstop for it (the module docstring
    above says which and why); each claim here is a drive of the live code."""

    def test_it_cannot_fire_at_shipped_caps_either_way(self):
        """Read from the live constants, so a raised cap fails here first. The
        highest leverage an order can run at is the MAX_LEVERAGE ceiling
        (driven two tests down)."""
        from bot.config import CONFIG
        from bot.core.leverage import leverage_ceiling
        from bot.core.live_executor import MICRO_MAX_POSITION_USD

        cap = default_demo_profile().max_margin_usd
        top = leverage_ceiling(CONFIG.exchange)
        assert MICRO_MAX_POSITION_USD < cap, (MICRO_MAX_POSITION_USD, cap)
        assert MICRO_MAX_POSITION_USD * top < cap, (
            f"the notional at the {top}x ceiling now reaches the ${cap:,.0f} cap: "
            "Lock 4 is a margin ceiling and this arithmetic is the reason the "
            "rename was a name rather than a behaviour change")

    def test_at_the_highest_leverage_an_order_can_run_the_cap_permits_that_much(self):
        """The field comment's own figure, driven: $10,000 x the 10x ceiling."""
        from bot.config import CONFIG
        from bot.core.leverage import leverage_ceiling

        cap = default_demo_profile().max_margin_usd
        assert _authorize(cap).granted
        assert cap * leverage_ceiling(CONFIG.exchange) == 100_000.0

    def test_the_f3_hard_block_is_the_ceilings_backstop(self):
        """It used to check the order's arithmetic and nothing about its
        leverage: a consistent order at six times `max_leverage` PASSED. It
        reads the ceiling alone now, so that order is BLOCKED, one at the
        ceiling passes, and the arithmetic half (a doubled quantity) is kept.
        `tests/test_max_leverage_is_the_ceiling.py` drives the rest."""
        from bot.config import CONFIG
        from bot.core.live_executor import MICRO_MAX_POSITION_USD, LiveExecutor

        ex = LiveExecutor.__new__(LiveExecutor)
        market = {"limits": {"amount": {"min": 0.0}, "cost": {"min": 0.0}}}
        price, margin = 100.0, MICRO_MAX_POSITION_USD
        ceil = int(CONFIG.exchange.max_leverage)
        above = ex._notional_boundary_gate(
            "BTC/USDT", margin * 6 * ceil / price, price, margin, 6 * ceil, market)
        assert above and "BLOCKED" in above, above
        at = ex._notional_boundary_gate(
            "BTC/USDT", margin * ceil / price, price, margin, ceil, market)
        assert at is None, at
        doubled = ex._notional_boundary_gate(
            "BTC/USDT", 2 * margin * ceil / price, price, margin, ceil, market)
        assert doubled and "BLOCKED" in doubled, doubled

    def test_max_leverage_is_read_in_two_places_and_both_are_the_ceiling(self):
        """The reads pin, kept here so a THIRD reader of the field arrives by
        name: the leaf's reading, which every placement and the F-3 block ask,
        and the config's own clamp of its default and floor under it. Before
        2026-09-28 the one read was a `max()` arm in the gate, and this test's
        predecessor pinned that it bound nothing."""
        reads = _reads_of_max_leverage()
        assert set(reads) == {"bot/core/leverage.py:leverage_ceiling",
                              "bot/config.py:__post_init__"}, reads

    def test_what_does_bound_it_is_the_standard_every_order_is_lowered_from(self):
        """Three of the four readers past the standard, driven as reduce-only;
        the fourth (the quality ladder) has a suite of its own. The override's
        own 20x backstop stays, and the standard it feeds is capped at the
        ceiling before any of them read it."""
        from bot.config import CONFIG, RUNTIME
        from bot.core.leverage import (
            RISK_CAP_ATTR,
            apply_margin_risk_cap,
            leverage_ceiling,
            operator_standard,
            resolve_user_leverage,
        )

        std = int(CONFIG.exchange.default_leverage)
        assert resolve_user_leverage(60, std) == std  # a preference only lowers
        assert resolve_user_leverage(None, std) == std
        assert resolve_user_leverage(2, std) == min(2, std)
        assert apply_margin_risk_cap(std, SimpleNamespace(**{RISK_CAP_ATTR: 60})) == std
        assert apply_margin_risk_cap(std, SimpleNamespace(**{RISK_CAP_ATTR: 2})) == min(2, std)
        was = RUNTIME.leverage_override
        try:
            RUNTIME.leverage_override = 60
            assert RUNTIME.leverage_override == RUNTIME.LEVERAGE_OVERRIDE_MAX == 20
            assert operator_standard(CONFIG.exchange, RUNTIME.leverage_override).leverage \
                == leverage_ceiling(CONFIG.exchange)
        finally:
            RUNTIME.leverage_override = was
