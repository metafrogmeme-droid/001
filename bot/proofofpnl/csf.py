"""Common Statement Format v0 — the deterministic, provenance-honest core.

Pure: no engine, exchange, clock, or network. A ``Fill`` and an ``Epoch`` are
plain dicts (JSON-native) so ``verify.py`` can re-hash them byte-for-byte without
importing this package. Numbers are carried as **decimal strings** (never floats)
so the canonical form — and therefore every hash — is reproducible on any machine.

Two invariants this module enforces mechanically:

1. **Determinism.** ``canonical(obj)`` → the same bytes for the same logical value
   on any platform (sorted keys, no whitespace, decimal-string numbers). ``fill_hash``
   and the Merkle root are functions of those bytes only.
2. **Trust-tier honesty.** ``epoch_tier(fills)`` is the *minimum* tier across the
   fills. A weaker tier can never render as a stronger one — the headline tier is
   the epoch minimum, full stop.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

CSF_VERSION = "v0"

# Trust tiers, strongest → weakest. The integer is the ordering key; the epoch
# headline tier is the MINIMUM across its fills.
TRUST_TIERS = {
    "onchain_public": 3,       # re-derivable from public chain data
    "cex_tee_attested": 2,     # CEX fills fetched inside a TEE, attested
    "cex_operator_signed": 1,  # CEX fills signed by the operator only (weakest)
}


def _dec(x: Any) -> Optional[Decimal]:
    """Parse to Decimal, or None if not a finite number. Accepts int/float/str."""
    if x is None or isinstance(x, bool):
        return None
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    return d


def _numstr(x: Any) -> Optional[str]:
    """Canonical decimal string for a number: plain (no exponent), no trailing
    ``.0`` ambiguity. ``None`` passes through as None. Used for every numeric
    field so the hash is float-free and reproducible."""
    d = _dec(x)
    if d is None:
        return None
    # format 'f' → plain decimal notation; normalize the zero case.
    s = format(d, "f")
    return s


# Numeric fields that must be carried as canonical decimal strings in a Fill.
_FILL_NUMERIC = ("price", "qty", "fee")
# The full ordered field set of a canonical Fill (excluding fill_hash).
_FILL_FIELDS = ("venue", "venue_type", "market", "side", "price", "qty",
                "fee", "fee_ccy", "ts", "source_ref", "trust_tier")


def make_fill(venue: str, venue_type: str, market: str, side: str,
              price: Any, qty: Any, fee: Any, fee_ccy: str, ts: int,
              source_ref: str, trust_tier: str) -> dict:
    """Build a canonical Fill dict (with its ``fill_hash``). Numbers → decimal
    strings; ``side`` normalized to buy/sell; ``ts`` an integer ms epoch."""
    if trust_tier not in TRUST_TIERS:
        raise ValueError(f"unknown trust_tier {trust_tier!r}")
    if venue_type not in ("cex", "onchain"):
        raise ValueError(f"unknown venue_type {venue_type!r}")
    fill = {
        "venue": str(venue),
        "venue_type": str(venue_type),
        "market": str(market),
        "side": "sell" if str(side).lower().startswith("s") else "buy",
        "price": _numstr(price),
        "qty": _numstr(qty),
        # Unknown fee stays None → the fill is INCOMPLETE (cannot reconcile).
        # Callers pass fee=0 explicitly only when the fee is known to be zero.
        "fee": _numstr(fee),
        "fee_ccy": str(fee_ccy or ""),
        "ts": int(ts),
        "source_ref": str(source_ref),
        "trust_tier": str(trust_tier),
    }
    fill["fill_hash"] = fill_hash(fill)
    return fill


# ── funding: a cost that is not a fill ────────────────────────────────────
#
# THE AUDIT CALLED THIS "net edge is systematically overstated" AND THAT IS
# HALF RIGHT. `compute_metrics` emitted `"funding": "0"` with a comment saying
# perp funding was "PENDING (not in v0 data)" — an unmeasured cost rendered as
# a measured zero, on a statement whose own module docstring is about
# trust-tier honesty, inside the commitment hash, and re-derived identically by
# `verify.py` so the verifier could never catch its own blind spot.
#
# What saved it from publishing a false proof is the balance-delta check in
# `reconcile`: the signed close-open delta includes funding and `fills_net_pnl`
# does not, so the residual IS the funding and the epoch goes INCOMPLETE.
# Driven: a round trip that paid $3.20 of funding reconciles with residual
# -3.200 against a $0.01 tolerance.
#
# So the real cost was not a false headline. It was that EVERY perp epoch is
# unpublishable, and the reason given is the selective-omission alarm — the one
# `reconcile`'s docstring reserves for "a dishonest operator could sign only
# winning trades and drop the losers". A structural gap firing the fraud alarm
# is the "a heuristic is never a verdict" corollary, on the surface where a
# false accusation costs the most.
#
# FUNDING IS NOT A FILL and must not be modelled as a field on one. It is a
# periodic transfer on an OPEN position: no price, no side, no quantity, and it
# happens when no trade happens. It gets its own record, its own field tuple
# and its own hash — which also means every existing fill_hash, and therefore
# every v0 merkle root over fills alone, is byte-identical to before.
_FUNDING_FIELDS = ("venue", "venue_type", "market", "amount", "ccy", "ts",
                   "source_ref", "trust_tier")

#: What a funding record's `kind` says. Present on funding records only, so a
#: fill dict is unchanged and hashes exactly as it did in v0.
FUNDING_KIND = "funding"


def make_funding(venue: str, venue_type: str, market: str, amount: Any,
                 ccy: str, ts: int, source_ref: str, trust_tier: str) -> dict:
    """Build a canonical funding record (with its ``funding_hash``).

    ``amount`` is SIGNED in quote units and the sign is the account's: negative
    when the account paid, positive when it received. Both happen — funding is
    not a fee — so an unsigned magnitude would lose half the information.

    ``amount=None`` means the venue was asked and did not say, and it travels
    the same way an unknown fee does on a fill: the record is incomplete and the
    epoch cannot reconcile. Callers pass 0 only when funding is known to be
    zero.
    """
    if trust_tier not in TRUST_TIERS:
        raise ValueError(f"unknown trust_tier {trust_tier!r}")
    if venue_type not in ("cex", "onchain"):
        raise ValueError(f"unknown venue_type {venue_type!r}")
    rec = {
        "venue": str(venue),
        "venue_type": str(venue_type),
        "market": str(market),
        "amount": _numstr(amount),
        "ccy": str(ccy or ""),
        "ts": int(ts),
        "source_ref": str(source_ref),
        "trust_tier": str(trust_tier),
        "kind": FUNDING_KIND,
    }
    rec["funding_hash"] = funding_hash(rec)
    return rec


def funding_hash(rec: dict) -> str:
    """SHA-256 over the canonical funding record (excluding its own hash)."""
    core = {k: rec.get(k) for k in _FUNDING_FIELDS}
    return hashlib.sha256(canonical(core)).hexdigest()


def funding_is_complete(rec: dict) -> bool:
    """A funding record is complete only if it carries a usable amount."""
    return _dec(rec.get("amount")) is not None


def market_is_perp(market: Any) -> bool:
    """Whether a ccxt unified market symbol is a perpetual (so funding applies).

    ccxt spells a swap ``BASE/QUOTE:SETTLE`` and spot ``BASE/QUOTE``; the settle
    suffix is the discriminator and `ingest_cex` passes the unified symbol
    straight through. `venue_type` cannot answer this — it is only cex/onchain —
    and guessing from the quote currency would call every USDT market a perp.
    """
    return ":" in str(market or "")


def funding_applies(fills: list[dict]) -> bool:
    """Whether ANY fill in the epoch is on a market that pays funding.

    This is what makes "no funding records" readable. On a pure-spot epoch it
    means there was nothing to fetch; on an epoch holding a perp it means nobody
    fetched it, and those must not produce the same number.
    """
    return any(market_is_perp(f.get("market")) for f in fills or [])


def is_funding(rec: dict) -> bool:
    """Whether a record is a funding transfer rather than a fill.

    Keyed on the explicit ``kind``, never on "does it lack a price": a fill
    whose price the venue did not state also lacks one, and that is exactly the
    incomplete-fill case the epoch must keep reporting as an incomplete FILL.
    """
    return isinstance(rec, dict) and rec.get("kind") == FUNDING_KIND


def split_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(fills, funding)``. One door, so no caller re-derives the split."""
    fills: list[dict] = []
    funding: list[dict] = []
    for r in records or []:
        (funding if is_funding(r) else fills).append(r)
    return fills, funding


def canonical(obj: Any) -> bytes:
    """Deterministic bytes for any JSON-native value: sorted keys, no whitespace,
    UTF-8. Numbers must already be strings (we never hash floats)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def fill_hash(fill: dict) -> str:
    """SHA-256 over the canonical fill (excluding the ``fill_hash`` field itself)."""
    core = {k: fill.get(k) for k in _FILL_FIELDS}
    return hashlib.sha256(canonical(core)).hexdigest()


def fill_is_complete(fill: dict) -> bool:
    """A fill is metrics-complete only if it carries a usable price, qty and fee.
    Incomplete fills force the epoch to ``INCOMPLETE`` (they cannot reconcile)."""
    return (_dec(fill.get("price")) is not None
            and _dec(fill.get("qty")) is not None
            and _dec(fill.get("fee")) is not None)


def canonical_order(fills: list[dict]) -> list[dict]:
    """Canonical fill ordering: (ts, source_ref). Stable + reproducible."""
    return sorted(fills, key=lambda f: (int(f.get("ts", 0)), str(f.get("source_ref", ""))))


#: Stands in for a leaf whose hash is missing or malformed. A constant rather
#: than a raise: the root then differs from any honest one, which is the FAIL a
#: verifier can report, and it is not a valid hash of anything so it cannot
#: collide with a real leaf.
_UNHASHABLE = "f" * 64


def _is_hex64(h: str) -> bool:
    """Whether a string is a well-formed SHA-256 hex digest."""
    if len(h) != 64:
        return False
    try:
        bytes.fromhex(h)
    except ValueError:
        return False
    return True


def record_hash(rec: dict) -> str:
    """The hash a record carries, whichever kind it is.

    ONE DOOR, so no caller re-derives the dispatch. A funding record is hashed
    over its own fields and carries ``funding_hash``; a fill is untouched and
    carries ``fill_hash``, byte-identical to v0 — which is what lets an epoch
    with no funding produce exactly the merkle root it produced before.
    """
    return funding_hash(rec) if is_funding(rec) else fill_hash(rec)


def merkle_root(records: list[dict]) -> str:
    """Merkle root over the records' hashes, in canonical order.
    Uses the same SHA-256 bottom-up construction as ``bot.utils.attestation``
    (odd node duplicated), re-implemented here so ``verify.py`` needs no bot deps.

    Funding records are leaves like fills: the whole point of carrying funding
    is that it is bound by the commitment, and a cost outside the merkle tree
    would be a cost a verifier cannot check.
    """
    # A RECORD WITH NO USABLE HASH MUST FAIL VERIFICATION, NOT CRASH IT.
    # mypy caught this on the commit that introduced it: `r.get("funding_hash")`
    # is Optional, and `bytes.fromhex(None)` raises TypeError. `verify.py` runs
    # this over a statement supplied by whoever is being verified, so an
    # unparseable leaf has to come back as a mismatched root — a clean FAIL —
    # rather than an exception out of the verifier. A crash is not a verdict.
    leaves: list[str] = []
    for r in canonical_order(records):
        h = r.get("funding_hash") if is_funding(r) else r.get("fill_hash")
        leaves.append(h if isinstance(h, str) and _is_hex64(h) else _UNHASHABLE)
    if not leaves:
        return "0" * 64
    nodes = [bytes.fromhex(h) for h in leaves]
    while len(nodes) > 1:
        nxt = []
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            right = nodes[i + 1] if i + 1 < len(nodes) else left
            nxt.append(hashlib.sha256(left + right).digest())
        nodes = nxt
    return nodes[0].hex()


def epoch_tier(fills: list[dict]) -> str:
    """The headline trust tier = the MINIMUM tier across all fills. This is the
    honesty invariant: a statement can never claim more than its weakest fill."""
    if not fills:
        return "cex_operator_signed"
    tiers = [str(f.get("trust_tier", "cex_operator_signed")) for f in fills]
    return min(tiers, key=lambda t: TRUST_TIERS.get(t, 0))


# ── fills-only metrics ────────────────────────────────────────────────

def compute_metrics(fills: list[dict]) -> dict:
    """Derive metrics from fills ONLY (never a summary): realized PnL via FIFO lot
    matching per market, fees, profit factor, round-trip count, and Sharpe / max
    drawdown from the realized-PnL equity curve. Quote-currency PnL.

    Returns decimal-string numbers. Raises nothing; unusable fills are skipped and
    reflected by ``round_trips`` being lower than the fill count (the caller's
    completeness gate is what refuses to publish)."""
    all_records = canonical_order(fills)
    fills, funding_records = split_records(all_records)
    ordered = canonical_order(fills)

    # FUNDING: measured, or explicitly not. `None` is the third value and it is
    # the whole point — see `make_funding`. A record whose amount the venue did
    # not state makes the TOTAL unmeasurable, because a partial sum printed as a
    # whole is the shape this repo's honesty gate exists to catch.
    funding_total: Optional[Decimal] = None
    _running = Decimal(0)
    _all_readable = True
    for fr in funding_records:
        amt = _dec(fr.get("amount"))
        if amt is None:
            _all_readable = False
            break
        _running += amt
    if _all_readable:
        funding_total = _running

    # NO RECORDS IS NOT ZERO ON A MARKET THAT PAYS FUNDING, and the first draft
    # of this fix got that wrong in exactly the way the original did: it summed
    # an empty list to 0 and reported a measured zero for an epoch nobody had
    # fetched funding for. `orphan_position.py` states the rule for its own
    # case — "an empty map means 'no stops found' only if somebody looked".
    #
    # A spot epoch has nothing to look for, so 0 is a real reading there. An
    # epoch holding a perp with no funding record at all has an unmeasured cost.
    # An ingestor that scanned and genuinely found none says so by emitting a
    # zero-amount record, which is a measurement and survives this.
    if funding_total is not None and not funding_records and funding_applies(ordered):
        funding_total = None

    # market -> [[qty, price, fee_per_unit], ...] open long lots (FIFO)
    lots: dict[str, list[list[Decimal]]] = {}
    realized: list[Decimal] = []                # per-close NET realized PnL (quote)
    total_fees = Decimal(0)
    gross_win = Decimal(0)
    gross_loss = Decimal(0)

    for f in ordered:
        price, qty = _dec(f.get("price")), _dec(f.get("qty"))
        fee = _dec(f.get("fee")) or Decimal(0)
        total_fees += fee
        if price is None or qty is None or qty <= 0:
            continue
        market = str(f.get("market", ""))
        book = lots.setdefault(market, [])
        if f.get("side") == "buy":
            fee_per_unit = (fee / qty) if qty > 0 else Decimal(0)
            book.append([qty, price, fee_per_unit])
            continue
        # sell → match FIFO against open longs. Charge BOTH the matched entry fees
        # (carried on the lot) and this exit fill's fee, pro-rated by matched qty,
        # so Σ(net realized) == Σ(gross) − Σ(all fees) for a flat→flat epoch.
        remaining = qty
        pnl = Decimal(0)
        while remaining > 0 and book:
            lot = book[0]
            take = min(remaining, lot[0])
            entry_fee = take * lot[2]
            exit_fee = fee * (take / qty) if qty > 0 else Decimal(0)
            pnl += take * (price - lot[1]) - entry_fee - exit_fee
            lot[0] -= take
            remaining -= take
            if lot[0] <= 0:
                book.pop(0)
        realized.append(pnl)
        if pnl >= 0:
            gross_win += pnl
        else:
            gross_loss += -pnl

    # Funding is a realized cash movement on the account over the epoch, so it
    # belongs in net P&L beside fees. It is NOT per-round-trip — it accrues on
    # whatever was open when each settlement landed — so it is added once at the
    # epoch level rather than attributed to a lot.
    #
    # `net_pnl` is None when funding was not measured. That propagates: a number
    # that silently dropped a real cost is worse than an absent one, and
    # `reconcile` reads this to say what is actually missing.
    fills_net = sum(realized, Decimal(0))
    net_pnl = (fills_net + funding_total) if funding_total is not None else None
    pf = (gross_win / gross_loss) if gross_loss > 0 else (Decimal(0) if gross_win == 0 else Decimal(-1))
    sharpe = _sharpe(realized)
    max_dd = _max_drawdown([sum(realized[:i + 1], Decimal(0)) for i in range(len(realized))])

    return {
        "net_pnl": _numstr(net_pnl),
        "fills_net_pnl": _numstr(fills_net),
        "fees": _numstr(total_fees),
        # WAS `"0"` WITH A COMMENT SAYING THE DATA WAS NOT THERE. `_numstr`
        # passes None through and every other field already relies on that, so
        # the honest value was always available and a literal was chosen over it.
        "funding": _numstr(funding_total),
        "pf": (_numstr(pf) if pf >= 0 else "inf"),
        "sharpe": _numstr(sharpe),
        "max_dd": _numstr(max_dd),
        "round_trips": len(realized),
    }


def _sharpe(returns: list[Decimal]) -> Decimal:
    """Sharpe of the per-close realized-PnL series (0 risk-free, unannualized).
    Deterministic: mean/stdev via Decimal. Returns 0 when < 2 closes or zero var."""
    n = len(returns)
    if n < 2:
        return Decimal(0)
    mean = sum(returns, Decimal(0)) / n
    var = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (n - 1)
    if var <= 0:
        return Decimal(0)
    std = var.sqrt()
    return (mean / std) if std > 0 else Decimal(0)


def _max_drawdown(curve: list[Decimal]) -> Decimal:
    """Max peak-to-trough drawdown (absolute quote units) of a cumulative curve."""
    if not curve:
        return Decimal(0)
    peak = curve[0]
    mdd = Decimal(0)
    for v in curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
    return mdd
