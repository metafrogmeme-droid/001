"""The engine's own read on one symbol, for the chat model to answer from.

"is SOL a buy right now", "whats your read on HYPE" and "deep dive into ETH
structure" match no router rule, so they reach the model and not a card. The
model then answered them from nothing, under a prompt that asked it for an RSI
range and a timeframe-by-timeframe read that no block and no tool supplied, and
under a full-scan answer shape that wanted a "setup quality, 1-10".
`market_read` is the reading the model can ask for instead.

WHAT `Analyzer.scan_read` IS, read before it was wired here: the indicators,
regime detection and confluence electorate the analyzer trades on, and its
rule-based thesis (`_rule_based_thesis`, a staticmethod), never the LLM. It
writes nothing the analyzer keeps: no audit row, no `_record_no_trade`, no
`_last_rejection_diag`, and the per-symbol regime smoothing `_detect_regime`
touches is snapshotted and restored around the call. Its one read beyond the
candles is the voter-weight learner's file, cached, when that flag is on.

WHAT IT IS NOT, and the card says so: not a trade idea (no entry, stop, target
or size, and nothing is queued), not the LLM's thesis, and not an RSI, another
timeframe, order flow or a setup grade. Those are absent here, and the card
tells the model to leave them out rather than estimate them.

BOUNDED. The candles come through `engine._cached_ohlcv` on the analyzer's own
leg (`chat_draft.ANALYZER_TIMEFRAME`, the same cache key the sweep fills), so a
symbol the engine read recently costs no fetch, and a fetch is held to
`FETCH_BUDGET_S`, inside `run_tool`'s own timeout. A failure names its
exception class, never its message.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from bot.compat import UTC
from bot.nlp.chat_draft import ANALYZER_TIMEFRAME
from bot.skills.skill_registry import BaseSkill

#: Closed bars asked for: the analyzer's own primary fetch (`limit=100`).
BARS = 100
#: The whole candle read, across both markets tried. `run_tool` allows 12 s.
FETCH_BUDGET_S = 8.0


def _base(symbol: str) -> str:
    return str(symbol or "").strip().upper().split("/")[0].split(":")[0]


async def _closed_bars(engine: Any, symbol: str) -> tuple[Optional[list], str, str]:
    """``(rows, market, why_not)``: the analyzer's closed 1h bars for ``symbol``.

    The spot market first, as the analyzer reads a crypto symbol, then the
    perpetual for a perp-only listing. ``rows`` is None with ``why_not`` set
    when neither answered.
    """
    from bot.core.engine import _mtf_ttl
    from bot.core.market_scanner import _classify_symbol

    base = _base(symbol)
    spot, perp = f"{base}/USDT", f"{base}/USDT:USDT"
    markets = []
    if _classify_symbol(spot) == "Crypto":
        markets.append(("spot", spot))
    markets.append(("perp", perp))
    failures = []
    for kind, market in markets:
        try:
            exchange = (await engine.get_exchange("Crypto") if kind == "spot"
                        else await engine.get_futures_exchange())
            rows = await engine._cached_ohlcv(exchange, market, ANALYZER_TIMEFRAME,
                                              limit=BARS, ttl=_mtf_ttl(ANALYZER_TIMEFRAME))
        except Exception as exc:
            failures.append(f"{market}: {type(exc).__name__}")
            continue
        if isinstance(rows, list) and rows:
            return rows, market, ""
        failures.append(f"{market}: no candles")
    return None, "", "; ".join(failures) or "no market was asked"


def _signal(symbol: str, rows: list):
    """The scanner's signal shape, from the closed bars alone: the last close
    as the price, and the 24h change and volume only where 25 bars exist."""
    from bot.utils.models import MarketSignal

    close = float(rows[-1][4])
    change = None
    volume = 0.0
    if len(rows) >= 25:
        then = float(rows[-25][4])
        change = (close - then) / then * 100.0 if then > 0 else None
        volume = sum(float(r[4]) * float(r[5]) for r in rows[-24:] if len(r) > 5)
    return MarketSignal(symbol=symbol, price=close, change_pct_24h=change,
                        volume_usd_24h=volume, timestamp=datetime.now(UTC))


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def confluence_words(confluence: float, direction: Optional[str]) -> str:
    """Confluence is a BULLISHNESS score: 0.5 is neutral, a strong short reads
    low. Printed bare beside a SHORT it reads as weak, so it is oriented to the
    direction, with the raw figure named as what it is."""
    if direction == "LONG":
        return f"{_pct(confluence)} for a LONG (bullishness {_pct(confluence)}; 50% is neutral)"
    if direction == "SHORT":
        return (f"{_pct(1.0 - confluence)} for a SHORT (bullishness "
                f"{_pct(confluence)}; 50% is neutral)")
    return f"bullishness {_pct(confluence)} (50% is neutral)"


async def market_read(engine: Any, symbol: str) -> tuple[str, str]:
    """``(tag, prose)``: READ with the engine's read, or UNREAD with why not."""
    analyzer = getattr(engine, "analyzer", None)
    if analyzer is None or not callable(getattr(analyzer, "scan_read", None)):
        return "UNREAD", ("No engine read: this process holds no analyzer, so "
                          "nothing about the market was measured.")
    base = _base(symbol)
    if not base:
        return "UNREAD", "No engine read: no symbol was named."
    try:
        rows, market, why_not = await asyncio.wait_for(
            _closed_bars(engine, symbol), timeout=FETCH_BUDGET_S)
    except asyncio.TimeoutError:
        return "UNREAD", (f"No engine read on {base}: its {ANALYZER_TIMEFRAME} "
                          f"candles did not arrive within {FETCH_BUDGET_S:.0f} s, "
                          "so nothing about it was measured.")
    if rows is None:
        return "UNREAD", (f"No engine read on {base}: its {ANALYZER_TIMEFRAME} "
                          f"candles could not be read ({why_not}), so nothing "
                          "about it was measured.")
    try:
        read = await analyzer.scan_read(_signal(market, rows), rows)
    except Exception as exc:
        read = None
        why_not = type(exc).__name__
    if not isinstance(read, dict):
        return "UNREAD", (f"No engine read on {base}: {len(rows)} closed "
                          f"{ANALYZER_TIMEFRAME} bars were read, and the engine "
                          "could not score them" + (f" ({why_not})" if why_not else "")
                          + ". Nothing about it was measured.")
    try:
        as_of = datetime.fromtimestamp(float(rows[-1][0]) / 1000.0, UTC).strftime(
            "%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, IndexError, OverflowError):
        as_of = "an unstated time"
    direction = read.get("direction")
    lines = [f"Engine read on {market}: the rule engine (no LLM) on {len(rows)} "
             f"closed {ANALYZER_TIMEFRAME} bars, the last one opened {as_of}."]
    if direction in ("LONG", "SHORT"):
        lines.append(f"Direction: {direction}.")
        lines.append(f"Score: {_pct(float(read['score']))}, the rule engine's "
                     "conviction in that direction, halved when it fades a strong "
                     "trend. It is not a setup grade.")
    else:
        lines.append("Direction: none. The rule engine found no directional bias.")
        lines.append(f"Distance from neutral: {_pct(float(read['score']))}.")
    lines.append(f"Regime: {read.get('regime') or 'not stated'}.")
    if read.get("confluence") is not None:
        lines.append(f"Confluence: {confluence_words(float(read['confluence']), direction)}.")
    lines.append(f"Last closed {ANALYZER_TIMEFRAME} close: {float(rows[-1][4]):,.6g} "
                 "(a closed bar, not a live price).")
    lines.append("This is not a trade idea: it has no entry, stop, target or "
                 "size, and nothing was queued or placed. Not read here: RSI or "
                 "any other single indicator, other timeframes, order flow, "
                 "funding, and a setup-quality grade. Leave those out rather "
                 f"than estimate them. The engine's full analysis with levels is "
                 f"\"analyze {base}\".")
    return "READ", "\n".join(lines)


class MarketReadSkill(BaseSkill):
    """The chat tool. ``execute`` returns a tagged string ``run_tool`` unwraps."""

    name = "market_read"
    description = ("The engine's deterministic read on one symbol (direction, "
                   "score, regime, confluence) from its own closed 1h bars.")

    async def execute(self, engine: Any, **kwargs: Any) -> str:
        tag, prose = await market_read(engine, str(kwargs.get("symbol") or ""))
        return f"{tag}\n{prose}"
