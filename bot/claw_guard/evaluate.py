"""Per-order checks. The Guard evaluates the action it would sign.

Unknown action types are refused. Wallet-only actions that this key cannot
see (a convert, a token approval, a transfer) get no verdict: those are
covered by L6 session-watch alerts (separate PR) or a wallet-level module.

``origin`` is a keyword the Guard sets. A tag on the action is not an origin.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from bot.claw_guard.network import endpoint_refusal
from bot.claw_guard.payload import order_hash
from bot.claw_guard.verdicts import ALLOW_TEXT, blocked, needs_ok

OUT_OF_SCOPE = (
    "covered by L6 session-watch alerts (separate PR) or a wallet-level module"
)

#: No verdict. The order path does not sign these.
_NO_VERDICT = frozenset({
    "convert", "negRiskConvert", "setApprovalForAll",
    "transfer", "erc1155Transfer",
})

_HL_TYPES = frozenset({
    "order", "modify", "batchModify", "twapOrder", "trailingStop", "trigger",
    "updateLeverage", "updateIsolatedMargin", "topUpIsolatedOnlyMargin",
    "cancel", "cancelByCloid", "cancelByOid", "scheduleCancel", "merge",
})

_PM_TYPES = frozenset({
    "order", "merge",
})

_LEVERAGE = frozenset({
    "updateLeverage", "updateIsolatedMargin", "topUpIsolatedOnlyMargin",
})

_CANCELS = frozenset({"cancel", "cancelByCloid", "cancelByOid", "scheduleCancel"})

_PRICE_ACTIONS = frozenset({
    "order", "modify", "batchModify", "twapOrder", "trailingStop", "trigger",
})

_DENY_REASON = "this key may only place and cancel orders."
_WINDOW_S = 600.0
_CLOSE_WINDOW_S = 300.0
_NEW_SIGNER_S = 86400.0


def evaluate(
    action: Mapping[str, Any],
    account: Mapping[str, Any],
    *,
    now: float,
    origin: str = "user",
) -> dict[str, Any]:
    """Every rule that fires. Block beats confirm. Otherwise allow."""
    kind = str(action.get("type") or "")
    if kind in _NO_VERDICT:
        return _bare(action, now, out_of_scope=OUT_OF_SCOPE)

    rules: list[dict[str, str]] = []
    venue = str(action.get("venue") or "")
    refusal = endpoint_refusal(action.get("endpoint"), action.get("chain_id"), action.get("chain"))
    if refusal:
        rules.append(_hit("NET", "block", refusal))

    allowed = _PM_TYPES if venue == "polymarket" else _HL_TYPES
    if venue not in ("hyperliquid", "polymarket") or kind not in allowed:
        rules.append(_hit("DENY", "block", _DENY_REASON))
        return _finish(rules, action, now)

    if _bad_scope(action, account):
        rules.append(_hit("SCOPE", "block", "this key may only trade on the order book."))

    acct = _resolve_account(action, account)
    if acct is None:
        rules.append(_hit("ACCT", "block", "that account could not be read."))
        return _finish(rules, action, now)

    if _deregistered(action, acct):
        rules.append(_hit("HL-6", "block", "this API key was replaced and can't be used again."))
        return _finish(rules, action, now)

    if kind in _LEVERAGE:
        rules.extend(_leverage_rules(action, acct))
        return _finish(rules, action, now)

    if kind in _CANCELS:
        rules.extend(_cancel_rules(action, acct, now))
        return _finish(rules, action, now)

    if kind == "merge":
        rules.extend(_exit_rules(action, acct, now, origin))
        return _finish(rules, action, now)

    legs = _legs(action)
    if not legs:
        rules.append(_hit("DENY", "block", _DENY_REASON))
        return _finish(rules, action, now)

    resolved_rows = [_resolve_leg(leg, action, acct) for leg in legs]
    if any(row is None for row in resolved_rows):
        rules.append(_hit("HL-9", "block", "that market could not be read."))
        return _finish(rules, action, now)
    resolved = [row for row in resolved_rows if row is not None]

    if venue == "polymarket":
        increasing = any(
            leg.get("side") == "buy" and action.get("complement") is not True and leg.get("complement") is not True
            for leg in resolved
        )
    else:
        increasing = _action_increases(resolved, acct)
    if _price_stale(resolved, acct, venue) and kind in _PRICE_ACTIONS:
        if increasing:
            rules.append(_hit("STALE", "block", "price data is older than 10 s."))
            return _finish(rules, action, now)
        rules.extend(_allowlist_rules(resolved, acct))
        rules.extend(_reduce_only_rules(resolved, acct))
        return _finish(rules, action, now)

    if venue == "polymarket":
        rules.extend(_pm_rules(action, resolved, acct, now, origin, increasing))
    else:
        rules.extend(_hl_rules(action, resolved, acct, now, origin, increasing, kind))
    return _finish(rules, action, now)


def _bare(action: Mapping[str, Any], now: float, *, out_of_scope: str) -> dict[str, Any]:
    return {
        "decision": None,
        "rules": [],
        "order_hash": order_hash(action),
        "ts": now,
        "text": "",
        "out_of_scope": out_of_scope,
    }


def _finish(rules: list[dict[str, str]], action: Mapping[str, Any], now: float) -> dict[str, Any]:
    blocks = [row for row in rules if row["level"] == "block"]
    confirms = [row for row in rules if row["level"] == "confirm"]
    ordered = blocks + confirms
    if blocks:
        decision = "BLOCK"
        text = blocks[0]["text"]
    elif confirms:
        decision = "CONFIRM"
        text = confirms[0]["text"]
    else:
        decision = "ALLOW"
        text = ALLOW_TEXT
    return {
        "decision": decision,
        "rules": ordered,
        "order_hash": order_hash(action),
        "ts": now,
        "text": text,
        "out_of_scope": None,
    }


def _hit(rule_id: str, level: str, reason: str) -> dict[str, str]:
    text = blocked(reason) if level == "block" else needs_ok(reason)
    return {"id": rule_id, "level": level, "text": text}


def _bad_scope(action: Mapping[str, Any], account: Mapping[str, Any]) -> bool:
    scope = str(action.get("scope") or "")
    if scope.lower() in {"combosrfq", "all"}:
        return True
    signer = _signer_row(action, account)
    if signer is None:
        return False
    owned = str(signer.get("scope") or "")
    return owned.lower() in {"combosrfq", "all"}


def _resolve_account(
    action: Mapping[str, Any], account: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    vault = action.get("vaultAddress") or action.get("vault_address")
    if vault is None or str(vault).strip() == "":
        return dict(account)
    subs = account.get("subaccounts")
    if not isinstance(subs, Mapping):
        return None
    sub = subs.get(vault) or subs.get(str(vault).lower())
    if not isinstance(sub, Mapping):
        return None
    merged = dict(account)
    merged.update(sub)
    if "markets" not in sub:
        merged["markets"] = account.get("markets")
    if "meta" not in sub:
        merged["meta"] = account.get("meta")
    if "signers" not in sub:
        merged["signers"] = account.get("signers")
    if "rules" not in sub:
        merged["rules"] = account.get("rules")
    return merged


def _signer_row(action: Mapping[str, Any], account: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    signer = action.get("signer")
    if signer is None:
        return None
    book = account.get("signers")
    if not isinstance(book, Mapping):
        return None
    row = book.get(signer)
    if row is None:
        row = book.get(str(signer).lower())
    if isinstance(row, Mapping):
        return row
    return None


def _deregistered(action: Mapping[str, Any], account: Mapping[str, Any]) -> bool:
    row = _signer_row(action, account)
    if row is not None and row.get("deregistered") is True:
        return True
    signer = str(action.get("signer") or "").lower()
    banned = account.get("deregistered_signers") or []
    return signer != "" and signer in {str(item).lower() for item in banned}


def _rules(account: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = account.get("rules")
    return raw if isinstance(raw, Mapping) else {}


def _legs(action: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = action.get("orders")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, Mapping)]
    return [action]


def _resolve_leg(
    leg: Mapping[str, Any],
    action: Mapping[str, Any],
    account: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    raw_meta = account.get("meta")
    meta: dict[Any, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    asset_id = leg.get("asset_id", action.get("asset_id"))
    if asset_id is not None:
        row = meta.get(asset_id)
        if row is None:
            row = meta.get(str(asset_id))
        if not isinstance(row, Mapping):
            return None
        return {
            "name": str(row.get("name") or ""),
            "kind": str(row.get("kind") or "perp"),
            "underlying": str(row.get("underlying") or row.get("name") or ""),
            "side": leg.get("side", action.get("side")),
            "size": _num(leg.get("size", action.get("size"))),
            "price": _num(leg.get("price", action.get("price"))),
            "trigger_px": _num(leg.get("trigger_px", action.get("trigger_px"))),
            "reduce_only": bool(leg.get("reduce_only", action.get("reduce_only"))),
            "tif": leg.get("tif", action.get("tif")),
        }
    name = str(leg.get("market") or action.get("market") or "")
    if not name:
        return None
    underlying = name.split(":", 1)[-1]
    if underlying.endswith("-PERP"):
        underlying = underlying[: -len("-PERP")]
    kind = "hip3" if ":" in name else "perp"
    return {
        "name": name,
        "kind": kind,
        "underlying": underlying,
        "side": leg.get("side"),
        "size": _num(leg.get("size")),
        "price": _num(leg.get("price")),
        "trigger_px": _num(leg.get("trigger_px")),
        "reduce_only": bool(leg.get("reduce_only")),
        "tif": leg.get("tif"),
    }


def _num(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _market_view(account: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    markets = account.get("markets")
    if not isinstance(markets, Mapping):
        return {}
    row = markets.get(name)
    return row if isinstance(row, Mapping) else {}


def _position(account: Mapping[str, Any], name: str) -> Optional[Mapping[str, Any]]:
    positions = account.get("positions")
    if not isinstance(positions, Mapping):
        return None
    row = positions.get(name)
    if not isinstance(row, Mapping):
        return None
    size = _num(row.get("size"))
    if size is None or size == 0:
        return None
    return row


def _mark(view: Mapping[str, Any]) -> Optional[float]:
    return _num(view.get("mark"))


def _price_stale(
    resolved: list[dict[str, Any]], account: Mapping[str, Any], venue: str,
) -> bool:
    limit = _num(_rules(account).get("stale_s"))
    if limit is None:
        limit = 10.0
    for leg in resolved:
        view = _market_view(account, leg["name"])
        if venue == "polymarket":
            if _num(view.get("mid")) is None and _num(view.get("best_bid")) is None:
                return True
            age = _num(view.get("mid_age_s"))
            if age is None:
                return True
            if age > limit:
                return True
            continue
        if _mark(view) is None:
            return True
        age = _num(view.get("mark_age_s"))
        if age is None or age > limit:
            return True
    return False


def _ref_price(leg: Mapping[str, Any], view: Mapping[str, Any]) -> Optional[float]:
    price = leg.get("price")
    if isinstance(price, float):
        return price
    trigger = leg.get("trigger_px")
    if isinstance(trigger, float):
        return trigger
    return _mark(view)


def _equity(account: Mapping[str, Any]) -> Optional[float]:
    return _num(account.get("equity_usd"))


def _fraction(notional: Optional[float], equity: Optional[float]) -> Optional[float]:
    if notional is None or equity is None:
        return None
    if equity == 0:
        return None if notional == 0 else float("inf")
    return notional / equity


def _action_increases(resolved: list[dict[str, Any]], account: Mapping[str, Any]) -> bool:
    for leg in resolved:
        if _leg_increases(leg, _position(account, leg["name"])):
            return True
    return False


def _held_size(position: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Size on a position row. None when the row's size cannot be read.

    A missing position is ``0.0``: there is no position, which is a known
    fact. An unreadable size is not turned into zero.
    """
    if position is None:
        return 0.0
    return _num(position.get("size"))


def _leg_increases(leg: Mapping[str, Any], position: Optional[Mapping[str, Any]]) -> bool:
    if leg.get("reduce_only"):
        return False
    side = leg.get("side")
    size = leg.get("size")
    if not isinstance(size, float):
        return True
    if position is None:
        return True
    pos_side = position.get("side")
    pos_size = _held_size(position)
    if pos_size is None:
        return True
    if side == "buy" and pos_side == "short" and size <= pos_size:
        return False
    if side == "sell" and pos_side == "long" and size <= pos_size:
        return False
    return True


def _pct(fraction: float) -> str:
    value = fraction * 100.0
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.1f}"


def _pct1(fraction: float) -> str:
    return f"{fraction * 100.0:.1f}"


def _pct_trim(fraction: float) -> str:
    value = fraction * 100.0
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.1f}"


def _qty(size: float) -> str:
    text = f"{size:.8f}".rstrip("0").rstrip(".")
    if "." not in text:
        return text + ".00"
    whole, frac = text.split(".", 1)
    if len(frac) < 2:
        frac = frac.ljust(2, "0")
    return whole + "." + frac


def _coin(leg: Mapping[str, Any]) -> str:
    return str(leg.get("underlying") or leg.get("name") or "")


def _gt(left: float, right: float) -> bool:
    return left > right + 1e-9


def _confirm_line(account: Mapping[str, Any]) -> float:
    rules = _rules(account)
    fixed = _num(rules.get("size_confirm_frac"))
    if fixed is None:
        fixed = 0.25
    days = _num(rules.get("history_days"))
    learned = _num(rules.get("learned_confirm_frac"))
    if days is not None and days >= 14 and learned is not None:
        return min(fixed, learned)
    return fixed


def _block_line(account: Mapping[str, Any]) -> float:
    value = _num(_rules(account).get("size_block_frac"))
    return 1.0 if value is None else value


def _hl_rules(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
    origin: str,
    increasing: bool,
    kind: str,
) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    rules.extend(_allowlist_rules(resolved, account))
    rules.extend(_builder_fee_rules(action, account))
    rules.extend(_reduce_only_rules(resolved, account))
    rules.extend(_flip_rules(action, resolved, account, now, kind))
    rules.extend(_size_rules(action, resolved, account, now, kind))
    rules.extend(_gross_rules(resolved, account))
    rules.extend(_effective_leverage(resolved, account))
    rules.extend(_price_rules(resolved, account))
    rules.extend(_sweep_rules(resolved, account))
    if kind == "twapOrder" and not _twap_allowed(resolved, account):
        rules.append(_hit("HL-7", "confirm", "a TWAP on this market is outside your list."))
    rules.extend(_new_signer_rules(action, resolved, account, now, increasing))
    rules.extend(_close_burst(action, resolved, account, now, origin))
    rules.extend(_shared_caps(account, now, increasing))
    return rules


def _pm_rules(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
    origin: str,
    increasing: bool,
) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    rules.extend(_pm_builder_rules(action, account))
    rules.extend(_pm_bounds(resolved, account))
    rules.extend(_pm_price(resolved, account))
    rules.extend(_pm_depth(resolved, account, increasing))
    rules.extend(_exit_rules(action, account, now, origin, resolved))
    rules.extend(_pm_new_key(action, resolved, account, now))
    rules.extend(_shared_caps(account, now, increasing))
    return rules


def _allowlist_rules(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    allow = _rules(account).get("allowlist")
    if not isinstance(allow, (list, tuple, set)):
        allow = ("BTC", "ETH")
    names = {str(item) for item in allow}
    hits = []
    for leg in resolved:
        kind = leg["kind"]
        name = leg["name"]
        if name not in names or (kind in {"spot", "hip3", "outcome"} and name not in names):
            hits.append(_hit("HL-9", "block", f"{name} is not on your list."))
    return hits


def _builder_fee_rules(
    action: Mapping[str, Any], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    builder = action.get("builder")
    if not isinstance(builder, Mapping):
        return []
    allow = _rules(account).get("builder_allowlist")
    allowed = {str(item).lower() for item in allow} if isinstance(allow, (list, tuple, set)) else set()
    addresses = []
    head = builder.get("b")
    if head is not None and str(head).strip() != "":
        addresses.append(str(head))
    subs = builder.get("s")
    if isinstance(subs, list):
        for row in subs:
            if isinstance(row, Mapping) and row.get("b") is not None:
                addresses.append(str(row.get("b")))
    hits = []
    for addr in addresses:
        if addr.lower() not in allowed:
            hits.append(_hit("HL-5", "block", "builder fee goes to an address not on your list."))
            break
    fee = builder.get("f")
    if isinstance(fee, bool) or not isinstance(fee, (int, float)):
        return hits
    user_max = _num(_rules(account).get("builder_max_f"))
    if user_max is None:
        user_max = 0.0
    chain_cap = _num(account.get("max_builder_fee_f"))
    perp_cap = 100.0
    effective = min(user_max, perp_cap)
    if chain_cap is not None:
        effective = min(effective, chain_cap)
    if _gt(float(fee), effective):
        hits.append(_hit(
            "HL-5", "block",
            f"builder fee {_bps(float(fee))} bps, your max is {_bps(effective)} bps.",
        ))
    return hits


def _bps(tenths: float) -> str:
    bps = tenths / 10.0
    if abs(bps - round(bps)) < 1e-9:
        return str(int(round(bps)))
    return f"{bps:.1f}"


def _pm_builder_rules(
    action: Mapping[str, Any], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    code = action.get("builder")
    if code is None or str(code).strip() == "":
        return []
    text = str(code).lower()
    if text in {"0", "0x", "0x" + "0" * 64}:
        return []
    allow = _rules(account).get("pm_builder_allowlist")
    allowed = {str(item).lower() for item in allow} if isinstance(allow, (list, tuple, set)) else set()
    if text not in allowed:
        return [_hit("PM-7", "block", "builder code is not on your list.")]
    return []


def _reduce_only_rules(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    hits = []
    for leg in resolved:
        if not leg.get("reduce_only"):
            continue
        position = _position(account, leg["name"])
        side = leg.get("side")
        if position is None:
            hits.append(_hit("HL-4", "block", "reduce-only orders may not exceed your position."))
            continue
        pos_side = position.get("side")
        wrong = (side == "buy" and pos_side == "long") or (side == "sell" and pos_side == "short")
        if wrong:
            hits.append(_hit("HL-4", "block", "reduce-only orders may not exceed your position."))
    return hits


def _order_fraction(leg: Mapping[str, Any], account: Mapping[str, Any]) -> Optional[float]:
    view = _market_view(account, leg["name"])
    price = _ref_price(leg, view)
    size = leg.get("size")
    if not isinstance(size, float) or price is None:
        return None
    return _fraction(size * price, _equity(account))


def _exposure_fraction(
    action: Mapping[str, Any],
    leg: Mapping[str, Any],
    account: Mapping[str, Any],
    kind: str,
) -> Optional[float]:
    """Notional this action adds, as a fraction of equity.

    A new order is its own notional. A same-side modify includes the open
    position, because the resting order is replaced by a larger one. A flip
    counts only the size that opens the other side.
    """
    view = _market_view(account, leg["name"])
    price = _ref_price(leg, view)
    size = leg.get("size")
    equity = _equity(account)
    if not isinstance(size, float) or price is None:
        return None
    position = _position(account, leg["name"])
    pos_size = _held_size(position)
    if pos_size is None:
        return None
    pos_side = position.get("side") if position else None
    side = leg.get("side")
    flips = (
        position is not None
        and not leg.get("reduce_only")
        and ((side == "sell" and pos_side == "long") or (side == "buy" and pos_side == "short"))
        and size > pos_size
    )
    if flips:
        opened = size - pos_size
        return _fraction(opened * price, equity)
    if kind in {"modify", "batchModify"} and position is not None and side == _same_side(pos_side):
        return _fraction((size + pos_size) * price, equity)
    return _fraction(size * price, equity)


def _same_side(pos_side: object) -> str:
    return "buy" if pos_side == "long" else "sell"


def _size_rules(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
    kind: str,
) -> list[dict[str, str]]:
    hits = []
    confirm_at = _confirm_line(account)
    block_at = _block_line(account)
    fractions = []
    for leg in resolved:
        if not _leg_increases(leg, _position(account, leg["name"])):
            fractions.append(0.0)
            continue
        frac = _exposure_fraction(action, leg, account, kind)
        if frac is None:
            hits.append(_hit("HL-1", "block", "that order's size could not be read."))
            return hits
        fractions.append(frac)
    batch = isinstance(action.get("orders"), list) and len(action.get("orders") or []) > 1
    total = sum(fractions)
    if batch and _gt(total, block_at):
        hits.append(_hit(
            "HL-1", "block",
            f"this batch would use {_pct(total)}% of account value (max 100%).",
        ))
    elif not batch:
        for leg, frac in zip(resolved, fractions):
            flips = _opened_size(leg, _position(account, leg["name"])) is not None
            if _gt(frac, block_at):
                hits.append(_hit(
                    "HL-1", "block",
                    f"one order may use at most 100% of account value (this one: {_pct(frac)}%).",
                ))
            elif _gt(frac, confirm_at) and not flips:
                hits.append(_hit(
                    "HL-1", "confirm",
                    f"one order would use {_pct(frac)}% of account value (above {_pct(confirm_at)}%).",
                ))
    elif _gt(total, confirm_at):
        hits.append(_hit(
            "HL-1", "confirm",
            f"one order would use {_pct(total)}% of account value (above {_pct(confirm_at)}%).",
        ))
    hits.extend(_rolling(account, now, total if _gt(total, 0) else 0.0, block_at))
    return hits


def _rolling(
    account: Mapping[str, Any], now: float, this_fraction: float, block_at: float,
) -> list[dict[str, str]]:
    equity = _equity(account)
    if equity is None or equity == 0 or not _gt(this_fraction, 0):
        return []
    window = _num(_rules(account).get("rolling_window_s"))
    if window is None:
        window = _WINDOW_S
    recent = account.get("recent_orders")
    if not isinstance(recent, list):
        recent = []
    total_notional = this_fraction * equity
    for row in recent:
        if not isinstance(row, Mapping):
            continue
        if row.get("risk_increasing") is not True:
            continue
        ts = _num(row.get("ts"))
        notional = _num(row.get("notional_usd"))
        if ts is None or notional is None:
            continue
        if now - ts <= window:
            total_notional += notional
    fraction = total_notional / equity
    if _gt(fraction, block_at):
        return [_hit(
            "HL-1", "block",
            f"orders in the last 10 min would use {_pct(fraction)}% of account value (max 100%).",
        )]
    return []


def _gross_rules(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    equity = _equity(account)
    cap = _num(_rules(account).get("gross_underlying_frac"))
    if cap is None:
        cap = 1.0
    if equity is None:
        return []
    hits = []
    seen = set()
    for leg in resolved:
        if not _leg_increases(leg, _position(account, leg["name"])):
            continue
        coin = _coin(leg)
        if coin in seen:
            continue
        seen.add(coin)
        view = _market_view(account, leg["name"])
        price = _ref_price(leg, view)
        size = leg.get("size")
        if price is None or not isinstance(size, float):
            continue
        base = _underlying_notional(account, coin)
        position = _position(account, leg["name"])
        mark = _mark(view)
        pos_size = _held_size(position)
        if pos_size is None:
            continue
        if position is not None and mark is not None:
            base -= abs(pos_size * mark)
        opened = _opened_size(leg, position)
        if opened is not None:
            added = opened * price
        elif position is not None and leg.get("side") == _same_side(position.get("side")):
            added = (pos_size + size) * price
        else:
            added = size * price
        fraction = _fraction(base + added, equity)
        if fraction is not None and _gt(fraction, cap):
            hits.append(_hit(
                "HL-1", "block",
                f"total {coin} exposure {_pct(fraction)}% of account value (max 100%).",
            ))
    return hits


def _underlying_notional(account: Mapping[str, Any], coin: str) -> float:
    positions = account.get("positions")
    if not isinstance(positions, Mapping):
        return 0.0
    total = 0.0
    for name, row in positions.items():
        if not isinstance(row, Mapping):
            continue
        underlying = str(row.get("underlying") or name)
        if underlying != coin:
            continue
        size = _num(row.get("size"))
        view = _market_view(account, str(name))
        mark = _mark(view)
        if size is None or mark is None:
            continue
        total += abs(size * mark)
    return total


def _effective_leverage(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    cap = _num(_rules(account).get("leverage_cap"))
    if cap is None:
        cap = 5.0
    equity = _equity(account)
    if equity is None or equity == 0:
        return []
    hits = []
    for leg in resolved:
        if not _leg_increases(leg, _position(account, leg["name"])):
            continue
        view = _market_view(account, leg["name"])
        mark = _mark(view)
        size = leg.get("size")
        position = _position(account, leg["name"])
        pos_size = _held_size(position)
        if mark is None or not isinstance(size, float) or pos_size is None:
            continue
        if position is not None and leg.get("side") == _same_side(position.get("side")):
            resulting = pos_size + size
        elif position is not None and size > pos_size:
            resulting = size - pos_size
        else:
            resulting = size
        lev = (resulting * mark) / equity
        if _gt(lev, cap):
            hits.append(_hit("HL-2", "block", f"max leverage {_lev(cap)}x (this change: {_lev(lev)}x)."))
    return hits


def _lev(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.1f}"


def _leverage_rules(action: Mapping[str, Any], account: Mapping[str, Any]) -> list[dict[str, str]]:
    cap = _num(_rules(account).get("leverage_cap"))
    if cap is None:
        cap = 5.0
    kind = str(action.get("type") or "")
    if kind == "updateLeverage":
        requested = _num(action.get("leverage"))
        if requested is None:
            return [_hit("HL-2", "block", "max leverage could not be read.")]
        if _gt(requested, cap):
            return [_hit("HL-2", "block", f"max leverage {_lev(cap)}x (requested {_lev(requested)}x).")]
        return []
    resulting = _num(action.get("resulting_leverage"))
    if resulting is None:
        return [_hit("HL-2", "block", "max leverage could not be read.")]
    if _gt(resulting, cap):
        return [_hit("HL-2", "block", f"max leverage {_lev(cap)}x (this change: {_lev(resulting)}x).")]
    return []


def _flip_rules(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
    kind: str,
) -> list[dict[str, str]]:
    hits = []
    triggered = kind in {"trigger", "trailingStop"}
    for leg in resolved:
        if leg.get("reduce_only"):
            continue
        position = _position(account, leg["name"])
        size = leg.get("size")
        if not isinstance(size, float):
            continue
        coin = _coin(leg)
        opened = _opened_size(leg, position)
        if opened is not None and _flip_blocked(leg, account, kind):
            continue
        if opened is not None:
            prefix = "if triggered, " if triggered else ""
            side_from = "long" if position and position.get("side") == "long" else "short"
            side_to = "short" if side_from == "long" else "long"
            hits.append(_hit(
                "HL-3", "confirm",
                f"{prefix}this order closes your {side_from} and opens a {side_to} of {_qty(opened)} {coin}.",
            ))
            continue
        if _recent_reversal(leg, account, now):
            hits.append(_hit(
                "HL-3", "confirm",
                f"this reverses your {coin} position from long to short within 10 minutes.",
            ))
    return hits


def _opened_size(leg: Mapping[str, Any], position: Optional[Mapping[str, Any]]) -> Optional[float]:
    if position is None:
        return None
    size = leg.get("size")
    pos_size = _held_size(position)
    if pos_size is None or not isinstance(size, float) or size <= pos_size:
        return None
    side = leg.get("side")
    pos_side = position.get("side")
    if (side == "sell" and pos_side == "long") or (side == "buy" and pos_side == "short"):
        return size - pos_size
    return None


def _flip_blocked(leg: Mapping[str, Any], account: Mapping[str, Any], kind: str) -> bool:
    frac = _exposure_fraction({"type": kind}, leg, account, kind)
    if frac is None:
        return False
    return _gt(frac, _block_line(account))


def _recent_reversal(leg: Mapping[str, Any], account: Mapping[str, Any], now: float) -> bool:
    if leg.get("reduce_only"):
        return False
    closes = account.get("recent_closes")
    if not isinstance(closes, list):
        return False
    window = _num(_rules(account).get("reversal_window_s"))
    if window is None:
        window = _WINDOW_S
    for row in closes:
        if not isinstance(row, Mapping):
            continue
        ts = _num(row.get("ts"))
        if ts is None or now - ts > window or now < ts:
            continue
        if str(row.get("market") or "") != leg["name"]:
            continue
        closed = row.get("closed_side")
        if closed == "long" and leg.get("side") == "sell":
            return True
        if closed == "short" and leg.get("side") == "buy":
            return True
    return False


def _price_rules(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    confirm_at = _num(_rules(account).get("price_confirm_frac"))
    block_at = _num(_rules(account).get("price_block_frac"))
    if confirm_at is None:
        confirm_at = 0.02
    if block_at is None:
        block_at = 0.04
    hits = []
    for leg in resolved:
        view = _market_view(account, leg["name"])
        mark = _mark(view)
        if mark is None or mark == 0:
            continue
        for label, raw in (("price", leg.get("price")), ("trigger", leg.get("trigger_px"))):
            if not isinstance(raw, float) or raw == 0:
                continue
            side = leg.get("side")
            if side == "buy":
                gap = (raw - mark) / mark
                word = "above"
                kind_word = "buy"
            else:
                gap = (mark - raw) / mark
                word = "below"
                kind_word = "sell"
            if gap <= 0:
                continue
            if label == "trigger":
                shown = "trigger price"
            else:
                shown = f"{kind_word} price"
            if _gt(gap, block_at):
                hits.append(_hit(
                    "HL-7", "block",
                    f"{shown} {_pct1(gap)}% {word} mark (max {_pct(block_at)}%).",
                ))
            elif _gt(gap, confirm_at):
                hits.append(_hit(
                    "HL-7", "confirm",
                    f"{shown} {_pct1(gap)}% {word} mark (max {_pct(block_at)}%).",
                ))
    return hits


def _sweep_rules(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    confirm_at = _num(_rules(account).get("price_confirm_frac"))
    if confirm_at is None:
        confirm_at = 0.02
    hits = []
    for leg in resolved:
        if leg.get("side") != "sell":
            continue
        view = _market_view(account, leg["name"])
        mark = _mark(view)
        bid = _num(view.get("best_bid"))
        price = leg.get("price")
        if mark is None or mark == 0 or bid is None or not isinstance(price, float):
            continue
        gap = (mark - bid) / mark
        if not _gt(gap, confirm_at):
            continue
        if price <= mark and price >= bid * 0.99:
            hits.append(_hit(
                "HL-7", "confirm",
                f"this sell would fill up to {_pct_trim(gap)}% below mark (thin book).",
            ))
    return hits


def _twap_allowed(resolved: list[dict[str, Any]], account: Mapping[str, Any]) -> bool:
    allow = _rules(account).get("twap_allowlist")
    if not isinstance(allow, (list, tuple, set)):
        return False
    names = {str(item) for item in allow}
    return all(leg["name"] in names for leg in resolved)


def _signer_age(action: Mapping[str, Any], account: Mapping[str, Any], now: float) -> Optional[float]:
    row = _signer_row(action, account)
    if row is None:
        return 0.0
    seen = _num(row.get("first_seen_ts"))
    if seen is None:
        return 0.0
    return now - seen


def _new_signer(age: Optional[float]) -> bool:
    return age is not None and age < _NEW_SIGNER_S


def _new_signer_rules(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
    increasing: bool,
) -> list[dict[str, str]]:
    age = _signer_age(action, account, now)
    if not _new_signer(age):
        return []
    row = _signer_row(action, account)
    prior = 0
    if row is not None:
        count = _num(row.get("orders"))
        if count is not None:
            prior = int(count)
    first_n = _num(_rules(account).get("new_signer_first_n"))
    if first_n is None:
        first_n = 3
    large = False
    cap = _num(_rules(account).get("new_signer_equity_frac"))
    if cap is None:
        cap = 0.10
    for leg in resolved:
        frac = _order_fraction(leg, account)
        if frac is not None and _gt(frac, cap):
            large = True
    if prior < int(first_n) or (large and increasing):
        seen_for = 0.0 if age is None else age
        return [_hit("HL-6", "confirm", f"first orders from an API key added {_age_phrase(seen_for)}.")]
    return []


def _age_phrase(seconds: float) -> str:
    if seconds < 3600:
        minutes = max(1, int(round(seconds / 60.0)))
        return f"{minutes} min ago"
    hours = max(1, int(round(seconds / 3600.0)))
    return f"{hours} h ago"


def _close_burst(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
    origin: str,
) -> list[dict[str, str]]:
    if origin in {"kill_switch", "tp_ladder"}:
        return []
    if not any(_is_close(leg, account) for leg in resolved):
        return []
    own = _num(_rules(account).get("own_30d_5min_max"))
    if own is None:
        own = 3
    floor = _num(_rules(account).get("close_burst_floor"))
    if floor is None:
        floor = 5
    limit = max(int(floor), int(2 * own + 1))
    count = 1
    seen = set()
    recent = account.get("recent_orders")
    if isinstance(recent, list):
        for row in recent:
            if not isinstance(row, Mapping) or row.get("closing") is not True:
                continue
            if str(row.get("origin") or "") in {"kill_switch", "tp_ladder"}:
                continue
            ts = _num(row.get("ts"))
            if ts is None or now - ts > _CLOSE_WINDOW_S or now < ts:
                continue
            oid = str(row.get("oid") or "")
            if oid and oid in seen:
                continue
            if oid:
                seen.add(oid)
            count += 1
    if count > limit:
        return [_hit(
            "HL-8", "confirm",
            f"{count} closes in 5 minutes, more than your limit of {limit}.",
        )]
    return []


def _is_close(leg: Mapping[str, Any], account: Mapping[str, Any]) -> bool:
    if leg.get("reduce_only"):
        return True
    position = _position(account, leg["name"])
    if position is None:
        return False
    size = leg.get("size")
    if not isinstance(size, float):
        return False
    side = leg.get("side")
    pos_side = position.get("side")
    return (side == "sell" and pos_side == "long") or (side == "buy" and pos_side == "short")


def _cancel_rules(
    action: Mapping[str, Any], account: Mapping[str, Any], now: float,
) -> list[dict[str, str]]:
    kind = str(action.get("type") or "")
    positions = account.get("positions")
    open_positions = []
    if isinstance(positions, Mapping):
        for name, row in positions.items():
            held = _num(row.get("size")) if isinstance(row, Mapping) else None
            if isinstance(row, Mapping) and held is not None and held > 0:
                open_positions.append((str(name), row))
    if kind == "scheduleCancel" and open_positions:
        name = str(action.get("market") or open_positions[0][0])
        coin = _coin({"name": name, "underlying": _underlying_name(account, name)})
        return [_hit("HL-10", "confirm", f"this removes the last stop on your {coin} position.")]
    removes = action.get("stop") is True or action.get("removes_last_stop") is True
    if not removes:
        return []
    age = _signer_age(action, account, now)
    if not _new_signer(age):
        return []
    name = str(action.get("market") or (open_positions[0][0] if open_positions else ""))
    row = None
    if isinstance(positions, Mapping):
        row = positions.get(name)
    if not isinstance(row, Mapping) or row.get("has_stop") is not True:
        return []
    coin = _coin({"name": name, "underlying": str(row.get("underlying") or _underlying_name(account, name))})
    return [_hit("HL-10", "confirm", f"this removes the last stop on your {coin} position.")]


def _underlying_name(account: Mapping[str, Any], name: str) -> str:
    positions = account.get("positions")
    if isinstance(positions, Mapping) and isinstance(positions.get(name), Mapping):
        return str(positions[name].get("underlying") or name)
    if name.endswith("-PERP"):
        return name[: -len("-PERP")]
    if ":" in name:
        return name.split(":", 1)[-1]
    return name


def _pm_bounds(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    hits = []
    for leg in resolved:
        price = leg.get("price")
        if not isinstance(price, float):
            hits.append(_hit("PM-5", "block", "price must be between 0 and 1."))
            continue
        if price <= 0 or price >= 1:
            hits.append(_hit("PM-5", "block", "price must be between 0 and 1."))
            continue
        view = _market_view(account, leg["name"])
        tick = _num(view.get("tick_size"))
        if tick is None:
            tick = 0.01
        if tick <= 0:
            continue
        steps = price / tick
        if abs(steps - round(steps)) > 1e-6:
            hits.append(_hit("PM-5", "block", "price is not on the market's tick."))
    return hits


def _pm_reference(view: Mapping[str, Any], side: object) -> Optional[float]:
    bid = _num(view.get("best_bid"))
    ask = _num(view.get("best_ask"))
    mid = _num(view.get("mid"))
    if bid is not None and ask is not None and (ask - bid) > 0.10 + 1e-9:
        if side == "sell":
            return bid
        if side == "buy":
            return ask
    return mid


def _pm_price(
    resolved: list[dict[str, Any]], account: Mapping[str, Any],
) -> list[dict[str, str]]:
    confirm_at = _num(_rules(account).get("pm_price_confirm"))
    block_at = _num(_rules(account).get("pm_price_block"))
    if confirm_at is None:
        confirm_at = 0.05
    if block_at is None:
        block_at = 0.15
    hits = []
    for leg in resolved:
        view = _market_view(account, leg["name"])
        price = leg.get("price")
        ref = _pm_reference(view, leg.get("side"))
        if not isinstance(price, float) or ref is None:
            continue
        side = leg.get("side")
        if side == "sell":
            gap = ref - price
            word = "sell"
        else:
            gap = price - ref
            word = "buy"
        if gap <= 0:
            continue
        shown_ref = _num(view.get("mid"))
        if shown_ref is None:
            shown_ref = ref
        if _gt(gap, block_at):
            hits.append(_hit("PM-2", "block", f"{word} at {_px(price)} when mid is {_px(shown_ref)}."))
        elif _gt(gap, confirm_at):
            hits.append(_hit("PM-2", "confirm", f"{word} at {_px(price)} when mid is {_px(shown_ref)}."))
    return hits


def _px(value: float) -> str:
    return f"{value:.2f}"


def _pm_depth(
    resolved: list[dict[str, Any]], account: Mapping[str, Any], increasing: bool,
) -> list[dict[str, str]]:
    cap = _num(_rules(account).get("pm_thin_frac"))
    if cap is None:
        cap = 0.50
    hits = []
    for leg in resolved:
        view = _market_view(account, leg["name"])
        depth = _num(view.get("depth"))
        size = leg.get("size")
        if depth is None:
            if increasing:
                hits.append(_hit("STALE", "block", "price data is older than 10 s."))
            continue
        if isinstance(size, float) and _gt(size, cap * depth):
            hits.append(_hit("PM-3", "confirm", "this order is bigger than half the book near mid."))
    return hits


def _exit_rules(
    action: Mapping[str, Any],
    account: Mapping[str, Any],
    now: float,
    origin: str,
    resolved: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    if origin in {"kill_switch", "tp_ladder"}:
        return []
    kind = str(action.get("type") or "")
    legs = resolved if resolved is not None else _legs_as_resolved(action, account)
    exits = []
    for leg in legs:
        if _is_pm_exit(action, leg):
            exits.append(leg)
    if kind == "merge":
        exits = [_merge_leg(action)]
    if not exits:
        return []
    window = _num(_rules(account).get("pm_exit_window_s"))
    if window is None:
        window = _WINDOW_S
    need = _num(_rules(account).get("pm_exit_markets"))
    if need is None:
        need = 3
    portion = _num(_rules(account).get("pm_exit_frac"))
    if portion is None:
        portion = 0.80
    covered = _covered_markets(account, now, window, portion)
    for leg in exits:
        value = _exit_value(leg, account)
        held = _held_value(account, leg["name"])
        if value is None or held is None or held == 0:
            continue
        if _gt(value / held, portion) or abs(value / held - portion) <= 1e-9:
            covered.add(leg["name"])
    if len(covered) < int(need):
        return []
    # The order that completes the Nth market is the one that adds it.
    fresh = [leg["name"] for leg in exits if leg["name"] in covered]
    if not fresh:
        return []
    prior = _covered_markets(account, now, window, portion)
    if len(prior) >= int(need):
        completing = True
    else:
        completing = len(covered) >= int(need) and len(prior) < int(need)
    if not completing:
        return []
    age = _signer_age(action, account, now)
    new = _new_signer(age)
    if new:
        phrase = _session_phrase(0.0 if age is None else age)
        return [_hit(
            "PM-1", "block",
            f"closing most positions in {int(need)} markets in 10 min from a session started {phrase}.",
        )]
    return [_hit("PM-1", "confirm", f"closing most positions in {int(need)} markets in 10 min.")]


def _legs_as_resolved(action: Mapping[str, Any], account: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for leg in _legs(action):
        resolved = _resolve_leg(leg, action, account)
        if resolved is not None:
            rows.append(resolved)
    return rows


def _merge_leg(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(action.get("market") or ""),
        "side": "sell",
        "size": _num(action.get("size")),
        "price": _num(action.get("price")),
        "reduce_only": True,
        "kind": "outcome",
        "underlying": str(action.get("market") or ""),
        "trigger_px": None,
        "tif": None,
    }


def _is_pm_exit(action: Mapping[str, Any], leg: Mapping[str, Any]) -> bool:
    if action.get("complement") is True or leg.get("complement") is True:
        return True
    return leg.get("side") == "sell"


def _held_value(account: Mapping[str, Any], name: str) -> Optional[float]:
    holdings = account.get("holdings")
    if not isinstance(holdings, Mapping):
        return None
    row = holdings.get(name)
    if not isinstance(row, Mapping):
        return None
    shares = _num(row.get("shares"))
    view = _market_view(account, name)
    bid = _num(view.get("best_bid"))
    if bid is None:
        bid = _num(view.get("mid"))
    if shares is None or bid is None:
        return None
    return shares * bid


def _exit_value(leg: Mapping[str, Any], account: Mapping[str, Any]) -> Optional[float]:
    size = leg.get("size")
    view = _market_view(account, leg["name"])
    bid = _num(view.get("best_bid"))
    if bid is None:
        bid = _num(view.get("mid"))
    if not isinstance(size, float) or bid is None:
        return None
    return size * bid


def _covered_markets(
    account: Mapping[str, Any], now: float, window: float, portion: float,
) -> set[str]:
    found: set[str] = set()
    recent = account.get("recent_exits")
    if not isinstance(recent, list):
        return found
    by_market: dict[str, float] = {}
    for row in recent:
        if not isinstance(row, Mapping):
            continue
        ts = _num(row.get("ts"))
        if ts is None or now - ts > window or now < ts:
            continue
        name = str(row.get("market") or "")
        value = _num(row.get("value"))
        if value is None:
            continue
        by_market[name] = by_market.get(name, 0.0) + value
    for name, value in by_market.items():
        held = _held_value(account, name)
        if held is None or held == 0:
            continue
        if value / held + 1e-9 >= portion:
            found.add(name)
    return found


def _session_phrase(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60.0)))
    return f"{minutes} min ago"


def _pm_new_key(
    action: Mapping[str, Any],
    resolved: list[dict[str, Any]],
    account: Mapping[str, Any],
    now: float,
) -> list[dict[str, str]]:
    age = _signer_age(action, account, now)
    if not _new_signer(age):
        return []
    cap = _num(_rules(account).get("new_signer_equity_frac"))
    if cap is None:
        cap = 0.10
    portfolio = _portfolio_value(account)
    if portfolio is None or portfolio == 0:
        return []
    for leg in resolved:
        if leg.get("side") != "sell" and action.get("complement") is not True:
            continue
        value = _exit_value(leg, account)
        if value is None:
            continue
        if _gt(value / portfolio, cap):
            row = _signer_row(action, account)
            prior = _num(row.get("sells")) if row else 0
            if prior is None:
                prior = 0
            if prior < 1:
                return [_hit("PM-4", "confirm", "first large sell from a key added today.")]
    return []


def _portfolio_value(account: Mapping[str, Any]) -> Optional[float]:
    holdings = account.get("holdings")
    if not isinstance(holdings, Mapping):
        return None
    total = 0.0
    seen = False
    for name, row in holdings.items():
        if not isinstance(row, Mapping):
            continue
        value = _held_value(account, str(name))
        if value is None:
            return None
        total += value
        seen = True
    if not seen:
        return None
    return total


def _shared_caps(
    account: Mapping[str, Any], now: float, increasing: bool,
) -> list[dict[str, str]]:
    if not increasing:
        return []
    hits = []
    cap = _num(_rules(account).get("order_rate_per_min"))
    if cap is None:
        cap = 30
    recent = account.get("recent_orders")
    count = 0
    if isinstance(recent, list):
        for row in recent:
            if not isinstance(row, Mapping) or row.get("risk_increasing") is not True:
                continue
            ts = _num(row.get("ts"))
            if ts is not None and now - ts <= 60:
                count += 1
    if count + 1 > int(cap):
        hits.append(_hit("RATE", "confirm", f"more than {int(cap)} risk-increasing orders in a minute."))
    loss_cap = _num(_rules(account).get("daily_loss_cap_frac"))
    loss = _num(account.get("daily_loss_frac"))
    if loss_cap is not None and loss is not None and _gt(loss, loss_cap):
        hits.append(_hit("DAILY", "block", "the day's loss is above your limit."))
    notional_cap = _num(_rules(account).get("daily_notional_cap_frac"))
    notional = _num(account.get("daily_notional_frac"))
    if notional_cap is not None and notional is not None and _gt(notional, notional_cap):
        hits.append(_hit("DAILY", "block", "the day's orders are above your limit."))
    return hits
