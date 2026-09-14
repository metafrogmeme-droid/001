"""The caller's own net-worth reading — one reading, two surfaces.

`/networth` (bot/skills/portfolio_commands.py) and the web gateway's
`GET /gateway/networth` (bot/web/user_gateway.py) each carried a byte-for-byte
copy of the same read: the paper snapshot plus one read-only balance fetch on
the venue the caller connected. Two copies of one reading are two answers,
and the two had already drifted on the one branch that matters — a credential
store that RAISED. The web copy stamped `error: cex_unavailable` on the
answer; the command copy folded it into `{"connected": False}`, which the card
renders as "Exchange: not connected — /connect to link one". An exchange that
could not be asked is not an exchange nobody linked, and the door under that
sentence sends the caller to re-link an account they may have linked already.

Read-only. Credentials are decrypted in-process for the fetch and never leave
this function; nothing here can place, modify or cancel an order.
"""
from __future__ import annotations

import asyncio
from typing import Any

from bot.utils.logger import audit, system_log

#: How long one balance fetch may take before the venue is reported as having
#: timed out — the venue's answer, not the store's.
BALANCE_TIMEOUT_S = 25


async def networth_reading(engine: Any, user_id: str) -> dict:
    """``{"paper": {...} | None, "cex": {...}}`` for one caller.

    ``paper`` is None when the paper book could not be snapshotted (the card
    says "no snapshot yet"). ``cex`` is four things, and the card has a word
    for each:

    * ``{"connected": False}`` — the store holds no venue for this caller;
    * ``{"connected": True, "equity_usd": None, "detail": ...}`` — a venue is
      linked and its balance could not be read (credentials that will not
      decrypt, or a venue that did not answer in time);
    * ``{"connected": True, **snapshot}`` — read;
    * ``{"connected": False, "error": "cex_unavailable"}`` — the credential
      store itself could not be asked. NOT "not connected": the ``error`` key
      is what keeps the two apart downstream, and the wire shape is the one
      the web endpoint has always sent.
    """
    paper = None
    try:
        snap = engine.user_portfolios.get(user_id).snapshot()
        paper = {"equity_usd": round(float(snap.equity_usd), 2),
                 "total_pnl": round(float(snap.total_pnl), 2),
                 "simulated": True}
    except Exception:
        paper = None                                   # the card says so
    cex: dict = {"connected": False}
    try:
        from bot.core.exchange_credentials import (balance_snapshot,
                                                   get_credential_store)
        store = get_credential_store()
        if store.has(user_id):
            venue = store.get_venue(user_id)
            fields = store.get(user_id)
            if not fields:
                cex = {"connected": True, "venue": venue, "ok": False,
                       "equity_usd": None, "detail": "credentials unreadable"}
            else:
                try:
                    snap_cex = await asyncio.wait_for(
                        balance_snapshot(venue, fields), timeout=BALANCE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    snap_cex = {"ok": False, "venue": venue,
                                "equity_usd": None, "detail": "venue timeout"}
                cex = {"connected": True, **snap_cex}
    except Exception as exc:
        audit(system_log, f"Net-worth CEX read failed for {user_id}: {exc}",
              action="networth_read", result="ERROR")
        cex = {"connected": False, "error": "cex_unavailable"}
    return {"paper": paper, "cex": cex}
