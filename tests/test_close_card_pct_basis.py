"""
Close-card % basis (live incident: TRIA 10x SHORT, 2026-07-11).

The live position card shows the LEVERAGED (margin) return (+22.69%), but the
close card showed the raw price move (+2.66%) for the same winning trade —
reading like the gain evaporated at close. The close card now prefers
pnl_pct_margin (falling back to pnl_pct for producers that don't send it),
and every executor close-data producer includes pnl_pct_margin.
"""
import inspect

from bot.core.live_executor import LiveExecutor
from bot.formatters import signal_card

_TRIA = {
    "symbol": "TRIA/USDT:USDT", "direction": "SHORT", "reason": "closed",
    "entry": 0.009167, "exit": 0.008923, "pnl_usd": 1.89, "fees": 0.09,
    "size_usd": 7.44, "leverage": 10, "hold_time": "1.5h",
}


def test_close_card_prefers_margin_pct():
    """Driven, not grepped.

    This asserted the literal expression `data.get("pnl_pct_margin",
    data.get("pnl_pct", 0))` and so failed the moment that read was made
    tri-state — while the BEHAVIOUR it names was untouched. Rendering the
    same payload three ways settles which number reached the card, and no
    rewrite of the read can fake it.
    """
    both = signal_card.render_close_card(
        dict(_TRIA, pnl_pct=2.66, pnl_pct_margin=26.62))
    margin_only = signal_card.render_close_card(dict(_TRIA, pnl_pct=26.62))
    raw_only = signal_card.render_close_card(dict(_TRIA, pnl_pct=2.66))

    # Carrying both draws the MARGIN figure: identical to a card given 26.62
    # as its only percentage.
    assert both == margin_only
    # ...and distinguishable from the raw price move, which is the whole
    # incident: +2.66% shown at close for a trade the live card had at
    # +26.62%, reading like the gain evaporated.
    assert both != raw_only


def test_close_card_falls_back_to_pnl_pct():
    """A producer that sends only pnl_pct still renders that number."""
    only_raw = signal_card.render_close_card(dict(_TRIA, pnl_pct=2.66))
    explicit = signal_card.render_close_card(
        dict(_TRIA, pnl_pct=2.66, pnl_pct_margin=None))
    assert only_raw == explicit
    assert len(only_raw) > 0


def test_all_close_data_producers_send_margin_pct():
    """Every _last_close_data producer in the executor must include
    pnl_pct_margin so the close card can render on the margin basis."""
    src = inspect.getsource(LiveExecutor)
    producers = src.count("_last_close_data = {")
    assert producers >= 2
    assert src.count('"pnl_pct_margin"') >= producers


def test_close_card_renders_with_margin_pct():
    """End-to-end: the card renders (PNG bytes) from a TRIA-shaped payload
    carrying both keys — no KeyError, non-empty output."""
    png = signal_card.render_close_card({
        "symbol": "TRIA/USDT:USDT", "direction": "SHORT", "reason": "closed",
        "entry": 0.009167, "exit": 0.008923,
        "pnl_pct": 2.66, "pnl_pct_margin": 26.62,
        "pnl_usd": 1.89, "fees": 0.09, "size_usd": 7.44,
        "leverage": 10, "hold_time": "1.5h",
    })
    assert isinstance(png, bytes) and len(png) > 0
