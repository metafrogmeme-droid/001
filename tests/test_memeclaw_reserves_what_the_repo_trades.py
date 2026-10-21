"""Memeclaw refuses a coin symbol the repository already trades or names.

The reserved list is generated from ``bot/config.py`` and the measured majors
(``scripts/memeclaw_reserved_symbols.py``), and the website reads the
committed copy. A stock the bot starts trading is a symbol a launch must not
take, the day it is added, so a stale copy fails here.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "memeclaw_reserved_symbols.py"


def _gen():
    spec = importlib.util.spec_from_file_location("memeclaw_reserved_symbols", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_committed_list_is_the_generated_one():
    gen = _gen()
    assert gen.OUT.read_text(encoding="utf-8") == gen.render(), (
        "app/lib/memeclaw_reserved.json is stale: run python3 scripts/memeclaw_reserved_symbols.py")


def test_it_reserves_the_stocks_the_majors_and_our_own_names():
    symbols = json.loads((ROOT / "app" / "lib" / "memeclaw_reserved.json").read_text())["symbols"]
    # One of each list the generator reads, so a list it stopped reading fails.
    for sym in ("AAPL", "AAPLON", "RAAPL", "TWTSM", "SPY", "OPENAI", "XAU", "CL"):
        assert sym in symbols["tokenized"], sym
    for sym in ("BTC", "ETH", "DOGE", "SOL"):
        assert sym in symbols["majors"], sym
    assert "RCLAW" in symbols["project"] and "USDT" in symbols["money"]
    # The other arm: a plain meme symbol is not reserved.
    every = {s for values in symbols.values() for s in values}
    assert "WOOF" not in every and "CLAW" not in every


def test_a_new_stock_in_the_bot_is_reserved_by_the_next_run(tmp_path, monkeypatch):
    gen = _gen()
    config = tmp_path / "config.py"
    text = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
    config.write_text(text.replace('"TSLA/USDT:USDT",', '"TSLA/USDT:USDT", "ZZNEW/USDT:USDT",', 1),
                      encoding="utf-8")
    monkeypatch.setattr(gen, "CONFIG", config)
    assert "ZZNEW" in gen.build()["symbols"]["tokenized"]
