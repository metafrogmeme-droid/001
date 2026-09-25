"""What a native-value transfer is worth in USD — read by the bot, never told.

`/web3/sign` used to authorize one number and sign another. It signed whatever
``value_wei`` the client sent, and asked the Authority Envelope about a
separate, client-supplied ``amount_usd`` that the shipped dashboard always sends
as ``null``. Driven under an envelope capped at $1 a trade and $2 a day, 1000
test ETH were signed and broadcast; a second request claiming ``amount_usd:
0.5`` for the same 1000 ETH, labelled ``asset: USDC`` to walk past an ETH
blocklist, was signed too. The envelope was never asked about the transaction
it authorized.

So the notional is computed HERE, from the exact ``value_wei`` that is signed
and a mark this process reads itself, and the asset is the network's own
native coin from the gate's table — not a word from the request.

THREE OUTCOMES, because two of them look alike from a distance:

  * a ``value_wei`` of 0 is a MEASURED $0 — a pure contract call moves no
    value, and that is a reading whether or not a mark could be read;
  * a positive ``value_wei`` with a readable mark is ``value × mark``;
  * a positive ``value_wei`` with NO readable mark is ``None`` — unpriced — and
    the signer refuses it by name. It is never $0: $0 clears every cap there is.

TESTNET COINS HAVE NO MARKET PRICE, and that is decided rather than dodged: a
testnet transfer is priced at the MAINNET mark of the same coin. The envelope's
ceilings describe the operator's exposure habits, and a rehearsal is bound by
the dollar caps the real thing will be. The alternative — refusing every
testnet transfer under a capped envelope because its coin is "worthless" —
would make the caps untestable exactly where they are meant to be rehearsed.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from bot.core.position_telemetry import price_on_record
from bot.web import web3_exec_gate as gate

#: Every native coin in ``web3_exec_gate.NETWORKS`` has 18 decimals.
NATIVE_DECIMALS = 18

#: How long to wait on the venue before calling the mark unreadable.
MARK_TIMEOUT_S = 8.0

#: The sentence every surface that shows a testnet notional carries beside it.
TESTNET_PRICING = ("testnet coins have no market price; this is priced at the "
                   "mainnet mark of the same coin, because the envelope's caps "
                   "describe exposure habits a rehearsal should be bound by")


def native_asset(network: str) -> Optional[str]:
    """The network's native coin from the gate's table, or None when unknown."""
    net = gate.resolve_network(network)
    coin = (net or {}).get("native")
    return str(coin).upper() if coin else None


def notional_usd(value_wei: int, mark: Optional[float]) -> Optional[float]:
    """USD value of ``value_wei`` of a native coin at ``mark``.

    0 wei is a measured $0 whatever the mark. A positive value with no mark is
    None — unpriced — never 0. A negative value is not a transfer at all."""
    v = int(value_wei)
    if v < 0:
        return None
    if v == 0:
        return 0.0
    if mark is None:
        return None
    return v / (10 ** NATIVE_DECIMALS) * float(mark)


async def read_native_mark(engine: Any, native: Optional[str]) -> tuple[Optional[float], str]:
    """The USD mark of ``native``, read now from the engine's crypto exchange.

    Returns ``(price, "")`` or ``(None, reason)``. The reason names the failure
    by CLASS and never quotes the venue's text, which can echo request
    parameters. ``price_on_record`` is the reading: a zero, a NaN or a missing
    ``last`` is a level nobody stated, never a price."""
    if not native:
        return None, "this network's native coin is not named"
    try:
        exchange = await engine.get_exchange("Crypto")
        ticker = await asyncio.wait_for(
            exchange.fetch_ticker(f"{native}/USDT"), MARK_TIMEOUT_S)
    except Exception as exc:                                    # noqa: BLE001
        return None, f"the {native} mark could not be read ({type(exc).__name__})"
    px = price_on_record((ticker or {}).get("last") if isinstance(ticker, dict) else None)
    if px is None:
        return None, f"the venue answered no usable {native} price"
    return px, ""
