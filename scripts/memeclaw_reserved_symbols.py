#!/usr/bin/env python3
"""Write the symbols a Memeclaw coin may not take: app/lib/memeclaw_reserved.json.

A coin on the launchpad must not pass for something it is not. Three kinds
of symbol are reserved, and each is read from where the repository already
names it, not typed again here:

- **Tokenized assets** (the owner's decision of 21 October 2026: no tokenized
  stocks): every stock, ETF, pre-IPO, metal and commodity symbol the bot
  trades, from the lists in ``bot/config.py``. Read with ``ast``, so nothing
  in the bot is imported or run.
- **The majors** the bot measures, from ``benchmark/majors_1h/manifest.json``.
- **Money a wallet already holds** and **this project's own names**: a short
  list below, each with its reason, because no file in the repository lists
  them as such.

The web deploy ships ``app/`` alone, so the output lives there.
``tests/test_memeclaw_reserves_what_the_repo_trades.py`` fails when the
committed file is not what this script writes.

    python3 scripts/memeclaw_reserved_symbols.py           # write
    python3 scripts/memeclaw_reserved_symbols.py --check   # exit 1 on drift
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "bot" / "config.py"
MAJORS = ROOT / "benchmark" / "majors_1h" / "manifest.json"
OUT = ROOT / "app" / "lib" / "memeclaw_reserved.json"

#: The bot's lists of tokenized assets, by name in bot/config.py.
TOKENIZED_LISTS = (
    "US_STOCK_SYMBOLS", "STOCK_PERPETUALS", "TAIWAN_STOCK_PERPETUALS",
    "ETF_PERPETUALS", "PRE_IPO_PERPETUALS", "METAL_PERPETUALS",
    "COMMODITY_PERPETUALS",
)

#: Coins a wallet already holds as money, which a launch must not imitate.
MONEY = ("USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "WETH", "WBTC", "WDOGE", "WSOL")

#: This project's names: a coin carrying them would read as ours.
PROJECT = ("RCLAW", "RUNECLAW", "RUNE", "MEMECLAW", "HUMANOID")


def _base(symbol: str) -> str:
    """'AAPL/USDT:USDT' -> 'AAPL'; 'AAPLON/USDT' -> 'AAPLON'."""
    return symbol.split("/")[0].strip().upper()


def tokenized() -> list[str]:
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target in TOKENIZED_LISTS and isinstance(value, ast.List):
            found[target] = [_base(e.value) for e in value.elts
                             if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    missing = [name for name in TOKENIZED_LISTS if not found.get(name)]
    if missing:
        raise SystemExit(f"bot/config.py no longer has a literal list for: {', '.join(missing)}")
    return sorted({s for v in found.values() for s in v})


def majors() -> list[str]:
    symbols = json.loads(MAJORS.read_text(encoding="utf-8"))["symbols"]
    out = sorted({_base(s) for s in symbols})
    if not out:
        raise SystemExit("benchmark/majors_1h/manifest.json lists no symbols")
    return out


def build() -> dict:
    symbols = {
        "tokenized": tokenized(),
        "majors": majors(),
        "money": sorted(MONEY),
        "project": sorted(PROJECT),
    }
    for kind, values in symbols.items():
        bad = [s for s in values if not re.fullmatch(r"[A-Z0-9]{1,16}", s)]
        if bad:
            raise SystemExit(f"{kind}: not a plain symbol: {bad}")
    return {
        "_generated": "scripts/memeclaw_reserved_symbols.py from bot/config.py and "
                      "benchmark/majors_1h/manifest.json. Do not edit by hand.",
        "symbols": symbols,
    }


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
        if current != text:
            print("app/lib/memeclaw_reserved.json is stale: run "
                  "python3 scripts/memeclaw_reserved_symbols.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
