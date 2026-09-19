"""The live margin bounds are derived from the account, not typed once.

MICRO_MAX_POSITION_USD, MICRO_MAX_TOTAL_EXPOSURE and the capital-buffer
reserve were flat absolute dollars read once at import, so they said the same
thing to a $200 account and a $20,000 one: the first is refused a position its
balance supports, the second is held to a ceiling a twentieth of its book. The
SIZE was never the problem -- `_evaluate_locked` already sizes off the account
-- it is the CEILING that knew nothing, and it lands on that sizing at one
line (`position_usd = min(position_usd, max_position_usd)`).

WHAT IS DRIVEN HERE
-------------------
* the reading's three bases, and that a READ 0.0 is a measurement
* that an UNREAD balance never widens and never narrows a bound
* that arming the flag alone can only TIGHTEN, and growth needs a second
  number the operator types
* that the cap, the clamp, the engine's execution cap and the card all read
  ONE function -- proved by PATCHING it, because a byte-identical copy agrees
  with every fixture and diverges on the first edit to either
* that the reserve is a share of the ACCOUNT when one was read, of the
  configured limit when none was, and that the audit line says which
"""

import ast
import asyncio
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core import live_executor as lx
from bot.core import size_bounds as sb
from tests.source_scan import code_only

EXEC_RAW = open("bot/core/live_executor.py", encoding="utf-8").read()
EXEC_SRC = code_only(EXEC_RAW)
ENGINE_SRC = code_only(open("bot/core/engine.py", encoding="utf-8").read())


def _body(raw: str, name: str) -> str:
    """One function's own source, bounded by its `ast.FunctionDef`.

    The first draft sliced with `src.index("    # ── Order idempotency")` --
    an anchor `code_only` had already blanked, because blanking comments is
    what it is for. That is CLAUDE.md's own recorded trap ("not a comment
    that matched, a comment that was GONE"), and a character window is the
    other half: "a boundary that is whatever happens to be next is a boundary
    that manufactures accusations".

    The segment is taken from the RAW source and the comments are blanked
    AFTER: `code_only` also blanks docstrings, so a class whose body opens
    with one no longer parses, and the second draft failed on that.
    """
    tree = ast.parse(raw)
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == name]
    assert len(found) == 1, f"{name}: {len(found)} definitions, not one"
    return code_only(ast.get_source_segment(raw, found[0]) or "")


def _clause(why: str, label: str) -> str:
    """The parenthesised word in ONE clause of a `why` sentence.

    `"(ceiling)" in why` is satisfied by whichever clause happens to say it,
    so an assertion written that way passes for a reason unrelated to the
    bound it names. The mutation round is what said so.
    """
    for part in why.split(": ", 1)[-1].split(", "):
        if part.startswith(label):
            return part.rsplit(" ", 1)[-1]
    raise AssertionError(f"no {label!r} clause in {why!r}")


def _cfg(**kw):
    """A config stand-in carrying only what `resolve` reads."""
    base = dict(
        max_live_position_usd=100.0,
        max_live_total_exposure_usd=500.0,
        balance_relative_bounds_enabled=False,
        balance_bounds_per_trade_pct=10.0,
        balance_bounds_total_pct=50.0,
        balance_bounds_reserve_pct=20.0,
        balance_bounds_max_position_usd=100.0,
        balance_bounds_max_total_usd=500.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestTheReading:
    def test_off_is_the_operator_figures_byte_for_byte(self):
        b = sb.resolve(20_000.0, _cfg())
        assert (b.per_trade_usd, b.total_usd) == (100.0, 500.0)
        assert b.basis == "flat"
        assert b.available_usd is None, "nothing was read, so nothing is quoted"
        assert b.reserve_usd is None

    def test_an_unread_balance_never_widens_and_never_narrows(self):
        """The one direction a cap must not move on evidence nobody has.

        `clamp_to_free_margin` already refuses an unreadable free margin one
        layer down, with its own two reasons, so refusing again here would be
        a second answer to one question.
        """
        b = sb.resolve(None, _cfg(balance_relative_bounds_enabled=True))
        assert (b.per_trade_usd, b.total_usd) == (100.0, 500.0)
        assert b.basis == "unread"
        assert b.reserve_usd is None
        assert "not read" in b.why

    def test_a_read_zero_is_a_measurement_and_bounds_to_zero(self):
        """Fully-deployed capital and an empty wallet are real states.

        Folding them into `unread` would be the defect this whole repository
        is built around, at the one reader that decides whether money moves.
        """
        b = sb.resolve(0.0, _cfg(balance_relative_bounds_enabled=True))
        assert b.basis == "balance"
        assert b.per_trade_usd == 0.0
        assert b.total_usd == 0.0
        assert b.available_usd == 0.0
        assert b.reserve_usd == 0.0

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0])
    def test_a_figure_that_is_not_money_is_unread_not_zero(self, bad):
        b = sb.resolve(bad, _cfg(balance_relative_bounds_enabled=True))
        assert b.basis == "unread"
        assert (b.per_trade_usd, b.total_usd) == (100.0, 500.0)

    def test_arming_the_flag_alone_can_only_tighten(self):
        """THE WHOLE SAFETY ARGUMENT, driven in both directions.

        The ceilings default to the flat figures, so a $20,000 account is
        still held at the operator's $100 and a $200 one gets a bound its
        balance supports. No position on any account grows because somebody
        set one environment variable.
        """
        cfg = _cfg(balance_relative_bounds_enabled=True)
        big = sb.resolve(20_000.0, cfg)
        assert (big.per_trade_usd, big.total_usd) == (100.0, 500.0)
        assert big.basis == "balance", "it was read; the ceiling is what bit"
        # Per CLAUSE, not per sentence: `"(ceiling)" in why` was satisfied by
        # the TOTAL clause alone, so the mutation that made the per-trade one
        # always say "balance" survived a green suite.
        assert _clause(big.why, "per-trade") == "(ceiling)"
        assert _clause(big.why, "total") == "(ceiling)"

        small = sb.resolve(200.0, cfg)
        assert small.per_trade_usd == 20.0
        assert small.total_usd == 100.0
        assert _clause(small.why, "per-trade") == "(balance)"
        assert _clause(small.why, "total") == "(balance)"

    def test_each_clause_names_its_own_bound(self):
        """One bound at its ceiling, the other still on the balance."""
        cfg = _cfg(balance_relative_bounds_enabled=True,
                   balance_bounds_max_total_usd=10_000.0)
        b = sb.resolve(1_500.0, cfg)
        assert b.per_trade_usd == 100.0, "10% of 1500 is over the $100 ceiling"
        assert b.total_usd == 750.0, "50% of 1500 is under the $10k ceiling"
        assert _clause(b.why, "per-trade") == "(ceiling)"
        assert _clause(b.why, "total") == "(balance)"

    def test_growth_needs_a_second_number_the_operator_types(self):
        cfg = _cfg(balance_relative_bounds_enabled=True,
                   balance_bounds_max_position_usd=2_000.0,
                   balance_bounds_max_total_usd=10_000.0)
        big = sb.resolve(20_000.0, cfg)
        assert big.per_trade_usd == 2_000.0
        assert big.total_usd == 10_000.0
        # ...and the small account is unchanged by the growth half.
        assert sb.resolve(200.0, cfg).per_trade_usd == 20.0

    def test_the_callers_flat_figures_win_over_config(self):
        """`live_executor` holds the constants a long list of guards patches.

        Re-reading config inside the leaf would mean two answers to "what is
        the flat cap", and the patched one would stop binding.
        """
        b = sb.resolve(None, _cfg(balance_relative_bounds_enabled=True),
                       flat_per_trade=1_000.0, flat_total=5_000.0)
        assert (b.per_trade_usd, b.total_usd) == (1_000.0, 5_000.0)

    def test_a_stale_config_ceiling_cannot_retighten_a_raised_flat_cap(self):
        """`max(ceiling, flat)` is a floor on the CEILING, not on the bound.

        With the caller's flat cap raised to $1,000 and the config ceiling
        left at its $100 default, a $20,000 account must not be re-tightened
        to $100 by a number the caller never asked to be bound by.
        """
        cfg = _cfg(balance_relative_bounds_enabled=True)
        b = sb.resolve(20_000.0, cfg, flat_per_trade=1_000.0, flat_total=5_000.0)
        assert b.per_trade_usd == 1_000.0
        assert b.total_usd == 5_000.0

    def test_every_basis_is_in_the_vocabulary_and_each_is_reachable(self):
        cfg_on = _cfg(balance_relative_bounds_enabled=True)
        seen = {
            sb.resolve(20_000.0, _cfg()).basis,
            sb.resolve(None, cfg_on).basis,
            sb.resolve(1.0, cfg_on).basis,
        }
        assert seen == set(sb.BASES)

    def test_the_why_never_carries_a_driver_message(self):
        for args in ((20_000.0, _cfg()), (None, _cfg(balance_relative_bounds_enabled=True)),
                     (7.5, _cfg(balance_relative_bounds_enabled=True))):
            why = sb.resolve(*args).why
            assert why and why == why.strip()
            assert "Traceback" not in why and "Error" not in why


class TestOneReadingThreeReaders:
    """A byte-identical copy agrees with every fixture. Patch and read back."""

    def _patched(self, per_trade=42.0, total=77.0):
        calls = []

        def fake(available_usd=None):
            calls.append(available_usd)
            return sb.SizeBounds(per_trade_usd=per_trade, total_usd=total,
                                 reserve_usd=None, basis="flat",
                                 available_usd=None, why="planted")
        return fake, calls

    def test_the_preflight_refuses_on_the_one_bound(self):
        fake, calls = self._patched()
        real = lx.size_bounds_for
        lx.size_bounds_for = fake
        try:
            ex = lx.LiveExecutor()
            err = ex._preflight_check(50.0, available_usd=900.0)
        finally:
            lx.size_bounds_for = real
        assert err is not None and "$42.00" in err, err
        assert calls == [900.0], "the available margin has to reach the bound"

    def test_the_total_cap_refuses_on_the_one_bound(self):
        fake, _ = self._patched(per_trade=1_000.0, total=77.0)
        real = lx.size_bounds_for
        lx.size_bounds_for = fake
        try:
            ex = lx.LiveExecutor()
            ex._positions = {"t1": SimpleNamespace(
                trade_id="t1", symbol="BTC/USDT", status="open",
                cost_usd=70.0, direction="LONG")}
            err = ex._preflight_check(50.0)
        finally:
            lx.size_bounds_for = real
        assert err is not None and "$77.00" in err, err

    def test_the_engines_execution_cap_is_the_one_bound(self):
        """Both engine sites, read as CALLS rather than as a literal."""
        tree = ast.parse(ENGINE_SRC)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "size_bounds_for"]
        assert len(calls) == 3, [ast.unparse(c) for c in calls]
        for c in calls:
            assert ast.unparse(c.func).endswith("_live_executor_mod.size_bounds_for")

    def test_the_live_path_hands_the_ceiling_the_balance_it_read(self):
        """The 4th argument is the figure THIS confirm already read.

        `test_both_sizing_paths_use_it` counts the CALLS and says nothing
        about their arguments, so dropping the available figure survived it:
        the live ceiling then fell back to the flat cap and a high-conviction
        target above what the account can carry was raised here and silently
        clamped back at the executor.

        The first draft asserted the literal name `_avail_recheck`, and the
        rename to a NamedTuple field broke it while the property held
        throughout -- a scan measuring the SPELLING rather than the claim,
        which is `test_unread_mark_is_not_break_even`'s recorded lesson. The
        name the recheck was bound to is DERIVED here, so the argument has to
        be that same read's own `available_usd` however either is spelled.
        """
        tree = ast.parse(ENGINE_SRC)
        # Whatever the one `await self._live_recheck_context(...)` was
        # assigned to. A second read in one confirm would be two answers, so
        # there must be exactly one.
        bound = [n.targets[0].id for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                 and isinstance(n.value, ast.Await)
                 and isinstance(n.value.value, ast.Call)
                 and isinstance(n.value.value.func, ast.Attribute)
                 and n.value.value.func.attr == "_live_recheck_context"]
        assert len(bound) == 1, bound

        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_high_conviction_margin"]
        four = [c for c in calls if len(c.args) == 4]
        assert len(four) == 1, [ast.unparse(c) for c in calls]
        assert ast.unparse(four[0].args[3]) == f"{bound[0]}.available_usd"

    def test_the_recheck_context_carries_the_available_margin(self):
        """DRIVEN on the operator branch: returning None survived every
        assertion about the tuple's first two figures."""
        from bot.core.engine import RuneClawEngine

        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng.live_executor = SimpleNamespace(open_positions=[], user_id=None)
        eng._user_executors = {}
        eng.live_balance_cached = lambda: {"total": 5_000.0, "free": 4_120.0}
        eng._executor_for = lambda uid: eng.live_executor

        real_live = type(CONFIG).is_live
        type(CONFIG).is_live = lambda self: True
        try:
            _rc = asyncio.run(eng._live_recheck_context(""))
            eq, avail = _rc.equity, _rc.available_usd
        finally:
            type(CONFIG).is_live = real_live
        assert eq == 5_000.0
        assert avail == 4_120.0, "the bound is derived from THIS figure"

    def test_the_card_prints_the_bound_and_not_a_constant(self):
        card = code_only(open("bot/skills/skill_registry.py", encoding="utf-8").read())
        assert "size_bounds_for(" in card
        assert "MICRO_MAX_POSITION_USD" not in card, (
            "the card printed the flat constant to every account")
        assert "MICRO_MAX_TOTAL_EXPOSURE" not in card

    def test_the_card_asks_the_bound_with_the_ACCOUNTS_available_balance(
            self, monkeypatch):
        """DRIVEN, because a scan cannot see WHICH argument reaches the bound.

        `size_bounds_for(None)` passes every source assertion in this class
        and prints the operator's flat figures to a $20,000 account, which is
        the defect the whole slice removes. So the card is rendered and the
        argument is read off the call.
        """
        import bot.core.live_executor as _lx_mod

        from tests.test_chat_prompt_describes_only_the_callers_book import (
            TestTheTools,
        )

        seen = []
        real = _lx_mod.size_bounds_for

        def spy(available_usd=None):
            seen.append(available_usd)
            return real(available_usd)

        monkeypatch.setattr(_lx_mod, "size_bounds_for", spy)

        h = TestTheTools()
        reg = h._cfg(monkeypatch)
        book = SimpleNamespace(open_positions=[], closed_positions=[],
                               closed_trades_read_failed=False)
        engine = h._tool_engine(viewer=lambda uid: book, equity=20_000.0,
                                balance={"total": 20_000.0, "free": 18_250.0})
        out = asyncio.run(reg.build_default_registry().get("playbook")
                          .execute(engine, user_id="555"))
        assert 18_250.0 in seen, seen
        assert "- Limits: <code>" in out, out

    def test_the_card_says_why_only_when_the_basis_is_not_flat(self):
        card = code_only(open("bot/skills/skill_registry.py", encoding="utf-8").read())
        assert '_lim.basis != "flat"' in card, (
            "a permanent 'operator limits' line under a healthy card is the row "
            "that trains a reader to stop reading the line")
        assert "_lim.why" in card


class TestTheExecutorClampReadsTheSameFigureAsTheRefusal:
    def test_execute_reads_the_available_margin_once(self):
        """Two reads in one order would be two answers to one question."""
        fn = _body(EXEC_RAW, "execute")
        assert fn.count("await self.available_margin()") == 1, fn.count(
            "await self.available_margin()")
        assert "size_usd = min(size_usd, _bounds.per_trade_usd)" in fn
        assert "available_usd=_avail" in fn, (
            "the preflight must refuse on the figure the clamp used")


class TestTheAvailableMarginReading:
    def _ex(self, answers):
        ex = lx.LiveExecutor()
        seq = list(answers)

        async def fetch():
            got = seq.pop(0)
            if isinstance(got, Exception):
                raise got
            return got
        ex.fetch_balance = fetch
        return ex

    def test_it_is_three_valued_off_the_payload(self):
        ex = self._ex([{"free": 412.5, "total": 900.0}])
        assert asyncio.run(ex.available_margin()) == 412.5

    def test_an_absent_free_key_is_none_not_zero(self):
        """RC-2026-017: `.get("free", 0)` minted a measured 0.0 for a venue
        that never reported the balance-coin entry."""
        ex = self._ex([{"total": 900.0}])
        assert asyncio.run(ex.available_margin()) is None

    def test_a_read_zero_survives_as_a_reading(self):
        ex = self._ex([{"free": 0.0, "total": 900.0}])
        assert asyncio.run(ex.available_margin()) == 0.0

    def test_a_failed_read_is_none_and_never_raises(self):
        ex = self._ex([RuntimeError("venue down")])
        assert asyncio.run(ex.available_margin()) is None

    def test_it_is_cached_within_the_ttl(self):
        ex = self._ex([{"free": 10.0}, {"free": 999.0}])
        assert asyncio.run(ex.available_margin()) == 10.0
        assert asyncio.run(ex.available_margin()) == 10.0

    def test_a_failed_read_does_not_revive_an_expired_reading(self):
        """A stale figure presented as the account's free margin NOW is the
        defect this bound exists to remove."""
        import time as _t
        ex = self._ex([{"free": 10.0}, RuntimeError("venue down")])
        assert asyncio.run(ex.available_margin()) == 10.0
        ex._avail_margin_cache = (_t.monotonic() - lx.LiveExecutor._AVAIL_MARGIN_TTL_SEC - 1,
                                  10.0)
        assert asyncio.run(ex.available_margin()) is None


class TestTheReserveIsAShareOfTheAccount:
    def test_it_is_the_balance_when_one_was_read(self):
        b = sb.resolve(1_000.0, _cfg(balance_relative_bounds_enabled=True))
        assert b.reserve_usd == 200.0, "20% of the ACCOUNT"

    def test_it_is_none_when_no_balance_was_read(self):
        """There is no balance to reserve a share OF, and quoting a share of
        the CEILING is the defect this replaces."""
        assert sb.resolve(None, _cfg(balance_relative_bounds_enabled=True)).reserve_usd is None
        assert sb.resolve(1_000.0, _cfg()).reserve_usd is None

    def test_the_preflight_names_which_basis_it_warned_on(self):
        fn = _body(EXEC_RAW, "_preflight_check")
        assert "MIN_RESERVE_PCT" not in fn, (
            "a percent OF A CONSTANT read like a percent and was not one")

    @pytest.mark.parametrize("reserve,expect_basis,expect_reserve", [
        # $900 available, $700 committed, $50 more -> $150 left, under both
        # targets and above zero, which is what the WARNING branch needs.
        (200.0, "available balance", 200.0),
        (None, "the configured total limit", 160.0),   # 20% of the $800 total
    ])
    def test_the_warning_is_driven_and_records_its_basis(
            self, reserve, expect_basis, expect_reserve, monkeypatch):
        """DRIVEN, because a scan cannot see which branch runs.

        The source assertion held under `if False:` around the balance arm --
        both literals are still in the file -- so the mutation survived a
        green suite while every buffer warning quoted a share of a constant.
        """
        recorded = []
        monkeypatch.setattr(
            lx, "audit",
            lambda *a, **k: recorded.append(k.get("data") or {}))
        monkeypatch.setattr(lx, "size_bounds_for", lambda available_usd=None:
                            sb.SizeBounds(per_trade_usd=1_000.0,
                                          total_usd=800.0,
                                          reserve_usd=reserve,
                                          basis="balance" if reserve else "flat",
                                          available_usd=900.0 if reserve else None,
                                          why="planted"))
        ex = lx.LiveExecutor()
        ex._positions = {"t1": SimpleNamespace(
            trade_id="t1", symbol="BTC/USDT", status="open",
            cost_usd=700.0, direction="LONG")}
        # 700 committed + 50 more leaves less than either reserve target,
        # and still under the $800 total, so the WARNING is what fires.
        assert ex._preflight_check(50.0) is None
        warn = [d for d in recorded if "reserve_basis" in d]
        assert warn, recorded
        assert warn[0]["reserve_basis"] == expect_basis
        assert round(warn[0]["reserve"], 2) == expect_reserve


class TestTodayIsUnchangedByDefault:
    def test_the_stock_config_bounds_are_the_stock_constants(self):
        b = lx.size_bounds_for(None)
        assert b.per_trade_usd == lx.MICRO_MAX_POSITION_USD
        assert b.total_usd == lx.MICRO_MAX_TOTAL_EXPOSURE
        assert b.basis == "flat"

    def test_the_flag_is_off_by_default(self):
        """A live-money bound is not widened by a deploy nobody decided on."""
        assert CONFIG.execution.balance_relative_bounds_enabled is False

    def test_a_read_balance_changes_nothing_while_the_flag_is_off(self):
        assert lx.size_bounds_for(20_000.0).per_trade_usd == lx.MICRO_MAX_POSITION_USD
        assert lx.size_bounds_for(5.0).per_trade_usd == lx.MICRO_MAX_POSITION_USD

    def test_the_shipped_growth_ceilings_are_the_shipped_flat_caps(self):
        """Arming the flag alone cannot raise any position ON THIS BUILD.

        The tightening claim was driven against config STAND-INS, so nothing
        checked what the shipped defaults are -- and the mutation that gave
        SIZE_BOUNDS_MAX_POSITION_USD a $10,000 default survived a green
        suite. That mutation makes one env var a 100x raise.
        """
        ex = CONFIG.execution
        assert ex.balance_bounds_max_position_usd == ex.max_live_position_usd
        assert ex.balance_bounds_max_total_usd == ex.max_live_total_exposure_usd

    def test_the_module_constants_decide_the_flat_figures(self):
        """A guard that patches MICRO_MAX_POSITION_USD must still bind.

        `size_bounds_for` hands the leaf this module's constants rather than
        config's; deleting that survived every fixture, because the two agree
        until one is patched -- which is the whole reason the parameter
        exists.
        """
        real = lx.MICRO_MAX_POSITION_USD
        lx.MICRO_MAX_POSITION_USD = 777.0
        try:
            assert lx.size_bounds_for(None).per_trade_usd == 777.0
        finally:
            lx.MICRO_MAX_POSITION_USD = real
