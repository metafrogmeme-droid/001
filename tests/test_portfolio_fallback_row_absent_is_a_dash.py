"""/portfolio's exchange-fallback card printed `uPnL: $+0.00` for a mark it never read.

The card appears when the bot's own book is empty and the venue's is not --
"Showing exchange data -- local tracking out of sync" -- which is the one
moment the operator has no other view. Its rows were built inline in the
handler with `float(p.get("unrealizedPnl") or 0)`, `float(p.get("markPrice")
or 0)` and `int(float(p.get("leverage") or 1))`: an unpriced position read
as break-even at $0.0000, at 1x. `orphan_position_row`, four thousand lines
away in the same file's `/open_positions`, already treated the same missing
field as unknown. Third surface, same measurement, different answer.

The row is a pure function now, next to the one that already got it right.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

from bot.formatters.orphan_position import exchange_position_lines

ROOT = Path(__file__).resolve().parent.parent

FULL = {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.01,
        "entryPrice": 60000.0, "markPrice": 60500.0, "unrealizedPnl": 5.0,
        "leverage": 10}


def _line(row: str, label: str) -> str:
    return next(line for line in row.splitlines() if line.startswith(f"- {label}:"))


def test_a_fully_reported_position_renders_its_numbers():
    row = exchange_position_lines(FULL)
    assert "LONG BTC" in row and " 10x" in row
    assert _line(row, "Entry") == "- Entry: <code>$60,000.0000</code>"
    assert _line(row, "Mark") == "- Mark: <code>$60,500.0000</code>"
    assert _line(row, "uPnL") == "- uPnL: <code>+$5.00</code>"


def test_an_absent_unrealized_pnl_is_a_dash_not_break_even():
    pos = {k: v for k, v in FULL.items() if k != "unrealizedPnl"}
    line = _line(exchange_position_lines(pos), "uPnL")
    assert line == "- uPnL: <code>—</code>", line


def test_a_real_zero_from_the_venue_still_prints_as_zero():
    pos = dict(FULL, unrealizedPnl=0.0)
    assert _line(exchange_position_lines(pos), "uPnL") == "- uPnL: <code>+$0.00</code>"


def test_an_absent_mark_is_a_dash():
    pos = {k: v for k, v in FULL.items() if k != "markPrice"}
    assert _line(exchange_position_lines(pos), "Mark") == "- Mark: <code>—</code>"


def test_an_absent_leverage_is_not_one_x():
    pos = {k: v for k, v in FULL.items() if k != "leverage"}
    head = exchange_position_lines(pos).splitlines()[0]
    assert head.endswith("</b>"), head
    assert "1x" not in head


def test_a_loss_keeps_its_sign_and_the_direction_icon_is_still_shown():
    """A short at a loss: the icon encodes DIRECTION and is a true statement
    whatever the mark did -- do not strip it in the name of honesty. The
    display symbol strips "/" and ":USDT", as the inline version always did."""
    row = exchange_position_lines(dict(FULL, side="short", unrealizedPnl=-3.25))
    assert row.startswith("\U0001f534 <b>SHORT BTCUSDT</b>")
    assert _line(row, "uPnL") == "- uPnL: <code>-$3.25</code>"


def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return " ".join(t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                    if t.type != tokenize.COMMENT)


def test_the_fallback_card_is_built_from_the_pure_row():
    """Wiring, with comments stripped: the handler's fallback block must call
    the row builder and must no longer coerce the fields itself."""
    code = _code_only(ROOT / "bot" / "skills" / "telegram_handler.py")
    i = code.find("if not filled_pos and not pending_pos :")
    assert i > 0, "fallback block not found"
    block = code[i:i + 3000]
    assert "exchange_position_lines (" in block, "the fallback rows are not built by the pure function"
    assert 'p . get ( "unrealizedPnl" ) or 0' not in block, "the inline coercion is back"
