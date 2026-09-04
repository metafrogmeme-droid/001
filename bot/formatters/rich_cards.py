"""Rich Telegram card formatters for RUNECLAW.

Produces the detailed, war-room-grade analysis cards with VWAP, orderbook
depth, support/resistance levels, comparison tables, PNL reports, and
pending order views.

All functions return plain HTML strings (Telegram parse_mode="HTML").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from bot.utils.i18n import t

log = logging.getLogger("runeclaw.formatters")

# ── Visual constants ─────────────────────────────────────────────
SEP = "\u2500" * 16  # ────────────────


def display_symbol(symbol: str) -> str:
    """Consistent short display name for any symbol format.

    MEGA/USDT:USDT  → MEGA
    NATGAS/USDT:USDT → NATGAS
    XLK/USDT        → XLK
    MEGAUSDT         → MEGA
    BTC/USDT:USDT   → BTC
    """
    s = symbol.upper()
    # Strip settle suffix :USDT
    if ":USDT" in s:
        s = s.split(":")[0]
    # Strip quote /USDT
    if "/USDT" in s:
        s = s.split("/")[0]
    # Handle raw concatenated form (BTCUSDT → BTC)
    if s.endswith("USDT") and "/" not in s and ":" not in s:
        base = s[:-4]
        if base:
            s = base
    return s


# ── Market-data helpers ──────────────────────────────────────────

def compute_vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 volumes: np.ndarray) -> float:
    """Volume-weighted average price from OHLCV arrays."""
    typical = (highs + lows + closes) / 3.0
    total_vol = np.sum(volumes)
    if total_vol == 0:
        return float(closes[-1])
    return float(np.sum(typical * volumes) / total_vol)


def compute_support_resistance(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    current_price: float, n_levels: int = 2,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Derive support/resistance zones from recent swing points.

    Returns (supports, resistances) as lists of (low, high) tuples.
    """
    # Find local minima/maxima using a simple rolling window
    window = 5
    supports: list[float] = []
    resistances: list[float] = []

    for i in range(window, len(closes) - window):
        if lows[i] == np.min(lows[i - window:i + window + 1]):
            supports.append(float(lows[i]))
        if highs[i] == np.max(highs[i - window:i + window + 1]):
            resistances.append(float(highs[i]))

    # Cluster nearby levels and pick the closest below/above current price
    def _cluster(levels: list[float], price: float, side: str) -> List[Tuple[float, float]]:
        if not levels:
            return []
        filtered = [l for l in levels if (l < price if side == "support" else l > price)]
        if not filtered:
            return []
        # Sort by distance from current price
        filtered.sort(key=lambda x: abs(x - price))
        zones: List[Tuple[float, float]] = []
        used = set()
        for lvl in filtered:
            if any(abs(lvl - u) / price < 0.005 for u in used):
                continue
            used.add(lvl)
            spread = price * 0.005  # 0.5% zone width
            zones.append((round(lvl - spread / 2, 6), round(lvl + spread / 2, 6)))
            if len(zones) >= n_levels:
                break
        # Sort supports descending (closest first), resistances ascending
        zones.sort(key=lambda z: z[0], reverse=(side == "support"))
        return zones

    return (_cluster(supports, current_price, "support"),
            _cluster(resistances, current_price, "resistance"))


def rsi_or_none(closes: np.ndarray, period: int = 14) -> Optional[float]:
    """RSI when there is enough history to compute one; None when there is not.

    compute_rsi below answers 50.0 on short history, and the scanner relies
    on that (its regime arithmetic compares the number). On a DISPLAY that
    same 50.0 is "RSI 50 (neutral)" for a symbol with a dozen bars -- a
    reading never taken, printed with the confidence of one. Cards read
    through this and render a dash; the scanner keeps its own default.
    """
    if len(closes) < period + 1:
        return None
    return compute_rsi(closes, period)


def _fmt_rsi(v: Optional[float]) -> str:
    return "\u2014" if v is None else f"{float(v):.1f}"


def rsi_label(v: Optional[float]) -> str:
    """overbought / oversold / neutral -- or "unread" when there is no reading.
    Never "neutral" for None: neutral is a verdict about a number."""
    if v is None:
        return "unread"
    return "overbought" if v > 70 else "oversold" if v < 30 else "neutral"


def market_context_line(adata: Optional[dict]) -> str:
    """The one-line market context on the position-details card.

    Was inline in the callback handler as
    `rsi_val = adata.get('rsi', 0)` -> "RSI 0 (oversold)" for an absent
    reading, and "RSI 50 (neutral)" for a short history. A seam, so the
    None case can be driven: it says "RSI \u2014 (unread)" and keeps the
    structure, which is still known.
    """
    if not isinstance(adata, dict):
        return ""
    rsi = adata.get("rsi")
    structure = str(adata.get("structure", "") or "")
    return f"RSI {_fmt_rsi(rsi)} ({rsi_label(rsi)}) | {structure}"


def compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    """Canonical Wilder RSI (audit fix #20: this card previously displayed a
    simple-mean approximation that could disagree with the bot's real RSI)."""
    if len(closes) < period + 1:
        return 50.0
    from bot.core.ta_utils import rsi_series
    return float(rsi_series(closes, period)[-1])


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    h, l, c = highs[1:], lows[1:], closes[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - c), np.abs(l - c)))
    return float(np.mean(tr[-period:]))


def _fmt_price(p) -> str:
    """Smart price formatter — fewer decimals for larger prices.

    None renders as an em dash, never `$0.00`. Every caller is a display path,
    and a price is exactly the field this repo's doctrine opens on: an
    unfetchable one shown as a number is the defect, not the crash. Guarding
    here rather than at each call site means a new caller inherits the honest
    behaviour instead of having to remember it.
    """
    if p is None:
        return "—"
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "—"
    if p != p:            # NaN: not a price, and every comparison below is False
        return "—"
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:,.5f}"
    return f"${p:,.6f}"


def _fmt_vol(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1e9:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1e6:.1f}M"
    if v >= 1_000:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.0f}"


def _pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _verdict_header(status_icon: str, status_label: str, bias: str = "",
                     risk_state: str = "", action: str = "") -> str:
    """Build a one-glance verdict header block."""
    lines = []
    lines.append("⚔️ <b>RUNECLAW VERDICT</b>")
    lines.append(SEP)
    lines.append(f"  Status: {status_icon} <b>{status_label}</b>")
    if bias:
        lines.append(f"  Bias: <i>{bias}</i>")
    if risk_state:
        lines.append(f"  Risk State: <i>{risk_state}</i>")
    if action:
        lines.append(f"  Action: <i>{action}</i>")
    lines.append("")
    return "\n".join(lines)


# ── Data-fetching helper (async) ─────────────────────────────────

async def fetch_analysis_data(exchange, symbol: str, timeframe: str = "1h",
                              limit: int = 100) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV + orderbook for a symbol. Returns None on failure."""
    # Normalize symbol: try original, then variants
    candidates = [symbol]
    if ":USDT" in symbol:
        candidates.append(symbol.split(":")[0])  # SUSHI/USDT:USDT -> SUSHI/USDT
    elif "/" in symbol and ":USDT" not in symbol:
        candidates.append(f"{symbol}:USDT")  # SUSHI/USDT -> SUSHI/USDT:USDT
    if "/" not in symbol and symbol.endswith("USDT"):
        base = symbol[:-4]  # Strip trailing "USDT" only (not mid-string)
        if base:
            candidates = [f"{base}/USDT", f"{base}/USDT:USDT", symbol]
    try:
        ohlcv, orderbook = None, None
        for sym in candidates:
            try:
                ohlcv = await exchange.fetch_ohlcv(sym, timeframe, limit=limit)
                if ohlcv and len(ohlcv) >= 20:
                    symbol = sym  # use the working symbol
                    break
                ohlcv = None
            except Exception as e:
                log.debug("OHLCV fetch failed for %s: %s", sym, e)
                continue
        if not ohlcv or len(ohlcv) < 20:
            log.warning("OHLCV unavailable for %s (tried: %s)", symbol, candidates)
            return None

        # NO fetch_ticker HERE, deliberately. It was awaited on every call and
        # its result read by nothing — the only mentions in this file were the
        # docstring, a None init, the await, and a `ticker = {}` fallback. An
        # exchange REST round-trip for nobody, and telegram_handler calls this
        # in a loop over up to four chart timeframes, so up to four of them per
        # card, against the same rate-limited ccxt instance the analyze phase
        # is already starving on.
        #
        # Everything the card renders comes from the candles below. Worth
        # knowing if a future edit wants the venue's own 24h stats back:
        # `high_24h` / `low_24h` / `change_pct` are computed over 24 BARS
        # (`h[-24:]`, `c[-25:]`), which equals 24 hours only at the 1h
        # timeframe. Both callers that read those fields pass "1h"; the one
        # that passes another timeframe reads only `ohlcv_raw`. Latent, not
        # live — a ticker fetch is the right fix if that ever changes, and it
        # would then have a reader.
        try:
            orderbook = await exchange.fetch_order_book(symbol, limit=20)
        except Exception as e:
            log.debug("Orderbook fetch failed for %s: %s", symbol, e)
            orderbook = {"bids": [], "asks": []}

        if not ohlcv:
            return None

        o = np.array([c[1] for c in ohlcv], dtype=float)
        h = np.array([c[2] for c in ohlcv], dtype=float)
        l = np.array([c[3] for c in ohlcv], dtype=float)
        c = np.array([c[4] for c in ohlcv], dtype=float)
        v = np.array([c[5] for c in ohlcv], dtype=float)

        price = float(c[-1])
        high_24h = float(np.max(h[-24:])) if len(h) >= 24 else float(np.max(h))
        low_24h = float(np.min(l[-24:])) if len(l) >= 24 else float(np.min(l))
        vwap = compute_vwap(h, l, c, v)
        vwap_pct = ((price - vwap) / vwap * 100) if vwap > 0 else 0
        # None on short history, not 50.0 / 0.0: these feed DISPLAYS, and a
        # neutral RSI or a zero range for a dozen bars is a reading never
        # taken. _fmt_price(None) already renders an em dash; _fmt_rsi too.
        rsi = rsi_or_none(c)
        atr = compute_atr(h, l, c) if len(c) >= 15 else None

        # Volume
        vol_24h = float(np.sum(v[-24:])) if len(v) >= 24 else float(np.sum(v))
        vol_avg = float(np.mean(v[-48:-24])) if len(v) >= 48 else float(np.mean(v[:-24])) if len(v) > 24 else vol_24h
        vol_spike = vol_24h / vol_avg if vol_avg > 0 else 1.0

        # Price change
        price_24h_ago = float(c[-25]) if len(c) >= 25 else float(c[0])
        change_pct = ((price - price_24h_ago) / price_24h_ago * 100) if price_24h_ago > 0 else 0

        # Orderbook depth
        bid_depth = sum(b[1] for b in orderbook.get("bids", [])[:10])
        ask_depth = sum(a[1] for a in orderbook.get("asks", [])[:10])
        # Convert to USD-equivalent
        bid_depth_usd = bid_depth * price
        ask_depth_usd = ask_depth * price

        # Support/Resistance
        supports, resistances = compute_support_resistance(h, l, c, price)

        # SMA
        sma9 = float(np.mean(c[-9:])) if len(c) >= 9 else price
        sma20 = float(np.mean(c[-20:])) if len(c) >= 20 else price
        sma50 = float(np.mean(c[-50:])) if len(c) >= 50 else sma20

        # 1H structure description
        recent_low = float(np.min(l[-24:])) if len(l) >= 24 else float(np.min(l))
        recent_high = float(np.max(h[-24:])) if len(h) >= 24 else float(np.max(h))
        if price > sma20 and price > recent_low * 1.03:
            structure = f"Breakout from {_fmt_price(recent_low)} base \u2192 {_fmt_price(recent_high)} high"
            if abs(price - recent_high) / price < 0.01:
                structure += ", consolidating at top"
            else:
                structure += ", pulling back from high"
        elif price < sma20:
            structure = f"Downtrend from {_fmt_price(recent_high)} \u2192 testing {_fmt_price(recent_low)}"
        else:
            structure = f"Range-bound {_fmt_price(recent_low)} \u2013 {_fmt_price(recent_high)}"

        return {
            "symbol": symbol,
            "pair": display_symbol(symbol),
            "price": price,
            "high_24h": high_24h,
            "low_24h": low_24h,
            "change_pct": change_pct,
            "volume_24h_usd": vol_24h * price,
            "vol_spike": vol_spike,
            "vwap": vwap,
            "vwap_pct": vwap_pct,
            "rsi": rsi,
            "atr": atr,
            "sma9": sma9,
            "sma20": sma20,
            "sma50": sma50,
            "bid_depth": bid_depth_usd,
            "ask_depth": ask_depth_usd,
            "supports": supports,
            "resistances": resistances,
            "structure": structure,
            "ohlcv": {"o": o, "h": h, "l": l, "c": c, "v": v},
            "ohlcv_raw": ohlcv,  # raw CCXT [ts,o,h,l,c,v] — used by chart renderer
        }
    except Exception as e:
        log.error("fetch_analysis_data failed for %s: %s", symbol, e)
        return None


# ── Card renderers ───────────────────────────────────────────────

def render_analysis_card(data: Dict[str, Any], idea: Optional[Any] = None) -> str:
    """Render a single-asset rich analysis card.

    `data` is the dict from fetch_analysis_data().
    `idea` is an optional TradeIdea with entry/SL/TP.
    """
    pair = data["pair"]
    price = data["price"]
    change = data["change_pct"]
    vol = data["volume_24h_usd"]

    # Header
    lines = [
        f"\u2694\ufe0f <b>{pair}</b> \u2014 {_fmt_price(price)} | {_pct(change)} | Vol {_fmt_vol(vol)}",
        "",
        "<b>Current Snapshot:</b>",
        f"- Last: {_fmt_price(price)} | High: {_fmt_price(data['high_24h'])} | Low: {_fmt_price(data['low_24h'])}",
        f"- VWAP: {_fmt_price(data['vwap'])} \u2014 price is {_pct(data['vwap_pct'])} {'above' if data['vwap_pct'] >= 0 else 'below'} VWAP"
        + (" (very extended)" if abs(data["vwap_pct"]) > 10 else " (moderate)" if abs(data["vwap_pct"]) > 5 else ""),
        f"- Bid/Ask: {_fmt_vol(data['bid_depth'])} bid vs {_fmt_vol(data['ask_depth'])} ask \u2014 "
        + (_bid_ask_read(data["bid_depth"], data["ask_depth"])),
        f"- {data.get('timeframe', '1H')} structure: {data['structure']}",
        f"- RSI: {_fmt_rsi(data['rsi'])} | ATR: {_fmt_price(data['atr'])} | Vol spike: {data['vol_spike']:.1f}x",
    ]

    # Key Levels
    lines.append("")
    lines.append("<b>Key Levels (from kline analysis):</b>")
    for i, s in enumerate(data.get("supports", []), 1):
        desc = ""
        if i == 1:
            desc = " (breakout retest zone)"
        elif i == 2:
            desc = " (VWAP area)" if abs(s[0] - data["vwap"]) / data["vwap"] < 0.02 else " (deeper support)"
        lines.append(f"- Support {i}: {_fmt_price(s[0])}-{_fmt_price(s[1])}{desc}")
    for i, r in enumerate(data.get("resistances", []), 1):
        desc = " (current high)" if i == 1 else ""
        lines.append(f"- Resistance{'' if i == 1 else f' {i}'}: {_fmt_price(r[0])}{desc}")

    # Setup (from TradeIdea)
    if idea:
        entry, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
        sl_pct = abs(entry - sl) / entry * 100
        tp_pct = abs(tp - entry) / entry * 100
        rr = idea.risk_reward_ratio
        direction = idea.direction.value

        lines.append("")
        lines.append(f"<b>Setup \u2014 {direction} {'on Pullback' if direction == 'LONG' and entry < price else ''}:</b>")
        lines.append(f"- Entry: {_fmt_price(entry)}")
        lines.append(f"- SL: {_fmt_price(sl)} (-{sl_pct:.1f}%)")
        lines.append(f"- TP: {_fmt_price(tp)} (+{tp_pct:.1f}%)")
        lines.append(f"- Risk/Reward: 1:{rr:.1f}")
        lines.append(f"- Confidence: {idea.confidence:.0%}")

    # Velocity gate warning
    if abs(data["change_pct"]) > 15:
        lines.append("")
        gate_dir = "counter-trend short is blocked" if data["change_pct"] > 0 else "counter-trend long is blocked"
        lines.append(f"\u26a0\ufe0f Velocity Gate: {data.get('timeframe', '1H')} change {_pct(data['change_pct'])} \u2014 {gate_dir}")

    # Orderbook concern
    if data["ask_depth"] > data["bid_depth"] * 2:
        lines.append("")
        lines.append(f"\u26a0\ufe0f Concern: Ask-side dominance ({_fmt_vol(data['ask_depth'])} vs {_fmt_vol(data['bid_depth'])}) suggests distribution.")

    return "\n".join(lines)


def _bid_ask_read(bid: float, ask: float) -> str:
    """Human-readable orderbook bias."""
    if bid > ask * 3:
        return "buyers stacking hard"
    if bid > ask * 1.5:
        return "bid-side dominant (bullish)"
    if ask > bid * 3:
        return "heavy sell wall (distribution)"
    if ask > bid * 1.5:
        return "ask-side dominant (bearish)"
    return "balanced"


def render_comparison_table(assets: List[Dict[str, Any]],
                            ideas: Optional[List[Any]] = None) -> str:
    """Render a side-by-side comparison table for multiple assets."""
    if len(assets) < 2:
        return ""

    ideas_map = {}
    if ideas:
        for idea in ideas:
            ideas_map[idea.asset] = idea

    lines = ["\u2694\ufe0f <b>COMPARISON</b>", ""]

    # Build rows
    rows = [
        ("Current Price", [_fmt_price(a["price"]) for a in assets]),
        ("24h Change", [_pct(a["change_pct"]) for a in assets]),
        ("Above VWAP", [f"{_pct(a['vwap_pct'])} ({'extended' if abs(a['vwap_pct']) > 10 else 'moderate' if abs(a['vwap_pct']) > 5 else 'tight'})" for a in assets]),
        ("Bid/Ask", [f"{_fmt_vol(a['bid_depth'])} vs {_fmt_vol(a['ask_depth'])} ({'bullish' if a['bid_depth'] > a['ask_depth'] else 'bearish'})" for a in assets]),
        ("RSI", [_fmt_rsi(a['rsi']) for a in assets]),
        ("Volume", [_fmt_vol(a["volume_24h_usd"]) for a in assets]),
    ]

    # Add idea-specific rows
    for a in assets:
        sym = a["symbol"]
        if sym in ideas_map:
            idea = ideas_map[sym]
            idx = assets.index(a)
            if len(rows) <= 6:
                entry_row = [""] * len(assets)
                sl_row = [""] * len(assets)
                rr_row = [""] * len(assets)
                for j, aa in enumerate(assets):
                    if aa["symbol"] in ideas_map:
                        ii = ideas_map[aa["symbol"]]
                        entry_row[j] = _fmt_price(ii.entry_price)
                        sl_pct = abs(ii.entry_price - ii.stop_loss) / ii.entry_price * 100
                        sl_row[j] = f"-{sl_pct:.1f}%"
                        rr_row[j] = f"1:{ii.risk_reward_ratio:.1f}"
                rows.append(("Entry", entry_row))
                rows.append(("SL Distance", sl_row))
                rows.append(("R:R", rr_row))
                break

    # Determine verdict
    scores = []
    for a in assets:
        s = 0
        s += (1 if a["bid_depth"] > a["ask_depth"] else -1)
        s += (1 if abs(a["vwap_pct"]) < 10 else -1)
        s += (1 if a["vol_spike"] > 1.2 else 0)
        if a["symbol"] in ideas_map:
            s += ideas_map[a["symbol"]].risk_reward_ratio
        scores.append(s)

    verdict_row = []
    best_idx = scores.index(max(scores))
    for i in range(len(assets)):
        verdict_row.append("<b>Preferred</b>" if i == best_idx else "Secondary")
    rows.append(("Verdict", verdict_row))

    # Format as bullet list
    names = [a["pair"] for a in assets]
    for label, vals in rows:
        parts = "; ".join(f"{names[i]}: {vals[i]}" for i in range(len(assets)))
        lines.append(f"\u2022 {label}: {parts}")

    return "\n".join(lines)


def render_recommended_orders(assets: List[Dict[str, Any]],
                              ideas: List[Any]) -> str:
    """Render RECOMMENDED ORDERS section."""
    lines = ["", SEP, "", "<b>RECOMMENDED ORDERS</b>", ""]

    ideas_map = {idea.asset: idea for idea in ideas}
    scores = []
    for a in assets:
        s = 0
        s += (1 if a["bid_depth"] > a["ask_depth"] else -1)
        if a["symbol"] in ideas_map:
            s += ideas_map[a["symbol"]].risk_reward_ratio
        scores.append(s)

    best_idx = scores.index(max(scores)) if scores else 0

    for i, a in enumerate(assets):
        if a["symbol"] not in ideas_map:
            continue
        idea = ideas_map[a["symbol"]]
        rank = "Primary" if i == best_idx else "Secondary"
        sl_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
        tp_pct = abs(idea.take_profit - idea.entry_price) / idea.entry_price * 100

        lines.append(f"<b>{a['pair']}</b> ({rank}):")
        lines.append(f"- Entry: {_fmt_price(idea.entry_price)}")
        lines.append(f"- SL: {_fmt_price(idea.stop_loss)} (-{sl_pct:.1f}%) | TP: {_fmt_price(idea.take_profit)} (+{tp_pct:.1f}%)")
        lines.append(f"- R:R 1:{idea.risk_reward_ratio:.1f} | Conf: {idea.confidence:.0%}")
        if i == best_idx and a["bid_depth"] > a["ask_depth"]:
            lines.append("- \u2705 Bid dominance \u2014 cleaner setup")
        elif a["ask_depth"] > a["bid_depth"] * 1.5:
            lines.append("- \u26a0\ufe0f Ask wall present \u2014 caution")
        lines.append("")

    is_pullback = any(
        ideas_map.get(a["symbol"]) and ideas_map[a["symbol"]].entry_price < a["price"]
        for a in assets
    )
    if is_pullback:
        lines.append("<i>Pullback entries \u2014 not chases.</i>")

    # Next Best Action
    if lines:
        lines.append("")
        lines.append("🎯 <b>Next Action:</b> <i>Entry is conditional. Wait for confirmation trigger before execution.</i>")

    return "\n".join(lines)


# ── Pending orders card ──────────────────────────────────────────

def render_pending_orders(orders: List[Dict[str, Any]],
                          current_prices: Dict[str, float]) -> str:
    """Render pending/open orders with distances from current price."""
    if not orders:
        return ("\u2694\ufe0f <b>PENDING ORDERS</b>\n"
                f"{SEP}\n\n"
                "No pending orders.")

    lines = [
        "\u2694\ufe0f <b>PENDING ORDERS \u2014 Live</b>",
        "",
    ]

    for i, order in enumerate(orders, 1):
        pair = order.get("symbol", "").replace("/", "")
        direction = order.get("side", "buy").upper()
        d_icon = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
        entry = order.get("price", 0)
        qty = order.get("amount", 0)
        order_id = order.get("id", "N/A")
        sl = order.get("stopLoss", order.get("sl", 0))
        tp = order.get("takeProfit", order.get("tp", 0))
        status = order.get("status", "open").capitalize()
        leverage = order.get("leverage", "")

        # Distance from current price
        current = current_prices.get(order.get("symbol", ""), entry)
        distance_pct = ((current - entry) / entry * 100) if entry > 0 else 0

        lines.append(f"{i}. {d_icon} <b>{pair} {direction.replace('BUY', 'Long').replace('SELL', 'Short')}</b>")
        lines.append(f"- Order ID: <code>{order_id}</code>")
        lines.append(f"- Limit: {_fmt_price(entry)} | Qty: {qty:,.3f}")
        if leverage:
            lines.append(f"- Leverage: {leverage}x")
        if sl:
            lines.append(f"- SL: {_fmt_price(sl)} | TP: {_fmt_price(tp)}")
        lines.append(f"- Status: {status}")
        lines.append(f"- Distance: {pair} at ~{_fmt_price(current)} \u2192 entry is {_pct(-distance_pct)} {'below' if distance_pct > 0 else 'above'} current")
        lines.append("")

    return "\n".join(lines)


# ── PNL report card ──────────────────────────────────────────────

def render_pnl_report(
    equity: float,
    available: float,
    locked: float,
    open_positions: int,
    closed_trades: List[Dict[str, Any]],
    pending_orders: List[Dict[str, Any]] = None,
) -> str:
    """Render a detailed PNL report with session tally."""
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        f"\u2694\ufe0f <b>PNL REPORT</b> \u2014 {now}",
        "",
        f"Account Equity: <b>{_fmt_vol(equity)}</b>"
        + (f" | {open_positions} open position{'s' if open_positions != 1 else ''}" if open_positions else " | No open positions"),
        "",
        SEP,
    ]

    # Closed trades detail
    session_pnl = 0.0
    if closed_trades:
        for trade in closed_trades:
            pair = trade.get("pair", trade.get("symbol", "N/A")).replace("/", "")
            entry_price = trade.get("entry_price", 0)
            exit_price = trade.get("exit_price", 0)
            size = trade.get("size", 0)
            pnl = trade.get("pnl", 0)
            fees = trade.get("fees", 0)
            net_pnl = pnl - fees
            session_pnl += net_pnl
            win = net_pnl > 0

            lines.extend([
                "",
                f"<b>{pair} Trade</b> {'(closed)' if trade.get('closed') else ''}",
                "",
                f"\u2022 Entry: {_fmt_price(entry_price)}",
                f"\u2022 Exit: {_fmt_price(exit_price)}",
                f"\u2022 Size: {size:,.4f}",
                f"\u2022 Gross PnL: {_pct(pnl) if isinstance(pnl, float) and abs(pnl) < 100 else _fmt_price(pnl)}",
            ])
            if fees:
                lines.append(f"\u2022 Fees: ~{_fmt_price(fees)}")
            icon = "\u2705" if win else "\u274c"
            lines.append(f"\u2022 <b>Net PnL: {_fmt_price(net_pnl)}</b> {icon}")
            lines.append("")
            lines.append(SEP)

    # Current state
    lines.extend([
        "",
        "<b>Current State</b>",
        "",
        f"- Equity: <b>{_fmt_price(equity)}</b>",
        f"- Available: {_fmt_price(available)}",
    ])
    if locked > 0:
        lines.append(f"- Locked: {_fmt_price(locked)}")
    lines.append(f"- Open positions: {open_positions}")

    if pending_orders:
        for po in pending_orders:
            pair = po.get("symbol", "").replace("/", "")
            lines.append(f"- Pending: {pair} limit @ {_fmt_price(po.get('price', 0))}")

    # Session tally
    lines.extend([
        "",
        SEP,
        "",
        "<b>Session Tally</b>",
        "",
    ])

    if closed_trades:
        # Dict rows here rather than positions, same rule: a row with no
        # recorded pnl is scored neither way, and `len(...) - wins` would
        # have shown it as an L.
        from bot.utils.win_rate import win_stats as _win_stats
        _ws = _win_stats(closed_trades)
        wins = _ws["wins"]
        losses = _ws["scored"] - wins
        lines.append(
            f"{'Today was green.' if session_pnl > 0 else 'Today was red.' if session_pnl < 0 else 'Flat session.'} "
            f"{wins}W/{losses}L, net {_fmt_price(session_pnl)}."
        )
    else:
        lines.append("No closed trades this session.")

    return "\n".join(lines)


# ── Multi-asset analysis card ────────────────────────────────────

def render_multi_analysis(
    assets: List[Dict[str, Any]],
    ideas: Optional[List[Any]] = None,
) -> str:
    """Full multi-asset analysis: individual cards + comparison + orders."""
    if not assets:
        return "No data available."

    # Dedup: keep only unique symbols (last occurrence wins)
    seen = {}
    for a in assets:
        seen[a.get("symbol") or a.get("pair", "")] = a
    assets = list(seen.values())

    # Dedup ideas by asset
    if ideas:
        seen_ideas = {}
        for i in ideas:
            seen_ideas[i.asset] = i
        ideas = list(seen_ideas.values())

    names = " & ".join(a["pair"] for a in assets)
    parts = [
        f"\u2694\ufe0f <b>{names}</b> \u2014 LIVE ANALYSIS",
        "",
        SEP,
        "",
    ]

    ideas_map = {}
    if ideas:
        ideas_map = {i.asset: i for i in ideas}

    for a in assets:
        parts.append("")
        idea = ideas_map.get(a["symbol"])
        parts.append(render_analysis_card(a, idea))
        parts.append("")
        parts.append(SEP)

    # Comparison (if multiple)
    if len(assets) >= 2:
        parts.append("")
        parts.append(render_comparison_table(assets, ideas))
        parts.append("")
        parts.append(SEP)

    # Recommended orders
    if ideas:
        parts.append(render_recommended_orders(assets, ideas))

    return "\n".join(parts)


# ── Open positions card ──────────────────────────────────────────

def render_live_portfolio_summary(equity: float, open_count: int,
                                  exposure: float,
                                  realized_pnl: Optional[float],
                                  total_closed: int,
                                  win_rate: Optional[float],
                                  unscored: int = 0,
                                  read_failed: bool = False) -> List[str]:
    """The /portfolio (LIVE) header block, as a PURE function.

    Extracted so the WINDOW on each number can be asserted by rendering the
    card rather than by grepping the file that builds it. On 2026-07-30 this
    block printed an unlabelled lifetime total directly above a "Recent:"
    list of four green closes, on a day the exchange put at about +$6.36 —
    every figure correct, one wrong conclusion invited. A source scan can see
    the string; only a render can see what sits next to it.

    `realized_pnl` is LIFETIME: it sums the executor's PERSISTED closed
    trades (F-14), which survive restarts. The label says so.

    `unscored` is the same problem in miniature, one line down. "Trades: 20"
    and "Win rate: 60%" sit side by side, so the pair implies the rate ran
    over all twenty -- and after #1020 it runs over however many carried a
    recorded P&L. Defaults to 0 so callers that genuinely have none say
    nothing extra.

    `realized_pnl` and `win_rate` are BOTH Optional, and None means "no
    measurement", never zero. An empty closed-trades file used to reach this
    function as 0.0 and render `🟢 $+0.00` -- a green accent and a signed
    figure, two independent claims of a measured break-even, on the number
    an operator reads first. It is what turned a UI artifact into a day-long
    data-loss investigation. `total_closed == 0` forces that path too: with
    no closed trades there is nothing a total COULD have measured, whatever
    the caller passed, so a legacy caller still cannot print a fake zero.

    `read_failed` separates the two absences. An unreadable store also
    arrives here as an empty list, and "No closed trades recorded" over a
    failed read is a confident negative standing in for a missing
    measurement — the same shape as a 503 rendered "No venues found".
    """
    # A total nothing could price gets neither digits nor a colour -- the
    # accent is a claim in its own right, so "unknown" owns the muted one.
    if realized_pnl is None or total_closed <= 0:
        pnl_cell = "⚪ <code>—</code>"
    else:
        pnl_icon = "\U0001f7e2" if realized_pnl >= 0 else "\U0001f534"
        pnl_cell = f"{pnl_icon} <code>${realized_pnl:+,.2f}</code>"
    lines = [
        "<b>Portfolio</b> (LIVE)\n",
        f"Equity: <code>${equity:,.2f}</code>",
        f"Open: <code>{open_count}</code> | Exposure: <code>${exposure:,.2f}</code>",
        f"Realized PnL (all-time): {pnl_cell}",
    ]
    if read_failed:
        # An unreadable store is not an empty one. Saying "no closed trades
        # recorded" over a failed read is the 503-as-"No venues found" shape:
        # a confident negative standing in for a missing measurement.
        lines.append("<i>Closed-trade records could not be read — "
                     "figures here are incomplete, not zero.</i>")
    elif total_closed <= 0:
        # Says which absence it is. A bare em-dash reads as a render fault;
        # "no closed trades recorded" is the actual state of the book.
        lines.append("<i>No closed trades recorded.</i>")
    if total_closed > 0:
        # None is not 0% -- "everything lost" and "nothing could be priced"
        # are different findings and only one of them is a measurement.
        wr_cell = "—" if win_rate is None else f"{win_rate:.0f}%"
        lines.append(
            f"Trades: <code>{total_closed}</code> | "
            f"Win rate: <code>{wr_cell}</code>")
        # Coerced here rather than trusted: this is a display path, and a
        # card that raises is worse than one that omits a caveat.
        try:
            _u = max(0, int(unscored or 0))
        except (TypeError, ValueError):
            _u = 0
        from bot.utils.win_rate import coverage_note as _wr_note
        _note = _wr_note({"unscored": _u, "total": total_closed,
                          "scored": max(0, total_closed - _u)})
        if _note:
            lines.append(_note.strip())
    return lines


def render_adoption_card(adopted_symbols: List[str],
                         positions: Optional[List[Any]] = None) -> str:
    """The "Adopted Exchange Positions" notice, as a PURE function.

    Extracted from the Telegram handler because it was unreachable by tests
    while it lived inline, and that is exactly how it shipped broken: the
    per-position SL/TP outcome (#999) never rendered once in production. The
    callback receives symbols, the lookup matched none of them, and nothing
    failed — there was no seam at which to assert "the card names the levels".

    ``positions`` is the executor's position list (or None). Each adopted
    symbol is matched against the ADOPTED, OPEN ones; a symbol with no match
    renders bare rather than being dropped, because losing an adoption notice
    is worse than rendering one without levels.
    """
    from bot.core.live_executor import normalize_symbol

    lines = [
        "\u26a0\ufe0f <b>Adopted Exchange Positions</b>",
        "",
        f"Found <b>{len(adopted_symbols)}</b> position(s) on the exchange",
        "that were not tracked locally:",
        "",
    ]
    for sym in adopted_symbols:
        detail = ""
        try:
            pos = next(
                (p for p in (positions or [])
                 if getattr(p, "origin", "") == "adopted"
                 and normalize_symbol(getattr(p, "symbol", "")) == normalize_symbol(sym)
                 and getattr(p, "status", "") == "open"),
                None)
            if pos is not None:
                if getattr(pos, "unprotected", False):
                    detail = (" \u2014 \U0001f6a8 <b>UNPROTECTED</b>: the safety "
                              "stop could not be placed. Set one NOW.")
                elif getattr(pos, "stop_loss", 0):
                    _tp = getattr(pos, "take_profit", 0)
                    detail = (f" \u2014 SL <code>{pos.stop_loss:g}</code>"
                              + (f" / TP <code>{_tp:g}</code>" if _tp else "")
                              + " active")
        except Exception:
            detail = ""
        lines.append(f"  \u2022 <code>{_html_escape(str(sym))}</code>{detail}")
    lines.extend([
        "",
        "These may have been opened in a previous session",
        "or directly on the exchange.",
        "",
        "Use <b>Positions</b> to review. Close any you didn't intend.",
    ])
    return "\n".join(lines)


def _html_escape(s: str) -> str:
    import html as _h
    return _h.escape(s)


def render_open_positions(positions: List[Dict[str, Any]], lang: str = "en") -> str:
    """Render open positions — compact card format. Telegram HTML (CJK-safe)."""
    if not positions:
        return t("open_pos_empty", lang)

    # A row whose mark could not be read contributes NOTHING to the total —
    # not a zero. Summing an unknown as 0 quietly drags the headline toward
    # break-even and paints a colour on the result, and colour is a claim as
    # loud as the number. Test `is None`, not falsiness: 0.0 is a real,
    # measured, break-even position and must still count.
    from bot.utils.portfolio_return import coverage_note, open_book_return
    _book = open_book_return(positions)
    total_pnl = _book["pct"]

    if total_pnl is None:
        header = (f"<b>{t('open_positions_n', lang, n=len(positions))}</b> "
                  f"\u26a0\ufe0f {t('pnl_unknown', lang)}")
    else:
        pnl_icon = "\U0001f7e2" if total_pnl > 0 else "\U0001f534" if total_pnl < 0 else ""
        header = (f"<b>{t('open_positions_n', lang, n=len(positions))}</b> "
                  f"{pnl_icon} {_pct(total_pnl)} {t('lbl_total', lang)}")
        header += coverage_note(_book)

    lines = [header, ""]

    for p in positions:
        pair = p.get("pair", "N/A").replace("/", "")
        direction = p.get("direction", "LONG")
        d_icon = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
        entry = p.get("entry", 0)
        pnl = p.get("pnl_pct")
        _unread = pnl is None or p.get("price_unavailable")
        current = p.get("current")
        # None is now reachable: an orphan whose venue response omitted
        # initialMargin has no size to state. `.get(k, 0)` printed "$0" for it,
        # and the f-string below crashes on None — a blank is the honest
        # rendering and neither of the other two is.
        size_usd = p.get("size_usd")
        size_str = "—" if size_usd is None else f"${size_usd:.0f}"
        if _unread:
            # No mark, so no P&L, no direction-of-travel, and NO COLOUR. A
            # green stripe beside an unknown says "in profit" as loudly as
            # a number would.
            pnl_icon = "\u26aa"
            pnl_usd_val = None
        else:
            pnl_icon = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < 0 else ""
            pnl_usd_val = p.get("pnl_usd")
            if pnl_usd_val is None:
                pnl_usd_val = (size_usd * pnl / 100
                               if size_usd is not None else None)
        leverage = p.get("leverage")
        rr_live = p.get("rr_live")
        sl = p.get("sl")
        tp = p.get("tp")
        sl_dist = p.get("sl_dist_pct")
        tp_dist = p.get("tp_dist_pct")
        sl_order = p.get("sl_order")
        hold_h = p.get("hold_hours")

        # Hold time. An orphan the venue gave no timestamp for has an age
        # nobody knows; `0` rendered that as "0m", i.e. just opened.
        if hold_h is None:
            hold_str = "?"
        elif hold_h < 1:
            hold_str = f"{hold_h * 60:.0f}m"
        elif hold_h < 24:
            hold_str = f"{hold_h:.1f}h"
        else:
            hold_str = f"{hold_h / 24:.1f}d"

        lev_str = f" | {leverage:.0f}x" if leverage and leverage > 1 else ""
        rr_str = f" | R:R {rr_live:.1f}" if rr_live else ""
        sl_tag = f" {t('lbl_on_exchange', lang)}" if sl_order == "exchange" else ""
        # THREE STATES, NOT TWO. "None" is a finding — this position has no
        # protective order. It was also what an UNREADABLE order book produced,
        # because one failed `fetch_open_orders` left every symbol at 0 and
        # every row therefore said the position was unprotected. On a list of
        # positions the bot did not open, that is the line an operator acts on.
        _none = f"<i>{t('val_none', lang)}</i>"
        _unknown = "<i>unknown</i>"

        def _level(price, order_state):
            if order_state == "unknown":
                return _unknown
            return _fmt_price(price) if price and price > 0 else _none

        sl_str = _level(sl, sl_order)
        tp_str = _level(tp, p.get("tp_order"))
        untracked = p.get("untracked", False)
        strategy_type = p.get("strategy_type", "").upper()
        st_tag = f" [{strategy_type}]" if strategy_type else ""

        if _unread:
            _pnl_cell = f"{pnl_icon} {t('pnl_unknown', lang)}"
            _mark_cell = f"<i>{t('price_unread', lang)}</i>"
        else:
            _pnl_cell = f"{pnl_icon} {_pct(pnl)} (${pnl_usd_val:+,.2f})"
            _mark_cell = _fmt_price(current)
        lines.extend([
            f"{d_icon} <b>{pair}</b> {direction}{st_tag} | {_pnl_cell}",
            f"  {_fmt_price(entry)} -> {_mark_cell} | {size_str}{lev_str}{rr_str} | {hold_str}",
            f"  {t('lbl_sl', lang)} {sl_str} / {t('lbl_tp', lang)} {tp_str}{sl_tag}",
        ])
        if untracked:
            lines.append(f"  \u26a0\ufe0f <i>{t('untracked_outside', lang)}</i>")
        if p.get("origin") == "adopted":
            # WHICH ladder rung supplied the levels. "default" is the row the
            # operator actually needs to act on \u2014 3%/6% safety stops are a
            # floor, not a strategy \u2014 so it carries the warning glyph while
            # the other sources stay informational.
            _src = p.get("sl_tp_source") or ""
            if _src == "default":
                lines.append(f"  \u26a0\ufe0f <i>{t('adopted_levels_default', lang)}</i>")
            elif _src == "inherited":
                lines.append(f"  \u2691 <i>{t('adopted_levels_inherited', lang)}</i>")
            elif _src == "exchange":
                lines.append(f"  \u2691 <i>{t('adopted_levels_exchange', lang)}</i>")
            else:
                lines.append(f"  \u2691 <i>{t('adopted_position', lang)}</i>")
        lines.append("")

    return "\n".join(lines)


# ── Status card ──────────────────────────────────────────────────

def tick_error_line(rec: Optional[dict], lang: str = "en") -> str:
    """What the last failed tick actually raised, or nothing at all.

    The warning-rate breaker alert says new entries are suppressed, names the
    trigger key, and then asserted "Usually transient (exchange API / WS)".
    The engine audits the real exception one line above where it feeds that
    breaker; the alert simply did not carry it, and the /status it points the
    operator at showed no tick failures either.

    OMITTED when there is no record — a tick that has not failed has nothing
    to report, and an empty verdict here would read as a measured all-clear.
    When the counter says a tick DID fail and no detail was stored, that is a
    third outcome and it says so rather than staying quiet.
    """
    if not isinstance(rec, dict):
        return ""
    etype = rec.get("type")
    if not etype:
        return t('fmt_tick_error_unknown', lang)
    try:
        count = int(rec.get("consecutive") or 0)
    except (TypeError, ValueError):
        count = 0
    line = t('fmt_tick_error', lang).format(
        etype=_html_escape(str(etype)), count=max(count, 1))
    phase = rec.get("last_phase_timeout")
    if phase:
        line += t('fmt_tick_error_phase', lang).format(
            phase=_html_escape(str(phase)))
    return line


def analyze_budget_line(capacity: Optional[dict], lang: str = "en") -> str:
    """The measured reason the analyze phase cannot finish, and the fix.

    "Phase analyze exceeded its 300s cap" says a phase died. It does not say
    the universe is simply wider than the budget, which is the one thing the
    operator can actually change. This says it with numbers the engine
    measured — effective wall-clock throughput from the previous batch, so it
    already carries the concurrency in force.

    OMITTED, not guessed, when there is no shortfall to report: no forecast
    on file (no batch has completed, so no rate has been measured), or the
    work fits. Neither is a claim that it fits — the phase-timeout line
    beside this one says independently whether a phase died. Inventing a
    forecast from a guessed rate is what this whole instrument exists to
    avoid, and a fabricated remedy is worse than none: it sends someone to
    change a setting that was not the problem.

    Extracted from _cmd_status, where it was inline and therefore reusable by
    nothing — the degraded ALERT, which is what actually wakes an operator,
    named the dying phase while the remedy stayed on a screen they had to go
    and open.
    """
    if not isinstance(capacity, dict):
        return ""
    try:
        if int(capacity.get("shortfall") or 0) <= 0:
            return ""
        # A rate measured on a batch that was itself cancelled omits the
        # analyses still running at the cap — the slow ones — so the shortfall
        # it yields is a FLOOR. Saying "4 will not be analysed" from such a
        # rate, on a tick that managed 20 of 40, is the defect this instrument
        # exists to prevent, one level up: an honest number wrapped in a
        # sentence that overstates what it knows.
        if capacity.get("partial"):
            return t('fmt_analyze_budget_short_floor', lang).format(
                of=int(capacity["of"]),
                per=float(capacity["per_signal_s"]),
                cap=float(capacity["cap_s"]),
                short=int(capacity["shortfall"]),
                measured_from=int(capacity["measured_from"]),
                measured_of=int(capacity["measured_of"]),
            )
        return t('fmt_analyze_budget_short', lang).format(
            of=int(capacity["of"]),
            per=float(capacity["per_signal_s"]),
            needed=float(capacity["needed_s"]),
            cap=float(capacity["cap_s"]),
            fits=int(capacity["fits"]),
            short=int(capacity["shortfall"]),
        )
    except (KeyError, TypeError, ValueError):
        # A malformed forecast is not a measurement either. Say nothing
        # rather than render half a sentence with a stray number in it.
        return ""


def monitor_checks_line(failures: Optional[dict], lang: str = "en") -> str:
    """Which proactive-monitor checks are down right now, if any.

    The monitor isolates each of its checks and counts the ones that raise
    (ProactiveMonitor.check_failures()). Without this line the operator reads
    /status on a bot whose circuit-breaker alert cannot fire and sees nothing
    unusual -- a silenced alert and a quiet market read the same. OMITTED when
    nothing is down or the record is not a dict; it reports what the monitor
    counted and diagnoses nothing.
    """
    if not isinstance(failures, dict) or not failures:
        return ""
    names = ", ".join(sorted(str(k) for k in failures))
    return t("fmt_monitor_checks_down", lang).format(n=len(failures), names=names)


def session_skip_line(dropped: Optional[dict], lang: str = "en") -> str:
    """What the sweep left out on purpose, and why.

    The scanner drops a session-gated class (stock perps, ETFs) when the
    market its prices reference is shut, and records the count on itself as
    `_session_dropped`. Without this line the operator sees a smaller universe
    -- "4 of 60" -- and nothing saying it shrank by design; a quieter market
    and a closed one read the same. This says which classes and how many.

    OMITTED when there is nothing to say: no record, an empty one, or counts
    that are not numbers. It never claims a market is closed on its own -- the
    scanner's clock decided that, and this only reports what it did.
    """
    if not isinstance(dropped, dict) or not dropped:
        return ""
    parts = []
    for cls, n in dropped.items():
        try:
            count = int(n)
        except (TypeError, ValueError):
            continue
        if count > 0:
            parts.append(f"{cls} \u00d7{count}")
    if not parts:
        return ""
    return t("fmt_session_skipped", lang).format(classes=", ".join(parts))


def position_watch_line(watch: Optional[dict], lang: str = "en",
                        verbose: bool = False) -> str:
    """Say whether the SL/TP monitor actually ran on the last tick.

    The degraded alert tells the operator that open positions "could be
    unmonitored" and points them at /status and /positions. The process knows
    which it is — `_backstop_position_monitor` audits RAN / INCOMPLETE — and
    that line went to a log the operator may have no way to reach. This is the
    same fix the phase-cause carry already made one level up: a diagnosis that
    does not reach the operator is not a diagnosis.

    FOUR renderings, not two:

      tick        watched on the normal path. The only one that may be
                  omitted, and only when `verbose` is off, because /status is
                  already long and every ABNORMAL state still renders.
      backstop    watched, but by the back-stop — the tick is failing. Never
                  omitted: "your stops are fine AND your loop is broken" is
                  two facts and the second one is actionable.
      incomplete  the back-stop ran and did not finish.
      error       the back-stop raised.
      (none)      nothing recorded. Rendered EXPLICITLY as unknown, never
                  omitted and never coloured green — this is the surface the
                  alert sends people to, and silence there reads as "fine".

    An outcome string this function does not recognise renders as unknown for
    the same reason: a new verdict added upstream must not arrive here as an
    all-clear by default.

    Colour is a claim: red only for stops that were genuinely not watched,
    a muted circle for unknown.
    """
    if not isinstance(watch, dict):
        return f"\u26aa {t('lbl_sltp_monitor', lang)}: {t('val_sltp_unknown', lang)}"
    outcome = watch.get("outcome")

    # Age is a clause, not a number the line depends on. `age_s` is None when
    # the timestamp could not be read; omit it rather than printing "0s ago",
    # which is the most reassuring answer there is and would be invented.
    age = watch.get("age_s")
    age_txt = ""
    if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 0:
        age_txt = f", {float(age):.0f}s {t('val_ago', lang)}"

    # Consecutive ticks that ended with the stops unwatched. One is a blip;
    # a run of them is the incident the back-stop's docstring warns about.
    streak = watch.get("unwatched_streak")
    streak_txt = ""
    if isinstance(streak, int) and not isinstance(streak, bool) and streak > 1:
        streak_txt = f"\u00d7{streak} {t('val_ticks', lang)}, "

    if outcome == "tick":
        if not verbose:
            return ""
        return (f"\u2705 {t('lbl_sltp_monitor', lang)}: "
                f"{t('val_sltp_ran', lang)}"
                + (f" ({age_txt[2:]})" if age_txt else ""))
    if outcome == "backstop":
        return (f"\u26a0\ufe0f {t('lbl_sltp_monitor', lang)}: "
                f"{t('val_sltp_backstop', lang)}"
                + (f" ({age_txt[2:]})" if age_txt else ""))
    if outcome in ("incomplete", "error"):
        body = (t('val_sltp_unwatched', lang) if outcome == "incomplete"
                else t('val_sltp_backstop_failed', lang))
        detail = f"{streak_txt}{age_txt[2:] if age_txt else ''}".strip().rstrip(",")
        return (f"\U0001f534 <b>{t('lbl_sltp_monitor', lang)}: {body}</b>"
                + (f" ({detail})" if detail else ""))
    return f"\u26aa {t('lbl_sltp_monitor', lang)}: {t('val_sltp_unknown', lang)}"


def _gave_up_note(progress: Optional[dict], lang: str = "en") -> str:
    """" -- 4 of them gave up at the per-symbol cap", or nothing.

    `done` counts ATTEMPTS: the batch's `finally` increments it for a symbol
    that timed out as readily as for one that finished, so "85/85" on a batch
    where four hit the 90s cap is true of attempts and false of analyses --
    and "analysed" is what the label used to say. The label now says
    "attempted", and this adds the give-ups when the record carries them.
    Omitted when it does not: an older record has no `gave_up`, and absent is
    not zero.
    """
    if not isinstance(progress, dict):
        return ""
    try:
        n = int(progress.get("gave_up") or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f" \u2014 {n} {t('val_gave_up', lang)}"


def render_status_card(
    mode: str,
    active: bool,
    equity: Optional[float],
    open_positions: int,
    daily_pnl: Optional[float],
    drawdown: float,
    max_drawdown: float,
    market_bias: str,
    pending_ideas: int = 0,
    lang: str = "en",
    tick_age_s: Optional[float] = None,
    tick_stalled: bool = False,
    next_tick_in_s: Optional[float] = None,
    phase_timeout: Optional[dict] = None,
    phase_headroom: Optional[dict] = None,
    position_watch: Optional[dict] = None,
    tick_error: Optional[dict] = None,
) -> str:
    """Render a compact status dashboard. Returns Telegram HTML (CJK-safe)."""
    # Whether the SL/TP monitor actually ran. Sits with the phase timeout
    # because they are cause and consequence: analyze blowing its cap is what
    # unwinds the tick before its position check, and the degraded alert names
    # the first while leaving the second as "could be". Empty on the healthy
    # path; every other state, including "no reading", renders.
    _watch_line = position_watch_line(position_watch, lang)
    status = f"\U0001f7e2 {t('val_active', lang)}" if active else f"\U0001f534 {t('val_halted', lang)}"
    # `"LIVE" if ... else "PAPER"` was two-valued, and the mode is not.
    # `live_readiness.mode_label()` also answers IDLE (SIMULATION_MODE off but
    # live never armed — a REAL account placing nothing) and UNKNOWN (the
    # config could not be read). Both used to land on the else branch and print
    # a yellow 🟡 PAPER badge: colour is a claim, and telling the operator of a
    # real-money account that it is on paper is the reassuring direction of a
    # wrong one. `val_idle`/`val_unknown` are deliberately NOT looked up — t()
    # returns the key itself on a miss, so an untranslated key would render as
    # the literal string "val_idle" in all fourteen locales.
    if mode == "LIVE":
        mode_label = f"\U0001f534 {t('val_live', lang)}"
    elif mode == "PAPER":
        mode_label = f"\U0001f7e1 {t('val_paper', lang)}"
    elif mode == "IDLE":
        mode_label = "⚪ IDLE (not armed)"
    else:
        mode_label = "⚪ UNKNOWN"
    # Three outcomes, the same shape this card already uses for `equity`.
    # `daily_pnl` is None when today's closes exist but none could be priced —
    # "⚪ 0.0%" beside a "/ +5.0% limit" reads as a measured flat day, which is
    # the one thing it is not. A genuinely empty day is still a real 0.0.
    _dp_known = isinstance(daily_pnl, (int, float)) and not isinstance(daily_pnl, bool)
    pnl_icon = ("\u26a0\ufe0f" if not _dp_known
                else "\U0001f7e2\u25b2" if daily_pnl > 0
                else "\U0001f534\u25bc" if daily_pnl < 0 else "\u26aa")
    _dp_str = _pct(daily_pnl) if _dp_known else t("pnl_unknown", lang)

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        f"\U0001f43e <b>{t('status_title', lang)}</b> \u2014 {now}",
        "",
        f"{status} | {mode_label} | Bitget",
        "",
        SEP,
        "",
        f"<b>{t('hdr_engine', lang)}</b>",
        f"- {t('lbl_state', lang)}: {t('val_state_active', lang) if active else t('val_state_halted', lang)}",
        f"- {t('lbl_mode', lang)}: {mode}",
        f"- {t('lbl_market_bias', lang)}: {market_bias}",
        f"- {t('lbl_pending_ideas', lang)}: {pending_ideas}",
        # Loop liveness. During a parked tick the FSM state and the cached
        # equity FREEZE — so "Active" is not evidence the loop is running,
        # and an operator debugging a hang (2026-07-28) had no way to tell
        # from /status at all. This line is the one number that answers it.
        #
        # `tick_stalled` is the CALLER's verdict, taken from the SAME
        # ProactiveMonitor._is_tick_stalled predicate the watchdog pages on —
        # this card must never reach a different conclusion than the alert.
        # It had its own 120s rule once, which called a healthy engine
        # "stalled" during any inter-tick gap over two minutes: the smart-scan
        # quiet-market sleep alone reaches 600s, and the run loop's own failure
        # backoff is capped at 300s. Both are DECLARED waits the engine stamps
        # in _next_tick_due_ts, and both read as a stall under a bare age
        # threshold. A liveness line that cries stall while the engine is
        # deliberately waiting is worse than no line — it spends the operator's
        # trust in the one number meant to answer "is the loop alive?".
        *([] if tick_age_s is None else [
            f"- {t('lbl_last_tick', lang)}: "
            + (f"\u26a0 {tick_age_s / 60:.0f}m {t('val_ago', lang)} \u2014 "
               + t('val_loop_stalled', lang) if tick_stalled
               else f"{tick_age_s:.0f}s {t('val_ago', lang)}"
                    + (f" ({t('val_next_tick_in', lang)} {next_tick_in_s:.0f}s)"
                       if next_tick_in_s is not None and next_tick_in_s > 0
                       else ""))]),
        # The named cause of a failing tick. /status is where the degraded
        # alert SENDS the operator, so the answer has to be here — otherwise
        # the alert says "check /status" and /status repeats the symptom.
        *([] if not (phase_timeout or {}).get("phase") else [
            f"- {t('lbl_last_phase_timeout', lang)}: "
            f"<b>{phase_timeout['phase']}</b> "
            f"({t('val_exceeded_cap', lang)} "
            f"{float(phase_timeout.get('cap_s') or 0):.0f}s"
            + (f", \u00d7{int(phase_timeout['count'])}"
               if int(phase_timeout.get('count') or 0) > 1 else "") + ")"
            # How far it got. "Exceeded its 300s" says the phase died; this
            # says whether it was nearly done or barely started, which is the
            # difference between "the budget is too small" and "something is
            # stuck". Omitted when unknown — never guessed.
            + (f"\n  \u21b3 {int((phase_timeout.get('progress') or {}).get('done') or 0)}"
               f"/{int((phase_timeout['progress'])['of'])} "
               f"{t('val_signals_done', lang)}"
               + _gave_up_note((phase_timeout or {}).get('progress'), lang)
               if (phase_timeout.get('progress') or {}).get('of') else "")]),
        # WHAT the failing tick raised. A phase timeout is one cause of a tick
        # failure and not the only one, and the warning-rate breaker that
        # suppresses new entries counts them all under `engine_tick_failure`.
        # Its alert points here; without this line /status could not answer,
        # so the alert guessed "exchange API / WS" instead. Omitted on a tick
        # that has not failed.
        *([] if not tick_error_line(tick_error, lang) else [
            f"- {tick_error_line(tick_error, lang)}"]),
        # The HEADROOM, not just the breach. Recording only the breach made
        # every phase a cliff: 299s of a 300s cap looked identical to 30s,
        # and the first signal was a dead tick. Shown only once a phase has
        # actually completed \u2014 an unmeasured margin is not a margin of 100%.
        *([] if not (phase_headroom or {}).get("cap_s") else [
            f"- {t('lbl_phase_headroom', lang)}: "
            + ("\u26a0 " if float(phase_headroom['used_ratio']) >= 0.8 else "")
            + f"<b>{phase_headroom['phase']}</b> "
            # A CANCELLED phase ran for at least the cap; the true figure was
            # never observed. "≥" says so rather than presenting a floor
            # as a reading.
            + ("≥" if phase_headroom.get("timed_out") else "")
            + f"{float(phase_headroom['peak_s']):.0f}s "
            f"{t('val_peak_of', lang)} "
            f"{float(phase_headroom['cap_s']):.0f}s "
            f"({float(phase_headroom['used_ratio']) * 100:.0f}%"
            + (f" — {t('val_cap_hit', lang)}"
               if phase_headroom.get("timed_out") else "") + ")"]),
        *([] if not _watch_line else [f"- {_watch_line}"]),
        "",
        f"<b>{t('hdr_capital', lang)}</b>",
        # equity is None only in LIVE mode when the balance is unreadable —
        # say so, never fall back to the paper baseline.
        f"- {t('lbl_equity', lang)}: "
        f"{_fmt_price(equity) if equity is not None else 'unavailable'}",
        f"- {t('lbl_open_positions', lang)}: {open_positions}",
        f"- {t('lbl_daily_pnl', lang)}: {pnl_icon} {_dp_str}",
        "",
        f"<b>{t('hdr_risk', lang)}</b>",
        f"- {t('lbl_drawdown', lang)}: {_pct(drawdown)} / {_pct(max_drawdown)} {t('lbl_limit_word', lang)}",
    ]

    # Drawdown gauge
    ratio = drawdown / max_drawdown if max_drawdown > 0 else 0
    bar_len = 12
    filled = int(ratio * bar_len)
    bar = "\u2501" * filled + "\u254c" * (bar_len - filled)
    tip = "\U0001f7e2" if ratio < 0.5 else "\U0001f7e1" if ratio < 0.8 else "\U0001f534"
    lines.append(f"  {tip} \u2502{bar}\u2502")

    return "\n".join(lines)
