"""The three spellings of a market, read one way.

A leaf: it imports nothing from the bot, so a money module (the risk gate's
live-universe check) can read a base ticker without dragging the scanner, the
engine and the skills into mypy's graph. `bot/core/strategy_gate.py` re-exports
these under the names its callers already use (`_base`, `perp_symbol`).
"""
from __future__ import annotations


def base_ticker(sym: str) -> str:
    """'BTC/USDT:USDT' | 'BTCUSDT' | 'BTC' → 'BTC' (base ticker)."""
    s = str(sym or "").upper().split(":")[0].split("/")[0].strip()
    if s.endswith("USDT") and len(s) > 4:
        s = s[:-4]
    return s


def perp_symbol(sym: str) -> str:
    """'BTCUSDT' | 'BTC/USDT' | 'BTC' → 'BTC/USDT:USDT', the USDT-M perpetual."""
    return f"{base_ticker(sym)}/USDT:USDT"


__all__ = ["base_ticker", "perp_symbol"]
