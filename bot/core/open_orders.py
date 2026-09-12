"""The exchange's open-order book: read once, rendered anywhere.

`/orders` held all of this inline — a 244-line Telegram handler doing the
account-wide fetch, the per-symbol retry, the reconciliation with the bot's
own pending records, the desync resolution, the classification and the card
— so nothing but that one command could ask the exchange what is resting.
Meanwhile the intent router had been sending "my open orders", "what's
pending" and "my limit orders" to a skill named `get_orders` that was never
registered: Telegram special-cased the name to the command, the web aliased
it to `get_portfolio` — a POSITIONS card, in answer to a question about
ORDERS, so "no positions" printed over resting limits — and the chat model,
on either surface, was told in its own prompt that "/orders asks the
exchange" while holding no tool that could.

This module is the seam. `read_open_orders` is the ONE read — every surface
asks it, so none can disagree about what is resting — and its answers keep
the three outcomes apart: orders (with the venue's own word on the ones
that filled or were cancelled while the bot still tracked them), the venue
answering NONE, and the venue not answering at all. The third is RAISED
rather than returned as an empty list, because "nothing resting" and "could
not look" are the two answers `_cmd_open_positions` was once caught printing
as one (`SL None` for every orphan when a single `fetch_open_orders` failed).

Every price on a row is a reading or the word "unread" — never `0.0`. ccxt
hands back `price: None` for a market-trigger stop and `filled: None` for an
order the venue has not touched, and `float(x or 0)` turned both into a
number the card then printed as `$0.0000`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from bot.compat import UTC
from bot.core.live_executor import display_symbol


def _f(v: Any) -> Optional[float]:
    """A finite float, or None. NaN and inf are absences wearing digits."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (f != f or f in (float("inf"), float("-inf"))) else f


def _px(v: Optional[float]) -> str:
    """A dollar figure, or the word for its absence. Never a zero standing in."""
    return f"${v:,.4f}" if v is not None and v > 0 else "unread"


def synth_order_from_tracked(p) -> dict:
    """Build a ccxt-order-shaped dict from a bot-tracked pending_fill position.

    Lets a bot-tracked pending limit flow through the same rendering path as
    a real exchange order when the exchange query can't see it.
    """
    side = "buy" if getattr(p, "direction", "") == "LONG" else "sell"
    opened = getattr(p, "opened_at", None)
    # None, not 0, for a field the record does not carry: the row prints
    # "unread" for it, where a 0 printed as `$0.0000`.
    qty = getattr(p, "quantity", None)
    return {
        "id": getattr(p, "trade_id", "") or "",
        "symbol": getattr(p, "symbol", "") or "",
        "type": "limit",
        "side": side,
        "price": getattr(p, "entry_price", None),
        "amount": qty,
        "remaining": qty,
        "filled": 0,
        "status": "open",
        "triggerPrice": 0,
        "datetime": opened.isoformat() if opened is not None else "",
    }


def reconcile_open_orders(exchange_orders, tracked_pending, per_symbol_orders):
    """Decide what an open-orders surface should display, reconciling the live
    exchange query with the bot's own tracked pending_fill orders.

    Returns ``(orders, desync)`` where ``desync`` is True when the exchange
    reports nothing but the bot is still tracking pending limit(s) — i.e. the
    bot-tracked records are being surfaced and should carry a warning.

    Priority:
      1. account-wide exchange result, if non-empty (source of truth);
      2. else, if the bot tracks nothing pending, genuinely empty;
      3. else, the per-symbol re-fetch result, if it found anything;
      4. else, the bot-tracked records, flagged as a possible desync.
    """
    if exchange_orders:
        return list(exchange_orders), False
    if not tracked_pending:
        return [], False
    if per_symbol_orders:
        return list(per_symbol_orders), False
    return [synth_order_from_tracked(p) for p in tracked_pending], True


async def resolve_desync_orders(exchange, tracked_pending):
    """Resolve an open-orders desync definitively instead of guessing.

    When fetch_open_orders (account-wide AND per-symbol) shows nothing but
    the bot still tracks pending limits, the truth is one fetch_order call
    away: open-order queries exclude filled/cancelled orders BY DESIGN, so
    "exchange shows nothing" usually just means "it filled seconds ago"
    (live case 2026-07-13: a SHORT limit below market — marketable, cannot
    rest — showed as a scary desync when it had simply filled). Query each
    tracked order by id and report what actually happened.

    Returns ``(notes, synth_orders)``: human-readable resolution lines, and
    ccxt-shaped dicts for records that still merit rendering as open (order
    genuinely resting, or status unverifiable).
    """
    notes: list = []
    synths: list = []
    for p in tracked_pending:
        oid = getattr(p, "limit_order_id", None)
        sym = display_symbol(getattr(p, "symbol", ""))
        side = getattr(p, "direction", "") or "?"
        order = None
        status = None
        if oid:
            try:
                order = await exchange.fetch_order(oid, p.symbol)
                status = (order.get("status") or "").lower()
            except Exception:
                status = None
        if status in ("closed", "filled"):
            avg = _f((order.get("average") or order.get("price")) if order else None)
            notes.append(
                f"✅ {side} {sym} limit <b>FILLED</b>"
                + (f" @ ${avg:,.4f}" if avg is not None and avg > 0 else "")
                + " — the bot books the fill on its next check tick.")
        elif status in ("canceled", "cancelled", "rejected", "expired"):
            notes.append(
                f"❌ {side} {sym} limit <b>{status.upper()}</b> on the "
                "exchange — the bot clears it on its next check tick.")
        elif status == "open":
            # Genuinely resting — the open-orders queries missed it.
            synths.append(synth_order_from_tracked(p))
        else:
            synths.append(synth_order_from_tracked(p))
            notes.append(
                f"⚠️ {side} {sym}: order status could not be verified — "
                "possible desync; the bot reconciles on its next tick.")
    return notes, synths


def classify_order(o: dict, *, now: datetime, expire_sec: float) -> dict:
    """One ccxt order as a row: its kind, its readings, and the words for what
    the venue did not state.

    The kind is read from ccxt's `type` AND Bitget's `info.planType`
    (loss_plan / profit_plan / pos_loss / pos_profit): a plan stop arrives
    with `type: "market"` and its stop-ness only in `planType`, so the old
    /orders classifier filed every exchange-side stop under "Other" and
    printed it `@ $0.0000`. The adoption ladder reads both for the same
    reason.
    """
    info = o.get("info") or {}
    plan = str(info.get("planType") or "") if isinstance(info, dict) else ""
    otype = f"{o.get('type') or ''} {plan}".lower().strip()
    raw_dt = o.get("datetime") or ""
    ttl_str = ""
    if raw_dt and (o.get("type") or "").lower() == "limit" and not plan:
        try:
            created_dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
            age_sec = (now - created_dt).total_seconds()
            remaining = max(0.0, float(expire_sec) - age_sec)
            if remaining <= 0:
                ttl_str = " | ⏰ expiring..."
            else:
                hrs = int(remaining // 3600)
                mins = int((remaining % 3600) // 60)
                ttl_str = (f" | ⏰ {hrs}h {mins}m left" if hrs > 0
                           else f" | ⏰ {mins}m left")
        except Exception:
            ttl_str = ""
    if "stop" in otype or "loss" in otype:
        kind = "sl"
    elif "take" in otype or "profit" in otype:
        kind = "tp"
    elif otype.startswith("limit"):
        kind = "limit"
    else:
        kind = "other"
    amount = _f(o.get("amount"))
    if amount is None:
        amount = _f(o.get("remaining"))
    trigger = _f(o.get("triggerPrice"))
    if trigger is None:
        trigger = _f(o.get("stopPrice"))
    return {
        "kind": kind,
        "sym": display_symbol(o.get("symbol", "") or ""),
        "raw_symbol": o.get("symbol", "") or "",
        "side": (o.get("side") or "").upper(),
        "price": _f(o.get("price")),
        "trigger": trigger,
        "amount": amount,
        "filled": _f(o.get("filled")),
        "status": o.get("status", "open"),
        "oid": (o.get("id") or "")[:12],
        "created": str(raw_dt)[:16] if raw_dt else "",
        "type": (o.get("type") or "").lower(),
        "plan_type": plan,
        "ttl_str": ttl_str,
    }


@dataclass
class OpenOrdersReading:
    """What the exchange said is resting, and what it did not say.

    `prices_read` is three-valued on purpose: None when no limit order needed
    a current price, True when the tickers were read, False when that read
    failed — the card says "distance to fill not shown" in the last case
    rather than printing a distance from a price of zero.
    """
    orders: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    desync: bool = False
    prices: dict[str, float] = field(default_factory=dict)
    prices_read: Optional[bool] = None
    read_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "the exchange"
    #: Why there is no list to show, when there is none: "paper" (no exchange
    #: order book exists), "no_account" (the caller resolves to no live
    #: account), "unreadable" (the venue did not answer). None when the venue
    #: answered — and then `orders` empty is the venue's own NONE.
    unavailable: Optional[str] = None
    #: The exception CLASS on an unreadable read — coarse on purpose, the way
    #: /readyz reports: an error's text is not something to hand a model or
    #: print on a card.
    error_kind: str = ""

    @property
    def state(self) -> str:
        return self.unavailable or ("read" if self.orders else "empty")

    def by_kind(self, kind: str) -> list[dict]:
        return [o for o in self.orders if o.get("kind") == kind]


def _tracked_pending(executor) -> list:
    try:
        return [p for p in executor._positions.values()
                if getattr(p, "status", "") == "pending_fill"]
    except Exception:
        return []


async def read_open_orders(executor, *, now: Optional[datetime] = None,
                           expire_sec: Optional[float] = None) -> OpenOrdersReading:
    """Ask the exchange what is resting for THIS executor's account.

    Raises whatever the account-wide query raised: an unreadable order book
    is the caller's to name, and it is not an empty one. The per-symbol
    retry and the ticker read are best-effort inside — each failure is
    recorded on the reading, never rendered as a number.
    """
    now = now or datetime.now(UTC)
    if expire_sec is None:
        from bot.config import CONFIG
        expire_sec = float(CONFIG.limit_orders.expire_seconds)
    exchange = await executor._get_exchange()
    venue = getattr(executor, "_venue", None)
    params = (venue.futures_params() if venue is not None and hasattr(venue, "futures_params")
              else {"productType": "USDT-FUTURES"})
    source = (getattr(venue, "display_name", "") or "the exchange") if venue is not None else "the exchange"

    open_orders = list(await exchange.fetch_open_orders(params=params) or [])
    tracked_pending = _tracked_pending(executor)

    # The account-wide query can miss the bot's own pending limits (Bitget's
    # no-symbol futures order query is unreliable): retry per symbol before
    # believing "none".
    per_symbol: list = []
    if not open_orders and tracked_pending:
        seen_ids: set = set()
        for p in tracked_pending:
            try:
                per = await exchange.fetch_open_orders(p.symbol)
            except Exception:
                per = []
            for o in (per or []):
                oid = o.get("id", "")
                if oid not in seen_ids:
                    seen_ids.add(oid)
                    per_symbol.append(o)

    orders, desync = reconcile_open_orders(open_orders, tracked_pending, per_symbol)
    notes: list[str] = []
    if desync:
        # Don't guess ("may have filled or been cancelled — verify on the
        # venue"): fetch each tracked order by id and say what happened.
        # Filled/cancelled orders drop out — they are not open.
        notes, orders = await resolve_desync_orders(exchange, tracked_pending)

    rows = [classify_order(o, now=now, expire_sec=expire_sec) for o in orders]

    prices: dict[str, float] = {}
    prices_read: Optional[bool] = None
    limit_raw_syms = sorted({r["raw_symbol"] for r in rows
                             if r["kind"] == "limit" and r["raw_symbol"]})
    if limit_raw_syms:
        try:
            tickers = await exchange.fetch_tickers(limit_raw_syms)
            for s, t in (tickers or {}).items():
                px = _f((t or {}).get("last"))
                if px is not None and px > 0:
                    prices[display_symbol(s)] = px
            prices_read = True
        except Exception:
            prices_read = False
    return OpenOrdersReading(orders=rows, notes=notes, desync=desync, prices=prices,
                             prices_read=prices_read, read_at=now, source=source)


async def open_orders_for(engine, user_id: str) -> OpenOrdersReading:
    """The caller's open orders, from THEIR account, as a reading in every
    state — the one entry every surface uses, so the skill, the /orders card,
    the web and the chat tool cannot disagree about whose book it is or what
    the venue said.

    `engine.viewer_executor` is the resolver: under per-user live a caller the
    engine does not map to an account gets `no_account`, never the operator's
    book — the leak /orders carried until this read existed.
    """
    from bot.config import CONFIG
    if not CONFIG.is_live():
        return OpenOrdersReading(unavailable="paper")
    executor = engine.viewer_executor(user_id or "")
    if executor is None:
        return OpenOrdersReading(unavailable="no_account")
    try:
        return await read_open_orders(executor)
    except Exception as exc:
        return OpenOrdersReading(unavailable="unreadable", error_kind=type(exc).__name__)


def render_open_orders_html(reading: OpenOrdersReading) -> str:
    """The Telegram/web/tool text for a reading, in every state.

    The three unavailable states are worded to rule out "no orders": a model
    or an operator handed an empty list would conclude nothing is resting,
    which on an unreadable book is the SL-None-for-every-orphan mistake.
    """
    if reading.unavailable == "paper":
        return ("<b>Open Orders</b>\n\nPaper mode \u2014 there is no exchange order "
                "book to ask. Paper limit orders live with the paper portfolio "
                "(<code>/portfolio</code>).")
    if reading.unavailable == "no_account":
        return ("<b>Open Orders</b>\n\nNo linked live account. Use "
                "<code>/connect</code> to link your exchange keys.")
    if reading.unavailable == "unreadable":
        kind = f" ({reading.error_kind})" if reading.error_kind else ""
        return ("\U0001f534 <b>Open Orders: COULD NOT BE READ</b> \u2014 the exchange "
                f"did not answer the open-orders query{kind}, so nothing here is "
                "known. This is not \"no orders\": it is unread. Try again in a "
                "moment.")
    if reading.notes and not reading.orders:
        # A desync that resolved to filled/cancelled: nothing rests, and the
        # venue's word on what happened rides above the venue's none.
        pass
    if not reading.orders:
        return ("<b>Open Orders</b>\n\n"
                f"No pending orders on {reading.source} right now.\n\n"
                "<i>Tip: Use the \"Limit\" button when confirming a trade to set a "
                "custom limit price.</i>")
    limit, sl, tp, other = (reading.by_kind(k) for k in ("limit", "sl", "tp", "other"))
    lines = [f"<b>Open Orders ({len(reading.orders)})</b>", ""]
    if reading.desync:
        lines.append("⚠️ <i>The exchange query could not see these; they are "
                     "the bot's own pending records, not confirmed just now.</i>")
        lines.append("")
    if limit:
        lines.append(f"<b>\U0001f4cb Limit Orders ({len(limit)}):</b>")
        lines.append("")
        for o in limit:
            d_icon = "\U0001f7e2" if o["side"] == "BUY" else "\U0001f534"
            dir_label = "LONG" if o["side"] == "BUY" else "SHORT"
            fill_str = (f" ({o['filled']:.4f} filled)"
                        if o["filled"] is not None and o["filled"] > 0 else "")
            lines.append(f"{d_icon} <b>{o['sym']} {dir_label}</b> — Limit Order")
            lines.append(f"  \U0001f4cd Limit: <code>{_px(o['price'])}</code>{fill_str}")
            cur = reading.prices.get(o["sym"])
            if cur is not None and o["price"] is not None and o["price"] > 0:
                dist = ((cur - o["price"]) / cur) * 100
                fill_hint = ("⬇️" if (o["side"] == "BUY" and cur > o["price"]) else
                             ("⬆️" if (o["side"] != "BUY" and cur < o["price"]) else "✅"))
                lines.append(f"  \U0001f4b2 Current: <code>${cur:,.4f}</code>  {fill_hint} {dist:+.2f}% to fill")
            elif reading.prices_read is False:
                lines.append("  \U0001f4b2 Current: unread — distance to fill not shown")
            qty = f"{o['amount']:.4f}" if o["amount"] is not None else "unread"
            lines.append(f"  \U0001f4b0 Qty: <code>{qty}</code>{o['ttl_str']}")
            lines.append(f"  ID: <code>{o['oid']}</code>")
            if o["created"]:
                lines.append(f"  ⏳ Placed: {o['created']}")
            lines.append("")
    if sl:
        lines.append(f"<b>Stop-Loss Orders ({len(sl)}):</b>")
        for o in sl:
            lines.append(f"  \U0001f6d1 <b>{o['sym']}</b> {o['side']} trigger {_px(o['trigger'])}")
        lines.append("")
    if tp:
        lines.append(f"<b>Take-Profit Orders ({len(tp)}):</b>")
        for o in tp:
            lines.append(f"  \U0001f3af <b>{o['sym']}</b> {o['side']} trigger {_px(o['trigger'])}")
        lines.append("")
    if other:
        lines.append(f"<b>Other ({len(other)}):</b>")
        for o in other:
            lines.append(f"  <b>{o['sym']}</b> {o['side']} {o['type']} @ <code>{_px(o['price'])}</code>")
        lines.append("")
    lines.append(f"<i>Source: {reading.source}</i>")
    return "\n".join(lines)
