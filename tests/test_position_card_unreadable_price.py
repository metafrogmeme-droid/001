"""An unreadable live PnL is not a flat one.

The live position cards fetch the current price per symbol:

    async def _last(sym):
        try:    return float(tk.get("last") or 0)
        except: return 0.0

    cur = await _last(p.symbol) if exchange else 0.0
    pnl_usd = pnl_pct = 0.0
    if cur > 0 and p.entry_price > 0:
        ...

So a failed ticker — or no exchange client at all — produced pnl_pct = 0.0,
and the renderer drew "+0.00%" beside a GREEN accent stripe running the full
width of the card. A position that may be well underwater, presented as
exactly break-even, in an image, about real money.

_fmt() already rendered an unreadable PRICE as "—". The PnL derived from that
same missing price kept claiming a number, which is the harder half: a dash
reads as absent, while "+0.00%" reads as a measurement that was taken.

Same rule as the website's panels this week and the same rule the dashboard
states on _haveTrades: absent is not zero. Omit, never invent.
"""

import pytest

from bot.formatters import signal_card
from bot.formatters.signal_card import render_position_card


BASE = {
    "symbol": "GRASS/USDT", "direction": "LONG", "is_live": True,
    "entry": 1.2345, "size_usd": 200.0, "leverage": 10, "hold_time": "42m",
    "rr": 2.0, "sl": 1.10, "tp": 1.60, "sl_pct": 10.0, "tp_pct": 20.0,
    "sl_status": "on exchange", "tp_status": "bot-managed", "fees": 0.0,
}


def _card(**over):
    d = dict(BASE)
    d.update(over)
    return render_position_card(d)


# ── the renderer ──────────────────────────────────────────────────────────

def test_a_known_pnl_still_renders():
    png = _card(now=1.30, pnl_pct=5.3, pnl_usd=10.6, net_pnl=10.6)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_unreadable_pnl_renders_without_crashing():
    png = _card(now=0, pnl_pct=None, pnl_usd=None, net_pnl=None)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", (
        "None must be a supported value, not an exception — the card is the "
        "only view of a live position on Telegram")


def test_the_unknown_pnl_is_drawn_as_a_dash_not_a_zero():
    src = signal_card.__file__
    with open(src, encoding="utf-8") as fh:
        code = fh.read()
    block = code[code.index("def render_position_card"):
                 code.index("def render_close_card")]
    assert 'pnl_text = "—" if pnl_unknown' in block
    assert 'net_text = "—" if net_unknown' in block


def test_the_unknown_pnl_names_the_reading_that_is_missing():
    """Drawn, not spelled: the sentence under an unknown P&L is the cause the
    producer names, and "price unavailable" only when it names none. A mark
    that was read beside a margin that was not used to print
    "price unavailable" too."""
    from tests.png_text import text_of
    plain = text_of(render_position_card, dict(
        BASE, now=0, pnl_pct=None, pnl_usd=None, net_pnl=None))
    assert "(price unavailable)" in plain
    named = text_of(render_position_card, dict(
        BASE, now=1.30, pnl_pct=5.3, pnl_usd=None, net_pnl=None,
        pnl_unread="margin unread"))
    assert "(margin unread)" in named
    assert "price unavailable" not in named


def test_colour_is_a_claim_too():
    """A green stripe says "in profit" as loudly as the number does."""
    src = signal_card.__file__
    with open(src, encoding="utf-8") as fh:
        code = fh.read()
    block = code[code.index("def render_position_card"):
                 code.index("def render_close_card")]
    for line in ("pnl_color = _MUTED if pnl_unknown",
                 "net_color = _MUTED if net_unknown",
                 "stripe_color = _MUTED if pnl_unknown"):
        assert line in block, f"unreadable must not be coloured as profit/loss: {line}"
    assert "_MUTED = (" in code
    # ...and _MUTED must genuinely not be either signal colour.
    assert signal_card._MUTED != signal_card._GREEN
    assert signal_card._MUTED != signal_card._RED


def test_a_real_zero_pnl_is_still_a_zero():
    """Exactly break-even is a legitimate measurement and must survive."""
    src = signal_card.__file__
    with open(src, encoding="utf-8") as fh:
        code = fh.read()
    block = code[code.index("def render_position_card"):
                 code.index("def render_close_card")]
    assert "pnl_unknown = pnl_pct is None or pnl_usd is None" in block, (
        "the unknown test must be `is None`, never falsiness — 0.0 is falsy "
        "and 0.0 is a real, measured, break-even position")
    png = _card(now=1.2345, pnl_pct=0.0, pnl_usd=0.0, net_pnl=0.0)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_missing_keys_still_default_to_zero_for_every_other_field():
    # Only the PnL trio carries the None contract; the rest keep their old
    # defaults so no other caller changes behaviour.
    png = render_position_card({"symbol": "BTC/USDT", "direction": "SHORT"})
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


# ── the caller ────────────────────────────────────────────────────────────

def _handler_src() -> str:
    # Every file the handler class is made of: the position cards live in
    # the trading mixin since the handler split, and a scan of one file
    # reads the move as the three-valued price read vanishing.
    from tests.source_scan import handler_sources
    return "\n".join(p.read_text(encoding="utf-8") for p in handler_sources())


def test_a_failed_ticker_returns_none_not_zero():
    # The card's read is the executor's `last_price` now (the venue's own
    # spelling), which answers None for a ticker that states no price and
    # RAISES for a read that failed; `_last` turns the raise into None.
    # These used to pin the inline `px > 0` spelling it replaced.
    src = _handler_src()
    block = src[src.index("async def _last(sym):"):]
    block = block[:block.index("now = datetime.now")]
    assert "return await executor.last_price(sym)" in block
    assert "except Exception:\n                    return None" in block
    assert "return 0.0" not in block, "0.0 is a price; None is the absence of one"


@pytest.mark.parametrize("last", [None, 0, 0.0, -1.0, float("nan"), "n/a"])
def test_a_ticker_that_states_no_price_is_none(last):
    import asyncio
    from types import SimpleNamespace

    from bot.core.live_executor import LiveExecutor

    class _X:
        async def fetch_ticker(self, sym):
            return {"last": last}

    ex = LiveExecutor.__new__(LiveExecutor)
    ex._venue = SimpleNamespace(order_symbol=lambda s: s + ":USDT")

    async def _get():
        return _X()
    ex._get_exchange = _get
    assert asyncio.run(ex.last_price("SOL/USDT")) is None


def test_no_exchange_client_is_the_same_fact_as_a_failed_ticker():
    # Not knowing the price because there is no client, and not knowing it
    # because the fetch failed, are the same thing to the reader: both reach
    # `_last` as a raise from `last_price` and both answer None.
    import asyncio
    from types import SimpleNamespace

    from bot.core.live_executor import LiveExecutor

    ex = LiveExecutor.__new__(LiveExecutor)
    ex._venue = SimpleNamespace(order_symbol=lambda s: s)

    async def _none():
        raise RuntimeError("no exchange client")
    ex._get_exchange = _none
    with pytest.raises(RuntimeError):
        asyncio.run(ex.last_price("SOL/USDT"))
    src = _handler_src()
    assert "cur = await _last(p.symbol)" in src


def _live(cur):
    """The card's data for one position, from the one reading the handler
    asks (``live_position_card_data``). These were scans of the inline block
    it replaced, and a test that ran its own copy of the guard; a copy agrees
    with every fixture whatever the handler does."""
    from datetime import datetime, timedelta, timezone

    from bot.core.live_executor import LivePosition
    from bot.skills.trading_commands import live_position_card_data
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    p = LivePosition(trade_id="G", symbol="GRASS/USDT", direction="LONG",
                     entry_price=1.2345, quantity=1000.0, cost_usd=200.0,
                     stop_loss=1.10, take_profit=1.60, leverage=10)
    p.opened_at = now - timedelta(minutes=42)
    return live_position_card_data(p, cur, now)


@pytest.mark.parametrize("cur", [None, 0.0, -1.0, float("nan")])
def test_the_card_is_handed_none_rather_than_a_fabricated_zero(cur):
    d = _live(cur)
    assert d["pnl_pct"] is None and d["pnl_usd"] is None
    assert d["net_pnl"] is None and d["now"] is None
    assert d["pnl_unread"] == "price unavailable"


@pytest.mark.parametrize("cur", [None, 0.0])
def test_the_stop_distances_survive_an_unknown_price(cur):
    d = _live(cur)
    assert d["sl"] == pytest.approx(1.10) and d["tp"] == pytest.approx(1.60)
    assert d["sl_pct"] is None and d["tp_pct"] is None


def test_a_read_price_prices_the_card():
    d = _live(1.30)
    assert d["pnl_pct"] == pytest.approx((1.30 - 1.2345) / 1.2345 * 100 * 10)
    assert d["pnl_usd"] is not None and d["pnl_unread"] is None
