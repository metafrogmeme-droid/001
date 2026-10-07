"""What an account-equity figure includes: the one list, for the producer and
the cards that describe it.

`LiveExecutor.fetch_balance` reads the account's equity from the first of
these fields the venue's response carries, which include open positions'
unrealized profit and loss, and otherwise falls back to the coin's wallet
balance, which does not. It reports which in ``equity_source``. A card that
says "open positions marked to market included" is true only of the first
kind; the drawdown tier card said it of every live reading.
"""
from __future__ import annotations

from typing import Optional

#: Venue fields that state equity with open positions marked to market, in
#: the order `fetch_balance` reads them. The unified-account envelope's
#: figure is ``usdtEquity`` too.
MARKED_EQUITY_FIELDS: tuple[str, ...] = ("usdtEquity", "accountEquity", "equity")

#: `fetch_balance`'s fallback: the coin's wallet balance, unrealized excluded.
WALLET_BALANCE = "wallet_total"

MARKED = "marked"
WALLET = "wallet"


def equity_basis(source: object) -> Optional[str]:
    """``MARKED``, ``WALLET``, or None when the source is not one we know."""
    if source in MARKED_EQUITY_FIELDS:
        return MARKED
    if source == WALLET_BALANCE:
        return WALLET
    return None
