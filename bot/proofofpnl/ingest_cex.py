"""Normalize CEX (Bitget) raw fills into CSF fills.

Two adapters:
* ``fills_from_ccxt_trades`` — the live path: CCXT ``fetch_my_trades`` output
  (unified trade dicts, which carry fee + realized data) → CSF fills.
* ``fills_from_proof_file`` — reads the legacy ``live_trade_proof.json``. It reads
  ONLY the ``trades[]`` array and **never** the ``summary`` block (per the no-
  summary rule). Because that file has no per-fill fees and no prices on 2 of 3
  round-trips, the fills it yields are deliberately INCOMPLETE — which is the
  honest outcome: that file is not fills-grade evidence.

All fills default to the weakest tier ``cex_operator_signed`` unless fetched via a
TEE (``cex_tee_attested`` — not implemented in v0).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from bot.proofofpnl.csf import make_fill, make_funding

_DEFAULT_TIER = "cex_operator_signed"


def _iso_ms(s: str) -> int:
    """ISO-8601 (…Z) → integer ms epoch. Parses a given string; no clock read."""
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fills_from_ccxt_trades(trades: list[dict], *, venue: str = "bitget",
                           trust_tier: str = _DEFAULT_TIER) -> list[dict]:
    """CCXT unified trade dicts → CSF fills. Expects each trade to carry
    ``price``, ``amount``, ``side``, ``timestamp`` (ms), ``symbol``, ``id``,
    ``order`` and ``fee`` ({cost, currency}). A trade with no ``fee`` object yields
    an incomplete fill (fee=None) — CCXT normally provides it for Bitget."""
    out: list[dict] = []
    for t in trades or []:
        fee_obj = t.get("fee") or {}
        fee_cost = fee_obj.get("cost")           # None if absent → incomplete
        fee_ccy = fee_obj.get("currency", "")
        source_ref = f"{t.get('id', '')}@{t.get('order', '')}"
        out.append(make_fill(
            venue=venue, venue_type="cex", market=str(t.get("symbol", "")),
            side=str(t.get("side", "")),
            price=t.get("price"), qty=t.get("amount"),
            fee=fee_cost, fee_ccy=fee_ccy,
            ts=int(t.get("timestamp") or 0), source_ref=source_ref,
            trust_tier=trust_tier,
        ))
    return out


def funding_from_ccxt_history(entries: list[dict], *, markets: list[str],
                              venue: str = "bitget",
                              trust_tier: str = _DEFAULT_TIER) -> list[dict]:
    """CCXT ``fetch_funding_history`` output → CSF funding records.

    ``markets`` is the set of perpetual markets the caller ACTUALLY QUERIED, and
    it is not optional. It is what separates "this window had no funding
    settlements" from "nobody asked", which the entries alone cannot say — the
    same distinction `orphan_position.py` carries as `orders_read`, and the one
    `compute_metrics` refuses to guess. A queried market with no entries gets a
    zero-amount record so the epoch can still reconcile; a market never queried
    gets nothing, and `reconcile` then names it.

    SIGN CONVENTION. CCXT reports ``amount`` from the account's point of view —
    negative paid, positive received — and `make_funding` keeps it signed for
    that reason. An entry with no usable ``amount`` yields an incomplete record
    (amount=None) rather than a zero, so a venue that answers with a blank does
    not read as a settlement that cost nothing.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for e in entries or []:
        market = str(e.get("symbol", ""))
        seen.add(market)
        ref = str(e.get("id") or f"{market}@{e.get('timestamp', 0)}")
        out.append(make_funding(
            venue=venue, venue_type="cex", market=market,
            amount=e.get("amount"), ccy=str(e.get("code", "")),
            ts=int(e.get("timestamp") or 0), source_ref=ref,
            trust_tier=trust_tier,
        ))
    # Coverage: a market we queried and found nothing for is a MEASURED zero.
    for market in markets or []:
        if str(market) in seen:
            continue
        out.append(make_funding(
            venue=venue, venue_type="cex", market=str(market),
            amount=0, ccy="", ts=0,
            source_ref=f"scanned:{market}", trust_tier=trust_tier,
        ))
    return out


def fills_from_proof_file(path: str, *, trust_tier: str = _DEFAULT_TIER) -> list[dict]:
    """Read ``live_trade_proof.json`` → CSF fills from ``trades[]`` ONLY.

    Never touches the ``summary`` block. Each round-trip becomes a buy fill + a
    sell fill. Fees are absent in that file → fee=None → INCOMPLETE (honest). RT#2
    and RT#3 have no prices → also INCOMPLETE. The point of this adapter is to
    prove the pipeline *refuses* to publish a non-fills-grade record."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    # Read strictly from trades[] — do NOT read doc["summary"].
    trades = doc.get("trades", [])
    venue = str(doc.get("exchange", "bitget")).lower()
    out: list[dict] = []
    for rt in trades:
        symbol = str(rt.get("symbol", ""))
        ts = _iso_ms(rt.get("timestamp", "1970-01-01T00:00:00Z"))
        buy_qty = rt.get("buy_qty", rt.get("buy_filled"))
        sell_qty = rt.get("sell_qty", rt.get("sell_filled", buy_qty))
        # buy fill
        out.append(make_fill(
            venue=venue, venue_type="cex", market=symbol, side="buy",
            price=rt.get("buy_price"), qty=buy_qty,
            fee=None, fee_ccy="",           # unknown fee → incomplete (by design)
            ts=ts, source_ref=str(rt.get("buy_order_id", "")),
            trust_tier=trust_tier,
        ))
        # sell fill (1 ms later so canonical order keeps buy→sell)
        out.append(make_fill(
            venue=venue, venue_type="cex", market=symbol, side="sell",
            price=rt.get("sell_price"), qty=sell_qty,
            fee=None, fee_ccy="",
            ts=ts + 1, source_ref=str(rt.get("sell_order_id", "")),
            trust_tier=trust_tier,
        ))
    return out
