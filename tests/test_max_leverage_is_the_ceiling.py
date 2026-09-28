"""MAX_LEVERAGE is the ceiling on the leverage any order is placed at.

Driven on 2026-09-28, before the change, on the shipped config (DEFAULT_LEVERAGE
5, MAX_LEVERAGE 10, the override backstop 20):

    /leverage set 15   -> standard 15x, and the venue was asked for 15x
    /leverage set 20   -> standard 20x, and the venue was asked for 20x
    F-3 notional gate  -> a consistent 20x order on $100 of margin PASSED, the
                          ceiling reading $2,100: `max(MAX_LEVERAGE, lev)` took
                          the order's own leverage

`MAX_LEVERAGE` had exactly one reader, that `max()`, and bound nothing; the
compliance-cap chapter measured it and filed the decision. The decision
(operator, 2026-09-28): a real ceiling.

ONE READING. `bot.core.leverage.operator_standard` is what every placement
starts from -- the `/leverage` override or the configured default, capped at
the ceiling -- and `LiveExecutor._standard_leverage`, the risk gate's
margin-risk block, `/leverage` and `/risk` all ask it. Every step after it (a
user's preference, dynamic scaling, the quality ladder, the margin-risk cap)
only lowers, so the cap there is the whole enforcement. The config places its
own default and floor under the ceiling where the fields are declared, so the
twenty raw readers of `default_leverage` -- the paper fill, the analyzer's stop
tightening, the notional estimates, the web ledger, the cards -- inherit it
with no edit of their own. And the executor's F-3 notional block reads the
ceiling ALONE now, so it is the backstop that measures the ceiling on the order
itself, after every step that could have raised the leverage.

What is NOT changed is stated: the override's own 20x backstop stays (a ceiling
above 20 is still not reachable by a command); the paper fill and the analyzer
still read the DEFAULT rather than the override, a pre-existing asymmetry filed
rather than folded into a slice about the ceiling; and the frozen benchmark runs
at 5x under a 10x ceiling, so the ceiling binds nothing there.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import logging
import re
import textwrap
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.config import CONFIG, RUNTIME, ExchangeConfig
from bot.core.leverage import (
    MAX_LEVERAGE_DEFAULT,
    StandardLeverage,
    leverage_ceiling,
    operator_standard,
)
from bot.core.live_executor import MICRO_MAX_POSITION_USD, LiveExecutor
from tests.source_scan import code_only
from tests.test_the_compliance_cap_is_the_quantity_it_checks import _reads_of_max_leverage
from tests.test_the_venue_gets_the_capped_leverage import _config_default

CEIL = int(CONFIG.exchange.max_leverage)
STD = int(CONFIG.exchange.default_leverage)
ABOVE = 2 * CEIL


@pytest.fixture(autouse=True)
def _clean_override():
    RUNTIME.leverage_override = None
    yield
    RUNTIME.leverage_override = None


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── the reading ─────────────────────────────────────────────────────────

class TestOneCeiling:
    def test_it_reads_the_configured_field(self):
        assert leverage_ceiling(SimpleNamespace(max_leverage=7)) == 7

    def test_an_absent_field_gets_the_declared_default(self):
        """A stand-in that names no ceiling gets the one `bot/config.py`
        declares, never "no ceiling": that is the loose direction, and a
        hand-written stand-in that forgot the attribute must not loosen
        anything. WHICH number is read off the config's own declaration, not
        off the constant under test -- `MIN_LEVERAGE_DEFAULT`'s own lesson."""
        declared = _config_default("MAX_LEVERAGE")
        assert MAX_LEVERAGE_DEFAULT == declared, (MAX_LEVERAGE_DEFAULT, declared)
        assert leverage_ceiling(SimpleNamespace()) == declared

    def test_junk_does_not_raise_into_the_money_path(self):
        assert leverage_ceiling(SimpleNamespace(max_leverage="x")) == MAX_LEVERAGE_DEFAULT
        assert leverage_ceiling(SimpleNamespace(max_leverage=0)) == 1

    def test_the_shipped_arrangement_the_drives_below_rely_on(self):
        """The standard sits under the ceiling and the ceiling under the
        override backstop, so `/leverage set` can reach the ceiling and a
        figure above it is a real input. Read off the live constants."""
        assert 1 < STD <= CEIL <= RUNTIME.LEVERAGE_OVERRIDE_MAX, (STD, CEIL)


class TestTheStandardIsUnderIt:
    def test_no_override_is_the_configured_default(self):
        std = operator_standard(CONFIG.exchange, None)
        assert std == StandardLeverage(STD, STD, "default", CEIL)
        assert not std.capped and std.source_note() == "configured default"

    def test_an_override_under_the_ceiling_is_the_standard(self):
        lower = max(1, CEIL - 1)
        std = operator_standard(CONFIG.exchange, lower)
        assert (std.leverage, std.requested, std.source) == (lower, lower, "override")
        assert not std.capped and std.source_note() == "runtime override"

    def test_an_override_above_the_ceiling_is_the_ceiling_and_says_so(self):
        std = operator_standard(CONFIG.exchange, ABOVE)
        assert std.leverage == CEIL and std.requested == ABOVE and std.capped
        assert std.source_note() == (
            f"runtime override {ABOVE}x, capped at the MAX_LEVERAGE ceiling {CEIL}x")

    def test_a_default_above_the_ceiling_is_the_ceiling_and_says_which(self):
        """Reachable only through a stand-in: the config itself places its
        default under the ceiling (driven below). The reading caps it anyway,
        because it is the placement reading and a broken invariant upstream
        must not break the ceiling with it."""
        cfg = SimpleNamespace(default_leverage=20, max_leverage=10)
        std = operator_standard(cfg, None)
        assert std == StandardLeverage(10, 20, "default", 10)
        assert std.source_note() == (
            "configured default 20x, capped at the MAX_LEVERAGE ceiling 10x")

    def test_an_unreadable_override_falls_to_the_default(self):
        std = operator_standard(CONFIG.exchange, "x")
        assert (std.leverage, std.source) == (STD, "default")


# ── the config places its own figures under it ───────────────────────────

class TestTheConfigPlacesTheDefaultUnderIt:
    def test_a_default_above_the_ceiling_is_placed_at_it_and_said(self, caplog):
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            cfg = ExchangeConfig(default_leverage=20, max_leverage=10)
        assert cfg.default_leverage == 10 and cfg.default_leverage_requested == 20
        said = [r.getMessage() for r in caplog.records if "MAX_LEVERAGE" in r.getMessage()]
        assert said and "DEFAULT_LEVERAGE=20" in said[0] and "MAX_LEVERAGE=10" in said[0], said

    def test_a_floor_above_the_ceiling_is_placed_at_it(self, caplog):
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            cfg = ExchangeConfig(min_leverage=12, max_leverage=10)
        assert cfg.min_leverage == 10
        assert any("MIN_LEVERAGE=12" in r.getMessage() for r in caplog.records)

    def test_a_default_under_the_ceiling_is_left_alone_and_nothing_is_said(self, caplog):
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            cfg = ExchangeConfig(default_leverage=5, max_leverage=10)
        assert cfg.default_leverage == 5 and cfg.default_leverage_requested == 5
        assert not any("MAX_LEVERAGE" in r.getMessage() for r in caplog.records)

    def test_the_live_config_holds_the_invariant(self):
        ex = CONFIG.exchange
        assert ex.default_leverage <= ex.max_leverage and ex.min_leverage <= ex.max_leverage
        assert ex.default_leverage_requested == ex.default_leverage

    def test_a_replaced_config_holds_it_too(self):
        """`dataclasses.replace` re-runs `__post_init__`, so the delegating
        stand-ins the risk suites build inherit the invariant."""
        cfg = dataclasses.replace(CONFIG.exchange, default_leverage=3 * CEIL)
        assert cfg.default_leverage == CEIL and cfg.default_leverage_requested == 3 * CEIL


# ── every placement starts under it ──────────────────────────────────────

def _bare_executor():
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._user_leverage_pref = None
    ex._last_atr_pct = {}
    return ex


class TestEveryPlacementStartsUnderIt:
    def test_an_override_above_the_ceiling_places_at_the_ceiling(self):
        """The drive that read 20 before the change."""
        RUNTIME.leverage_override = ABOVE
        assert RUNTIME.leverage_override == ABOVE     # the backstop admits it
        assert _bare_executor()._compute_target_leverage("BTC/USDT:USDT") == CEIL

    def test_an_override_under_it_is_placed_as_asked(self):
        lower = max(1, CEIL - 1)
        RUNTIME.leverage_override = lower
        assert _bare_executor()._compute_target_leverage("BTC/USDT:USDT") == lower

    def test_a_users_preference_cannot_lift_it(self):
        RUNTIME.leverage_override = ABOVE
        ex = _bare_executor()
        ex._user_leverage_pref = 3 * CEIL
        assert ex._compute_target_leverage("BTC/USDT:USDT") == CEIL

    def test_a_stand_in_default_above_the_ceiling_is_capped_too(self, monkeypatch):
        """The belt under the config's own clamp: a stand-in that breaks the
        invariant must not break the ceiling with it."""
        monkeypatch.setattr(le, "CONFIG", SimpleNamespace(exchange=SimpleNamespace(
            default_leverage=20, max_leverage=10, dynamic_leverage_enabled=False)))
        assert _bare_executor()._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_a_stand_in_that_names_no_ceiling_gets_the_declared_one(self, monkeypatch):
        monkeypatch.setattr(le, "CONFIG", SimpleNamespace(exchange=SimpleNamespace(
            default_leverage=200, dynamic_leverage_enabled=False)))
        assert _bare_executor()._compute_target_leverage("BTC/USDT:USDT") == MAX_LEVERAGE_DEFAULT

    def test_a_reclaimed_orders_intended_leverage_is_under_it(self):
        """The fill guard checks a reclaimed order against the standard the
        executor sets, which is now under the ceiling too."""
        RUNTIME.leverage_override = ABOVE
        pos = SimpleNamespace(origin="reclaimed", leverage=0, symbol="BTC/USDT:USDT")
        assert _bare_executor()._intended_fill_leverage(pos) == CEIL

    @pytest.mark.asyncio
    async def test_the_venue_is_asked_for_the_ceiling(self, tmp_path, monkeypatch):
        """The end the ceiling has to reach. Before the change the venue was
        asked for 20x under MAX_LEVERAGE=10."""
        from tests.test_leverage_standard import _executor, _mock_exchange
        monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
        RUNTIME.leverage_override = ABOVE
        ex = _executor(tmp_path)
        set_lev = AsyncMock()
        fetch_lev = AsyncMock(return_value={"longLeverage": CEIL})
        ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
        await ex._ensure_leverage("BTC/USDT:USDT")
        asked = [c.args[0] for c in set_lev.call_args_list]
        assert asked and set(asked) == {CEIL}, asked


# ── the F-3 block is the backstop ────────────────────────────────────────

_MARKET = {"limits": {"amount": {"min": 0.0}, "cost": {"min": 0.0}}}


def _gate(lev: int, qty_mult: float = 1.0):
    price, margin = 100.0, MICRO_MAX_POSITION_USD
    return LiveExecutor.__new__(LiveExecutor)._notional_boundary_gate(
        "BTC/USDT", qty_mult * margin * lev / price, price, margin, lev, _MARKET)


class TestTheBackstopMeasuresItOnTheOrder:
    def test_a_consistent_order_above_the_ceiling_is_blocked(self):
        """The drive that PASSED before the change, at 20x on $100."""
        verdict = _gate(ABOVE)
        assert verdict and "BLOCKED" in verdict, verdict

    def test_a_consistent_order_at_the_ceiling_passes(self):
        assert _gate(CEIL) is None

    def test_the_arithmetic_half_is_kept(self):
        """A quantity double what margin x leverage implies is still refused
        at a leverage the ceiling allows: the check audit F-3 was written for."""
        verdict = _gate(CEIL, qty_mult=2.0)
        assert verdict and "BLOCKED" in verdict, verdict

    def test_the_ceiling_binds_the_moment_it_is_lowered(self):
        """The inverse of the old `binds nothing` drive: set to 1, it refuses
        a consistent order at the shipped standard."""
        was = CONFIG.exchange.max_leverage
        object.__setattr__(CONFIG.exchange, "max_leverage", 1)  # frozen dataclass
        try:
            verdict = _gate(STD)
        finally:
            object.__setattr__(CONFIG.exchange, "max_leverage", was)
        assert verdict and "BLOCKED" in verdict, verdict

    def test_it_reads_the_ceiling_alone(self):
        """No `max()` arm with the order's own leverage, and the field itself
        is read in two places only: the leaf's reading and the config's own
        clamp. Every other site asks the reading."""
        assert set(_reads_of_max_leverage()) == {
            "bot/core/leverage.py:leverage_ceiling",
            "bot/config.py:__post_init__",
        }, _reads_of_max_leverage()
        src = code_only(inspect.getsource(LiveExecutor._notional_boundary_gate))
        tree = ast.parse(textwrap.dedent(src))
        max_calls = [ast.unparse(n) for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and ast.unparse(n.func) == "max"]
        assert not any("leverage_mult" in c for c in max_calls), max_calls
        assert "leverage_ceiling(CONFIG.exchange)" in src


# ── the risk gate measures the capped standard ───────────────────────────

class TestTheRiskGateMeasuresTheCappedStandard:
    @staticmethod
    def _cfg(monkeypatch):
        import bot.risk.risk_engine as re_mod
        base = re_mod.CONFIG
        exch = dataclasses.replace(base.exchange, default_leverage=5, min_leverage=2,
                                   max_leverage=10, dynamic_leverage_enabled=False)

        class _Cfg:
            exchange = exch

            def __getattr__(self, name):
                return getattr(base, name)

        monkeypatch.setattr(re_mod, "CONFIG", _Cfg())

    def test_the_margin_risk_cap_is_measured_at_the_ceiling_not_the_override(
            self, tmp_path, monkeypatch):
        """A 2% stop at 10x is 20% of margin, under the 30% cap; at the 20x the
        override asked for it would be 40% and REFUSED. The line names the
        leverage it measured at, and it is the one the venue is set to."""
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea
        self._cfg(monkeypatch)
        RUNTIME.leverage_override = 20
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                         stop_loss=98.0, take_profit=130.0, confidence=0.9,
                         reasoning="ceiling drive", source="test")
        engine = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                            state_file=str(tmp_path / "risk_state.json"))
        check = engine.evaluate(idea, atr=1.0)
        lines = [r for r in list(check.checks_passed) + list(check.checks_failed)
                 if r.startswith("MARGIN_RISK")]
        assert lines and "× 10x" in lines[0] and lines[0] in check.checks_passed, (
            lines, check.checks_failed)


# ── the cards say it ─────────────────────────────────────────────────────

def _leverage_cmd(args, *, admin=True, monkeypatch=None):
    from bot.skills.engine_ops_commands import EngineOpsCommands
    replies: list[str] = []

    async def _reply(update, text, **k):
        replies.append(text)

    host = SimpleNamespace(_is_admin=lambda u: admin, _get_tg_id=lambda u: "7",
                           _reply=_reply, engine=SimpleNamespace(_user_executors={}))
    _run(EngineOpsCommands._cmd_leverage.__wrapped__(
        host, SimpleNamespace(), SimpleNamespace(args=args)))
    return replies[-1]


class TestTheCardsSayIt:
    def test_set_above_the_ceiling_says_what_was_asked_and_what_is_placed(self):
        reply = _leverage_cmd(["set", str(ABOVE)])
        assert f"set to <b>{CEIL}x</b>" in reply, reply
        assert f"{ABOVE}x asked" in reply and "MAX_LEVERAGE" in reply, reply
        assert f"<b>{ABOVE}x</b>" not in reply, reply

    def test_set_above_both_ceilings_names_both(self):
        beyond = 3 * RUNTIME.LEVERAGE_OVERRIDE_MAX
        reply = _leverage_cmd(["set", str(beyond)])
        assert f"clamped from {beyond}x" in reply and "MAX_LEVERAGE" in reply, reply
        assert f"set to <b>{min(CEIL, RUNTIME.LEVERAGE_OVERRIDE_MAX)}x</b>" in reply, reply

    def test_set_under_the_ceiling_is_the_plain_reply(self):
        lower = max(1, CEIL - 1)
        reply = _leverage_cmd(["set", str(lower)])
        assert f"set to <b>{lower}x</b>." in reply and "MAX_LEVERAGE" not in reply, reply

    def test_the_card_prints_the_standard_the_ceiling_and_the_capping(self):
        RUNTIME.leverage_override = ABOVE
        card = _leverage_cmd([])
        assert (f"Standard: <b>{CEIL}x</b> (runtime override {ABOVE}x, capped at the "
                f"MAX_LEVERAGE ceiling {CEIL}x)") in card, card
        assert f"Ceiling: <b>{CEIL}x</b> (MAX_LEVERAGE)" in card, card
        top = min(RUNTIME.LEVERAGE_OVERRIDE_MAX, CEIL)
        assert f"takes 1-{top}" in card, card

    def test_the_card_with_no_override_names_the_default_and_the_ceiling(self):
        card = _leverage_cmd([])
        assert f"Standard: <b>{STD}x</b> (configured default)" in card, card
        assert f"Ceiling: <b>{CEIL}x</b>" in card, card

    def test_usage_names_the_reachable_range(self):
        top = min(RUNTIME.LEVERAGE_OVERRIDE_MAX, CEIL)
        assert _leverage_cmd(["set", "abc"]) == f"Usage: /leverage set <1-{top}>"

    def test_reset_names_the_default(self):
        RUNTIME.leverage_override = ABOVE
        reply = _leverage_cmd(["reset"])
        assert f"(<b>{STD}x</b>)" in reply, reply
        assert RUNTIME.leverage_override is None

    def test_a_users_preference_is_capped_at_the_placed_standard(self, monkeypatch):
        """The cap a preference is measured against is the standard the
        executor places at -- the override under the ceiling -- not the
        configured default, which is what the card used to read."""
        from bot.core import user_leverage_store
        monkeypatch.setattr(user_leverage_store, "set_pref", lambda uid, v: v)
        RUNTIME.leverage_override = ABOVE
        reply = _leverage_cmd(["set", str(3 * CEIL)], admin=False)
        assert f"is now <b>{CEIL}x</b> (capped at the operator {CEIL}x)" in reply, reply

    def test_the_risk_card_prints_both(self):
        from tests.test_the_risk_card_reads_the_leverage_it_shows import _live, _risk
        RUNTIME.leverage_override = ABOVE
        card = _risk(types.SimpleNamespace(open_positions=[_live(7, 10.0)]))
        line = next(x for x in card.splitlines() if "Leverage" in x and "in use" in x)
        assert f"standard {CEIL}x · ceiling {CEIL}x" in line, line
        RUNTIME.leverage_override = None
        card = _risk(types.SimpleNamespace(open_positions=[_live(7, 10.0)]))
        line = next(x for x in card.splitlines() if "Leverage" in x and "in use" in x)
        assert f"standard {STD}x · ceiling {CEIL}x" in line, line

    def test_the_risk_picture_tile_named_cap_carries_the_ceiling(self, monkeypatch):
        from unittest import mock

        from bot.skills.portfolio_commands import PortfolioCommands
        from tests.test_the_risk_card_reads_the_leverage_it_shows import _live, _Stand
        me = _Stand(types.SimpleNamespace(open_positions=[_live(7, 10.0)]))
        specs: list = []

        async def _photo(update, png, caption, reply_markup=None):
            return True
        me._send_photo = _photo

        def _render(spec):
            specs.append(spec)
            return b"png"
        with mock.patch.object(type(CONFIG), "is_live", lambda self: True), \
                mock.patch("bot.formatters.signal_card.render_stats_card", _render):
            asyncio.run(PortfolioCommands._cmd_risk(me, object(), object()))
        tile = next(x for x in specs[-1]["tiles"] if "Leverage" in str(x["label"]))
        assert tile["value"] == f"{CEIL}x", tile

    def test_the_renderer_reads_both_and_an_old_payload_as_before(self):
        from bot.warroom.warroom_bot import render_risk
        base = {"current_drawdown": 2.0, "drawdown_limit": 7.0, "max_open_trades": 5,
                "open_trades": 3, "leverage_in_use": 7.0}
        new = re.sub(r"<[^>]+>", "", render_risk({**base, "leverage_cap": 10,
                                                   "leverage_standard": 5})["text"])
        assert "7x in use (standard 5x · ceiling 10x)" in new, new
        old = re.sub(r"<[^>]+>", "", render_risk({**base, "leverage_cap": 5})["text"])
        assert "7x in use (standard 5x)" in old and "ceiling" not in old, old


# ── what went with it ────────────────────────────────────────────────────

class TestTheSecondClaimIsGone:
    def test_the_per_class_rows_carry_no_ceiling_of_their_own(self):
        """`order_rules.ASSET_RULES` carried a per-class `max_leverage`
        (Crypto 125) that nothing read. A second, unread claim about a
        ceiling beside the real one is two answers."""
        from bot.core.order_rules import ASSET_RULES
        assert ASSET_RULES and all("max_leverage" not in row for row in ASSET_RULES.values())

    def test_the_env_example_names_the_declared_default(self):
        import pathlib
        text = pathlib.Path(".env.example").read_text(encoding="utf-8")
        m = re.search(r"^# MAX_LEVERAGE=(\d+)$", text, re.M)
        assert m and int(m.group(1)) == _config_default("MAX_LEVERAGE"), m
