"""The smart-money line feeds nothing: not the score, not the risk gate, not
the duel's rules, seal or settlement.

A context line that leaked into a decision would be a verdict wearing a
caption, and the leak would be one attribute read away. This holds the
decision files to zero mentions and the duel service to the three sites the
card needs (the import, the prime at deal, the attach on the card), with the
comments stripped so a comment that names the line is not read as code.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
JS_COMMENT = re.compile(r"/\*[\s\S]*?\*/|(?<![:'\"])//[^\n]*")

NEVER = [
    # The risk package is walked whole below, so it is not listed here by name.
    "bot/core/engine.py", "bot/core/live_executor.py", "bot/core/signal_confidence.py",
    "app/lib/duel.js", "app/lib/callseal.js", "app/lib/candles.js", "app/lib/tickers.js",
]


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return code_only(text)
    return JS_COMMENT.sub("", text)


def test_the_decision_files_never_read_the_line():
    for rel in NEVER:
        p = ROOT / rel
        assert p.exists(), f"{rel} moved; re-anchor this list"
        assert "smart_money" not in _code(p) and "smartMoney" not in _code(p), rel


def test_the_risk_package_never_reads_it():
    for p in (ROOT / "bot" / "risk").glob("*.py"):
        assert "smart_money" not in _code(p), p.name


def test_the_duel_service_touches_it_only_where_the_card_is_built():
    src = _code(ROOT / "app/lib/duel_service.js")
    sites = re.findall(r"duelContext\.(\w+)\(", src)
    assert sorted(sites) == ["attachSmartMoney", "primeSmartMoney"], sites
    assert src.count("require('./duel_context')") == 1
    assert "smart_money" not in src, "the service names the line only through duel_context"
    # And the settlement path, bounded by its own function headers at column 0,
    # names neither the context module nor the line.
    m = re.search(r"^async function settleDue[\s\S]*?^async function handleFor", src, re.M)
    assert m, "settleDue/handleFor moved; re-anchor"
    assert "duelContext" not in m.group(0) and "smart" not in m.group(0).lower()
