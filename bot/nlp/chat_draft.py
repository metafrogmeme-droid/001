"""A chat ticket that registers nothing until the person presses Stage.

The model may ask ``draft_trade`` for levels. The levels are computed here
from the cached candles (price and ATR), the strategy's ATR multiples, the
stop floor ``record_idea_levels`` already applies, and the net reward:risk
``trade_costs`` already charges. Nothing is written into ``_pending_ideas``.

Stage is a button. It calls ``build_manual_idea`` with ``source="manual"``
and ``origin="chat_draft"``, then the existing confirm card. Confirm stays
the human door. This module never calls it.

The ask is the user's own message in this turn. A tool result is not an
argument of ``user_asks_for_ticket`` and is not read here.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Union

from bot.config import CONFIG
from bot.core.signal_levels import record_idea_levels
from bot.core.trade_costs import live_rr, net_reward_risk
from bot.skills.skill_registry import BaseSkill
from bot.utils.models import TradeIdea

_STRATEGIES = frozenset({"scalp", "intraday", "swing", "position"})
_TTL_S = 900.0
_EXTEND_STEPS = 8

#: The user's own words asked for a ticket. "draft the letter" does not.
_ASK = re.compile(
    r"\b(?:draft|write|make|build|prepare)\b.{0,48}\b(?:ticket|trade)\b"
    r"|\bticket\b.{0,24}\b(?:for|on)\b"
    r"|\bdraft\s+(?:me\s+)?(?:a\s+)?(?:long|short)\b",
    re.IGNORECASE,
)


def user_asks_for_ticket(text: str) -> bool:
    """True when this turn's own message asks for a ticket.

    One argument. Callers pass the user's text and nothing else, so a
    sentence planted in a tool result cannot become the ask.
    """
    return _ASK.search(str(text or "")) is not None


def _now() -> float:
    return time.monotonic()


@dataclass
class Draft:
    id: str
    user_id: str
    direction: str
    symbol: str
    entry: float
    sl: float
    tp: float
    gross_rr: Optional[float]
    net_rr: Optional[float]
    strategy: str
    order_type: str
    created: float
    staged: bool = False
    offered: bool = False

    def public(self) -> dict:
        """What a signed-in chat response may carry. No account dollars."""
        return {
            "id": self.id,
            "symbol": self.symbol.split("/")[0],
            "direction": self.direction,
            "entry": self.entry,
            "sl": self.sl,
            "tp": self.tp,
            "gross_rr": self.gross_rr,
            "net_rr": self.net_rr,
            "strategy": self.strategy,
        }


_DRAFTS: dict[str, Draft] = {}


def _clear_drafts() -> None:
    _DRAFTS.clear()


def split_tagged(result: str) -> tuple[str, str]:
    """``READ|UNREAD|ABSENT`` plus the prose the model and the store see."""
    head, sep, rest = str(result or "").partition("\n")
    mapped = {"READ": "read", "UNREAD": "unread", "ABSENT": "absent"}
    if sep and head in mapped:
        return mapped[head], rest
    return "read", str(result or "")


def _atr(rows: list) -> Optional[float]:
    """Mean true range of the last 14 closed bars, or None when that is not a reading."""
    bars = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            high, low, close = float(row[2]), float(row[3]), float(row[4])
        except (TypeError, ValueError):
            continue
        if high >= low and close > 0:
            bars.append((high, low, close))
    if len(bars) < 15:
        return None
    trs = []
    for i in range(1, len(bars)):
        high, low, close = bars[i]
        prev = bars[i - 1][2]
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    window = trs[-14:]
    if len(window) < 14:
        return None
    value = sum(window) / len(window)
    if value <= 0:
        return None
    return value


def market_for(engine, symbol: str) -> dict:
    """Price and ATR from candles the engine already cached. A miss is unread.

    This does not fetch. A cold cache is not a 2% stand-in for ATR.
    """
    unread = {"price": None, "atr": None, "as_of": None, "read_state": "unread"}
    cache = getattr(engine, "_ohlcv_cache", None)
    if not isinstance(cache, dict) or not cache:
        return unread
    base = str(symbol or "").split("/")[0].split(":")[0].upper()
    if not base:
        return unread
    needle = f"{base}/USDT"
    best_rows = None
    best_t = -1.0
    for key, val in cache.items():
        if not str(key).startswith(needle):
            continue
        if not isinstance(val, tuple) or len(val) < 2:
            continue
        try:
            stamped = float(val[0])
        except (TypeError, ValueError):
            continue
        rows = val[1]
        if not isinstance(rows, list) or not rows:
            continue
        if stamped >= best_t:
            best_t = stamped
            best_rows = rows
    if not best_rows:
        return unread
    last = best_rows[-1]
    try:
        price = float(last[4])
        as_of = int(last[0])
    except (TypeError, ValueError, IndexError):
        return unread
    atr = _atr(best_rows)
    if price <= 0 or atr is None:
        return unread
    return {"price": price, "atr": atr, "as_of": as_of, "read_state": "read"}


def _quote(direction: str, entry: float, atr: float, strategy: str,
           order_type: str) -> tuple[Optional[Draft], str]:
    """Levels that clear the strategy's net minimum, or a sentence and no draft."""
    st = CONFIG.strategy_types
    if strategy not in _STRATEGIES:
        return None, f"{strategy} is not a strategy type I can price a ticket for."
    sl_mult = st.get_sl_mult(strategy)
    tp_mult = st.get_tp_mult(strategy)
    floor = CONFIG.analyzer.min_stop_distance_pct
    minimum = st.get_min_rr(strategy)
    is_long = direction == "LONG"
    sign = 1.0 if is_long else -1.0
    stop = entry - sign * sl_mult * atr
    target = entry + sign * tp_mult * atr
    chosen = None
    for _ in range(_EXTEND_STEPS + 1):
        recorded = record_idea_levels(
            entry, stop, target, is_long=is_long, floor=floor)
        e, s, t = recorded
        gross = live_rr(e, s, t)
        net = net_reward_risk(e, s, t, order_type=order_type)
        net_value = None if net is None else net.net
        chosen = (e, s, t, gross, net_value)
        if net_value is not None and net_value + 1e-9 >= minimum:
            break
        target = target + sign * atr
    if chosen is None:
        return None, "No ticket. The levels could not be recorded."
    e, s, t, gross, net_value = chosen
    if net_value is None or net_value + 1e-9 < minimum:
        shown = "unread" if net_value is None else f"{net_value:.2f}"
        return None, (
            f"No ticket. Net reward:risk is {shown}, under the {strategy} "
            f"minimum of {minimum:.2f}. Nothing was registered."
        )
    return Draft(
        id="CD-" + uuid.uuid4().hex[:8],
        user_id="",
        direction=direction,
        symbol="",
        entry=e,
        sl=s,
        tp=t,
        gross_rr=None if gross is None else round(float(gross), 2),
        net_rr=round(float(net_value), 2),
        strategy=strategy,
        order_type=order_type,
        created=_now(),
    ), ""


def draft_ticket(engine, user_id: str, symbol: str, direction: str,
                 strategy: str = "intraday",
                 order_type: str = "limit") -> tuple[str, str]:
    """Compute one ticket. Returns ``(tag, prose)``. Stores a draft only when
    the net ratio clears the strategy minimum. Does not register an idea.
    """
    direction = str(direction or "").strip().upper()
    if direction in ("BUY",):
        direction = "LONG"
    elif direction in ("SELL",):
        direction = "SHORT"
    if direction not in ("LONG", "SHORT"):
        return "ABSENT", "No ticket. Say LONG or SHORT. Nothing was registered."
    strategy = str(strategy or "intraday").strip().lower()
    market = market_for(engine, symbol)
    if market.get("read_state") != "read":
        base = str(symbol or "").split("/")[0] or "that symbol"
        return "UNREAD", (
            f"The price or the ATR for {base} is not in the candle cache, "
            "so there is no ticket. Nothing was registered."
        )
    draft, why = _quote(direction, float(market["price"]), float(market["atr"]),
                        strategy, order_type)
    if draft is None:
        return "ABSENT", why
    draft.user_id = str(user_id or "")
    draft.symbol = str(symbol)
    _DRAFTS[draft.id] = draft
    gross = "unread" if draft.gross_rr is None else f"{draft.gross_rr:.2f}"
    return "READ", (
        f"Draft ticket {draft.id}, not registered. "
        f"{draft.symbol} {draft.direction}. "
        f"Entry {draft.entry}. Stop {draft.sl}. Target {draft.tp}. "
        f"Gross R:R {gross}. Net R:R {draft.net_rr:.2f}. "
        f"Strategy {draft.strategy}. "
        "Press Stage this ticket to open the confirm card. "
        "Nothing is placed until you confirm."
    )


def offer(user_id: str) -> Optional[dict]:
    """The newest unstaged draft for this caller, once, for the Stage button."""
    now = _now()
    found: Optional[Draft] = None
    for draft in _DRAFTS.values():
        if draft.user_id != str(user_id or "") or draft.staged or draft.offered:
            continue
        if now - draft.created > _TTL_S:
            continue
        if found is None or draft.created >= found.created:
            found = draft
    if found is None:
        return None
    found.offered = True
    return found.public()


def stage_draft(engine, draft_id: str, user_id: str) -> Union[TradeIdea, str]:
    """Register the draft as a manual idea, or a sentence when that did not happen.

    A sentence means nothing was registered. The idea's source is ``manual``
    and its origin is ``chat_draft``. This does not confirm.
    """
    draft = _DRAFTS.get(str(draft_id or ""))
    if draft is None or _now() - draft.created > _TTL_S:
        return "That draft is not here. Nothing was registered."
    if str(user_id or "") != draft.user_id:
        return "Only the person who asked for this ticket can stage it. Nothing was registered."
    if draft.staged:
        return "That ticket is already on its confirm card. Nothing new was registered."
    from bot.skills.manual_trade import build_manual_idea, register_manual_idea
    base = draft.symbol.split("/")[0].split(":")[0]
    try:
        idea = build_manual_idea(
            draft.direction, base, draft.entry, draft.sl, draft.tp,
            order_type=draft.order_type, origin="chat_draft")
    except ValueError:
        return "Those levels are not a valid order. Nothing was registered."
    register_manual_idea(engine, idea, None)
    draft.staged = True
    return idea


class DraftTradeSkill(BaseSkill):
    """The chat tool. ``execute`` returns a tagged string ``run_tool`` unwraps."""

    name = "draft_trade"
    description = (
        "A deterministic ticket from cached price and ATR. It registers "
        "nothing. Call it only when the user's own message asks for a ticket."
    )

    async def execute(self, engine, **kwargs: Any) -> str:
        symbol = kwargs.get("symbol") or ""
        direction = kwargs.get("direction") or ""
        strategy = kwargs.get("strategy") or "intraday"
        tag, prose = draft_ticket(
            engine, str(kwargs.get("user_id") or ""), symbol, direction,
            strategy=strategy)
        return f"{tag}\n{prose}"
