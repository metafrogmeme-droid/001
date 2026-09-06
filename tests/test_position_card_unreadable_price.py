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
    assert '"  (price unavailable)" if pnl_unknown' in block
    assert 'net_text = "—" if net_unknown' in block


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
    src = _handler_src()
    block = src[src.index("async def _last(sym):"):]
    block = block[:block.index("now = datetime.now")]
    assert "return px if px > 0 else None" in block
    assert "except Exception:\n                    return None" in block
    assert "return 0.0" not in block, "0.0 is a price; None is the absence of one"


def test_no_exchange_client_is_the_same_fact_as_a_failed_ticker():
    src = _handler_src()
    assert "cur = await _last(p.symbol) if exchange else None" in src, (
        "not knowing the price because there is no client, and not knowing it "
        "because the fetch failed, are the same thing to the reader")


def test_the_card_is_handed_none_rather_than_a_fabricated_zero():
    src = _handler_src()
    i = src.index("cur = await _last(p.symbol) if exchange else None")
    block = src[i:i + 1400]
    assert "pnl_usd = pnl_pct = None" in block
    assert "if cur and cur > 0 and p.entry_price > 0:" in block, (
        "cur is now Optional[float]; a bare `cur > 0` would raise on None")


def test_the_stop_distances_survive_an_unknown_price():
    src = _handler_src()
    i = src.index("cur = await _last(p.symbol) if exchange else None")
    block = src[i:i + 1400]
    assert "if (cur and cur > 0 and p.stop_loss > 0)" in block
    assert "if (cur and cur > 0 and p.take_profit > 0)" in block


@pytest.mark.parametrize("cur", [None, 0.0])
def test_the_maths_is_skipped_whenever_the_price_is_unusable(cur):
    """Exercised directly: the guard must reject both None and 0."""
    entry, cost, lev = 1.2345, 200.0, 10
    pnl_usd = pnl_pct = None
    if cur and cur > 0 and entry > 0:
        pnl_pct = ((cur - entry) / entry) * 100 * lev
        pnl_usd = cost * pnl_pct / 100
    assert pnl_pct is None and pnl_usd is None
