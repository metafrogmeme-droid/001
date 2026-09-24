"""`/whynot` reads the risk gate's refusals in the order they happened.

`engine._last_rejections` holds each symbol's last refusal, and the dict's
order is the only recency it has. A plain assignment kept a re-refused symbol
at its FIRST position, so after BTC, ETH, BTC:

- `/whynot` with no symbol showed ETH as "the most recent rejection";
- the cap pruned the symbols refused most often, first;
- and the not-found reply listed "Recent rejections" as `sorted(...)[-10:]`,
  the last ten ALPHABETICALLY.

`_remember_rejection` removes before inserting, so insertion order is refusal
order, and it is the store's only writer.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import WhyNotSkill

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._last_rejections = {}
    return eng


def _rec(sym, ts):
    return {"symbol": f"{sym}/USDT", "direction": "LONG", "confidence": 0.7,
            "entry_price": 100.0, "stop_loss": 97.0, "take_profit": 104.0,
            "checks_passed": ["OPEN_POSITIONS: 0 OK"],
            "checks_failed": ["RISK_REWARD: 1.33 < 1.5 minimum (swing)"],
            "reason": "REJECTED", "timestamp": ts}


def test_a_re_refused_symbol_becomes_the_newest():
    eng = _engine()
    for i, sym in enumerate(("BTC", "ETH", "BTC")):
        eng._remember_rejection(sym, _rec(sym, f"2026-09-24T00:0{i}:00+00:00"))
    assert list(eng._last_rejections) == ["ETH", "BTC"]


def test_the_cap_prunes_the_oldest_refusals_not_the_most_frequent():
    eng = _engine()
    cap = RuneClawEngine._REJECTIONS_CAP
    eng._remember_rejection("FIRST", _rec("FIRST", "t0"))
    for i in range(cap - 1):
        eng._remember_rejection(f"S{i}", _rec(f"S{i}", f"t{i}"))
    eng._remember_rejection("FIRST", _rec("FIRST", "latest"))   # refused again
    eng._remember_rejection("LAST", _rec("LAST", "later"))      # tips over the cap
    keys = list(eng._last_rejections)
    assert len(keys) == RuneClawEngine._REJECTIONS_KEEP
    assert keys[-2:] == ["FIRST", "LAST"]


def _card(eng, **kw):
    return asyncio.run(WhyNotSkill().execute(eng, **kw))


def test_whynot_with_no_symbol_shows_the_latest_refusal():
    eng = _engine()
    for i, sym in enumerate(("BTC", "ETH", "BTC")):
        eng._remember_rejection(sym, _rec(sym, f"2026-09-24T00:0{i}:00+00:00"))
    out = _card(eng)
    assert "REJECTED  BTC/USDT" in out, out


def test_recent_rejections_are_newest_first_not_alphabetical():
    eng = _engine()
    for i, sym in enumerate(("ZEC", "AAVE", "BTC")):
        eng._remember_rejection(sym, _rec(sym, f"t{i}"))
    out = _card(eng, symbol="SOL")
    assert "Recent rejections: <code>BTC, AAVE, ZEC</code>" in out, out


def test_an_empty_store_speaks_for_this_process_only():
    out = _card(_engine())
    assert "since the bot last started" in out
    assert "No trades rejected yet" not in out


def test_the_seam_is_the_stores_only_writer():
    # A second writer assigning straight into the dict would put a re-refused
    # symbol back at its first position, invisibly from any single reading.
    offenders = []
    for path in (ROOT / "bot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name == "_remember_rejection":
                continue
            for node in ast.walk(fn):
                targets = node.targets if isinstance(node, ast.Assign) else []
                for t in targets:
                    if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute)
                            and t.value.attr == "_last_rejections"):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} in {fn.name}")
    assert offenders == [], offenders
