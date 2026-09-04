"""The re-analysed trade is a DIFFERENT trade. Offer it; never execute it.

`_cmd_confirm` retries when the engine refuses on price drift. It rebuilt the
idea at the current price and called ``confirm_trade`` on it directly — so a
user who pressed "confirm" on one thesis got a market order for another one,
without being asked. Three things about the rebuilt idea are not the thing
they approved:

* the ENTRY is wherever the price has moved to, which is the reason the
  original was refused in the first place;
* the STOP and TARGET are flat ±3%/6% of that price, not the analyst's levels
  — a different risk/reward, on a different chart;
* the REASONING is the literal string "Auto re-analyzed after price drift".
  Nothing analysed anything. The word is the only part that survived.

So the re-analysis stays; the auto-execution goes. `reanalyzed_idea` builds the
candidate, the caller registers it as pending, and `render_reanalyzed_offer`
puts it in front of the user with the same confirm/limit/skip buttons any other
idea gets. One press instead of none.

The rest of this module is the same rule applied to the values those paths
print: a fill price the venue did not return, a close price no ticker gave,
and a "flattened" count that counted attempts rather than closes.
"""

from __future__ import annotations

from typing import Any, Optional

from bot.utils.models import Direction, TradeIdea

# The re-analysed geometry, named rather than spelled inline at the call site.
# Flat percentages are the honest description of what this is: a placeholder
# shape, not a thesis — which is exactly why the card says so out loud.
STOP_PCT = 0.03
TARGET_PCT = 0.06


def reanalyzed_idea(original: TradeIdea, new_price: float) -> Optional[TradeIdea]:
    """The drifted-price candidate, or None when the price could not be read.

    None, not the original: re-offering the stale idea would hide that the
    price moved, and inventing a price is the defect this module exists for.
    """
    if not new_price or new_price <= 0:
        return None
    is_long = original.direction == Direction.LONG
    return TradeIdea(
        asset=original.asset,
        direction=original.direction,
        entry_price=new_price,
        stop_loss=round(new_price * ((1 - STOP_PCT) if is_long else (1 + STOP_PCT)), 6),
        take_profit=round(new_price * ((1 + TARGET_PCT) if is_long else (1 - TARGET_PCT)), 6),
        confidence=original.confidence,
        reasoning=(f"Rebuilt at the current price after the original entry "
                   f"drifted. Levels are flat {STOP_PCT:.0%}/{TARGET_PCT:.0%} "
                   f"placeholders, not a fresh analysis."),
        source="auto_reanalyze",
    )


def atr_from_ohlcv(ohlcv: Any) -> float:
    """True-range mean over the last 14 bars; 0.0 when there are too few.

    0.0 is what every existing caller means by "no ATR" (the risk engine falls
    back to a percentage stop), so this is not an unread value rendered as a
    measurement — it is the absence the callers already handle.
    """
    rows = list(ohlcv or [])
    if len(rows) < 2:
        return 0.0
    trs: list[float] = []
    for prev, cur in zip(rows, rows[1:]):
        try:
            high, low, prev_close = float(cur[2]), float(cur[3]), float(prev[4])
        except (TypeError, ValueError, IndexError):
            return 0.0
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not trs:
        return 0.0
    window = trs[-14:]
    return float(sum(window) / len(window))


def render_reanalyzed_offer(original: TradeIdea, new_idea: TradeIdea) -> str:
    """The offer card. It must not read like a report of something done."""
    drift_pct = 0.0
    if original.entry_price:
        drift_pct = (new_idea.entry_price - original.entry_price) / original.entry_price * 100
    arrow = "🟢 LONG" if new_idea.direction == Direction.LONG else "🔴 SHORT"
    return (
        f"⚠️ <b>{new_idea.asset} — price moved, this is a NEW trade</b>\n"
        f"Price moved {drift_pct:+.1f}% since the analysis — not executed.\n\n"
        f"{arrow}  entry <code>{new_idea.entry_price:,.6g}</code>\n"
        f"SL <code>{new_idea.stop_loss:,.6g}</code> · "
        f"TP <code>{new_idea.take_profit:,.6g}</code>\n"
        f"<i>Levels are flat {STOP_PCT:.0%}/{TARGET_PCT:.0%} placeholders at the "
        f"new price — this is <b>not</b> the setup you confirmed.</i>\n\n"
        f"Confirm to take it, or skip."
    )


def venue_fill_price(order: Any) -> Optional[float]:
    """The price the venue actually reported, or None when it reported none.

    The close path used ``or entry_price`` here, which books a round-trip PnL
    of exactly the fees on a fill nobody read — a number that looks measured.
    """
    if not isinstance(order, dict):
        return None
    for key in ("average", "price"):
        try:
            value = float(order.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def paper_close_price(ticker: Any) -> Optional[float]:
    """Last price from a ticker, or None when the ticker carries none."""
    if not isinstance(ticker, dict):
        return None
    try:
        last = float(ticker.get("last") or 0)
    except (TypeError, ValueError):
        return None
    return last if last > 0 else None


def flatten_failed_messages(messages: Any) -> list[str]:
    """The messages from ``close_all_positions`` that report a FAILED close.

    It returns one message per position and reports failures in that text
    rather than raising, so "we called it" and "it closed" are the same value
    to every caller unless someone reads the text. One predicate, so the
    Telegram card, the engine's per-account rollup and the website ack cannot
    drift apart on what counts as flat.
    """
    out: list[str] = []
    for message in (messages or []):
        text = str(message)
        if text.startswith("Failed to close") or text.startswith("close_all_positions failed"):
            out.append(text)
    return out


def flatten_account_ok(messages: Any) -> bool:
    """Did every close on this account actually succeed?"""
    return not flatten_failed_messages(messages)


def flatten_headline(accounts: Any) -> str:
    """The emergency card's one-line count of what is actually flat.

    The card said "Accounts flattened: N" where N counted accounts ATTEMPTED.
    An operator reads that line to decide whether they still have exposure, so
    a failed close counted as flat is the most expensive false claim on the
    most urgent screen in the product.
    """
    rows = list(accounts or [])
    if not rows:
        return "Accounts flattened: none (no live accounts)"
    failed = [str(r.get("account", "?")) for r in rows if not r.get("ok")]
    line = f"Accounts flattened: {len(rows) - len(failed)} of {len(rows)}"
    if failed:
        line += (f" — ⚠️ closes FAILED on {', '.join(failed)}; exposure may "
                 f"remain, check /positions and the exchange")
    return line
