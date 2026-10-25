"""Follow-trade execution + position management for RUNECLAW v0.1.0.

Two entry points, both reached only in follow-trade mode:

* ``manage_open_state(cfg)`` reads the sub-account and the book. It does not
  cancel, close or move a stop. It confirms ``subaccount_exists()`` and
  ``total_value()`` and refuses the pass when either cannot be confirmed.
* ``apply_management`` does those mutations, and only from the follow
  callback. It touches a position or an order only when its id is one this
  playbook stored at placement and its symbol is in the manifest.
* ``open_if_allowed(decision, cfg, mgmt)`` places a limit only when that
  confirmation held and the limit rests at least one tick inside the spread.
  The contract SDK has no post-only flag. Margin is isolated when the opener
  accepts it.

Reliable controls (documented helpers / account equity): circuit breaker,
position cap, correlation budget, duplicate-entry guard. Best-effort controls
(depend on undocumented position/order fields, fail-safe to no-op): time-stop,
auto-breakeven, limit expiry.
"""
import os
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Optional

from getagent import trade

from . import features


# The sandbox persists `.state/` and nothing else. Tests may point this at a
# scratch directory. It is a path, never an account id or a key.
def _state_file() -> Path:
    root = os.environ.get("RUNECLAW_PLAYBOOK_STATE_DIR", "/workspace/.state")
    return Path(root) / "runeclaw_scanner.json"

# SDK serialises order/position records to snake_case (to_dict/model_dump), so the
# raw Bitget camelCase cTime/createTime never matched here -- a real ETH limit sat
# ~8h past its 4h limit_expiry because create_time was absent, so age was unknown
# and the time-expiry silently no-op'd. The position time-stop reads the same list,
# so it was latently broken too. Carry both cases, like every other key list. (v0.1.18)
_OPEN_TIME_KEYS = ("cTime", "ctime", "c_time", "create_time", "created_time", "createTime",
                   "createdTime", "openTime", "open_time", "uTime", "u_time", "update_time",
                   "updateTime")
_ENTRY_PRICE_KEYS = ("openPriceAvg", "open_price_avg", "averageOpenPrice", "average_open_price",
                     "avgPrice", "avg_price", "openAvgPrice", "entryPrice", "open_price")
_UPNL_KEYS = ("unrealizedPL", "unrealized_pnl", "unrealizedPnl", "upl", "uplValue")
_HOLD_SIDE_KEYS = ("holdSide", "hold_side", "side")
# Account-equity fields. On Bitget these include unrealized PnL, so the
# breaker must not add open PnL a second time.
_EQUITY_INCLUSIVE = ("usdtEquity", "accountEquity", "totalEquity", "equity",
                     "unifiedTotalEquity")
# Wallet-style fields. Open PnL is not in the number; it has to be added,
# and a position whose open PnL cannot be read makes the equity unreadable.
_EQUITY_WALLET = ("totalAmount", "accountValue", "usdt_equity")


def _to_mapping(value: Any) -> Optional[dict]:
    if isinstance(value, dict):
        return value
    for attr in ("to_dict", "dict", "model_dump"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return out
            except Exception:
                continue
    return None


def _find_number(value: Any, keys: tuple, depth: int = 0) -> Optional[float]:
    if depth > 4:
        return None
    mapping = _to_mapping(value)
    if mapping is not None:
        for key in keys:
            if key in mapping:
                try:
                    out = float(mapping[key])
                    return out
                except (TypeError, ValueError):
                    pass
        for nested_key in ("data", "result", "list", "assets", "account"):
            if nested_key in mapping:
                found = _find_number(mapping[nested_key], keys, depth + 1)
                if found is not None:
                    return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_number(item, keys, depth + 1)
            if found is not None:
                return found
    return None


def _find_string(record: dict, keys: tuple) -> str:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return str(record[key])
    return ""


def _result_reason(result: Any) -> str:
    """Compact exchange rejection reason from a non-success trade envelope.

    Bitget envelopes carry the original ``{"code", "msg"}`` on ``result.raw``;
    fall back to the envelope itself, then to ``str(result)``. This is what turns
    a silent ``placed=False`` into an operator-readable ``code:msg`` cause.
    """
    raw = getattr(result, "raw", None)
    mapping = _to_mapping(raw) or _to_mapping(result) or {}
    code = mapping.get("code") or mapping.get("retCode") or mapping.get("sCode")
    msg = (mapping.get("msg") or mapping.get("message") or mapping.get("retMsg")
           or mapping.get("sMsg"))
    if code not in (None, "") or msg not in (None, ""):
        return "{}:{}".format(code if code not in (None, "") else "?", msg or "")[:48]
    text = str(result).replace(" ", "_")
    return text[:48] if text else "unknown"


def _read_state() -> Optional[dict]:
    """The persisted book, or None when it cannot be read.

    A missing file is an empty book (nothing has been recorded yet), which
    is a reading. A file that is there and does not parse is unreadable:
    the caller manages nothing.
    """
    path = _state_file()
    try:
        if not path.exists():
            return {"orders": []}
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_state(state: dict) -> bool:
    path = _state_file()
    try:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
        return True
    except Exception:
        return False


def _labeled_number(value: Any, keys: tuple) -> tuple:
    """``(number, key)`` for the first listed key that reads, else ``(None, "")``."""
    mapping = _to_mapping(value)
    if mapping is None:
        return None, ""
    for key in keys:
        if key in mapping:
            try:
                return float(mapping[key]), key
            except (TypeError, ValueError):
                continue
    for nested_key in ("data", "result", "list", "assets", "account"):
        if nested_key in mapping:
            found, name = _labeled_number(mapping[nested_key], keys)
            if found is not None:
                return found, name
    return None, ""


def _account_equity() -> dict:
    """The breaker equity, and whether that number already includes open PnL.

    An inclusive field is used as-is. A wallet field does not include open
    PnL; the caller adds it, and refuses if it cannot. No field is unreadable,
    not zero.
    """
    out = {"equity": None, "includes_open_pnl": None, "field": ""}
    try:
        result = trade.account.total_value()
    except Exception:
        return out
    equity, field = _labeled_number(result, _EQUITY_INCLUSIVE)
    if equity is not None and equity == equity and equity != float("inf"):
        out.update(equity=equity, includes_open_pnl=True, field=field)
        return out
    wallet, field = _labeled_number(result, _EQUITY_WALLET)
    if wallet is not None and wallet == wallet and wallet != float("inf"):
        out.update(equity=wallet, includes_open_pnl=False, field=field)
    return out


def _sum_open_pnl(records: list) -> Optional[float]:
    """Sum of unrealized PnL. One unreadable open position makes it None."""
    total = 0.0
    for record in records:
        upnl = _find_number(record, _UPNL_KEYS)
        if upnl is None:
            return None
        total += upnl
    return total


def breaker_equity(equity: Any, includes_open: Any, records: list,
                   position_unreadable: bool) -> Optional[float]:
    """Equity the circuit compares, with open PnL in the number.

    An inclusive field already holds unrealized PnL and is not added again.
    A wallet field does not: the open positions' PnL is added, and one
    unreadable position makes the equity unreadable. Missing open PnL is
    not zero, and an unreadable book is not a flat day.
    """
    try:
        base = float(equity)
    except (TypeError, ValueError):
        return None
    if base != base or base == float("inf"):
        return None
    if includes_open is True:
        return base
    if position_unreadable:
        return None
    upnl = _sum_open_pnl(records)
    if upnl is None:
        return None
    return base + upnl


def circuit_of(pnl: Optional[float], pause: float, stop: float) -> str:
    """``tripped``, ``paused``, ``ok``, or ``unconfirmed`` when pnl is unread."""
    if pnl is None:
        return "unconfirmed"
    if pnl <= -abs(stop):
        return "tripped"
    if pnl <= -abs(pause):
        return "paused"
    return "ok"


# A chase toward the market. 0.25% is the ceiling. Half an ATR can only
# make it smaller. A configured 3% does not raise it: that was enough to
# walk a resting limit through the spread.
_CHASE_PCT_CAP = 0.0025


def chase_room(price: float, atr: Any, configured_pct: Any = None) -> float:
    """How far, in price, a limit may move toward the market.

    The smaller of 0.25% of ``price`` and half an ATR. A configured percent
    can lower that, not raise it.
    """
    if not price > 0:
        return 0.0
    cap = _CHASE_PCT_CAP
    try:
        if configured_pct is not None:
            cap = min(cap, float(configured_pct) / 100.0)
    except (TypeError, ValueError):
        cap = _CHASE_PCT_CAP
    if cap < 0:
        cap = 0.0
    room = price * cap
    try:
        atr_f = float(atr)
    except (TypeError, ValueError):
        atr_f = 0.0
    if atr_f > 0:
        room = min(room, 0.5 * atr_f)
    return room


def _is_maker(side: str, px: float, bid: float, ask: float, tick: float) -> bool:
    """One tick inside the near side, and not at the best opposite price."""
    if side == "short":
        return px > bid and px >= ask + tick
    return px < ask and px <= bid - tick


def quote_entry(side: str, entry: Any, bid: Any, ask: Any, tick: Any,
                atr: Any = None, last: Any = None, configured_pct: Any = None) -> tuple:
    """A maker limit, or ``(None, why)``.

    The entry may move toward ``last`` by at most :func:`chase_room`. The
    price that is sent has to stay strictly off the best opposite price
    (the ask for a buy, the bid for a sell) and at least one tick inside
    the near side. A move that would cross is not sent: the original price
    is kept when that price is already a maker, and otherwise the order
    is cancelled.
    """
    try:
        px = float(entry)
        bid_f = float(bid)
        ask_f = float(ask)
        tick_f = float(tick)
    except (TypeError, ValueError):
        return None, "book_or_tick_unread"
    if not (px > 0 and bid_f > 0 and ask_f > 0 and tick_f > 0 and ask_f > bid_f):
        return None, "book_or_tick_unread"
    room = chase_room(px, atr, configured_pct)
    side_l = "short" if str(side).lower() == "short" else "long"
    chased = px
    try:
        last_f = float(last) if last is not None else None
    except (TypeError, ValueError):
        last_f = None
    if last_f is not None and last_f > 0:
        if side_l == "short" and last_f < px:
            chased = max(px - room, last_f)
        elif side_l == "long" and last_f > px:
            chased = min(px + room, last_f)
    if _is_maker(side_l, chased, bid_f, ask_f, tick_f):
        return chased, ""
    if chased != px and _is_maker(side_l, px, bid_f, ask_f, tick_f):
        return px, ""
    return None, "would_cross"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _extract_rows(value: Any, depth: int = 0) -> list:
    """Recursively locate a list of record dicts (each carrying a 'symbol') inside
    a varied SDK result envelope. The unfiltered pending_orders() result nests its
    rows in a shape the old flat .get('data'/'list') parse missed -> the live DBG
    showed manage_open_state seeing zero pending orders that actually existed (pT0).
    This finds the row list wherever it is."""
    if depth > 6:
        return []
    if isinstance(value, (list, tuple)):
        recs = [m for m in (_to_mapping(x) for x in value) if m]
        if recs and all("symbol" in r for r in recs):
            return recs
        out = []
        for item in value:
            out.extend(_extract_rows(item, depth + 1))
        return out
    mapping = _to_mapping(value)
    if not mapping:
        return []
    for key in ("data", "list", "orders", "rows", "records", "result", "items",
                "entrustedList", "entrusted_list", "orderList", "raw"):
        if key in mapping:
            found = _extract_rows(mapping[key], depth + 1)
            if found:
                return found
    for nested in mapping.values():
        if isinstance(nested, (list, dict)):
            found = _extract_rows(nested, depth + 1)
            if found:
                return found
    return []


_ORDER_ID_KEYS = ("orderId", "order_id", "clientOid", "client_oid",
                  "openOrderId", "open_order_id", "entryOrderId")


def _manifest_symbols(cfg: dict) -> set:
    """Symbols this playbook is allowed to touch. An empty manifest touches nothing."""
    raw = cfg.get("trading_symbols") if isinstance(cfg, dict) else None
    if not isinstance(raw, (list, tuple)):
        return set()
    return {str(s).strip().upper() for s in raw if str(s).strip()}


def _known_order_ids(state: Optional[dict]) -> set:
    """Order ids this playbook recorded when it placed them. Unreadable state is none."""
    if not isinstance(state, dict):
        return set()
    ids = set()
    for row in state.get("orders") or []:
        if isinstance(row, dict) and row.get("order_id") not in (None, ""):
            ids.add(str(row["order_id"]))
    return ids


def _record_ids(record: dict) -> set:
    found = set()
    for key in _ORDER_ID_KEYS:
        if record.get(key) not in (None, ""):
            found.add(str(record[key]))
    return found


def _is_ours(record: dict, ids: set, symbols: set) -> bool:
    """True only when the record is one of our order ids on a manifest symbol.

    Notional is not ownership. A position with no order id is not ours.
    """
    if not ids or not symbols:
        return False
    symbol = _find_string(record, ("symbol",)).upper()
    if symbol not in symbols:
        return False
    return bool(_record_ids(record) & ids)


def confirm_subaccount() -> dict:
    """Whether the runner's GetAgent sub-account can be confirmed.

    Fail closed. ``subaccount_exists()`` must be true and ``total_value()``
    must read a finite number. A throw, a false, or an unreadable value
    refuses. The account id is not stored and not returned.
    """
    out = {"confirmed": False, "exists": None, "total_value": None, "reason": "subaccount_not_confirmed"}
    try:
        exists = trade.account.subaccount_exists()
    except Exception as exc:
        out["reason"] = "subaccount_exists_" + type(exc).__name__
        return out
    ok = _exists_true(exists)
    out["exists"] = ok
    if not ok:
        out["reason"] = "subaccount_not_confirmed"
        return out
    equity = _account_equity()
    if equity.get("equity") is None:
        out["reason"] = "total_value_unread"
        return out
    out["total_value"] = equity["equity"]
    out["equity_includes_open_pnl"] = equity["includes_open_pnl"]
    out["equity_field"] = equity["field"]
    out["confirmed"] = True
    out["reason"] = ""
    return out


def _exists_true(value: Any) -> bool:
    if value is True:
        return True
    mapping = _to_mapping(value) or {}
    flag = mapping.get("exists", mapping.get("subaccount_exists"))
    if flag is True:
        return True
    if isinstance(flag, str) and flag.strip().lower() in ("true", "1", "yes"):
        return True
    nested = mapping.get("data")
    nested_map = _to_mapping(nested)
    if nested_map is not None:
        return _exists_true(nested_map)
    return False


def read_top_of_book(symbol: str) -> dict:
    """Best bid, best ask and the price tick. Unread fields stay None.

    Last price is not a bid. A book that only has a last price is unread.
    """
    feats = features.fetch_symbol(symbol)
    tick = None
    try:
        rules = trade.helpers.contract_rules(symbol)
        step = getattr(rules, "price_step", None)
        if step not in (None, "", 0, "0"):
            tick = float(step)
            if not tick > 0:
                tick = None
    except Exception:
        tick = None
    return {"bid": feats.bid_price, "ask": feats.ask_price, "tick": tick}


def rests_as_maker(side: str, entry: Any, bid: Any, ask: Any, tick: Any) -> tuple:
    """A limit that cannot cross. Fail closed when the book or the tick is unread.

    The contract SDK has no post-only flag. A long is placed only at least one
    tick below the best bid; a short only at least one tick above the best ask.
    """
    try:
        px = float(entry)
        bid_f = float(bid)
        ask_f = float(ask)
        tick_f = float(tick)
    except (TypeError, ValueError):
        return False, "book_or_tick_unread"
    if not (px > 0 and bid_f > 0 and ask_f > 0 and tick_f > 0):
        return False, "book_or_tick_unread"
    if str(side).lower() == "short":
        if px >= ask_f + tick_f:
            return True, ""
        return False, "would_take"
    if px <= bid_f - tick_f:
        return True, ""
    return False, "would_take"


def _shape(value: Any, depth: int = 0) -> str:
    """Compact structural description of a result envelope, recursing one level
    into 'data', so a parse miss (rows present) vs an empty payload is visible."""
    if depth > 3:
        return "."
    mapping = _to_mapping(value)
    if mapping is not None:
        ks = ";".join(str(k) for k in list(mapping.keys())[:4])
        if "data" in mapping and depth < 2:
            return ks + ">(" + _shape(mapping["data"], depth + 1) + ")"
        return ks
    if isinstance(value, (list, tuple)):
        if not value:
            return "L0"
        first = _to_mapping(value[0])
        inner = (";".join(str(k) for k in list(first.keys())[:4]) if first
                 else type(value[0]).__name__)
        return "L{}:{}".format(len(value), inner)
    return type(value).__name__


def manage_open_state(cfg: dict) -> dict:
    actions: list = []
    status = {
        "circuit": "ok",
        "today_pnl": None,
        "open_count": 0,
        "open_symbols": [],
        "owned_symbols": [],
        "all_account_symbols": [],
        "controls_active": {"circuit_breaker": False, "time_stop": False, "auto_be": False},
        "actions": actions,
    }

    soft = float(cfg.get("circuit_pause_usdt", "30"))
    hard = float(cfg.get("circuit_stop_usdt", "40"))

    account = confirm_subaccount()
    status["subaccount_exists"] = account["exists"]
    status["total_value"] = account["total_value"]
    status["subaccount_confirmed"] = account["confirmed"]
    status["subaccount_reason"] = account["reason"]
    if not account["confirmed"]:
        # No order, no cancel, no close. The reason is the class name only.
        print("limit-scanner refused: " + str(account["reason"]))
        status["circuit"] = "unconfirmed"
        return status

    state = _read_state()
    if state is None:
        status["state_readable"] = False
        status["circuit"] = "unconfirmed"
        print("limit-scanner refused: state_unreadable")
        return status
    status["state_readable"] = True

    # --- live snapshot. Ownership is the ids in .state/, not the snapshot's size. ---
    try:
        positions = trade.contract.current_position()
        records = trade.helpers.contract_position_records(positions) or []
    except Exception as exc:
        status["position_query_error"] = type(exc).__name__
        records = []
    try:
        pending_raw = trade.contract.pending_orders()
    except Exception as exc:
        pending_raw = None
        status["pending_error"] = type(exc).__name__
    pending_records = _extract_rows(pending_raw) if pending_raw is not None else []
    status["pending_shape"] = _shape(pending_raw)[:40] if pending_raw is not None else "none"

    # Ownership is the order id we stored when we placed, on a manifest symbol.
    # A notional cap is not a claim. Unreadable state was already returned.
    ids = _known_order_ids(state)
    symbols = _manifest_symbols(cfg)
    status["known_orders"] = len(ids)
    owned_position_records = [r for r in records if _is_ours(r, ids, symbols)]
    owned_pending_records = [r for r in pending_records if _is_ours(r, ids, symbols)]
    status["_position_records"] = owned_position_records
    status["_pending_records"] = owned_pending_records
    pos_symbols = {_find_string(r, ("symbol",)).upper()
                   for r in owned_position_records if _find_string(r, ("symbol",))}
    pend_symbols = {_find_string(r, ("symbol",)).upper()
                    for r in owned_pending_records if _find_string(r, ("symbol",))}
    owned = pos_symbols | pend_symbols

    status["all_account_symbols"] = sorted({_find_string(r, ("symbol",)).upper()
                                            for r in records if _find_string(r, ("symbol",))})
    status["owned_symbols"] = sorted(owned)
    # Resting limits AND filled positions both count toward the concurrency cap,
    # so "max N" means N total commitments (resting + filled). (v0.1.12/0.1.14)
    status["open_symbols"] = sorted(owned)
    status["open_count"] = len(owned)
    status["filled_symbols"] = sorted(pos_symbols)
    # Diagnostic counts: total live pending vs the subset we recognise as ours.
    status["pending_total"] = len(pending_records)
    status["owned_pending"] = len(owned_pending_records)
    status["ran"] = True

    # Equity for the breaker. An inclusive field already holds open PnL.
    # A wallet field does not: add it, and refuse when a position's PnL
    # cannot be read. Missing open PnL is not zero.
    status["equity_includes_open_pnl"] = account.get("equity_includes_open_pnl")
    equity = breaker_equity(
        account["total_value"], account.get("equity_includes_open_pnl"),
        records, bool(status.get("position_query_error")))
    if equity is None:
        print("limit-scanner refused: open_pnl_unread")
        status["circuit"] = "unconfirmed"
        status["open_pnl_counted"] = False
        return status
    status["total_value"] = equity
    status["open_pnl_counted"] = True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("date") != today or "day_start_equity" not in state:
        state["date"] = today
        state["day_start_equity"] = equity
    today_pnl = equity - float(state.get("day_start_equity", equity))
    status["today_pnl"] = round(today_pnl, 4)
    status["controls_active"]["circuit_breaker"] = True
    status["circuit"] = circuit_of(today_pnl, soft, hard)
    state["last_equity"] = equity
    open_ids = set()
    for record in owned_position_records + owned_pending_records:
        open_ids |= _record_ids(record)
    status["open_risk_usdt"] = _stored_open_risk(state, open_ids)
    _write_state(state)

    # Reads only. Cancels, closes and stop changes run in apply_management,
    # which the live entry calls from inside the follow callback.
    return status


def apply_management(cfg: dict, status: dict) -> dict:
    """Cancel, close and move stops. Only our order ids, only manifest symbols.

    Caller must be the ``emit_signal_or_follow`` callback. A sub-account that
    was not confirmed, or a book that was not readable, is left untouched.
    """
    actions = status.setdefault("actions", [])
    if not status.get("subaccount_confirmed") or status.get("state_readable") is False:
        return status
    state = _read_state()
    ids = _known_order_ids(state)
    symbols = _manifest_symbols(cfg)
    if state is None or not ids or not symbols:
        return status
    positions = [r for r in (status.get("_position_records") or []) if _is_ours(r, ids, symbols)]
    pending = [r for r in (status.get("_pending_records") or []) if _is_ours(r, ids, symbols)]
    if status.get("circuit") == "tripped":
        _flatten_owned(cfg, positions, pending, actions)
        return status
    _best_effort_position_controls(cfg, positions, status, actions)
    _best_effort_limit_expiry(cfg, pending, actions, status)
    return status


def remember_order(order_id: str, symbol: str, side: str, risk_usdt: Any = None) -> bool:
    """Record an order this playbook just placed. No id, nothing is recorded.

    ``risk_usdt`` is the loss at the stop. A later pass needs it so the
    circuit stop is the sum of open risk, not a guess.
    """
    if not order_id or not symbol:
        return False
    state = _read_state()
    if state is None:
        return False
    orders = [o for o in (state.get("orders") or []) if isinstance(o, dict)]
    row = {"order_id": str(order_id), "symbol": str(symbol).upper(), "side": str(side)}
    if isinstance(risk_usdt, (int, float)) and not isinstance(risk_usdt, bool) and risk_usdt >= 0:
        row["risk_usdt"] = float(risk_usdt)
    orders.append(row)
    state["orders"] = orders[-200:]
    return _write_state(state)


def _stored_open_risk(state: Optional[dict], open_ids: set) -> Optional[float]:
    """USDT at risk on the ids still open. Unknown risk is None, not zero."""
    if not isinstance(state, dict):
        return None
    if not open_ids:
        return 0.0
    rows = {}
    for row in state.get("orders") or []:
        if isinstance(row, dict) and row.get("order_id") not in (None, ""):
            rows[str(row["order_id"])] = row
    total = 0.0
    for oid in open_ids:
        row = rows.get(str(oid))
        raw = None if row is None else row.get("risk_usdt")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0 or raw != raw:
            return None
        total += float(raw)
    return total


def placed_order_id(result: Any) -> str:
    """The exchange order id on a placement result, or empty when it was not there."""
    for attr in ("order_id", "orderId"):
        value = getattr(result, attr, None)
        if value not in (None, ""):
            return str(value)
    mapping = _to_mapping(getattr(result, "raw", None)) or _to_mapping(result) or {}
    found = _find_string(mapping, _ORDER_ID_KEYS)
    if found:
        return found
    for key in ("data", "result"):
        nested = _to_mapping(mapping.get(key)) if isinstance(mapping, dict) else None
        if nested:
            found = _find_string(nested, _ORDER_ID_KEYS)
            if found:
                return found
    return ""


def _flatten_owned(cfg: dict, owned_position_records: list, owned_pending_records: list,
                   actions: list) -> None:
    """Circuit hard-stop: cancel and close only records whose order id this
    playbook stored. A position with no stored id is left alone."""
    for rec in owned_pending_records:
        symbol = _find_string(rec, ("symbol",))
        order_id = _find_string(rec, ("orderId", "order_id", "clientOid"))
        if symbol and order_id:
            try:
                trade.contract.cancel_order(symbol=symbol, order_id=order_id)
                actions.append({"circuit_cancel": symbol})
            except Exception:
                pass

    for record in owned_position_records:
        symbol = _find_string(record, ("symbol",))
        hold_side = _find_string(record, _HOLD_SIDE_KEYS)
        if symbol and hold_side:
            try:
                trade.contract.close_position(symbol=symbol, hold_side=hold_side)
                actions.append({"circuit_close": symbol})
            except Exception:
                pass


def _best_effort_position_controls(cfg: dict, records: list, status: dict, actions: list) -> None:
    max_age_h = float(cfg.get("time_stop_hours", "4"))
    be_trigger_pct = float(cfg.get("breakeven_pct", "2.0")) / 100.0
    now_ms = _now_ms()

    for record in records:
        symbol = _find_string(record, ("symbol",))
        hold_side = _find_string(record, _HOLD_SIDE_KEYS)
        if not symbol or not hold_side:
            continue

        # Intraday time-stop (best effort: requires a parseable open time).
        open_ms = _find_number(record, _OPEN_TIME_KEYS)
        if open_ms is not None and open_ms > 0:
            age_h = (now_ms - open_ms) / 3_600_000.0
            if 0 < age_h <= 240 and age_h >= max_age_h:
                try:
                    trade.contract.close_position(symbol=symbol, hold_side=hold_side)
                    actions.append({"time_stop_close": symbol, "age_h": round(age_h, 2)})
                    status["controls_active"]["time_stop"] = True
                    continue
                except Exception:
                    pass

        # Auto-breakeven (best effort: requires entry price + current price).
        entry = _find_number(record, _ENTRY_PRICE_KEYS)
        if entry is None or entry <= 0:
            continue
        try:
            current = float(trade.helpers.contract_price(symbol))
        except Exception:
            continue
        is_long = hold_side.lower() in ("long", "buy")
        move_pct = (current - entry) / entry if is_long else (entry - current) / entry
        # One trigger, the percent. A dollar trigger is a different percent
        # at every notional ($20 on 1500 is 1.33%, not the stated 2%).
        if move_pct >= be_trigger_pct:
            _move_stop_to_breakeven(symbol, entry, actions, status)


def _move_stop_to_breakeven(symbol: str, entry: float, actions: list, status: dict) -> None:
    try:
        rules = trade.helpers.contract_rules(symbol)
        step = getattr(rules, "price_step", None)
        plan = trade.contract.plan_pending_orders(symbol=symbol)
        sl = trade.helpers.select_sl_plan_order(plan, symbol=symbol)
        order_id = getattr(sl, "order_id", "")
        if not order_id:
            return
        trade.contract.modify_stop_loss(symbol=symbol, order_id=order_id, trigger_price=_align(entry, step))
        actions.append({"auto_be": symbol})
        status["controls_active"]["auto_be"] = True
    except Exception:
        pass


def _best_effort_limit_expiry(cfg: dict, owned_pending_records: list, actions: list, status: dict) -> None:
    """Cancel stale resting limits this playbook stored -- either past the time
    budget (``limit_expiry_hours``) or left behind when price ran more than
    ``stale_limit_pct`` away in the direction the limit can never fill from.
    That distance is not a chase toward the market. Chasing uses
    :func:`chase_room`, which cannot walk the price through the spread.
    Operates only on the pre-scoped order records. (v0.1.13/0.1.14)"""
    max_age_h = float(cfg.get("limit_expiry_hours", "4"))
    chase_pct = float(cfg.get("stale_limit_pct", "3.0")) / 100.0
    now_ms = _now_ms()
    for record in owned_pending_records:
        symbol = _find_string(record, ("symbol",))
        order_id = _find_string(record, ("orderId", "order_id", "clientOid"))
        if not symbol or not order_id:
            continue

        # 1) Time-based expiry.
        created = _find_number(record, _OPEN_TIME_KEYS)
        if created and created > 0:
            age_h = (now_ms - created) / 3_600_000.0
            if max_age_h <= age_h <= 240:
                # v0.1.19: the create_time read works (v0.1.18 verified live), but
                # the cancel itself was failing silently -> act0 on an aged order.
                # Capture WHY -- thrown exception OR a rejected envelope -- into
                # status so the readable DBG surfaces it instead of a blank act0.
                try:
                    res = trade.contract.cancel_order(symbol=symbol, order_id=order_id)
                    if trade.is_success(res):
                        actions.append({"limit_expiry_cancel": symbol, "age_h": round(age_h, 2)})
                    else:
                        status["expiry_err"] = ("rej:" + _result_reason(res))[:36]
                    continue
                except Exception as exc:
                    status["expiry_err"] = ("exc:" + _exc_brief(exc))[:36]
                    continue

        # 2) Left behind: the market has run away from the limit, so the
        # pullback it was waiting for is gone. This cancels. It does not
        # reprice toward the market.
        if chase_pct <= 0:
            continue
        entry_price = _find_number(record, ("price", "orderPrice", "limitPrice", "executePrice"))
        if entry_price is None or entry_price <= 0:
            continue
        side = (_find_string(record, ("side",)) + " "
                + _find_string(record, ("posSide", "holdSide", "tradeSide"))).lower()
        try:
            current = float(trade.helpers.contract_price(symbol))
        except Exception:
            continue
        if not current or current <= 0:
            continue
        is_short = ("sell" in side) or ("short" in side)
        gap = (entry_price - current) / entry_price if is_short else (current - entry_price) / entry_price
        if gap > chase_pct:
            try:
                trade.contract.cancel_order(symbol=symbol, order_id=order_id)
                actions.append({"stale_limit_cancel": symbol, "gap_pct": round(gap * 100, 2)})
            except Exception:
                pass


def _exc_brief(exc: Exception) -> str:
    """Compact exception *message* (not just the class name) so a real SDK or
    exchange validation cause surfaces in the diagnostic instead of a bare type."""
    msg = str(exc).strip().replace("\n", " ").replace(",", ";")
    return (msg or type(exc).__name__)[:80]


def open_if_allowed(decision: dict, cfg: dict, mgmt: dict) -> dict:
    plan = decision.get("plan") or {}
    symbol = str(decision.get("symbol", ""))
    side = str(plan.get("side", "long"))
    if not symbol or not plan:
        return {"placed": False, "reason": "incomplete_plan"}

    if not mgmt.get("subaccount_confirmed"):
        return {"placed": False, "reason": "subaccount_unconfirmed"}

    if mgmt.get("circuit") in ("paused", "tripped", "unconfirmed"):
        return {"placed": False, "reason": "circuit_" + str(mgmt.get("circuit"))}

    open_count = int(mgmt.get("open_count", 0) or 0)
    open_symbols = [str(s).upper() for s in (mgmt.get("open_symbols") or [])]
    max_concurrent = int(cfg.get("max_concurrent", 3))
    if open_count >= max_concurrent:
        return {"placed": False, "reason": "max_concurrent_reached", "open_count": open_count}

    # Rule 7 correlation budget: treat every open alt as BTC-correlated; tighten
    # to a single fresh slot whenever BTC or ETH is already held.
    max_corr = int(cfg.get("max_correlated_alts", 2))
    if any(s in ("BTCUSDT", "ETHUSDT") for s in open_symbols):
        max_corr = min(max_corr, 1)
    if symbol.upper() not in open_symbols and len(open_symbols) >= max_corr:
        return {"placed": False, "reason": "correlation_budget", "open_symbols": open_symbols}

    leverage = max(int(cfg.get("leverage", 10)), 1)
    entry = plan.get("entry")
    sl_price = plan.get("sl_price")
    tp1 = plan.get("tp1")
    margin = plan.get("margin_usdt")
    if entry is None or sl_price is None or tp1 is None or not margin:
        return {"placed": False, "reason": "incomplete_plan"}

    # Duplicate guard: skip if already in a position or already resting an entry.
    # Best-effort ONLY -- a parse/type error here must never block an entry. The
    # v0.1.9 diagnostic proved find_contract_position raises TypeError on flat
    # hedge-mode slots (size returned as the string "0"), and the old
    # except-branch converted that into a hard skip that blocked 100% of orders.
    # count_open_contract_positions normalizes those shapes; on any error we
    # proceed and rely on max_concurrent + the exchange as backstops.
    try:
        pos_result = trade.contract.current_position(symbol=symbol)
        in_position = trade.helpers.count_open_contract_positions(pos_result, symbol=symbol) > 0
    except Exception:
        in_position = False
    if in_position:
        return {"placed": False, "reason": "already_in_position"}
    try:
        existing = trade.helpers.select_contract_order(trade.contract.pending_orders(symbol=symbol), symbol=symbol)
    except Exception:
        existing = None
    if existing is not None and getattr(existing, "order_id", ""):
        return {"placed": False, "reason": "entry_already_pending"}

    try:
        step = getattr(trade.helpers.contract_rules(symbol), "price_step", None)
    except Exception:
        step = None
    entry_price = _align(entry, step)
    tp1_price = _align(tp1, step)
    sl_price_aligned = _align(sl_price, step)

    try:
        qty_plan = trade.helpers.compute_qty(
            symbol=symbol, market="contract", budget_amount=str(margin),
            leverage=leverage, price=str(entry_price),
        )
    except Exception as exc:
        return {"placed": False, "reason": "compute_qty_error:" + _exc_brief(exc)}

    # Pass tick-aligned TP/SL trigger prices, and surface the real validation
    # text (not just the exception class) so any reject reason is actionable.
    try:
        tpsl = trade.helpers.resolve_contract_tpsl(
            symbol=symbol, side=side, leverage=leverage,
            tp_trigger_price=tp1_price, sl_trigger_price=sl_price_aligned,
            reference_price=str(entry_price),
        )
    except Exception as exc:
        return {"placed": False, "reason": "tpsl_error:" + _exc_brief(exc)}

    book = read_top_of_book(symbol)
    quoted, why = quote_entry(
        side, entry_price, book["bid"], book["ask"], book["tick"],
        atr=plan.get("atr"), last=plan.get("last"),
        configured_pct=cfg.get("limit_chase_pct", "0.25"))
    if quoted is None:
        return {"placed": False, "reason": why, "symbol": symbol, "side": side}
    entry_price = _align(quoted, book["tick"])
    maker, why = rests_as_maker(side, entry_price, book["bid"], book["ask"], book["tick"])
    if not maker:
        return {"placed": False, "reason": why, "symbol": symbol, "side": side}

    opener = trade.contract.open_short_limit if side == "short" else trade.contract.open_long_limit
    kwargs = {
        "symbol": symbol, "qty": qty_plan.qty, "price": entry_price, "leverage": leverage,
        "tp_trigger_price": tpsl.tp_trigger_price, "sl_trigger_price": tpsl.sl_trigger_price,
    }
    margin_mode = "unspecified"
    try:
        import inspect
        accepted = inspect.signature(opener).parameters
    except (TypeError, ValueError):
        accepted = {}
    if "margin_mode" in accepted:
        kwargs["margin_mode"] = "isolated"
        margin_mode = "isolated"
    elif "marginMode" in accepted:
        kwargs["marginMode"] = "isolated"
        margin_mode = "isolated"
    try:
        result = opener(**kwargs)
    except Exception as exc:
        return {"placed": False, "reason": "open_raise:" + _exc_brief(exc),
                "symbol": symbol, "side": side,
                "qty": str(getattr(qty_plan, "qty", "")), "entry": str(entry_price)}

    placed = bool(trade.is_success(result))
    order_id = placed_order_id(result) if placed else ""
    remembered = remember_order(order_id, symbol, side, plan.get("risk_usdt")) if order_id else False
    out = {
        "placed": placed, "symbol": symbol, "side": side,
        "qty": str(getattr(qty_plan, "qty", "")), "entry": str(entry_price),
        "tp1": str(tpsl.tp_trigger_price), "sl": str(tpsl.sl_trigger_price),
        "margin_mode": margin_mode,
        "order_id_recorded": remembered,
    }
    if not placed:
        # Surface the exchange's own rejection so a fully-sized, fully-guarded
        # order that never rests on the book stops being a silent no-op.
        out["reason"] = "exchange_reject:" + _result_reason(result)
    elif not order_id:
        # Placed, but we cannot prove it later. Do not guess by size.
        out["reason"] = "order_id_unread"
    return out


def _align(price: Any, step: Any) -> str:
    try:
        quoted = Decimal(str(price))
    except Exception:
        return str(price)
    if step in (None, "", 0, "0"):
        return str(quoted)
    try:
        increment = Decimal(str(step))
        if increment <= 0:
            return str(quoted)
        return str((quoted / increment).to_integral_value(rounding=ROUND_DOWN) * increment)
    except Exception:
        return str(quoted)
