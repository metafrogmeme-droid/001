"""MuleRun War Room — Telegram bot interface for the RUNECLAW Signal Engine.

Rich, dashboard-grade templates using the same visual vocabulary as the
skill registry: gauges, progress bars, sparklines, and sectioned cards.

No external dependencies beyond the Python standard library are required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

__all__ = [
    "render_start",
    "render_status",
    "render_signal",
    "render_risk",
    "render_performance",
    "render_positions",
    "render_daily_report",
    "render_strategy_mode",
    "render_pause",
    "render_resume",
    "render_emergency_stop",
    "handle_callback",
]

# ── Visual vocabulary (matches skill_registry.py) ─────────────
_OK = "\U0001f7e2"        # green circle
_WARN = "\U0001f7e1"      # yellow circle
_BAD = "\U0001f534"       # red circle
_NEU = "\u26aa"           # white circle
_SHIELD = "\U0001f6e1"
_BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

PRODUCT = "MuleRun War Room"
ENGINE = "RUNECLAW"

# ── Keyboard helpers ──────────────────────────────────────────
_Btn = Dict[str, str]
_Row = List[_Btn]
_Keyboard = List[_Row]


def _btn(text: str, callback_data: str) -> _Btn:
    return {"text": text, "callback_data": callback_data}


# ── Formatting helpers ────────────────────────────────────────

def _bar(val: float, mx: float = 1.0, w: int = 10) -> str:
    r = min(max(val / mx, 0), 1.0) if mx > 0 else 0
    f = int(r * w)
    return "\u2501" * f + "\u254c" * (w - f)


def _gauge(label: str, val: float, mx: float, unit: str = "%", w: int = 12) -> str:
    bar = _bar(val, mx, w)
    r = val / mx if mx > 0 else 0
    tip = _OK if r < 0.5 else _WARN if r < 0.8 else _BAD
    if unit == "%":
        return f"  {tip} {label:<10} \u2502{bar}\u2502 {val:.1f}%\u2009/\u2009{mx:.0f}%"
    return f"  {tip} {label:<10} \u2502{bar}\u2502 {val:.0f}\u2009/\u2009{mx:.0f}"


def _kv(key: str, val: str, w: int = 28) -> str:
    dots = w - len(key) - len(val) - 4
    if dots < 2:
        dots = 2
    return f"  {key} {'·' * dots} {val}"


def _header(emoji: str, title: str, w: int = 24) -> str:
    return f"{emoji} <b>{title}</b> {'━' * w}"


def _pill(text: str) -> str:
    return f"<code>\u2009{text}\u2009</code>"


# An em dash for a figure nobody measured. Guarded at the BOUNDARY, the way
# `_fmt_price(None)` is, so a new caller inherits the honest behaviour instead
# of each call site having to remember the check.
#
# RC-2026-009/010: these three raised TypeError on None, which meant a caller
# that honestly reported "not measured" took the whole card down through the
# nearest `except` -- so the honest value was the one that looked like a bug,
# and passing a confident 0.0 was the way to keep the card alive.
_DASH = "\u2014"


def _money(v: Optional[float], sign: bool = False) -> str:
    if v is None:
        return _DASH
    return f"${v:+,.2f}" if sign else f"${v:,.2f}"


def _spark(v: Optional[float]) -> str:
    if v is None:
        return _DASH
    if v > 2: return "\u25b2"
    if v > 0: return "\u25b3"
    if v < -2: return "\u25bc"
    if v < 0: return "\u25bd"
    return "\u25c7"


def _pnl_arrow(v: Optional[float]) -> str:
    # Deliberately NOT the flat glyph: `\u25c7` is what a MEASURED break-even
    # gets, and "flat" and "unknown" must not share a symbol.
    if v is None:
        return f"{_NEU}{_DASH}"
    if v > 0: return f"{_OK}\u25b2"
    if v < 0: return f"{_BAD}\u25bc"
    return f"{_NEU}\u25c7"


def _progress_ring(pct: float) -> str:
    rings = ["\u25cb", "\u25d4", "\u25d1", "\u25d5", "\u25cf"]
    idx = int(min(max(pct, 0), 100) / 25)
    return rings[min(idx, 4)]


def _sparkline(values: list[float], w: int = 12) -> str:
    if not values:
        return "\u2500" * w
    if len(values) > w:
        step = len(values) / w
        sampled = [values[int(i * step)] for i in range(w)]
    else:
        sampled = values
    mn, mx = min(sampled), max(sampled)
    rng = mx - mn if mx > mn else 1.0
    return "".join(_BLOCKS[max(0, min(7, int((v - mn) / rng * 7)))] for v in sampled)


def _conf_bar(pct: int, w: int = 10) -> str:
    fill = round(pct / 100 * w)
    return _BLOCKS[7] * fill + _BLOCKS[0] * (w - fill)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ═════════════════════════════════════════════════════════════════
# render_start
# ═════════════════════════════════════════════════════════════════

def render_start() -> Dict[str, Any]:
    text = (
        f"{_header(chr(0x2694) + chr(0xFE0F), 'MULERUN WAR ROOM')}\n\n"
        f"  Powered by <b>{ENGINE}</b> Signal Engine\n\n"
        "<pre>"
        f"{_kv('Status', 'ACTIVE ' + _OK)}\n"
        f"{_kv('Engine', 'v3.1')}\n"
        f"{_kv('Uptime', '99.7%')}"
        "</pre>\n\n"
        "  Signal locked. Risk checked. Claw ready.\n\n"
        "<i>\u25b8 Use the buttons below or /help for commands</i>"
    )
    keyboard: _Keyboard = [
        [_btn("\u2694\ufe0f Open War Room", "open_warroom"),
         _btn("\U0001f4ca Latest Signal", "latest_signal")],
        [_btn("\U0001f4c8 Performance", "performance"),
         _btn("\U0001f6e1 Risk Control", "risk_control")],
        [_btn("\u2699\ufe0f Strategy Mode", "strategy_mode"),
         _btn("\U0001f4c2 Positions", "positions")],
        [_btn("\u26d4 Emergency Stop", "risk_emergency_stop")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_status
# ═════════════════════════════════════════════════════════════════

def render_status(data: Dict[str, Any]) -> Dict[str, Any]:
    active = data.get("active", True)
    cb_s = f"{_OK} ACTIVE" if active else f"{_BAD} HALTED"
    mode = data.get("mode", "PAPER")
    pnl = data.get("daily_pnl", 0.0)
    risk = data.get("risk_used", 0.0)
    open_t = data.get("open_trades", 0)
    bias = data.get("market_bias", "Normal")
    last_sig = data.get("last_signal", "Use /scan")

    # Health score: based on drawdown headroom
    health = max(0, 100 - risk * 10)
    health_ring = _progress_ring(health)

    text = (
        f"\U0001f43e <b>{ENGINE} STATUS</b> {'━' * 18}\n\n"
        f"  {cb_s}  \u2502  {mode}  \u2502  {health_ring} Health {_pill(f'{health:.0f}%')}\n\n"
        # ── Engine card ──
        f"\u2699\ufe0f <b>Engine</b>\n"
        "<pre>"
        f"{_kv('State', 'ACTIVE' if active else 'HALTED')}\n"
        f"{_kv('Mode', mode)}\n"
        f"{_kv('Exchange', data.get('exchange', 'Bitget'))}\n"
        f"{_kv('Market Bias', bias)}"
        "</pre>\n\n"
        # ── Capital card ──
        f"\U0001f4b0 <b>Capital</b>\n"
        "<pre>"
        f"{_kv('Open Trades', str(open_t))}\n"
        f"{_kv('Daily PnL', f'{pnl:+.2f}%')}  {_pnl_arrow(pnl)}\n"
        f"{_kv('Last Signal', last_sig)}"
        "</pre>\n\n"
        # ── Risk gauge ──
        f"{_SHIELD} <b>Risk</b>\n"
        f"{_gauge('Drawdown', risk, 10.0)}\n\n"
        f"<i>\u23f1 {_timestamp()}</i>"
    )
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_signal
# ═════════════════════════════════════════════════════════════════

def render_signal(data: Dict[str, Any]) -> Dict[str, Any]:
    pair = data.get("pair", "N/A")
    direction = data.get("direction", "LONG")
    d_icon = _OK if direction.upper() == "LONG" else _BAD
    d_arrow = "\u25b2" if direction.upper() == "LONG" else "\u25bc"
    confidence = data.get("confidence", 0)
    risk_level = data.get("risk_level", "Medium")
    risk_icon = _OK if risk_level == "Low" else _WARN if risk_level == "Medium" else _BAD
    entry_low = data.get("entry_low", 0)
    entry_high = data.get("entry_high", 0)
    sl = data.get("sl", 0)
    tp1 = data.get("tp1", 0)
    tp2 = data.get("tp2", 0)
    reason = data.get("reason", "")

    # Dynamic precision based on price magnitude
    ref = max(entry_low, entry_high, tp1, sl, 0.001)
    if ref >= 100:
        p = 2
    elif ref >= 1:
        p = 4
    else:
        p = 5

    # Confidence bar
    conf_ring = _progress_ring(confidence)
    cbar = _conf_bar(confidence)

    text = (
        f"{_header(d_icon, f'{direction}  {pair}')}\n\n"
        f"  {conf_ring} Confidence \u2502{cbar}\u2502 {_pill(f'{confidence}%')}\n"
        f"  {risk_icon} Risk Level: <b>{risk_level}</b>\n\n"
        # ── Price ladder ──
        f"\U0001f3af <b>Price Levels</b>\n"
        "<pre>"
        f"  \U0001f3af TP2   \u2502 $  {tp2:>10.{p}f}\n"
        f"  \U0001f3af TP1   \u2502 $  {tp1:>10.{p}f}\n"
        f"  {'─' * 6}\u253c{'─' * 20}\n"
        f"  {d_arrow}  IN   \u2502 $  {entry_low:.{p}f} \u2013 ${entry_high:.{p}f}\n"
        f"  {'─' * 6}\u253c{'─' * 20}\n"
        f"  \U0001f6d1 SL    \u2502 $  {sl:>10.{p}f}"
        "</pre>\n\n"
        f"<blockquote>{reason[:200]}</blockquote>"
    )
    keyboard: _Keyboard = [
        [_btn("\u2705 Approve Trade", f"signal_approve_{pair}")],
        [_btn("\U0001f441 Watch Only", f"signal_watch_{pair}")],
        [_btn("\u274c Reject", f"signal_reject_{pair}")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_risk
# ═════════════════════════════════════════════════════════════════

def render_risk(data: Dict[str, Any]) -> Dict[str, Any]:
    # `.get("current_drawdown", 0.0)` scored an ABSENT reading as a measured
    # 0% drawdown — `healthy = 0.0 < ddl` — so this card printed
    # "HEALTHY · Health 100%" from a number nobody could read. That is the
    # exact contradiction the two comments below record, reached through a
    # different door: they were about a high-water mark erased by a restart;
    # this is about the reading never arriving at all.
    dd = data.get("current_drawdown")
    dd_known = isinstance(dd, (int, float)) and not isinstance(dd, bool)
    dll = data.get("daily_loss_limit", 5.0)
    # The DRAWDOWN cap, which is a different control from the daily-loss cap.
    # This whole card used to measure drawdown against `dll`: the verdict, the
    # health score and the drawdown gauge were all computed by comparing a
    # drawdown reading to the DAILY-LOSS limit. So /risk could report HEALTHY
    # with the drawdown breaker about to trip, or WARNING for a drawdown
    # nowhere near its actual cap. Falls back to dll only if the caller
    # supplies nothing, so an old caller degrades to the previous behaviour
    # rather than dividing by zero.
    ddl = data.get("drawdown_limit") or dll
    max_t = data.get("max_open_trades", 5)
    open_t = data.get("open_trades", 0)
    lev = data.get("leverage_cap", 5)

    # A card that scores RISK must know whether trading is BLOCKED.
    # This computed `healthy` from drawdown alone, so on 2026-07-30 at 17:40
    # the engine was HALTED on a drawdown breaker while /status said so — and
    # this card would have printed "Status: HEALTHY, Health 100%", because the
    # restart had erased the high-water mark and left dd reading 0.0%.
    #
    # #990 widened the PNG stats-card tile beside this one and did not widen
    # this renderer, which is the text fallback the operator gets whenever the
    # image fails to send. Half a surface fixed is a surface that disagrees
    # with itself.
    #
    # Absent key => unchanged behaviour for any caller that does not supply it.
    blocked = str(data.get("trading_blocked_by") or "")
    # THREE OUTCOMES, because "could not read it" is not one of the other two.
    # HEALTHY asserts the drawdown is inside its cap; WARNING asserts it is
    # not. Neither is available without the number, and defaulting to the
    # first is how an unreadable gauge comes out as an all-clear.
    healthy = dd_known and dd < ddl and not blocked
    if blocked:
        status_icon, status_label = _BAD, f"BLOCKED ({blocked})"
    elif not dd_known:
        status_icon, status_label = "⚠️", "UNKNOWN (drawdown unreadable)"
    else:
        status_icon = _OK if healthy else _BAD
        status_label = "HEALTHY" if healthy else "WARNING"

    # Risk health score — against the cap that actually governs drawdown.
    # None, not 100. The score measures how much headroom is left, and there
    # is none to compute without a drawdown — while 100% is the single most
    # reassuring value on the card.
    if not dd_known:
        risk_score = None
    else:
        risk_score = max(0, 100 - int(dd / ddl * 100)) if ddl > 0 else 100
    # A blocked engine cannot score 100% "risk health" — that is the number
    # the operator reads to decide whether anything needs attention, and a
    # green 100 beside a halted engine is the contradiction this card exists
    # to avoid.
    if blocked:
        risk_score = 0
    health_bar = _bar(risk_score, 100, 14) if risk_score is not None else "┄" * 14
    score_txt = "--" if risk_score is None else f"{risk_score}%"
    dd_gauge = (_gauge("Drawdown", dd, ddl) if dd_known
                else "  Drawdown │" + "┄" * 14 + "│ -- (unreadable)")

    text = (
        f"{_header(_SHIELD, 'RISK CONTROL')}\n\n"
        f"  {status_icon} Status: <b>{status_label}</b>\n"
        f"  \u25cf Health \u2502{health_bar}\u2502 {_pill(score_txt)}\n\n"
        # ── Gauges ──
        f"{dd_gauge}\n"
        f"{_gauge('Positions', float(open_t), float(max_t), unit='#')}\n"
        f"{_gauge('Leverage', 1.0, float(lev), unit='x')}\n\n"
        # ── Limits ──
        f"\U0001f512 <b>Limits</b>\n"
        "<pre>"
        f"{_kv('Daily Loss', f'{dll}%')}\n"
        f"{_kv('Drawdown', f'{ddl}%')}\n"
        f"{_kv('Max Trades', str(max_t))}\n"
        f"{_kv('Open Now', str(open_t))}\n"
        f"{_kv('Leverage', f'{lev}x')}"
        "</pre>"
    )
    keyboard: _Keyboard = [
        [_btn("\U0001f6e1 Safe Mode", "risk_safe_mode"),
         _btn("\u23f8 Pause Bot", "risk_pause")],
        [_btn("\u26d4 Emergency Stop", "risk_emergency_stop")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_performance
# ═════════════════════════════════════════════════════════════════

def render_performance(data: Dict[str, Any]) -> Dict[str, Any]:
    today = data.get("today_pnl", 0.0)
    week = data.get("week_pnl", 0.0)
    total = data.get("total_pnl", week)
    # `.get("win_rate", 0.0)` — an absent rate rendered as 0%, an empty ring
    # and an empty bar: the picture of total defeat, drawn from no data. The
    # caller ALREADY sends win_rate_scored / win_rate_unscored (the PNG tile
    # beside this card uses them to label itself "Win Rate (of N)"); this
    # renderer ignored both and took the reassuring default instead.
    wr = data.get("win_rate")
    trades = data.get("trades_today", 0)
    total_trades = data.get("total_trades", trades)
    _wr_scored = data.get("win_rate_scored")
    _wr_unscored = data.get("win_rate_unscored") or 0
    # `.get(k, "N/A")` only defaults when the KEY IS ABSENT: a caller that
    # honestly sends best_pair=None got None straight through, and the
    # concatenation below raised. `or` covers both the missing key and the
    # explicit "not measured".
    best = data.get("best_pair") or _DASH
    worst = data.get("worst_pair") or _DASH
    adopted_count = data.get("adopted_count", 0)
    adopted_pnl = data.get("adopted_pnl", 0.0)

    # The gauge is drawn at zero when the rate is unknown, but it is LABELLED
    # n/a — an empty ring next to "n/a" reads as "no reading", while an empty
    # ring next to "0%" reads as "measured, and terrible".
    _wr_known = isinstance(wr, (int, float)) and not isinstance(wr, bool)
    wr_bar = _bar(wr if _wr_known else 0.0, 100.0, 10)
    wr_ring = _progress_ring(wr if _wr_known else 0.0)
    wr_str = f"{wr:.0f}%" if _wr_known else "n/a"
    wr_note = ("" if not _wr_unscored else
               f"\n  <i>Rate covers {_wr_scored if _wr_scored is not None else '?'}"
               f" of {total_trades} closes — {_wr_unscored} carry no recorded "
               f"P&amp;L and are scored neither way.</i>")

    # Interpolated from `today` alone -- it is a shape, not a series, which is
    # why it is labelled fake. Three cases, not two: `today != 0` was True for
    # None as well, so an unmeasured day reached the arithmetic and raised.
    # The dashed line is deliberately NOT the solid one: solid is a measured
    # flat day, dashed is no reading at all.
    if today is None:
        pnl_trend = "┄┄┄┄┄┄┄┄"
    elif today != 0:
        pnl_trend = _sparkline([0, today * 0.3, today * 0.5, today * 0.8, today], w=8)
    else:
        pnl_trend = "━━━━━━━━"

    text = (
        f"{_header(chr(0x1F4CA), 'PERFORMANCE')}\n"
        f"   {_pnl_arrow(today)} {_pill(_money(today, sign=True))} today\n\n"
        # ── PnL card ──
        f"\U0001f4b0 <b>Returns</b>\n"
        "<pre>"
        f"{_kv('Today', _money(today, sign=True))}  {_pnl_arrow(today)}\n"
        f"{_kv('7-Day', _money(week, sign=True))}  {_pnl_arrow(week)}\n"
        f"{_kv('All-time', _money(total, sign=True))}  {_pnl_arrow(total)}\n"
        f"{_kv('Trades', f'{trades} today / {total_trades} total')}\n"
        f"{_kv('Trend', f'<code>{pnl_trend}</code>')}"
        "</pre>\n\n"
        # ── Win Rate gauge ──
        f"\U0001f3af <b>Win Rate</b>\n"
        f"  {wr_ring} \u2502{wr_bar}\u2502 {_pill(wr_str)}{wr_note}\n\n"
        # ── Pair breakdown ──
        f"\U0001f4ca <b>Pair Breakdown</b>\n"
        "<pre>"
        f"{_kv('Best', best + ' ' + chr(0x1F3C6))}\n"
        f"{_kv('Worst', worst)}"
        "</pre>"
    )

    # ── Adopted orphan trades (if any) ──
    if adopted_count > 0:
        text += (
            f"\n\n\u26a0\ufe0f <i>Excluded {adopted_count} adopted orphan"
            f"{'s' if adopted_count != 1 else ''}"
            f" ({_money(adopted_pnl, sign=True)})</i>"
        )

    text += f"\n\n<i>\u23f1 {_timestamp()}</i>"
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_positions
# ═════════════════════════════════════════════════════════════════

def render_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(positions)
    total_pnl = sum(p.get("pnl", 0) for p in positions)

    lines = [
        f"{_header(chr(0x1F4C8), f'OPEN POSITIONS  ({count})')}",
        f"   {_pnl_arrow(total_pnl)} Net: {_pill(f'{total_pnl:+.2f}%')}\n",
    ]
    keyboard: _Keyboard = []

    for pos in positions:
        pair = pos.get("pair", "N/A")
        direction = pos.get("direction", "LONG")
        d_icon = _OK if direction.upper() == "LONG" else _BAD
        d_arrow = "\u25b2" if direction.upper() == "LONG" else "\u25bc"
        entry = pos.get("entry", 0)
        current = pos.get("current", 0)
        pnl = pos.get("pnl", 0.0)
        sl = pos.get("sl", 0)
        tp1 = pos.get("tp1", 0)

        pnl_icon = _pnl_arrow(pnl)

        lines.append(
            f"  {d_icon}{d_arrow} <b>{pair}</b>  {direction}  "
            f"{pnl_icon} {_pill(f'{pnl:+.2f}%')}"
        )
        lines.append("<pre>")
        lines.append(f"{_kv('Entry', f'${entry:,.2f}')}")
        lines.append(f"{_kv('Current', f'${current:,.2f}')}")
        lines.append(f"{_kv('SL', f'${sl:,.2f}')}")
        lines.append(f"{_kv('TP', f'${tp1:,.2f}')}")
        lines.append("</pre>")

        keyboard.append([
            _btn(f"\U0001f4cb {pair}", f"pos_details_{pair}"),
            _btn("\u274c Close", f"pos_close_{pair}"),
        ])

    if not positions:
        lines.append(f"  {_NEU} <i>No open positions. Use /scan or /analyze.</i>")

    return {"text": "\n".join(lines), "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_daily_report
# ═════════════════════════════════════════════════════════════════

def _money_or_unread(v: Any) -> str:
    """A signed dollar figure, or ``unread`` when there is no figure."""
    return "unread" if v is None else f"${v:+.2f}"


def render_daily_report(data: Dict[str, Any]) -> Dict[str, Any]:
    trades = data.get("trades", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    # THE DEFAULTS WERE THE DEFECT, on three lines at once.
    #
    # `net_pnl` absent rendered `$+0.00` — a measured flat day where the truth
    # was that nothing could be priced. `risk_status` absent rendered
    # **Healthy**, the calmest of the verdicts, from no reading whatsoever;
    # the caller's live branch was hardcoding exactly that value, so the two
    # agreed about a thing neither had measured. And the icon expression had
    # no unknown arm, so any non-healthy/warning word — "Unknown" included —
    # painted the RED one, which is the opposite lie.
    net = data.get("net_pnl")
    best_t = data.get("best_trade", "N/A")
    best_p = data.get("best_pnl")
    worst_t = data.get("worst_trade", "N/A")
    worst_p = data.get("worst_pnl")
    risk_s = data.get("risk_status") or "Unknown"
    _rl = str(risk_s).lower()
    risk_icon = (_OK if _rl == "healthy" else _WARN if _rl == "warning"
                 else _NEU if _rl == "unknown" else _BAD)
    net_txt = _money_or_unread(net)
    dd_pct = data.get("drawdown_pct")
    risk_detail = ("" if dd_pct is None
                   else f"  <i>drawdown {dd_pct:.1f}%</i>\n")

    # A WIN RATE WHOSE NUMERATOR AND DENOMINATOR CAME FROM DIFFERENT SETS.
    #
    # `wins / trades` divided SCORED wins by ALL closes. The caller passes
    # `trades = len(closed)` and `wins = _win_stats(closed)["wins"]`, and
    # win_stats skips rows whose pnl_usd is None — which live_executor's loader
    # preserves by design. So every unpriced close silently counted against the
    # operator, and `Total 10 / Wins 4 / Losses 2` printed three numbers that
    # did not add up, directly above the gauge.
    #
    # `losses` is already the scored non-wins, so `wins + losses` IS the scored
    # count. Deriving the denominator from the two numbers printed beside the
    # gauge makes the card self-consistent by construction — it can no longer
    # show a rate over a population different from the one it just listed.
    #
    # The PUBLIC daily post got this right and this card did not, one call
    # frame apart. Its comment: "A 0% win rate is a claim that everything
    # lost." Same rule here — nothing scorable is `n/a`, not 0%, and the ring
    # and bar render empty rather than pretending to a measured zero.
    scored = wins + losses
    wr = (wins / scored * 100) if scored > 0 else None
    _unscored = max(0, trades - scored)
    wr_bar = _bar(wr if wr is not None else 0.0, 100.0, 10)
    wr_ring = _progress_ring(wr if wr is not None else 0.0)
    wr_str = "n/a" if wr is None else f"{wr:.0f}%"
    # Say the shortfall out loud rather than letting the reader assume the
    # rate covers the Total on the line above.
    wr_note = ("" if not _unscored else
               f"\n  <i>Rate covers {scored} of {trades} closes — "
               f"{_unscored} carry no recorded P&amp;L and are scored "
               f"neither way.</i>")

    text = (
        f"{_header(chr(0x1F4D3), 'DAILY REPORT')}\n"
        f"   {_pnl_arrow(net if net is not None else 0.0)} "
        f"Net PnL: {_pill(net_txt)}\n\n"
        # ── Trade summary ──
        f"\U0001f4ca <b>Trade Summary</b>\n"
        "<pre>"
        f"{_kv('Total', str(trades))}\n"
        f"{_kv('Wins', str(wins) + ' ' + _OK)}\n"
        f"{_kv('Losses', str(losses) + ' ' + _BAD)}\n"
        f"{_kv('Net PnL', net_txt)}"
        "</pre>\n\n"
        # ── Win Rate ──
        f"\U0001f3af <b>Win Rate</b>\n"
        f"  {wr_ring} \u2502{wr_bar}\u2502 {_pill(wr_str)}{wr_note}\n\n"
        # ── Highlights ──
        f"\U0001f3c6 <b>Highlights</b>\n"
        "<pre>"
        # The name is "N/A" exactly when the figure is None -- both come from
        # the same scorable-rows check -- so printing "N/A unread" would say
        # the same absence twice.
        f"{_kv('Best', best_t if best_p is None else f'{best_t} ${best_p:+.2f}')}"
        f"  {_OK}\n"
        f"{_kv('Worst', worst_t if worst_p is None else f'{worst_t} ${worst_p:+.2f}')}"
        f"  {_BAD}"
        "</pre>\n\n"
        # ── Risk ──
        f"{_SHIELD} <b>Risk Status</b>\n"
        f"  {risk_icon} <b>{risk_s}</b>\n{risk_detail}\n"
        f"<i>\u23f1 {_timestamp()}</i>"
    )
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_strategy_mode
# ═════════════════════════════════════════════════════════════════

def render_strategy_mode(current_mode: str) -> Dict[str, Any]:
    mode_icons = {
        "defensive": _SHIELD, "balanced": "\u2694\ufe0f",
        "aggressive": "\U0001f525", "manual": "\U0001f9d8",
    }
    icon = mode_icons.get(current_mode.lower(), "\u2694\ufe0f")

    text = (
        f"{_header(chr(0x2699) + chr(0xFE0F), 'STRATEGY MODE')}\n\n"
        f"  Active: {icon} <b>{current_mode.upper()}</b>\n\n"
        # ── Mode descriptions ──
        f"  {_SHIELD} <b>Defensive</b>\n"
        f"     <i>Conservative risk, fewer signals, wider SL</i>\n"
        f"  \u2694\ufe0f <b>Balanced</b>\n"
        f"     <i>Default mode, standard confluence thresholds</i>\n"
        f"  \U0001f525 <b>Aggressive</b>\n"
        f"     <i>More signals, tighter filters, higher exposure</i>\n"
        f"  \U0001f9d8 <b>Manual</b>\n"
        f"     <i>Bot analyzes, you decide. Full human control</i>\n\n"
        f"<i>\u25b8 Select a mode below to switch</i>"
    )
    keyboard: _Keyboard = [
        [_btn(f"{_SHIELD} Defensive", "mode_defensive"),
         _btn("\u2694\ufe0f Balanced", "mode_balanced")],
        [_btn("\U0001f525 Aggressive", "mode_aggressive"),
         _btn("\U0001f9d8 Manual", "mode_manual")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_pause / render_resume
# ═════════════════════════════════════════════════════════════════

def render_pause(scope: str = "shared") -> Dict[str, Any]:
    """Pause card. ``scope`` is "shared" (the operator stopped the engine, which
    stops it for everybody) or "own" (a per-user caller stopped their own).

    The parameter exists because the card asserted a scope it did not know. Its
    every line \u2014 "All trading activity suspended", "Scanning PAUSED" \u2014 is a
    claim about the WHOLE bot, and once /pause could act on one user's risk
    engine that claim became false for exactly the caller reading it. Scope is
    part of a claim, not decoration on it: a user who pauses their own account
    and is told scanning stopped has been told the operator's engine stopped.
    """
    own = scope == "own"
    title = "TRADING PAUSED" if own else "BOT PAUSED"
    lede = ("Your account's trading is <b>suspended</b>" if own
            else "All trading activity <b>suspended</b>")
    text = (
        f"{_header(chr(0x23F8), title)}\n\n"
        f"  {_WARN} {lede}\n\n"
        "<pre>"
        f"{_kv('Scanning', 'RUNNING' if own else 'PAUSED')}\n"
        f"{_kv('New Trades', 'BLOCKED')}\n"
        f"{_kv('Open Positions', 'UNCHANGED')}\n"
        f"{_kv('Circuit Breaker', 'ACTIVE')}"
        "</pre>\n\n"
        + ("  <i>\u25b8 Your account only \u2014 the engine keeps running for "
           "everyone else</i>\n\n" if own else "")
        + "<i>\u25b8 Use /resume to reactivate trading</i>"
    )
    return {"text": text}


def resume_gate_line(gate: Optional[str]) -> str:
    """What the entry gate says AFTER the reset, in the operator's words.

    Three states, none of them a default. "" means the gate is open and the
    card may say ENABLED. A non-empty string is the reason trades are still
    being refused (risk_engine.trading_blocked_by's vocabulary) and the line
    names it and what clears it -- the warning-rate breaker clears on its
    own once the rate drops and /resume does not touch it; the loss-streak
    gate has its own probe schedule on /status. None means the gate could
    not be read, which is said rather than rounded to either verdict.
    """
    if gate is None:
        return ("  \u26aa Could not read the entry gate after the reset \u2014 "
                "check /status before trusting this card.")
    if not gate:
        return ""
    if gate.startswith("warning_rate:"):
        key = gate.split(":", 1)[1]
        return (f"  \u26d4 New entries are still <b>refused</b>: warning-rate breaker "
                f"(<code>{key}</code>). It clears on its own once the warning rate "
                "drops; /resume does not clear it.")
    if gate.startswith("loss_streak:"):
        return ("  \u26d4 New entries are still <b>refused</b>: loss-streak gate \u2014 "
                "/status says when a probe trade is allowed.")
    return (f"  \u26d4 New entries are still <b>refused</b>: circuit breaker "
            f"(<code>{gate}</code>).")


def render_resume(retrip_warning: str = "", scope: str = "shared",
                  gate: Optional[str] = "") -> Dict[str, Any]:
    """Resume card. When the risk engine reports the breaker would RE-TRIP on
    the next evaluation (daily loss / drawdown condition still holds), the card
    says so instead of claiming a clean resume that the very next status check
    contradicts with a 'Paused' label.

    ``gate`` is trading_blocked_by read AFTER the reset (see resume_gate_line).
    On 2026-09-03 at 13:59 this card printed "Trading ENABLED / Circuit Breaker
    CLEAR" while the warning-rate breaker was refusing every entry; /start said
    so one minute later and /status said HALTED. reset_circuit_breaker() clears
    the circuit breaker and nothing else, and the re-trip warning only knows
    daily loss and drawdown -- the same "narrow breaker read as the whole
    answer" the bridge's /health was cured of on 2026-07-29. "Trading" is now
    ENABLED / REFUSED / UNREAD from the gate, and the CLEAR line stays because
    that part was true.

    ``scope`` — see render_pause. "RUNECLAW is back online" is a claim about the
    engine; a per-user resume clears one account's breaker and brings nothing
    online, so it must not say that.
    """
    own = scope == "own"
    trading = "UNREAD" if gate is None else ("ENABLED" if not gate else "REFUSED")
    text = (
        f"{_header(chr(0x25B6) + chr(0xFE0F), 'TRADING RESUMED' if own else 'BOT RESUMED')}\n\n"
        + (f"  {_OK} Your account is <b>trading again</b>\n\n" if own
           else f"  {_OK} {ENGINE} is <b>back online</b>\n\n")
        + "<pre>"
        f"{_kv('Scanning', 'ACTIVE')}\n"
        f"{_kv('Trading', trading)}\n"
        f"{_kv('Circuit Breaker', 'CLEAR' if not retrip_warning else 'CLEAR*')}"
        "</pre>\n\n"
    )
    _gate_line = resume_gate_line(gate)
    if _gate_line:
        text += _gate_line + "\n\n"
    if retrip_warning:
        text += (
            f"  {_WARN} <b>Heads up:</b> {retrip_warning}.\n"
            "  <i>Status will show Paused again once it re-trips.</i>\n\n"
        )
    text += "<i>\u25b8 Signal scanning will begin on next tick cycle</i>"
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_emergency_stop
# ═════════════════════════════════════════════════════════════════

def render_emergency_stop() -> Dict[str, Any]:
    text = (
        f"{_header(chr(0x26D4), 'EMERGENCY STOP')}\n\n"
        f"  {_BAD} This will <b>immediately</b>:\n\n"
        "<pre>"
        "  \u2718 Cancel all pending orders\n"
        "  \u2718 Close all open positions\n"
        "  \u2718 Trip circuit breaker\n"
        "  \u2718 Halt all scanning"
        "</pre>\n\n"
        f"  {_WARN} <b>Are you sure?</b>"
    )
    keyboard: _Keyboard = [
        [_btn("\u26d4 CONFIRM STOP", "emergency_confirm"),
         _btn("\u21a9\ufe0f Cancel", "emergency_cancel")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# Callback router
# ═════════════════════════════════════════════════════════════════

def handle_callback(callback_data: str) -> Dict[str, Any]:
    if callback_data == "open_warroom":
        return render_start()

    if callback_data == "latest_signal":
        return {"text": f"\U0001f4e1 <i>Fetching latest signal from {ENGINE}...</i>"}

    if callback_data == "performance":
        return {"text": "\U0001f4ca <i>Loading performance data...</i>"}

    if callback_data == "risk_control":
        return {"text": f"{_SHIELD} <i>Opening Risk Control Panel...</i>"}

    if callback_data == "strategy_mode":
        return render_strategy_mode("balanced")

    if callback_data == "positions":
        return {"text": "\U0001f4c2 <i>Loading open positions...</i>"}

    # Signal actions
    if callback_data.startswith("signal_approve_"):
        pair = callback_data.removeprefix("signal_approve_")
        return {"text": f"{_OK} Trade <b>approved</b> for {pair}. Executing."}

    if callback_data.startswith("signal_watch_"):
        pair = callback_data.removeprefix("signal_watch_")
        return {"text": f"\U0001f441 Watching <b>{pair}</b>. You will be notified on trigger."}

    if callback_data.startswith("signal_reject_"):
        pair = callback_data.removeprefix("signal_reject_")
        return {"text": f"{_BAD} Signal for <b>{pair}</b> rejected."}

    # Risk actions
    if callback_data == "risk_safe_mode":
        return {"text": f"{_SHIELD} <b>Safe Mode</b> activated. Reduced exposure."}

    if callback_data == "risk_pause":
        return render_pause()

    if callback_data == "risk_emergency_stop":
        return render_emergency_stop()

    if callback_data == "emergency_confirm":
        return {"text": (
            f"\u26d4 <b>EMERGENCY STOP EXECUTED</b>\n\n"
            f"  {_BAD} All orders cancelled\n"
            f"  {_BAD} Positions closed\n"
            f"  {_BAD} Bot halted\n\n"
            f"<i>Use /reset to restart</i>"
        )}

    if callback_data == "emergency_cancel":
        return {"text": f"{_OK} Emergency stop cancelled. Bot continues."}

    # Mode switches
    if callback_data.startswith("mode_"):
        mode = callback_data.removeprefix("mode_")
        return render_strategy_mode(mode)

    # Position actions
    if callback_data.startswith("pos_details_"):
        pair = callback_data.removeprefix("pos_details_")
        return {"text": f"\U0001f4cb <i>Loading details for {pair}...</i>"}

    if callback_data.startswith("pos_close_"):
        pair = callback_data.removeprefix("pos_close_")
        return {"text": f"{_BAD} <i>Closing position for {pair}...</i>"}

    return {"text": f"{_WARN} Unknown command."}
