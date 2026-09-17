"""The POC-retest sequence read off two real timeframes, and its card.

`poc_retest` is PURE — candles in, a state out — and this is the one place
that fetches. The split is the spec's own: *"4-hour chart: identify swing
highs/lows and calculate the volume profile. 1-hour chart: confirm the POC
retest and execute the entry"*, so the higher timeframe supplies the leg and
the POC and the lower one supplies the sequence.

A FORMING CANDLE'S CLOSE IS NOT A CLOSE, and this strategy is entirely about
closes: "a candle close at least 0.25x ATR(14) above it", "the candle must
close back above POC". The last bar a venue hands back is the one still being
built, so reading it as a close arms the sequence on a decisive close that has
not happened and may never — and unwinds it again on the next tick.
`drop_forming_candle` is the repo's existing reading of that (gated by
`DROP_UNCLOSED_CANDLE_ENABLED`, "eliminating repaint"), and both timeframes go
through it. `/sweep` next door does not, which is recorded rather than changed
here: it is a different detector with its own slice.

A FETCH THAT FAILED IS NOT A SETUP THAT DID NOT FORM. Two timeframes fail
independently, so `unread` names WHICH one and what happened, and it is kept
apart from every member of `STATES` — `no_breakout` is a claim about price and
would be a confident negative assembled from a read nobody made. Same rule for
a venue that answers with fewer candles than the window needs: a thin book and
a quiet market are different facts.

THE CARD SAYS WHAT IT READ. That is the CROSSFIRE focus-room footnote applied
to a setup rather than a chart: a sequence read off 20 closed 1h bars and one
read off 120 are visually identical on the card and are not the same evidence,
and the leg's own candle count decides whether a volume profile could be built
over it at all. It also says, in as many words, that nothing was placed and
that the thresholds are starting values rather than validated ones — the
operator's own framing, on the surface that would otherwise read as a
recommendation.
"""
from __future__ import annotations

import html
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Optional

from bot.core.poc_retest import PocRetestParams, RetestRead, SetupVerdict, retest_state, setup_verdict
from bot.utils.candles import drop_forming_candle

logger = logging.getLogger(__name__)

#: (structure, entry). The spec names both; neither is a default to be
#: overridden per call, because the leg and the sequence are read on different
#: charts BY DESIGN and swapping them silently would answer a different
#: question under the same word.
STRUCTURE_TF = "4h"
ENTRY_TF = "1h"

#: 4h x 120 is 20 days — long enough for a completed swing leg at
#: `swing_order=5`. 1h x 120 is five days, which covers ATR(14) plus a base,
#: the breakout and the retest window with room to spare.
STRUCTURE_BARS = 120
ENTRY_BARS = 120

# `compute_volume_profile` refuses a window under 10 candles, and that number
# is deliberately NOT restated anywhere here — a second copy of a floor is a
# second answer. A leg too short to profile comes back `no_poc` like any other
# unreadable profile, and the card prints the leg's own candle count so an
# operator can see which of the two it was. The first draft of this module
# declared a MIN_LEG_HINT constant for it and then read it nowhere, which is
# the fifth granularity in a module written the same hour as the rule.


@dataclass(frozen=True)
class SymbolSetup:
    """One symbol's answer: a read, or the reason there is not one.

    `unread` and `read` are mutually exclusive and BOTH may be absent of
    content in the sense that matters — what is never allowed is an `unread`
    rendered as a state. A caller that wants "is there a setup" asks
    `read.state == "confirmed"` and gets False for an unread symbol without
    ever being told price did something it did not.
    """
    symbol: str
    read: Optional[RetestRead] = None
    verdict: Optional[SetupVerdict] = None
    unread: Optional[str] = None
    structure_bars: Optional[int] = None
    entry_bars: Optional[int] = None


async def _maybe_await(value: Any) -> Any:
    """Await a value only if it is awaitable.

    `exchange_flow.py` carries this guard and its docstring records the same
    bug shipping once before: a coroutine function called without `await`
    fails into a broad handler as an AttributeError, so the reading returns
    `None` forever — wired, called, and dead, which no reachability checker
    can see because it has a caller.
    """
    return await value if inspect.isawaitable(value) else value


async def _ohlcv(exchange: Any, symbol: str, timeframe: str,
                 bars: int) -> tuple[Optional[list], Optional[str]]:
    """Closed candles for one timeframe, or the reason there are none.

    The reason is the SHAPE of the failure and never the venue's own text: a
    rejection can echo request parameters, and this string reaches a chat card.
    The exception's class name is logged and not printed, which is
    `_note_sltp_error`'s rule one module over.
    """
    try:
        raw = await _maybe_await(
            exchange.fetch_ohlcv(symbol, timeframe, limit=bars))
    except Exception as exc:
        logger.warning("poc_retest: %s %s fetch failed: %s",
                       symbol, timeframe, type(exc).__name__)
        return None, f"the {timeframe} candles could not be fetched"
    if not raw:
        return None, f"the venue returned no {timeframe} candles"
    closed = drop_forming_candle(raw, timeframe)
    return list(closed), None


def _cols(ohlcv: list) -> tuple[list, list, list, list]:
    """highs, lows, closes, volumes from ccxt's [ts, o, h, l, c, v] rows."""
    return ([float(r[2]) for r in ohlcv], [float(r[3]) for r in ohlcv],
            [float(r[4]) for r in ohlcv], [float(r[5]) for r in ohlcv])


async def read_setup(exchange: Any, symbol: str, *,
                     params: Optional[PocRetestParams] = None,
                     order_type: object = None) -> SymbolSetup:
    """Where this symbol's POC-retest sequence has got to, on two timeframes.

    Reads nothing about an account and places nothing. `order_type` is the
    entry leg's liquidity for the R figure and defaults to taker, because this
    strategy enters on a BREAK of a candle extreme — a stop order.
    """
    p = params or PocRetestParams()

    htf, why = await _ohlcv(exchange, symbol, STRUCTURE_TF, STRUCTURE_BARS)
    if htf is None:
        return SymbolSetup(symbol, unread=why)
    ltf, why = await _ohlcv(exchange, symbol, ENTRY_TF, ENTRY_BARS)
    if ltf is None:
        return SymbolSetup(symbol, unread=why,
                           structure_bars=len(htf))

    # A WINDOW TOO SHORT IS AN UNREAD, NOT A VERDICT. `swing_leg` answers
    # `no_leg` for a short series and `atr_reading` answers `atr_unread`, and
    # both are true statements about candles nobody has — but they read on the
    # card as facts about the market. Named here instead.
    need_htf = 2 * p.swing_order + 2
    if len(htf) < need_htf:
        return SymbolSetup(
            symbol, unread=(f"only {len(htf)} closed {STRUCTURE_TF} candles — "
                            f"the leg needs {need_htf}"),
            structure_bars=len(htf), entry_bars=len(ltf))
    need_ltf = p.atr_period + 1
    if len(ltf) < need_ltf:
        return SymbolSetup(
            symbol, unread=(f"only {len(ltf)} closed {ENTRY_TF} candles — "
                            f"ATR({p.atr_period}) needs {need_ltf}"),
            structure_bars=len(htf), entry_bars=len(ltf))

    h4, l4, c4, v4 = _cols(htf)
    h1, l1, c1, _ = _cols(ltf)
    read = retest_state(h4, l4, c4, v4, h1, l1, c1, params=p)
    verdict = setup_verdict(read, order_type=order_type, params=p)
    return SymbolSetup(symbol, read=read, verdict=verdict,
                       structure_bars=len(htf), entry_bars=len(ltf))


#: What the card says a state MEANS, in the operator's terms rather than the
#: vocabulary's. `RetestRead.why` is the detector's sentence and is printed
#: beside it; this is the headline.
_HEADLINE = {
    "atr_unread": "⚪ No reading — ATR unavailable",
    "no_leg": "⚪ No swing leg on the 4h",
    "no_poc": "⚪ No volume profile over the leg",
    "no_breakout": "⚪ No decisive close beyond the POC",
    "awaiting_retest": "🟡 Broke out — waiting for the retest",
    "window_expired": "⚪ Broke out, no retest in the window",
    "retest_failed": "⚪ Retest closed on the wrong side",
    "confirmed": "🟢 Sequence complete",
}

#: A word this card cannot place is its OWN failure, not the market's, so the
#: fallbacks below say so rather than borrowing "no reading" — which would
#: report a detector this build does not understand as a market that did
#: nothing. `test_the_card_can_place_every_state` pins both maps against the
#: two vocabularies, so a state added later fails here instead of rendering
#: the fallback in production.
_UNPLACEABLE = "⚪ This build cannot place that state"

_VERDICT_HEAD = {
    "ok": "🟢 Clears both filters",
    "no_target": "🔴 No structural target left",
    "stop_too_wide": "🔴 Stop too wide",
    "below_min_r": "🔴 Under the R floor",
    "unpriceable": "⚪ Nothing to price yet",
}


def _px(v: Optional[float]) -> str:
    return "—" if v is None else f"<code>{v:,.6f}</code>"


def setup_card(s: SymbolSetup) -> str:
    """The card, for a chat surface. Pure, so it can be driven.

    A card built inline in a handler is a card nothing can run — #999's own
    lesson — and every honesty claim in this slice is about what the card says
    rather than what the detector computed.
    """
    sym = html.escape(s.symbol)
    lines = [f"📐 <b>POC RETEST</b> — <b>{sym}</b>"]

    if s.unread is not None:
        lines.append(f"⚪ <b>Not read</b> — {html.escape(s.unread)}")
        lines.append("")
        lines.append("<i>Nothing was measured, so nothing is claimed about "
                     "this market.</i>")
        return "\n".join(lines)

    read, v = s.read, s.verdict
    if read is None:
        # NEITHER a read NOR a reason. `read_setup` never produces this, but
        # `SymbolSetup` is a dataclass any caller can build, and an `assert`
        # here (the first draft's) is stripped under -O and would then index
        # `None`. It is the same event as an unread arriving with no sentence,
        # and it gets the same shape rather than a card with silent gaps.
        lines.append("⚪ <b>Not read</b> — no reading and no reason recorded")
        lines.append("")
        lines.append("<i>Nothing was measured, so nothing is claimed about "
                     "this market.</i>")
        return "\n".join(lines)
    lines.append(_HEADLINE.get(read.state, _UNPLACEABLE))
    lines.append(f"<i>{html.escape(read.why)}</i>")
    lines.append("")

    if read.side:
        lines.append(f"Direction: <b>{read.side.upper()}</b> "
                     f"(the 4h leg's own)")
    lines.append(f"POC: {_px(read.poc)} · ATR({read.params.atr_period}) "
                 f"{ENTRY_TF}: {_px(read.atr)}")
    if read.leg is not None:
        span = read.leg.end_index - read.leg.start_index + 1
        lines.append(f"Leg: {_px(read.leg.low)} → {_px(read.leg.high)} "
                     f"over {span} {STRUCTURE_TF} candles")

    if read.state == "confirmed":
        lines.append("")
        lines.append(f"Entry (break of the retest candle): {_px(read.entry)}")
        # WHICH extreme, and which way. "past it" was the first draft, and
        # "it" could be read as the entry or the POC — on the one level where
        # a misreading costs money.
        past = ("below the retest candle's low" if read.side == "long"
                else "above the retest candle's high")
        lines.append(f"Stop ({read.params.atr_buffer:g}x ATR {past}): "
                     f"{_px(read.stop)}")
        lines.append(f"Target (the leg's own extreme): {_px(read.target)}")
        if v is not None:
            lines.append("")
            lines.append(_VERDICT_HEAD.get(v.verdict, _UNPLACEABLE))
            lines.append(f"<i>{html.escape(v.why)}</i>")
    elif v is not None and v.verdict != "unpriceable":
        lines.append("")
        lines.append(_VERDICT_HEAD.get(v.verdict, _UNPLACEABLE))

    lines.append("")
    # THE SAMPLE, then the two things this card is not. A COUNT NOBODY HAS IS
    # NOT A SAMPLE: `read_setup` always records both, but `SymbolSetup` is a
    # dataclass any caller can build, and the first draft interpolated the
    # field raw -- so a setup carrying a read and no counts printed "Read off
    # None closed 4h and None closed 1h candles" on the one line whose whole
    # job is to say what was read. This slice's own subject, on its own
    # footnote.
    if s.structure_bars is None or s.entry_bars is None:
        sample = "The sample this was read off was not recorded."
    else:
        sample = (f"Read off {s.structure_bars} closed {STRUCTURE_TF} and "
                  f"{s.entry_bars} closed {ENTRY_TF} candles.")
    lines.append(f"<i>{sample} Nothing was placed and nothing was "
                 f"armed.</i>")
    lines.append("<i>The buffer, the retest window and the R floor are "
                 "starting values, not validated results.</i>")
    return "\n".join(lines)
