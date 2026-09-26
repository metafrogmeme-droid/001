"""The one reading of which market a symbol names, whoever spelled it.

The bot spells a market ``BASE/USDT`` everywhere; a venue spells it the way
its perp is listed (``BTC/USDT:USDT`` on Bybit, ``BTC/USDC:USDC`` on
Hyperliquid). A reader that compares the two spellings as strings reads a
venue's own row as somebody else's, and a close verification that drops the
row reads a held position as flat. ``normalize_symbol`` lives here, in a leaf,
because `order_state.rows_for_side` needs it and `live_executor` imports
`order_state`: the executor re-exports it, so every existing caller keeps its
import.
"""

from __future__ import annotations


def normalize_symbol(s: str) -> str:
    """Canonical symbol normalizer — strips ccxt suffixes to a bare base.

    Examples:
        MEGA/USDT:USDT  →  MEGA
        MEGA/USDT       →  MEGA
        MEGAUSDT        →  MEGAUSDT  (no destructive mid-string strip)
        XAU/USDT:USDT   →  XAU
        BTC/USDC:USDC   →  BTC
    """
    result = s.upper()
    # L-01 FIX: Strip any :XXX settle suffix (not just :USDT)
    colon_idx = result.rfind(":")
    if colon_idx > 0:
        result = result[:colon_idx]
    if result.endswith("/USDT"):
        result = result[:-5]
    elif result.endswith("/USDC"):
        result = result[:-5]
    return result
