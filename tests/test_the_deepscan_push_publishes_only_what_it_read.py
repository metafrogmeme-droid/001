"""A deep scan publishes the patterns it read, and no trade it did not decide.

`DeepScanSkill` measures chart and candle patterns. It decides no direction,
reads no volume ratio and computes no trade levels. Its website push used to
build a scan row per hit anyway -- ``"dir": "LONG" if rsi < 50 or chg > 0``,
``"vol_ratio": 2.5 if vol_spike else 1.0``, an ATR that fell back to 2% of
price -- and hand those rows to `_build_scan_payload` as a real scan. They
became the public Setups panel's entry cards (with entry, stop, targets and an
R:R), the symbols table and the "Market bias" headline. Telegram's
`/deepscan` drew the same rows as a scan-results picture.

Both are gone. The push goes through `scanned=False`, the path the autonomous
cycle's summary push already takes: the scan blocks are omitted rather than
sent, so the ingest carries the last real scan's cards forward, and the
deep-scan block rides beside them.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: A flat series whose last bar jumps 5%, so every symbol is a hit through
#: the `abs(chg) > 3.0` rule, with an RSI above 50 and a positive move: the
#: shape the old push read as LONG.
_CANDLES = ([[i * 60_000, 1.0, 1.01, 0.99, 1.0 + (i % 3) * 0.001, 10.0]
             for i in range(59)]
            + [[59 * 60_000, 1.0, 1.06, 1.0, 1.05, 40.0]])


def _engine():
    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            return [list(c) for c in _CANDLES]

    async def _ex():
        return _Ex()

    return SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_ex, _get_futures_exchange=_ex),
        risk=SimpleNamespace(circuit_breaker_active=False),
        _last_deepscan_hits=None,
    )


@pytest.fixture
def pushed(monkeypatch):
    """Every payload the deep scan hands to the website sync."""
    import bot.utils.website_sync as ws

    seen: list[dict] = []
    monkeypatch.setattr(ws, "sync_scan_in_background", lambda p: seen.append(p))
    return seen


def _run(engine):
    from bot.skills.skill_registry import DeepScanSkill

    return asyncio.run(DeepScanSkill().execute(engine, timeframe="4h"))


def test_the_fixture_produces_hits_so_the_push_is_reached(pushed):
    """A fixture with no hits never reaches the push, and every assertion below
    would pass over code that was not run."""
    engine = _engine()
    _run(engine)
    assert engine._last_deepscan_hits, "no hits: the fixture measures nothing"
    assert len(pushed) == 1, pushed


def test_the_push_carries_no_scan_blocks(pushed):
    """An absent block is a push that scanned nothing; an empty list would be a
    scan that found nothing. The deep scan is the first, so it sends neither
    list, and the ingest keeps the last real scan's cards."""
    _run(_engine())
    payload = pushed[0]
    for key in ("entry_cards", "symbols", "entry_cards_read"):
        assert key not in payload, f"{key} was published from a deep scan"


def test_the_push_claims_no_regime_or_market_bias(pushed):
    """The regime was derived from BTC's made-up direction, and the headline
    counted made-up longs and shorts."""
    _run(_engine())
    payload = pushed[0]
    assert payload["regime"]["gate"] == 0, payload["regime"]
    assert payload["regime"]["label"] == "NEUTRAL"
    assert "Market bias" not in payload["key_call"], payload["key_call"]


def test_the_pattern_block_still_rides_the_push(pushed):
    engine = _engine()
    _run(engine)
    ds = pushed[0]["deepscan"]
    assert ds["count"] == len(engine._last_deepscan_hits)
    assert ds["hits"], ds
    assert ds["tf"] == "4h"


def test_a_hit_carries_no_volatility_nobody_read_back(pushed):
    """The ATR fell back to 2% of price and fed only the made-up setups. With
    those gone nothing reads it, and a field nobody reads is one the next
    reader trusts because it is there."""
    engine = _engine()
    _run(engine)
    assert all("atr" not in h for h in engine._last_deepscan_hits)


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(found) == 1, f"{name}: {len(found)} definitions"
    return found[0]


def _names(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.alias):
            out.add(n.name)
    return out


def test_telegram_deepscan_draws_no_setups_picture():
    """A SCAN, stated as one. `_cmd_deepscan` sits behind a Telegram update,
    the registry and two exchanges; the claim is that the handler no longer
    reaches for the setups renderer at all, which is a property of its body.
    The patterns picture, which draws what the scan read, is still sent."""
    fn = _function(ROOT / "bot" / "skills" / "scan_commands.py", "_cmd_deepscan")
    names = _names(fn)
    assert "render_scan_results_card" not in names
    assert "render_patterns_card" in names


def test_the_skill_builds_no_scan_rows():
    """The same claim on the skill: no `_build_scan_payload` call hands it rows,
    and every call says it scanned nothing. Walked on the class, because
    `execute` is defined by every skill in the module."""
    tree =ast.parse((ROOT / "bot" / "skills" / "skill_registry.py").read_text(encoding="utf-8"))
    cls = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "DeepScanSkill"]
    assert len(cls) == 1
    calls = [n for n in ast.walk(cls[0])
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_build_scan_payload"]
    assert calls, "the deep scan no longer pushes at all"
    for c in calls:
        assert isinstance(c.args[0], ast.List) and not c.args[0].elts, ast.unparse(c)
        kw = {k.arg: k.value for k in c.keywords}
        assert isinstance(kw.get("scanned"), ast.Constant) and kw["scanned"].value is False, ast.unparse(c)
