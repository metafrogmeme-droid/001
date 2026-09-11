"""CEX selective-omission defense — completeness + balance-delta.

The weakness of a CEX (Bitget) track record: an outsider cannot re-fetch the
fills, so a dishonest operator could sign only winning trades and drop the
losers. This module makes that attack fail loudly.

An epoch may reach ``published`` only if BOTH hold:

1. **Completeness** — every fill carries a usable price, qty AND fee. A fill with
   an unknown fee or price cannot contribute a reconcilable PnL, so the epoch is
   ``INCOMPLETE``. (This is exactly why the legacy ``live_trade_proof.json`` — no
   fees, no prices on 2 of 3 round-trips — cannot be published.)
2. **Balance-delta reconciliation** — the net realized PnL derived *from the
   fills* must equal the signed ``close − open`` balance delta within a tight
   tolerance. Omitting a losing fill inflates fills-PnL above the real balance
   change, so the reconciliation fails and the epoch stays ``INCOMPLETE``.

Pure: takes fills + two balance numbers. The balances come from *signed
snapshots* (see ``statement.py``), never from an exchange ``summary`` field.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.proofofpnl.csf import (
    _dec,
    compute_metrics,
    fill_is_complete,
    funding_applies,
    funding_is_complete,
    split_records,
)

# Default reconciliation tolerance (quote units). The balance delta should equal
# fills-net-PnL exactly for a flat→flat epoch; a few cents of slack absorbs
# venue-side rounding. Fees are already inside net_pnl, so this is tight.
DEFAULT_TOLERANCE = Decimal("0.01")


def completeness(records: list[dict]) -> tuple[bool, list[str]]:
    """(ok, reasons). ok only if there is ≥1 fill and every record is complete.

    FUNDING GETS ITS OWN REASON, AND THAT IS THE POINT. Before funding was
    carried at all, a perp epoch failed the balance-delta check below instead:
    the signed close-open delta includes funding, `fills_net_pnl` did not, and
    the residual WAS the funding. Driven, a round trip paying $3.20 reconciled
    with residual -3.200 against a $0.01 tolerance.

    So the epoch did not publish a false proof — but the reason it gave was the
    selective-omission alarm, the one this module's docstring reserves for "a
    dishonest operator could sign only winning trades and drop the losers". A
    structural gap firing the fraud alarm is the corollary this repo states as
    "a heuristic is never a verdict": the check ruled out reconciliation, and
    then named a cause it had not established.
    """
    fills, funding = split_records(records)
    if not fills:
        return False, ["no fills"]
    reasons = []
    for f in fills:
        if not fill_is_complete(f):
            missing = [k for k in ("price", "qty", "fee") if _dec(f.get(k)) is None]
            reasons.append(f"{f.get('source_ref', '?')}: missing {', '.join(missing)}")
    for fr in funding:
        if not funding_is_complete(fr):
            reasons.append(f"{fr.get('source_ref', '?')}: funding amount not stated "
                           f"by the venue")
    if funding_applies(fills) and not funding:
        markets = sorted({str(f.get("market")) for f in fills
                          if ":" in str(f.get("market", ""))})
        reasons.append(
            "funding never fetched for perpetual market(s) "
            f"{', '.join(markets)} — an epoch holding a perp has a funding cost "
            "whether or not it was measured; an ingestor that scanned and found "
            "none records a zero-amount funding entry to say so")
    return (len(reasons) == 0), reasons


def reconcile(records: list[dict],
              open_balance: Optional[object],
              close_balance: Optional[object],
              tolerance: Decimal = DEFAULT_TOLERANCE) -> dict:
    """Run the full CEX defense. Returns::

        { "status": "published" | "INCOMPLETE",
          "reasons": [str, ...],
          "fills_net_pnl": str, "balance_delta": str|None, "residual": str|None }

    ``open_balance``/``close_balance`` are the signed-snapshot quote balances.
    Missing snapshots → cannot reconcile → INCOMPLETE (this is the honest outcome
    for any record whose only balances live in an untrusted ``summary``)."""
    reasons: list[str] = []

    ok, why = completeness(records)
    if not ok:
        reasons.extend(why)

    metrics = compute_metrics(records)
    # `net_pnl` is None when funding was not measured; `completeness` has
    # already said so by name. Reconciling against a number that silently
    # dropped a real cost would re-manufacture the misleading residual this
    # change exists to remove, so the balance check is skipped rather than run
    # on a figure known to be incomplete.
    net = _dec(metrics["net_pnl"])
    fills_net = net if net is not None else Decimal(0)

    ob, cb = _dec(open_balance), _dec(close_balance)
    balance_delta = None
    residual = None
    if net is None:
        pass                       # cause already named; no residual to compute
    elif ob is None or cb is None:
        reasons.append("missing signed open/close balance snapshot (no summary allowed)")
    else:
        balance_delta = cb - ob
        residual = (balance_delta - fills_net)
        if abs(residual) > tolerance:
            reasons.append(
                f"unreconciled: balance_delta {balance_delta} vs fills_net_pnl "
                f"{fills_net} (residual {residual} > tol {tolerance})")

    status = "published" if not reasons else "INCOMPLETE"
    return {
        "status": status,
        "reasons": reasons,
        "fills_net_pnl": str(fills_net),
        "balance_delta": (str(balance_delta) if balance_delta is not None else None),
        "residual": (str(residual) if residual is not None else None),
    }
