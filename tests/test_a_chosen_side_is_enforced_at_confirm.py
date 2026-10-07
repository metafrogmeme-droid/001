"""A strategy's side rule is enforced at confirm.

#483 made Dip Sniper short-only and Momentum Hunter long-only, and told the
person who arms one with `/mystrategy` that "trades you confirm that break
its rules will be refused". The confirm gate filed `direction` under
`scan_only` and the engine never handed it the idea's side, so a LONG
confirmed under Dip Sniper went on to execution, live when live is on. A
TradeIdea carries its side; the gate's own rule is to enforce what the
confirm-time facts can evaluate.

The community gate had the side and read it wrong. `Direction` is a
`(str, Enum)`, and `str(Direction.LONG).upper()` is `"DIRECTION.LONG"`, so a
long-only community strategy refused every LONG it allowed.

`strategy_gate.side_rule` and `idea_side` are now the one reading of a side
rule and an idea's side: both confirm gates, `/run` and the backtest use
them. A rule that is set but unreadable, or an idea whose side cannot be
read, is refused, never admitted both ways.

Driven through the real `confirm_trade` with live mode on, and the real
`/mystrategy` reply.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.core import strategy_gate, user_strategy_store
from bot.skills.skill_registry import RunStrategySkill
from bot.utils.models import Direction
from tests.test_a_paper_users_first_confirm_is_practice import _armed
from tests.test_a_practice_confirm_reads_the_practice_book import _confirm, _uid
from tests.test_a_self_admitted_confirm_is_never_placed_live import _Record

PRESETS = RunStrategySkill.PRESETS
LIVE = "✅ LIVE order placed: BTC/USDT LONG"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


# ── the one reading ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, side", [
    ("long_only", "LONG"), ("short_only", "SHORT"), ("LONG", "LONG"),
    ("short", "SHORT"), ("Long-Only", "LONG"),
    (None, None), ("", None), ("   ", None),
    ("both", ""), ("longonly", ""),
])
def test_a_side_rule_reads_as_one_side_none_or_unreadable(raw, side):
    assert strategy_gate.side_rule(raw) == side


@pytest.mark.parametrize("direction, side", [
    (Direction.LONG, "LONG"), (Direction.SHORT, "SHORT"),
    ("LONG", "LONG"), ("short", "SHORT"),
    # The shape the community gate used to read: the str() of the enum.
    (str(Direction.LONG), None),
    (None, None), ("", None), (1, None), ("SIDEWAYS", None),
])
def test_an_ideas_side_is_its_value_never_its_str(direction, side):
    assert strategy_gate.idea_side(direction) == side


# ── the preset gate ───────────────────────────────────────────────────────

def test_a_short_only_preset_refuses_a_long_and_allows_a_short():
    preset = PRESETS["dip sniper"]
    bad = strategy_gate.check_confirm("dip sniper", preset, "BTCUSDT", 0.9, Direction.LONG)
    assert bad["ok"] is False
    assert "short-only" in bad["reason"] and "LONG refused" in bad["reason"]
    ok = strategy_gate.check_confirm("dip sniper", preset, "BTCUSDT", 0.9, Direction.SHORT)
    assert ok["ok"] is True and "direction" in ok["enforced"]


def test_a_long_only_preset_allows_a_long_and_refuses_a_short():
    preset = PRESETS["momentum hunter"]
    ok = strategy_gate.check_confirm("momentum hunter", preset, "BTCUSDT", 0.5, Direction.LONG)
    assert ok["ok"] is True
    bad = strategy_gate.check_confirm("momentum hunter", preset, "BTCUSDT", 0.5, Direction.SHORT)
    assert bad["ok"] is False and "long-only" in bad["reason"]


def test_a_side_nobody_can_read_is_refused_under_a_side_rule():
    preset = PRESETS["dip sniper"]
    v = strategy_gate.check_confirm("dip sniper", preset, "BTCUSDT", 0.9)
    assert v["ok"] is False and "side could not be read" in v["reason"]
    junk = dict(preset, direction="longonly")
    v2 = strategy_gate.check_confirm("dip sniper", junk, "BTCUSDT", 0.9, Direction.SHORT)
    assert v2["ok"] is False and "could not be read" in v2["reason"]


def test_a_preset_with_no_side_rule_takes_either_side():
    preset = PRESETS["safe scalper"]
    for d in (Direction.LONG, Direction.SHORT):
        assert strategy_gate.check_confirm("safe scalper", preset, "ETHUSDT", 0.9, d)["ok"]


def test_rsi_is_named_as_the_backtests_never_as_a_scan_or_confirm_gate():
    v = strategy_gate.check_confirm(
        "dip sniper", PRESETS["dip sniper"], "BTCUSDT", 0.9, Direction.SHORT)
    assert v["backtest_only"] == ["rsi_min"]
    assert "rsi_min" not in v["scan_only"] and "direction" not in v["scan_only"]
    confirm, scan = strategy_gate.describe_gates(PRESETS["dip sniper"])
    assert confirm == ["direction", "confidence>=70%"] and scan == ["regime"]
    assert strategy_gate.backtest_gates(PRESETS["safe scalper"]) == ["rsi_min"]


# ── the community gate ────────────────────────────────────────────────────

def _community(side):
    return {"slug": "one-side", "label": "One Side", "gates": {"direction": side}}


def test_a_long_only_community_strategy_allows_a_real_long():
    """The old reading compared "DIRECTION.LONG" with "LONG" and refused it."""
    v = strategy_gate.check_custom(_community("long_only"), "BTCUSDT", 0.9, Direction.LONG)
    assert v["ok"] is True, v


def test_a_long_only_community_strategy_refuses_a_real_short():
    v = strategy_gate.check_custom(_community("long_only"), "BTCUSDT", 0.9, Direction.SHORT)
    assert v["ok"] is False and "SHORT refused" in v["reason"]


# ── the confirm path ──────────────────────────────────────────────────────

def _drive_armed(arm):
    host, engine, idea, _ = _armed("trader")
    engine._user_store = _Record("trader", "999")
    uid = _uid()
    arm(uid)
    assert idea.direction is Direction.LONG
    return _confirm(host, engine, idea, uid=uid), engine


def test_a_long_confirmed_under_a_short_only_preset_is_refused(state):
    result, engine = _drive_armed(
        lambda uid: user_strategy_store.set_pref(uid, "dip sniper", PRESETS.keys()))
    assert "short-only" in result and "LONG refused" in result, result
    engine.live_executor.execute.assert_not_awaited()
    engine.compliance.issue_approval_token.assert_not_called()


def test_a_long_confirmed_under_a_long_only_preset_is_placed_live(state):
    result, engine = _drive_armed(
        lambda uid: user_strategy_store.set_pref(uid, "momentum hunter", PRESETS.keys()))
    assert result == LIVE, result
    engine.live_executor.execute.assert_awaited_once()


def test_a_long_under_a_long_only_community_strategy_is_placed_live(state):
    result, engine = _drive_armed(lambda uid: user_strategy_store.set_custom(
        uid, "longs-only", "Longs Only", {"direction": "long_only"}))
    assert result == LIVE, result


def test_a_long_under_a_short_only_community_strategy_is_refused(state):
    result, engine = _drive_armed(lambda uid: user_strategy_store.set_custom(
        uid, "shorts-only", "Shorts Only", {"direction": "short_only"}))
    assert "short-only" in result and "LONG refused" in result, result
    engine.live_executor.execute.assert_not_awaited()


# ── what /mystrategy says ─────────────────────────────────────────────────

def _mystrategy(*args):
    from bot.skills.trading_commands import TradingCommands
    replies = []

    async def _reply(update, text, **k):
        replies.append(text)

    host = SimpleNamespace(_get_tg_id=lambda u: "424242", _reply=_reply)
    asyncio.run(TradingCommands._cmd_mystrategy.__wrapped__(
        host, SimpleNamespace(), SimpleNamespace(args=list(args))))
    return "\n".join(replies)


def test_arming_a_preset_names_its_side_rule_where_it_is_enforced(state):
    text = _mystrategy("dip", "sniper")
    enforced = next(line for line in text.splitlines() if line.startswith("Enforced at confirm"))
    assert "short only" in enforced and "confidence ≥ 70%" in enforced
    scan = next(line for line in text.splitlines() if line.startswith("Applied when /run scans"))
    assert "regime TREND_DOWN" in scan and "RSI" not in scan
    bt = next(line for line in text.splitlines() if line.startswith("Checked in the backtest only"))
    assert "RSI ≥ 35" in bt


def test_arming_a_preset_with_no_rsi_names_no_backtest_line(state):
    text = _mystrategy("momentum", "hunter")
    assert any(line.startswith("Enforced at confirm") and "long only" in line
               for line in text.splitlines()), text
    assert "Checked in the backtest only" not in text


# ── where RSI is said to bind ─────────────────────────────────────────────

@pytest.mark.parametrize("key, slug", [("dip sniper", "dip-sniper"),
                                       ("safe scalper", "safe-scalper")])
def test_every_surface_that_names_rsi_says_only_the_backtest_checks_it(key, slug):
    """`/run` holds no candle window and an idea carries no RSI, so neither
    can check the floor. The `/run` list and the card say where it binds."""
    from bot.core.strategy_catalog import get_agent
    desc = PRESETS[key]["desc"]
    assert "RSI ≥ 35 in the backtest only" in desc, desc
    how = get_agent(slug)["how"]
    assert "RSI at or above 35 (checked in the backtest only)" in how, how


def test_a_preset_without_rsi_says_nothing_about_the_backtest():
    from bot.core.strategy_catalog import get_agent
    assert "backtest only" not in PRESETS["momentum hunter"]["desc"]
    assert "backtest only" not in get_agent("momentum-hunter")["how"]


# ── /run reads the same side ──────────────────────────────────────────────

def test_run_keeps_only_the_side_the_preset_allows():
    """`/run momentum hunter` analyzes two measured spikes; the analyzer
    returns a LONG and a SHORT. Only the LONG is offered."""
    from bot.skills.manual_trade import build_manual_idea
    from bot.utils.models import MarketSignal

    long_idea = build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0)
    short_idea = build_manual_idea("SHORT", "ETH", 3000.0, 3100.0, 2800.0)
    ideas = {"BTC/USDT:USDT": long_idea, "ETH/USDT:USDT": short_idea}

    class _Scanner:
        async def scan(self):
            return [MarketSignal(symbol=s, price=1.0, change_pct_24h=8.0,
                                 volume_usd_24h=5_000_000.0, volume_spike=True,
                                 momentum_score=0.7, volume_spike_ratio=5.0)
                    for s in ideas]

    class _Engine:
        scanner = _Scanner()
        _last_strategy_setups: list = []
        _pending_ideas: dict = {}

        async def _analyze_signal(self, sig):
            return ideas[sig.symbol]

    engine = _Engine()
    asyncio.run(RunStrategySkill().execute(engine, strategy="momentum hunter"))
    assert list(engine._pending_ideas.values()) == [long_idea]
