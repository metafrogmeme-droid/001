"""A preset the live bot does not run is nobody's strategy.

10 October. Arming ETH Moving-Average Trend (or Daily Volatility Rotation, or
ALT Sweep) with /mystrategy, or "Run on my bot" on the website, was accepted,
and the reply said its moving-average rule was "applied when /run scans".
/run places nothing for these presets: the live bot does not run their rule.
What the arming did was veto confirms against the preset's symbol list and
nothing else, so a house ETH idea was confirmed under the name "ETH
Moving-Average Trend" with no average read.

Now arming one is refused on both surfaces, /mystrategy lists them as not
armable instead of offering a command that refuses, the dashboard offers no
"Run on my bot" on their cards, and a selection armed before this refuses
every confirm, failing closed, with /mystrategy off as the remedy. Every
preset the live bot runs is unchanged. Which presets those are is one reading,
`strategy_catalog.live_runs`, so the lists here are derived from it.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bot.core import strategy_catalog as sc
from bot.core import strategy_gate
from bot.core import user_strategy_store as store
from bot.skills.skill_registry import RunStrategySkill
from bot.utils.models import Direction
from bot.web import user_gateway as ug

PRESETS = RunStrategySkill.PRESETS
UNRUN = sorted(k for k, c in PRESETS.items() if not sc.live_runs(c))
RUN = sorted(k for k, c in PRESETS.items() if sc.live_runs(c))
UID = "4242"


def test_the_lists_are_the_ones_this_names():
    assert UNRUN == ["alt sweep", "daily vol rotation", "eth ma trend", "limit scanner",
                     "multi trend"]
    assert "full scan" in RUN


# ── the confirm ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", UNRUN)
def test_an_armed_unrun_preset_refuses_every_confirm_with_the_remedy(key):
    cfg = PRESETS[key]
    inside = cfg["symbols"][0]
    for side in (Direction.LONG, Direction.SHORT):
        v = strategy_gate.check_confirm(key, cfg, inside, 0.99, side)
        assert v["ok"] is False and v["enforced"] == ["live_runs"]
        assert "does not run" in v["reason"] and "/mystrategy off" in v["reason"]


@pytest.mark.parametrize("key", RUN)
def test_a_preset_the_bot_runs_is_unchanged(key):
    cfg = PRESETS[key]
    syms = cfg.get("symbols")
    asset = syms[0] if isinstance(syms, list) and syms else "BTC/USDT:USDT"
    side = Direction.SHORT if cfg.get("direction") == "short_only" else Direction.LONG
    v = strategy_gate.check_confirm(key, cfg, asset, 0.99, side)
    assert v["ok"] is True, (key, v)
    assert "live_runs" not in v["enforced"]
    assert strategy_gate.arm_refusal(key, cfg) is None


# ── arming ──────────────────────────────────────────────────────────────────

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


def _mystrategy(args):
    from bot.skills.trading_commands import TradingCommands
    replies = []

    async def _reply(update, text, **k):
        replies.append(text)

    host = SimpleNamespace(_get_tg_id=lambda u: UID, _reply=_reply)
    asyncio.run(TradingCommands._cmd_mystrategy.__wrapped__(
        host, SimpleNamespace(), SimpleNamespace(args=args)))
    return "\n".join(replies)


def test_mystrategy_refuses_an_unrun_preset_and_arms_one_the_bot_runs(state):
    for alias in ("ethma", "volrotation", "altsweep"):
        text = _mystrategy([alias])
        assert "cannot be your strategy" in text and "does not run its rule" in text
        assert store.get(UID) is None, alias
    assert "is now YOUR strategy" in _mystrategy(["safe", "scalper"])
    assert store.get(UID) == "safe scalper"


def test_the_list_offers_no_command_that_refuses(state):
    text = _mystrategy([])
    for key in UNRUN:
        assert f"/mystrategy {key}" not in text
        assert PRESETS[key]["label"] in text
    assert "Not run by the live bot, so not armable:" in text
    for key in RUN:
        assert f"/mystrategy {key}" in text


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **k: None)


def _req(query=None, body=None):
    async def _json():
        return body or {}

    return SimpleNamespace(app={"tg_handler": SimpleNamespace(), "engine": None},
                           query=query or {}, json=_json)


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


def test_the_web_refuses_arming_an_unrun_preset(state, web):
    for slug in ("eth-ma-trend", "daily-vol-rotation", "alt-sweep"):
        resp = asyncio.run(ug.handle_user_strategy_set(
            _req(body={"telegram_id": UID, "strategy": slug})))
        assert resp.status == 409, slug
        body = _body(resp)
        assert body["error"] == "not_run_live" and "does not run its rule" in body["detail"]
        assert store.get(UID) is None
    resp = asyncio.run(ug.handle_user_strategy_set(
        _req(body={"telegram_id": UID, "strategy": "safe-scalper"})))
    assert resp.status == 200 and store.get(UID) == "safe scalper"
    listed = _body(asyncio.run(ug.handle_user_strategy_get(_req(query={"telegram_id": UID}))))
    armable = {p["key"]: p["armable"] for p in listed["presets"]}
    assert armable == {k: k in RUN for k in PRESETS}


def test_the_catalogue_says_which_cards_the_bot_runs():
    cards = {c["id"]: c for c in sc.catalog()}
    for key, cfg in PRESETS.items():
        assert cards[sc._slug(key)]["live_runs"] is sc.live_runs(cfg), key
