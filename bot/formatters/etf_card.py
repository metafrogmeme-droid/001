"""The US spot crypto ETF flows picture for Telegram's ``/etf``.

It draws the payload ``app/lib/etf_flows.js`` builds, which the bot fetches
over the card route. That module is the one reading: the week's sums, the
coin estimates, the provider grouping and the takeaway are all computed
there, and this renderer only lays them out. A figure the payload does not
hold is drawn as an em dash in the muted colour, never as zero, and colour
follows the sign of a figure that was read: green for net creations, red for
net redemptions, muted for a measured zero or an absence.

``render_etf_card`` answers ``b""`` for a payload it cannot draw (no Pillow,
not a dict, no asset read), so the command falls back to the text card
rather than sending a picture of nothing.
"""

from __future__ import annotations

import io
import logging
import math
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

log = logging.getLogger("runeclaw.etf_card")

_BG = (18, 22, 30)
_CARD_BG = (25, 30, 42)
_BORDER = (40, 50, 70)
_ACCENT = (212, 175, 55)
_GREEN = (0, 200, 100)
_RED = (230, 60, 60)
_WHITE = (230, 230, 240)
_GRAY = (130, 140, 160)
_MUTED = (150, 155, 170)

DASH = "—"


def _num(v: Any) -> Optional[float]:
    """A finite number the payload holds, or None. A bool is not a figure."""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _fixed(a: float, digits: int, *, grouped: bool = False) -> str:
    """``a`` (not negative) to ``digits`` places, rounded the way the
    website's ``toFixed`` and ``maximumFractionDigits`` round it: the exact
    binary value, and an exact tie rounded up. Python's own format rounds a
    tie to even, so ``3.25`` read ``3.2`` here and ``3.3`` on the website,
    one figure printed two ways on one reading. ``grouped`` is the
    ``toLocaleString`` form: thousands commas and no trailing zeros."""
    d = Decimal(a).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    if not grouped:
        return f"{d:f}"
    text = f"{d:,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def usd_signed(v: Any) -> str:
    """``+$310.2M``, ``-$1.30B``, ``$0``, or an em dash for an absence."""
    n = _num(v)
    if n is None:
        return DASH
    a = abs(n)
    sign = "+" if n > 0 else "-" if n < 0 else ""
    if a >= 1e9:
        return f"{sign}${_fixed(a / 1e9, 2)}B"
    if a >= 1e6:
        return f"{sign}${_fixed(a / 1e6, 1)}M"
    if a >= 1e3:
        return f"{sign}${_fixed(a / 1e3, 1)}K"
    return f"{sign}${_fixed(a, 0)}"


def coins_signed(v: Any, symbol: str) -> Optional[str]:
    """``≈ +16,971 BTC``: an estimate, marked as one. None for an absence."""
    n = _num(v)
    if n is None:
        return None
    a = abs(n)
    digits = 0 if a >= 100 else 1 if a >= 1 else 3
    sign = "+" if n > 0 else "-" if n < 0 else ""
    return f"≈ {sign}{_fixed(a, digits, grouped=True)} {symbol}"


def _dict(v: Any) -> dict:
    """A payload section, or an empty one when it is not a mapping."""
    return v if isinstance(v, dict) else {}


def _colour(v: Any) -> tuple:
    n = _num(v)
    if n is None or n == 0:
        return _MUTED
    return _GREEN if n > 0 else _RED


def _short_date(d: Any) -> str:
    if not isinstance(d, str):
        return DASH
    try:
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).strftime("%b %-d")
    except ValueError:
        return DASH


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


_UNREAD_WORDS = {
    "unavailable": "the source did not answer",
    "no_rows": "the source listed no days",
}


def render_etf_card(data: Any) -> bytes:
    """The weekly flows picture, or ``b""`` when there is nothing to draw."""
    if not isinstance(data, dict):
        return b""
    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
    if not any(a.get("read") is True for a in assets):
        return b""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log.warning("Pillow not installed, cannot render the ETF flows card")
        return b""

    W, PAD = 720, 24
    btc = next((a for a in assets if a.get("key") == "btc" and a.get("read") is True), None)
    providers = [p for p in ((btc or {}).get("providers") or []) if isinstance(p, dict)]
    moved = [p for p in providers if _num(p.get("net_flow_usd")) not in (None, 0.0)][:5]
    takeaway = _dict(data.get("takeaway")) or None

    H = 150 + 66 * len(assets) + 40
    if btc is not None and providers:
        H += 44 + 26 * max(1, len(moved)) + 26
    if takeaway:
        H += 70
    H += 60
    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)
    f_title = _font(22, bold=True)
    f_sub = _font(13)
    f_label = _font(12, bold=True)
    f_big = _font(34, bold=True)
    f_row = _font(16, bold=True)
    f_val = _font(15, bold=True)
    f_small = _font(12)

    draw.rectangle([0, 0, W, 4], fill=_ACCENT)
    y = 18
    draw.text((PAD, y), "US SPOT CRYPTO ETF FLOWS", fill=_WHITE, font=f_title)
    y += 32
    window = f"{_short_date(data.get('window_start'))} – {_short_date(data.get('as_of'))}"
    draw.text((PAD, y), f"Weekly net creations and redemptions · {window}", fill=_GRAY, font=f_sub)
    y += 30

    total = _dict(data.get("total"))
    net = total.get("net_flow_usd")
    draw.rounded_rectangle([PAD, y, W - PAD, y + 70], radius=10, fill=_CARD_BG, outline=_BORDER)
    draw.text((PAD + 16, y + 10), "NET FLOW", fill=_GRAY, font=f_label)
    draw.text((PAD + 16, y + 26), usd_signed(net), fill=_colour(net), font=f_big)
    inside = total.get("assets_in_total") or []
    if inside:
        label = "across " + ", ".join(str(s) for s in inside)
        draw.text((W - PAD - 16 - draw.textlength(label, font=f_sub), y + 40), label, fill=_GRAY, font=f_sub)
    y += 70 + 22

    # Bars by asset: length is the flow's size against the largest one read,
    # colour its sign. An asset that was not read gets a sentence, no bar.
    # Only figures that were read size a bar; an unread week is no length.
    flows = [_num(_dict(a.get("week")).get("net_flow_usd")) for a in assets if a.get("read") is True]
    sizes = [abs(f) for f in flows if f is not None]
    biggest = max(sizes) if sizes else 0.0
    bar_x, bar_w = PAD + 70, 300
    for a in assets:
        sym = str(a.get("symbol") or "?")
        draw.text((PAD, y + 8), sym, fill=_WHITE, font=f_row)
        if a.get("read") is not True:
            why = _UNREAD_WORDS.get(str(a.get("reason")), "unreadable")
            draw.text((bar_x, y + 10), f"not read ({why})", fill=_MUTED, font=f_small)
            y += 66
            continue
        week = _dict(a.get("week"))
        flow = week.get("net_flow_usd")
        n = _num(flow)
        draw.rounded_rectangle([bar_x, y + 6, bar_x + bar_w, y + 30], radius=5, fill=_CARD_BG)
        if n is not None and n != 0 and biggest > 0:
            length = max(4, int(bar_w * abs(n) / biggest))
            draw.rounded_rectangle([bar_x, y + 6, bar_x + length, y + 30], radius=5, fill=_colour(n))
        draw.text((bar_x + bar_w + 16, y + 8), usd_signed(flow), fill=_colour(flow), font=f_val)
        detail = []
        coins = coins_signed(week.get("coins"), sym)
        if coins:
            detail.append(coins)
        read, listed = week.get("days_read"), week.get("days_listed")
        if isinstance(read, int) and isinstance(listed, int) and read < listed:
            detail.append(f"{read} of {listed} days read")
        detail.append(f"last day {usd_signed(a.get('latest_day_flow_usd'))}")
        if a.get("latest_date") != data.get("as_of"):
            detail.append(f"last reported {_short_date(a.get('latest_date'))}, not in the total")
        draw.text((bar_x, y + 38), " · ".join(detail), fill=_GRAY, font=f_small)
        y += 66
    y += 8

    if btc is not None and providers:
        draw.line([PAD, y, W - PAD, y], fill=_BORDER, width=1)
        y += 14
        draw.text((PAD, y), f"BTC FUNDS · {_short_date(btc.get('funds_date')).upper()}", fill=_GRAY, font=f_label)
        y += 30
        if not moved:
            draw.text((PAD, y), "No provider reported a flow on this day.", fill=_MUTED, font=f_small)
            y += 26
        for p in moved:
            draw.text((PAD, y), str(p.get("provider") or DASH)[:32], fill=_WHITE, font=f_small)
            v = p.get("net_flow_usd")
            text = usd_signed(v)
            draw.text((W - PAD - draw.textlength(text, font=f_small), y), text, fill=_colour(v), font=f_small)
            y += 26
        flat = sum(1 for p in providers if _num(p.get("net_flow_usd")) == 0 and p.get("funds_read") == p.get("funds"))
        unread = sum(1 for p in providers if isinstance(p.get("funds_read"), int)
                     and isinstance(p.get("funds"), int) and p["funds_read"] < p["funds"])
        notes = []
        if flat:
            notes.append(f"{flat} of {len(providers)} providers reported no flow")
        if unread:
            notes.append(f"{unread} not read for this day")
        if notes:
            draw.text((PAD, y), " · ".join(notes), fill=_MUTED, font=f_small)
        y += 26

    if takeaway:
        days = _num(takeaway.get("days_of_issuance"))
        coins = coins_signed(takeaway.get("coins"), str(takeaway.get("symbol") or "BTC"))
        if days is not None and coins:
            draw.rounded_rectangle([PAD, y, W - PAD, y + 56], radius=10, fill=_CARD_BG, outline=_BORDER)
            verb = "Net buying" if takeaway.get("direction") == "inflow" else "Net selling"
            amount = coins.replace("≈ +", "≈ ").replace("≈ -", "≈ ")
            span = _fixed(days, 0) if days >= 10 else _fixed(days, 1)
            draw.text((PAD + 14, y + 9), f"{verb} of {amount} is about {span} days of newly mined bitcoin",
                      fill=_WHITE, font=f_small)
            draw.text((PAD + 14, y + 31),
                      f"derived: {takeaway.get('issuance_per_day')} BTC a day since the 2024 halving",
                      fill=_GRAY, font=f_small)
        y += 70

    src = _dict(data.get("source"))
    draw.text((PAD, y), f"Source: {src.get('name') or DASH} · data as of {data.get('as_of') or DASH}",
              fill=_GRAY, font=f_small)
    draw.text((PAD, y + 20),
              "Coin amounts are estimates at the funds' net-asset-implied price. Market data only.",
              fill=_GRAY, font=f_small)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
