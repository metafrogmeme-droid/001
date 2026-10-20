"""The positions list and the position buttons spell a pair through ONE reading.

QNTSTOCK/USDT:USDT, 2026-10-20. The list's live rows spelled a pair by
stripping the slash and the settle suffix; its paper rows stripped the slash
only, so a swap-spelled practice position was listed as "QNTSTOCKUSDT:USDT",
its Details button carried that id, the handler compared it with
"QNTSTOCKUSDT" and answered "position closed" over an open position, and the
chart's symbol, derived from the same spelling, came out "QNTSTOCK/USDT:/USDT".

The reading is `bot.core.live_executor.display_symbol` (imported as
`pair_symbol`). Both rows spell the pair through it, both button paths match
through it on BOTH sides (an id an older card sent still carries the suffix),
and the chart is asked for the position's own symbol, never an inverse of the
display name. The tap itself is driven in
`test_the_details_button_reads_the_callers_account.py`; this file holds the
shapes a tap cannot reach (the list's rows, the number of readings).
"""

from __future__ import annotations

import ast
import inspect
import pathlib

from bot.core.live_executor import display_symbol
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _rows_pair_values(path: str) -> list[str]:
    """Every `"pair": <expr>` inside a `positions_data.append({...})` call."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and node.args
                and isinstance(node.args[0], ast.Dict)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "positions_data"):
            continue
        for k, v in zip(node.args[0].keys, node.args[0].values):
            if isinstance(k, ast.Constant) and k.value == "pair":
                out.append(ast.unparse(v))
    return out


class TestTheListSpellsOnePair:
    def test_both_rows_go_through_the_one_reading(self):
        values = _rows_pair_values("bot/skills/trading_commands.py")
        assert len(values) == 2, values
        for v in values:
            assert v.startswith("pair_symbol("), v

    def test_the_reading_strips_the_slash_and_the_settle_suffix(self):
        assert display_symbol("QNTSTOCK/USDT:USDT") == "QNTSTOCKUSDT"
        assert display_symbol("QNTSTOCKUSDT:USDT") == "QNTSTOCKUSDT"
        assert display_symbol("QNTSTOCKUSDT") == "QNTSTOCKUSDT"
        assert display_symbol("TRUMP/USDT") == "TRUMPUSDT"

    def test_every_row_carries_its_own_symbol_for_the_card(self):
        # The card's chart and analysis take the position's symbol as the
        # book spells it; the inverse of the display name is a fallback for a
        # pair nothing matched.
        src = code_only((ROOT / "bot/skills/trading_commands.py").read_text(encoding="utf-8"))
        assert src.count('"asset": pos.symbol') == 1 and src.count('"asset": pos.asset') == 1, (
            "each positions_data row carries the position's own symbol under 'asset'")
        assert '"symbol": pos.get("asset") or (' in src


class TestTheButtonsMatchThroughTheSameReading:
    def _block(self, start: str, stop: str) -> str:
        from bot.skills.telegram_handler import TelegramHandler
        src = inspect.getsource(TelegramHandler._handle_callback)
        i = src.index(start)
        return code_only(src[i:src.index(stop, i)])

    def test_the_details_match_reads_both_sides(self):
        block = self._block('pos_details_', 'pos_close_')
        assert "pair_symbol(p.asset) == pair_symbol(ident)" in block
        assert 'p.asset.replace("/", "").replace(":USDT", "") == pair' not in block

    def test_the_close_match_reads_both_sides(self):
        block = self._block('if data.startswith("pos_close_")', 'if data.startswith("pane:")')
        assert "pair_symbol(pos.asset) == pair_symbol(ident)" in block
        assert 'pos.asset.replace("/", "").replace(":USDT", "") == pair' not in block

    def test_the_chart_takes_the_positions_own_symbol(self):
        block = self._block('pos_details_', 'pos_close_')
        assert 'getattr(pos_match, "asset", None) or getattr(pos_match, "symbol", None)' in block
