"""On-demand scan of ONE venue's own market catalogue — ``/scan <venue>``.

The engine's sweep reads Bitget, and overlays the ACTIVE venue's own markets
when that venue is not Bitget (``MarketScanner._scan_active_venue_extra``).
Nothing let a person look at a venue they are not trading on: a Hyperliquid
builder perp, a Bybit-only listing, what OKX is moving today.
``MarketScanner.scan_venue`` does that through a keyless public client, and
this module is the result's shape and its rendering — a seam, so the three
outcomes can be planted and read:

  answered, movers      → the list
  answered, none        → "{venue} answered with N markets, none cleared the floor"
  did not answer        → "{venue} did not answer — nothing was scanned"

The overlay returns ``[]`` for the last two alike, which is fine for an
overlay (the Bitget scan stands alone) and wrong for a command whose whole
answer this is: an unreachable venue rendered as an empty one tells the
reader the market is quiet on the day the venue is down.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Optional

from bot.utils.i18n import t
from bot.utils.models import MarketSignal


@dataclass
class VenueScan:
    """What one ``/scan <venue>`` learned. ``error`` set means the venue did
    not answer, and ``signals``/``markets`` say nothing in that case."""
    venue_id: str
    display_name: str
    signals: list[MarketSignal] = field(default_factory=list)
    markets: int = 0                 # tickers the venue returned
    error: Optional[str] = None      # set when the venue did not answer

    @property
    def unreadable(self) -> bool:
        return self.error is not None


def _change_text(change: Optional[float]) -> str:
    # None is "the venue reported no move", which is not 0.0%.
    if change is None:
        return "—"
    return f"{'+' if change >= 0 else ''}{change:.1f}%"


def _side_icon(change: Optional[float]) -> str:
    if change is None or change == 0:
        return "⚪"
    return "🟢" if change > 0 else "🔴"


def _fmt_vol(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def render_venue_scan(vs: VenueScan, lang: str = "en", *, limit: int = 15) -> str:
    """Telegram HTML for a venue scan. Three outcomes, not two."""
    name = html.escape(vs.display_name)
    if vs.unreadable:
        return t("venue_scan_unreachable", lang, venue=name,
                 detail=html.escape(vs.error or ""))
    if not vs.signals:
        return t("venue_scan_empty", lang, venue=name, markets=vs.markets)
    rows = []
    for s in vs.signals[:limit]:
        base = s.symbol.split("/")[0]
        rows.append(f"{_side_icon(s.change_pct_24h)} <b>{html.escape(base)}</b>  "
                    f"<code>{s.price:,.6g}</code>  {_change_text(s.change_pct_24h)}  "
                    f"vol {_fmt_vol(s.volume_usd_24h)}")
    head = t("venue_scan_header", lang, venue=name, n=len(rows), markets=vs.markets)
    return head + "\n" + "\n".join(rows)
