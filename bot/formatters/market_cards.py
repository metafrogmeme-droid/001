"""Cards for the four market-structure commands that never got any.

`/sweep`, `/zones`, `/squeeze` and `/holdtime` sit consecutively in
telegram_handler.py and were clearly written as one batch. They are the only 4
of 134 commands that reply with bare ``update.message.reply_text`` and no HTML,
and — the same batch, the same omission — the only market commands with no
``@guard`` at all, so they bypassed the F-2 allowlist entirely while three of
them spend an exchange call per invocation.

THE DEFECT THEY SHARE, and it is not decoration:

    price = float(closes[-1])       # /zones, line 1
    ...
    dist = abs(price - z.midpoint) / price * 100
    f"Strength: {z.strength:.0%}  Dist: {dist:.1f}%"

The price is computed, used, and never printed. `abs()` then discards the sign.
So "Dist: 0.8%" tells you a zone is 0.8% away and refuses to say which side —
on a SUPPLY/DEMAND map, where above and below is the entire content. Reading a
real card from production, the current price had to be back-solved from three
zones' distances before the screenshot could be interpreted at all. A distance
without an anchor is not a measurement, and neither is an unsigned one.

`/sweep` has it too: it prints Level, Entry and SL, all absolute, with no price
to place them against.

So every renderer here takes the price and puts it on the card. The rest —
price-ordering, bars, sane precision — follows from treating these as maps
rather than lists.

Bars are a claim, so `_bar(None)` is dots, never an empty or full block: a
strength nobody measured must not look like a strength of zero. Same rule as
the colour accents elsewhere in this repo.
"""
from __future__ import annotations

import html
from typing import Any, Optional

BAR_WIDTH = 6


def _opt(obj: Any, name: str) -> Optional[float]:
    """A float attribute, or None when absent or unreadable.

    NOT `getattr(obj, name, 0)`. A detector that failed to compute a strength
    and one that measured zero are different facts, and the whole point of the
    bar below is to show which.
    """
    value = getattr(obj, name, None)
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None       # NaN is unreadable, not a number


def _bar(fraction: Optional[float], width: int = BAR_WIDTH) -> str:
    """A 0..1 fraction as a block bar. Unknown renders as dots, never blocks."""
    if fraction is None:
        return "·" * width
    clamped = max(0.0, min(1.0, fraction))
    filled = int(round(clamped * width))
    return "█" * filled + "░" * (width - filled)


def money(value: Optional[float]) -> str:
    """Price to a precision that matches its magnitude.

    `${:,.4f}` gave `$64,260.0000` for BTC — four digits of noise on every row,
    and it truncates nothing useful off a sub-cent altcoin either, so both ends
    were served badly by one format.
    """
    if value is None:
        return "—"
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:,.2f}"
    if magnitude >= 0.1:
        return f"{value:,.4f}"
    return f"{value:,.6f}"


def _pct_from(price: Optional[float], level: Optional[float]) -> str:
    """Signed distance from price. The sign is the point — see module docstring."""
    if price in (None, 0) or level is None or price is None:
        return "  —  "
    return f"{(level - price) / price * 100:+5.1f}%"


def _header(title: str, symbol: str, price: Optional[float],
            subtitle: str = "") -> list[str]:
    """Every market card carries the price it was computed against.

    Without it the reader cannot place a single number on the card, and cannot
    tell how stale the reading is when they scroll back to it later.
    """
    out = [f"{title} — <b>{html.escape(symbol)}</b>"]
    anchor = f"price <code>{money(price)}</code>" if price is not None else (
        "<i>price unavailable — distances omitted</i>")
    out.append(f"{anchor}{('  ·  ' + subtitle) if subtitle else ''}")
    return out


# ── /zones ───────────────────────────────────────────────────────────

def render_zones(symbol: str, price: Optional[float], zones: list,
                 limit: int = 8) -> str:
    """Supply/demand as a price-ordered map with the current price in it.

    The old card listed zones by STRENGTH, so supply and demand interleaved and
    the spatial structure — the only thing a supply/demand map is for — was
    destroyed. Split at the price, supply descending above, demand descending
    below.
    """
    shown = list(zones)[:limit]
    if not shown:
        return "\n".join(_header("⚖️ <b>SUPPLY / DEMAND</b>", symbol, price)
                         + ["", "<i>No active zones.</i>"])

    def mid(z) -> Optional[float]:
        low, high = _opt(z, "zone_low"), _opt(z, "zone_high")
        if low is None or high is None:
            return _opt(z, "midpoint")
        return (low + high) / 2

    def row(z) -> str:
        low, high = _opt(z, "zone_low"), _opt(z, "zone_high")
        strength = _opt(z, "strength")
        volume = _opt(z, "volume_ratio")
        retests = getattr(z, "retests", 0) or 0
        status = str(getattr(z, "status", "") or "")
        tag = "FRESH" if status == "fresh" else (f"×{retests}" if retests else status)
        span = f"{money(low)}–{money(high)}"
        pct = "  —  " if strength is None else f"{strength * 100:>3.0f}%"
        vol = "  —  " if volume is None else f"×{volume:<4.1f}"
        return f"  {span:<19} {_bar(strength)} {pct}  {_pct_from(price, mid(z))}  {vol} {tag}"

    # A zone whose midpoint cannot be read has no place on the map; listing it
    # at an arbitrary position would assert a location nobody computed.
    placeable = [z for z in shown if mid(z) is not None]
    unplaceable = len(shown) - len(placeable)

    above, below = [], []
    if price is not None:
        above = sorted([z for z in placeable if (mid(z) or 0) > price],
                       key=lambda z: -(mid(z) or 0))
        below = sorted([z for z in placeable if (mid(z) or 0) <= price],
                       key=lambda z: -(mid(z) or 0))
    else:
        below = sorted(placeable, key=lambda z: -(mid(z) or 0))

    body = []
    if above:
        body.append("  ▲ SUPPLY / above")
        body += [row(z) for z in above]
    if price is not None:
        body.append(f"  ── price ── {money(price)} " + "─" * 18)
    if below:
        body.append("  ▼ DEMAND / below" if price is not None else "  ZONES")
        body += [row(z) for z in below]

    out = _header("⚖️ <b>SUPPLY / DEMAND</b>", symbol, price,
                  f"{len(shown)} zone{'s' if len(shown) != 1 else ''}")
    out += ["", "<pre>" + html.escape("\n".join(body)) + "</pre>"]
    if unplaceable:
        out.append(f"<i>{unplaceable} zone(s) omitted — price range unreadable.</i>")
    return "\n".join(out)


# ── /sweep ───────────────────────────────────────────────────────────

def render_sweeps(symbol: str, price: Optional[float], signals: list,
                  limit: int = 5) -> str:
    """Liquidity sweeps, with the level placed relative to price.

    The old card's direction marker was the literal string "UP"/"DOWN" built by
    `"UP" if "bullish" in s.sweep_type else "DOWN"` — which also means any
    sweep type that is neither reads as DOWN. Show the type and let an unknown
    one be unknown.

    Entry/SL are labelled SUGGESTED. They are a detector's arithmetic, not a
    working order, and a card that prints them beside a real level invites
    exactly that confusion.
    """
    shown = list(signals)[:limit]
    if not shown:
        return "\n".join(_header("🌊 <b>LIQUIDITY SWEEPS</b>", symbol, price)
                         + ["", "<i>No sweeps detected.</i>"])

    body = []
    for s in shown:
        kind = str(getattr(s, "sweep_type", "") or "unknown")
        if "bullish" in kind:
            arrow = "▲"
        elif "bearish" in kind:
            arrow = "▼"
        else:
            arrow = "•"          # not silently a downside sweep
        level = _opt(s, "level_price")
        conf = _opt(s, "confidence")
        depth = _opt(s, "depth_pct")
        rev = _opt(s, "reversal_strength")
        vol = _opt(s, "volume_ratio")
        body.append(f"  {arrow} {kind.upper()}")
        body.append(f"     level {money(level):<12} {_pct_from(price, level)}"
                    f"   conf {_bar(conf)} "
                    + ("  — " if conf is None else f"{conf * 100:>3.0f}%"))
        body.append(f"     depth {'  — ' if depth is None else f'{depth:.2f}%':<8}"
                    f" rev {'  — ' if rev is None else f'{rev * 100:.0f}%':<6}"
                    f" vol {'  — ' if vol is None else f'×{vol:.1f}'}")
        entry, stop = _opt(s, "suggested_entry"), _opt(s, "suggested_sl")
        if entry is not None or stop is not None:
            body.append(f"     suggested  entry {money(entry)}  sl {money(stop)}")
        body.append("")
    out = _header("🌊 <b>LIQUIDITY SWEEPS</b>", symbol, price,
                  f"{len(shown)} signal{'s' if len(shown) != 1 else ''}")
    out += ["", "<pre>" + html.escape("\n".join(body).rstrip()) + "</pre>",
            "<i>Suggested entry/SL are detector arithmetic, not live orders.</i>"]
    return "\n".join(out)


# ── /squeeze ─────────────────────────────────────────────────────────

def render_squeeze(symbol: str, price: Optional[float], sig: Any) -> str:
    """One signal, so the job is saying which of three states it is.

    The old line was `f"Status: {status} {direction}"` with `direction = ""`
    unless fired — a trailing space, and no way to tell "not squeezing" from
    "squeezing but we cannot say which way it will break".
    """
    if sig is None:
        return "\n".join(_header("🎯 <b>VOLATILITY SQUEEZE</b>", symbol, price)
                         + ["", "<i>Not computable — not enough data.</i>"])

    fired = bool(getattr(sig, "squeeze_fired", False))
    squeezing = bool(getattr(sig, "is_squeezing", False))
    bars = getattr(sig, "squeeze_bars", 0) or 0
    direction = str(getattr(sig, "fire_direction", "") or "").upper()

    if fired:
        icon, state = "🚀", f"FIRED {direction}".strip()
    elif squeezing:
        icon, state = "🟡", f"SQUEEZING · {bars} bar{'s' if bars != 1 else ''}"
    else:
        icon, state = "⚪", "No squeeze"

    width = _opt(sig, "bb_width_pct")
    pctile = _opt(sig, "bb_width_percentile")
    momentum = _opt(sig, "momentum")
    conf = _opt(sig, "confidence")

    body = [
        f"  state      {icon} {state}",
        f"  bb width   {'—' if width is None else f'{width:.2f}%'}"
        + ("" if pctile is None else f"  (P{pctile:.0f})"),
        f"  momentum   {'—' if momentum is None else f'{momentum:+.2f}%'}",
        f"  confidence {_bar(conf)} "
        + ("—" if conf is None else f"{conf * 100:.0f}%"),
    ]
    out = _header("🎯 <b>VOLATILITY SQUEEZE</b>", symbol, price)
    out += ["", "<pre>" + html.escape("\n".join(body)) + "</pre>"]
    description = str(getattr(sig, "description", "") or "").strip()
    if description:
        out.append(f"<i>{html.escape(description)}</i>")
    return "\n".join(out)


# ── /holdtime ────────────────────────────────────────────────────────

def render_holdtime(summary_text: str) -> str:
    """Wrap the existing analytics summary; do not rewrite it.

    ``HoldTimeAnalytics.summary()`` already prints "insufficient data" per
    strategy rather than "0 trades, 0% WR" — the exact defect this repo's rule
    is about — so it is left alone. This adds the card and nothing else.
    """
    text = (summary_text or "").strip()
    if not text:
        return ("⏱️ <b>HOLD-TIME ANALYTICS</b>\n\n"
                "<i>No analytics available yet.</i>")
    return ("⏱️ <b>HOLD-TIME ANALYTICS</b>\n\n"
            "<pre>" + html.escape(text) + "</pre>")


# ── Absence states ───────────────────────────────────────────────────
# Carded, not bare text. An unstyled line in the middle of an otherwise-carded
# command reads as an error; these are measured absences — the scan ran and
# found nothing — and should look like a result.

def render_no_zones(symbol: str) -> str:
    return ("⚖️ <b>SUPPLY / DEMAND</b> — <b>" + html.escape(symbol) + "</b>\n\n"
            "<i>No active zones right now. The scan ran — this is a reading, "
            "not a failure.</i>")


def render_no_sweeps(symbol: str) -> str:
    return ("🌊 <b>LIQUIDITY SWEEPS</b> — <b>" + html.escape(symbol) + "</b>\n\n"
            "<i>No sweeps detected in the last 100 candles.</i>")


def render_squeeze_unavailable(symbol: str) -> str:
    return ("🎯 <b>VOLATILITY SQUEEZE</b> — <b>" + html.escape(symbol) + "</b>\n\n"
            "<i>Not computable — the bands could not be derived from this "
            "history. Not the same as 'no squeeze'.</i>")
