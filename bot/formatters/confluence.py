"""Confluence, printed for the direction it is read against.

The analyzer's confluence is a BULLISHNESS score: 0.5 is neutral, above it
leans LONG, below it leans SHORT. The analyzer orients it before it blends
(`conf_term` in `Analyzer.analyze`: ``1 - confluence`` for a SHORT). A card
that prints it bare beside "Bias: SHORT" shows a strongly confirmed short as
"30%", which reads as weak. Every surface that prints it beside a direction
asks this.
"""
from __future__ import annotations

from typing import Any, Optional


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def confluence_words(confluence: Any, direction: Optional[str]) -> str:
    """``"80% for a SHORT (bullishness 20%; 50% is neutral)"``, or the
    bullishness alone, named as such, when there is no LONG or SHORT."""
    c = float(confluence)
    if direction == "LONG":
        return f"{_pct(c)} for a LONG (bullishness {_pct(c)}; 50% is neutral)"
    if direction == "SHORT":
        return f"{_pct(1.0 - c)} for a SHORT (bullishness {_pct(c)}; 50% is neutral)"
    return f"bullishness {_pct(c)} (50% is neutral)"
