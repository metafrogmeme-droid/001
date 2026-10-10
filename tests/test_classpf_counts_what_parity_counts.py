"""`/classpf` counts the trades `/parity` counts, and says what it leaves out.

`/parity`'s asset-class rows are `strategy_exits` (filled, priced, not an
execution abort), and an idea card's class line quotes them
(`class_record`). `/classpf` counted every filled close, execution aborts
included, so its Stock row could read a higher trade count than the report and
the card beside the Confirm button: three surfaces, two answers to "how many
stock trades".

Driven: a real `LiveExecutor` with planted closes of every kind `/parity`
partitions, its file written by the executor, the real `/classpf` and the real
`/parity` over it.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.market_scanner import category_icon
from bot.formatters.class_record import class_record

STOCK, CRYPTO, ETF = "TSLA/USDT:USDT", "BTC/USDT:USDT", "QQQ/USDT:USDT"


def _close(symbol, pnl, reason="TP HIT", n=[0]):
    n[0] += 1
    return LivePosition(
        trade_id=f"T{n[0]}", symbol=symbol, direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=20.0, stop_loss=97.0, take_profit=106.0, leverage=5,
        status="closed", opened_at=datetime.now(UTC) - timedelta(hours=3),
        close_reason=reason, pnl_usd=pnl)


def _book(tmp_path):
    """12 stock strategy exits (3 won), 4 crypto, and one of each row the
    report leaves out: two execution aborts (one the ETF's only row), an
    unpriced stock close and a limit that never filled."""
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = ([_close(STOCK, 2.0) for _ in range(3)]
                         + [_close(STOCK, -1.0, "SL HIT") for _ in range(9)]
                         + [_close(STOCK, -0.5, "leverage_overshoot")]
                         + [_close(ETF, -0.3, "slippage_guard")]
                         + [_close(STOCK, None, "TP HIT")]
                         + [_close(STOCK, 0.0, "expired")]
                         + [_close(CRYPTO, 5.0) for _ in range(4)])
    assert ex._save_closed_trades()
    return ex


def _classpf(ex):
    from bot.skills.portfolio_commands import PortfolioCommands
    from tests.test_the_record_cards_read_the_callers_book import Stand

    me = Stand({"scope": "own", "executor": ex, "balance": None, "total": None, "age_s": None})
    asyncio.run(PortfolioCommands._cmd_classpf(me, object(), object()))
    return "\n".join(me.sent)


def _parity(ex):
    from bot.skills.engine_ops_commands import EngineOpsCommands

    sent: list = []

    class Host:
        engine = NS(live_executor=ex)

        def _is_admin(self, update):
            return True

        def _lang(self, update):
            return "en"

        async def _send(self, update, text, **kw):
            sent.append(text)

    asyncio.run(EngineOpsCommands._cmd_parity(Host(), NS(), NS()))
    return sent[0]


def _line(said, cat):
    (line,) = [ln for ln in said.splitlines() if ln.startswith(f"{category_icon(cat)} <b>{cat}</b>:")]
    return line


def test_the_stock_row_is_the_report_s_and_the_card_s(tmp_path):
    ex = _book(tmp_path)
    said = _classpf(ex)
    # 12 strategy exits: the abort, the unpriced close and the unfilled limit
    # are not trades of the strategy. Every filled close was 14.
    assert _line(said, "Stock") == (
        f"{category_icon('Stock')} <b>Stock</b>: 12 trades · PF <b>0.67</b> · "
        "WR 25% · net $-3.00 · 1 unpriced")
    parity_stock = [ln for ln in _parity(ex).splitlines() if re.match(r"\s+Stock\s", ln)]
    assert len(parity_stock) == 1 and " 12 tr " in parity_stock[0], parity_stock
    assert class_record(ex, "Stock")["trades"] == 12


def test_the_header_counts_the_exits_and_names_what_it_left_out(tmp_path):
    said = _classpf(_book(tmp_path))
    assert "(16 strategy exits, net PnL: the trades /parity counts)" in said
    assert "Left out: 2 execution aborts · 1 unpriced · 1 never filled" in said
    # A class whose only row is an abort has no trade to score.
    assert "<b>ETF</b>" not in said


def test_classes_come_largest_net_first(tmp_path):
    said = _classpf(_book(tmp_path))
    assert _line(said, "Crypto") == (
        f"{category_icon('Crypto')} <b>Crypto</b>: 4 trades · PF <b>—</b> · "
        "WR 100% · net $+20.00")
    assert said.index("<b>Crypto</b>") < said.index("<b>Stock</b>")


def test_a_record_with_nothing_left_out_says_nothing_about_it(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = [_close(CRYPTO, 5.0), _close(CRYPTO, -1.0, "SL HIT")]
    said = _classpf(ex)
    assert "Left out" not in said
    assert "(2 strategy exits," in said
    assert "<b>Crypto</b>: 2 trades ·" in said


def test_one_trade_is_one_trade(tmp_path):
    """10 October: "Commodity: 1 trades" on the operator's card."""
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = [_close(CRYPTO, 5.0)]
    assert "<b>Crypto</b>: 1 trade · PF" in _classpf(ex)
